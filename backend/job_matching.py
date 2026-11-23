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
