"""Prepare application materials locally from explicitly saved resume facts."""

from builder import Master, render
from job_matching import ALIASES, contains


NEEDS_INPUT = "Needs your input."


def _answer(question, skills, examples):
    # Match the whole question. A familiar phrase inside a compound or
    # conditional question does not establish the rest of its premise.
    prompt = " ".join(question.casefold().split()).rstrip("?.")
    if prompt in {"what are your relevant skills", "what are your technical skills",
                  "which technical skills do you have", "list your relevant skills"}:
        return ", ".join(skills) or NEEDS_INPUT
    if prompt in {"describe a relevant project", "describe your relevant experience",
                  "what relevant experience do you have"}:
        section = "projects" if "project" in prompt else "experience"
        return "\n".join(examples[section][:2]) or NEEDS_INPUT
    for skill in skills:
        term = skill.casefold()
        if prompt in {f"do you know {term}", f"is {term} one of your skills"}:
            return f"My skills include {skill}."
    return NEEDS_INPUT


def generate_content(master: Master | None, posting: dict, questions: list[str],
                     selected: dict[str, set[str]] | None = None) -> dict:
    """Use posting words to select verified facts. Only master entries supply personal claims."""
    context = f"{posting['title']}\n{posting.get('description') or ''}"
    aliases = {alias.casefold(): names for names in ALIASES.values() for alias in names}
    selected = selected or {}
    matched = {}
    for skill in (master.skills if master else []):
        names = aliases.get(skill.casefold(), (skill,))
        if skill in selected.get("skills", ()) or any(contains(context, name) for name in names):
            matched[skill] = names
    terms = {term for names in matched.values() for term in names}
    examples = {section: [fact for fact in getattr(master, section, [])
                          if fact in selected.get(section, ()) or any(contains(fact, term) for term in terms)]
                for section in ("projects", "experience", "achievements")}
    paragraphs = ["Dear hiring team,",
                  f"I am applying for the {posting['title']} position at {posting['company']}."]
    if matched:
        paragraphs.append(f"My relevant skills include {', '.join(list(matched)[:5])}.")
    for entries in examples.values():
        if entries:
            fact = entries[0]
            paragraphs.append(fact if fact.endswith((".", "!", "?")) else fact + ".")
    paragraphs.append("Thank you for considering my application.")
    if master:
        paragraphs.append(f"Sincerely,\n{master.name}")
    answers = [{"question": question, "answer": _answer(question, list(matched), examples)}
               for question in questions]
    tasks = ["Review the job details.", "Review the tailored resume.", "Review the cover letter."]
    if questions:
        tasks.append("Review screening answers and complete any missing information.")
    tasks.append("Check the employer's application instructions.")
    return {"cover_letter": "\n\n".join(paragraphs), "answers": answers,
            "checklist": [{"text": task, "done": False} for task in tasks]}


def export_content(content: dict, artifact: str, format: str) -> bytes:
    if artifact == "cover_letter":
        body = content["cover_letter"]
    elif artifact == "answers":
        body = "\n\n".join(["Screening answers", *(
            item["question"] + "\n" + item["answer"] for item in content["answers"]
        )])
    elif artifact == "checklist":
        body = "\n".join(["Application checklist", *(
            ("[x] " if item["done"] else "[ ] ") + item["text"] for item in content["checklist"]
        )])
    else:
        raise ValueError("Unknown preparation artifact")
    if format == "txt":
        return body.encode("utf-8")
    if format in {"docx", "pdf"}:
        return render(body, format)
    raise ValueError("Unknown preparation format")

