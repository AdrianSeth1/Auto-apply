"""Interview prep pack builder — maps the profile's story bank onto a JD.

Deterministic (no LLM): given a job (title/company/description/requirements)
and a profile YAML dict containing ``story_bank`` entries
(``theme`` / ``context`` / ``action`` / ``result`` / ``applicable_to``),
produce a one-page markdown prep document:

  * role snapshot (must-have / preferred skills from the JD)
  * your strongest matching stories, ranked by JD relevance
  * likely behavioral questions per story theme
  * skill talking points (JD skills you actually have)

Pure functions only — file IO and DB access live in
:mod:`src.application.prep`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# Likely behavioral questions per story-bank theme. Generic enough to be
# useful for any client-facing / technical hybrid role; stories whose theme
# isn't listed fall back to _GENERIC_QUESTIONS.
_THEME_QUESTIONS: dict[str, list[str]] = {
    "ownership_impact": [
        "Tell me about a time you took ownership of a broken process.",
        "Describe a project where you drove measurable impact end to end.",
    ],
    "technical_challenge": [
        "Walk me through the hardest technical problem you've solved.",
        "Tell me about a time you had to learn a technology quickly to deliver.",
    ],
    "stakeholder_translation": [
        "How do you explain technical concepts to non-technical stakeholders?",
        "Tell me about a time engineering and the customer weren't aligned.",
    ],
    "relationship_management": [
        "Tell me about a difficult customer and how you handled them.",
        "How do you set expectations when you can't give someone what they want?",
    ],
    "conflict": [
        "Tell me about a disagreement with a teammate and how it resolved.",
    ],
    "failure": [
        "Tell me about a time you failed. What did you change afterwards?",
    ],
    "leadership": [
        "Describe a time you led without formal authority.",
    ],
    "ai_discovery": [
        "How would you scope an AI solution for a client who just says 'we want AI'?",
        "Walk me through how you learn a client's business quickly.",
    ],
    "automation_systems": [
        "Tell me about something you built to solve your own problem.",
        "How do you use AI tools in your own workflow, and where do you draw the line?",
    ],
    "quality_judgment": [
        "Tell me about applying structured judgment to an ambiguous task.",
        "How do you decide when an AI output is good enough to ship?",
    ],
    "science_communication": [
        "Explain something technical you know deeply as if I were a customer.",
        "How do you adjust a demo or presentation for a non-technical audience?",
    ],
}

_GENERIC_QUESTIONS = [
    "Tell me about a time this experience challenged you — what did you do?",
    "What would you do differently if you did this again?",
]

_STOP_WORDS = frozenset(
    "a an the is are was were be been have has had do does did will would "
    "shall should can could may might must need of in to for with on at by "
    "from as into through during and but if or because we you your they "
    "their this that these those it its what which who our".split()
)


def build_prep_pack(
    *,
    job: dict[str, Any],
    profile: dict[str, Any],
    top_stories: int = 4,
) -> str:
    """Render the prep pack markdown for ``job`` using ``profile``.

    ``job`` keys used: title, company, location, description,
    requirements ({must_have_skills, preferred_skills}), application_url,
    match_score (optional). Missing keys degrade to omitted sections.
    """
    title = job.get("title") or "Unknown Role"
    company = job.get("company") or "Unknown Company"
    description = job.get("description") or ""
    requirements = job.get("requirements") or {}
    must_have = [s for s in (requirements.get("must_have_skills") or []) if s]
    preferred = [s for s in (requirements.get("preferred_skills") or []) if s]

    stories = rank_stories(
        profile.get("story_bank") or [], title=title, description=description
    )[:top_stories]
    applicant_skills = _flatten_skills(profile.get("skills") or {})
    have_must = _matched_skills(must_have, applicant_skills)
    have_pref = _matched_skills(preferred, applicant_skills)

    lines: list[str] = []
    lines.append(f"# Interview Prep — {title} @ {company}")
    lines.append("")
    meta = [datetime.now(UTC).strftime("%Y-%m-%d")]
    if job.get("location"):
        meta.append(str(job["location"]))
    if job.get("match_score") is not None:
        meta.append(f"match score {job['match_score']:.2f}")
    lines.append(f"*{' · '.join(meta)}*")
    lines.append("")

    if must_have or preferred:
        lines.append("## What they're screening for")
        lines.append("")
        if must_have:
            lines.append(
                "**Must-have:** "
                + ", ".join(_mark_skills(must_have, have_must))
            )
        if preferred:
            lines.append(
                "**Preferred:** "
                + ", ".join(_mark_skills(preferred, have_pref))
            )
        lines.append("")
        lines.append("*(✓ = already on your profile — say these words out loud in answers.)*")
        lines.append("")

    if stories:
        lines.append("## Your strongest stories for this role")
        lines.append("")
        for rank, (story, _score) in enumerate(stories, 1):
            theme = str(story.get("theme") or "story")
            lines.append(f"### {rank}. {theme.replace('_', ' ').title()}")
            lines.append("")
            for label, key in (
                ("Situation", "context"),
                ("Action", "action"),
                ("Result", "result"),
            ):
                value = story.get(key)
                if value:
                    lines.append(f"- **{label}:** {value}")
            questions = _THEME_QUESTIONS.get(theme, _GENERIC_QUESTIONS)
            lines.append("- **Likely asked as:**")
            for question in questions:
                lines.append(f"  - {question}")
            lines.append("")

    lines.append("## Questions to ask them")
    lines.append("")
    for question in (
        f"What does success look like in the first 90 days for this {title} hire?",
        "What's the most common reason customers churn or escalate today?",
        "How does this team interact with product and engineering?",
    ):
        lines.append(f"- {question}")
    lines.append("")

    if job.get("application_url"):
        lines.append(f"*Posting: {job['application_url']}*")
        lines.append("")

    return "\n".join(lines)


def rank_stories(
    story_bank: list[dict[str, Any]],
    *,
    title: str,
    description: str,
) -> list[tuple[dict[str, Any], float]]:
    """Rank story-bank entries by relevance to the JD.

    Relevance = token overlap between the story text (context+action+result)
    and the JD, plus a boost when any ``applicable_to`` tag token appears in
    the JD/title (e.g. tag ``solutions_consulting`` matches a "Solutions
    Consultant" title).
    """
    jd_tokens = _tokens(f"{title} {description}")
    ranked: list[tuple[dict[str, Any], float]] = []
    for story in story_bank:
        if not isinstance(story, dict):
            continue
        text = " ".join(
            str(story.get(key) or "") for key in ("context", "action", "result")
        )
        story_tokens = _tokens(text)
        overlap = len(jd_tokens & story_tokens) / max(len(story_tokens), 1)
        tag_boost = 0.0
        for tag in story.get("applicable_to") or []:
            if _tokens(str(tag).replace("_", " ")) & jd_tokens:
                tag_boost = 0.3
                break
        ranked.append((story, round(overlap + tag_boost, 4)))
    ranked.sort(key=lambda pair: -pair[1])
    return ranked


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#]+", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 2}


def _flatten_skills(skills: dict[str, Any]) -> set[str]:
    flat: set[str] = set()
    for items in skills.values():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    flat.add(item.lower())
                elif isinstance(item, dict) and item.get("name"):
                    flat.add(str(item["name"]).lower())
    return flat


def _matched_skills(job_skills: list[str], applicant_skills: set[str]) -> set[str]:
    matched: set[str] = set()
    for skill in job_skills:
        lowered = skill.lower()
        if lowered in applicant_skills or any(
            lowered in have or have in lowered for have in applicant_skills
        ):
            matched.add(skill)
    return matched


def _mark_skills(skills: list[str], have: set[str]) -> list[str]:
    return [f"{skill} ✓" if skill in have else skill for skill in skills]
