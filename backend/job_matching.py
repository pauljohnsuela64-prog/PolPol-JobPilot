"""Compare explicit listing evidence with the saved search preferences."""

import re
from typing import Literal

from job_providers import Posting
from profile_routes import Preferences

Match = Literal["match", "uncertain", "missing", "not_match", "unconfigured"]
MONTHLY = {"month", "monthly", "per-month", "per month"}
EXPERIENCE = {
    "internship": r"\bintern(?:ship)?\b",
    "entry-level": r"\bentry[ -]level\b",
    "junior": r"\b(?:junior|jr\.?)\b",
}


def contains(value: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", value.casefold()) is not None


def compare(post: Posting, prefs: Preferences) -> tuple[Match, dict[str, str]]:
    # Values within title/location lists are alternatives. All listed skills are checked.
    checks = {}
    if prefs.job_titles:
        checks["Title"] = "match" if any(contains(post.title, title) for title in prefs.job_titles) else "not_match"
    if prefs.locations:
        checks["Location"] = ("missing" if not post.location else
                              "match" if any(contains(post.location, place) for place in prefs.locations) else
                              "uncertain" if contains(post.location, "remote") else "not_match")
    if prefs.technical_skills:
        checks["Skills"] = ("missing" if not post.description else
                            "match" if all(contains(post.description, skill) for skill in prefs.technical_skills)
                            else "uncertain")
    if prefs.work_arrangement != "any":
        checks["Work arrangement"] = ("missing" if post.arrangement is None else
                                      "match" if post.arrangement == prefs.work_arrangement else "not_match")
    if prefs.experience_level:
        title = post.title.lower()
        explicit = {level for level, pattern in EXPERIENCE.items() if re.search(pattern, title)}
        if re.search(r"\b(?:senior|sr\.?|principal|staff engineer|lead engineer)\b", title):
            explicit.add("senior")
        if explicit:
            checks["Experience"] = ("match" if explicit == {prefs.experience_level} else
                                    "uncertain" if prefs.experience_level in explicit else "not_match")
        else:
            evidence = f"{title} {post.description.lower()}"
            checks["Experience"] = ("uncertain" if any(re.search(pattern, evidence) for pattern in EXPERIENCE.values())
                                    or re.search(r"\b\d+\+? years?\b", evidence) else "missing")
    if prefs.minimum_salary_php is not None:
        pay = post.salary
        if pay is None:
            checks["Salary"] = "missing"
        elif pay.currency != "PHP" or pay.interval not in MONTHLY:
            checks["Salary"] = "uncertain"
        elif pay.maximum < prefs.minimum_salary_php:
            checks["Salary"] = "not_match"
        elif pay.minimum >= prefs.minimum_salary_php:
            checks["Salary"] = "match"
        else:
            checks["Salary"] = "uncertain"

    for status in ("not_match", "uncertain", "missing"):
        if status in checks.values():
            return status, checks
    return ("match" if checks else "unconfigured"), checks

WEIGHTS = {"Title": 25, "Skills": 30, "Experience": 15, "Location": 10, "Work arrangement": 10, "Salary": 10}
ALIASES = {
    "JavaScript": ("JavaScript", "JS"), "PostgreSQL": ("PostgreSQL", "Postgres"),
    "Python": ("Python",), "Active Directory": ("Active Directory",), "SQL": ("SQL",),
    "Java": ("Java",), "TypeScript": ("TypeScript",), "React": ("React",),
    "C++": ("C++",), "C#": ("C#",), "PowerShell": ("PowerShell",),
}
YEARS = re.compile(r"\b\d+(?:\s*[-–]\s*\d+)?\+? years?(?: of)? (?:relevant |professional )?experience\b", re.I)


def compatibility(post: Posting, prefs: Preferences, checks: dict[str, str]):
    """Award points for supported preferences. Confidence measures resolved evidence weight."""
    states = dict(checks)
    confirmed = {skill.casefold() for skill in prefs.technical_skills}
    vocabulary = dict(ALIASES)
    known = {alias.casefold() for aliases in ALIASES.values() for alias in aliases}
    for skill in prefs.technical_skills:
        if skill.casefold() not in known and len(skill) >= 3:
            vocabulary[skill] = (skill,)
    evidence = {}
    years = []
    stated_levels = set()
    for clause in re.split(r"[;!?\n]|\.\s+|,(?=\s*(?:preferred|required|optional)\b)", post.description):
        clause = clause.strip()
        level = re.search(r"\b(?:experience level|seniority)\s*:\s*(junior|entry[ -]level|internship|senior)\b", clause, re.I)
        if level and not re.search(r"\b(?:or|not)\b", clause, re.I):
            stated_levels.add(level[1].lower().replace(" ", "-"))
            years.append(level[0])
        for occurrence in YEARS.finditer(clause):
            years.append(occurrence.group())
        for skill, aliases in vocabulary.items():
            hit = next((alias for alias in aliases if contains(clause, alias)), None)
            if not hit:
                continue
            context = "mentioned"
            # Only an explicit heading or an immediate skill qualifier establishes its role.
            for label, pattern in (("preferred", r"preferred|nice to have|optional"),
                                   ("required", r"required|mandatory|must have")):
                if re.search(r"\b(?:not|no|without|or)\b", clause, re.I):
                    break
                if (re.match(rf"^(?:{pattern})(?: skills)?\s*:", clause, re.I)
                        or re.search(rf"(?<!\w){re.escape(hit)}\s+(?:is\s+)?(?:{pattern})\b", clause, re.I)):
                    context = label
                    break
            priority = {"mentioned": 0, "preferred": 1, "required": 2}
            if skill in evidence and priority[evidence[skill]["context"]] >= priority[context]:
                continue
            pos = clause.casefold().find(hit.casefold())
            evidence[skill] = {"skill": skill, "context": context,
                               "confirmed": any(alias.casefold() in confirmed for alias in aliases),
                               "excerpt": clause[max(0, pos-45):pos+95]}
    if prefs.experience_level and stated_levels:
        explicit_state = "match" if stated_levels == {prefs.experience_level} else "not_match"
        title_state = states["Experience"]
        states["Experience"] = ("uncertain" if len(stated_levels) > 1 or
                                (title_state in ("match", "not_match") and title_state != explicit_state)
                                else explicit_state)
    relevant = [entry for entry in evidence.values() if entry["context"] != "mentioned"]
    fraction = 0
    if relevant or prefs.technical_skills:
        states["Skills"] = "missing" if not evidence else "uncertain"
        if relevant:
            fraction = sum(entry["confirmed"] for entry in relevant) / len(relevant)
            if fraction == 1:
                states["Skills"] = "match"
    total = sum(WEIGHTS[key] for key in states)
    earned = sum(WEIGHTS[key] for key, state in states.items() if state == "match" and key != "Skills")
    resolved = sum(WEIGHTS[key] for key, state in states.items() if state in ("match", "not_match") and key != "Skills")
    skill_points = WEIGHTS["Skills"] * fraction
    return {"score": round(100 * (earned + skill_points) / total) if total else 0,
            "confidence": round(resolved + skill_points), "criteria": states,
            "skills": list(evidence.values()), "experience_evidence": list(dict.fromkeys(years))[:3]}
