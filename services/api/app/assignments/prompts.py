"""Prompts for the assignment planner.

Two calls, both returning JSON: first the brief is reduced to its
requirements, then a plan is drafted against those requirements and the
lecture material retrieved for them.

The model is asked to flag any step that relies on something outside the
supplied material. That flag is advisory: the deterministic coverage check in
:mod:`app.assignments.planner` makes the final grounding decision, and a step
the model calls grounded can still be marked as going beyond the slides.
"""

from __future__ import annotations

from app.agents.prompts import _GROUNDING_RULES, _INJECTION_GUARD
from app.models.base import EvidenceSnippet

REQUIREMENTS_SYSTEM = (
    "You read a university assignment brief and list exactly what it asks for.\n\n"
    f"{_INJECTION_GUARD}\n\n"
    "Return ONLY a JSON object, no prose, of the form:\n"
    "{\n"
    '  "summary": "one paragraph saying what the student must produce",\n'
    '  "requirements": [\n'
    "    {\n"
    '      "id": "r1",\n'
    '      "text": "what is required, in your own short words",\n'
    '      "kind": "deliverable" | "constraint" | "criterion" | "tool",\n'
    '      "quote": "the sentence or fragment of the brief this comes from"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- deliverable: something that must be handed in (a script, a report, a plot).\n"
    "- constraint: a rule the work must obey (word limit, sampling rate, format).\n"
    "- criterion: how it will be marked.\n"
    "- tool: software or a method the brief names (MATLAB, Python, a specific filter).\n"
    "- Keep the brief's order. Do not invent requirements that are not in the text.\n"
    "- The quote must be copied from the brief, not paraphrased."
)

PLAN_SYSTEM = (
    "You are StudyPilot, drafting a step-by-step guide for a student's assignment.\n\n"
    f"{_INJECTION_GUARD}\n\n"
    f"{_GROUNDING_RULES}\n\n"
    "The student's institution expects them to produce the work themselves. "
    "You must NOT write the code, the analysis, the report text or any answer. "
    "Each step says WHAT to do and WHY, and points to where the course material "
    "shows HOW. A step may name a tool the brief requires (for example MATLAB) "
    "and the functions or methods the lecture material teaches for it, but it "
    "never contains the student's implementation.\n\n"
    "Grounding is the whole point. The lecture material is supplied as numbered "
    "passages [1], [2], ... For every step:\n"
    "- If the material shows how to do it, cite the passages inline as [n] and set "
    '"beyond_slides": false.\n'
    "- If it needs a method, tool or fact that is NOT in the supplied material, set "
    '"beyond_slides": true and say in "beyond_reason" exactly what is missing. '
    "Do not pretend the material covers it. Such a step will be held for the "
    "student's approval and shown with a warning.\n"
    "- Never cite a passage that does not support the step.\n\n"
    "Return ONLY a JSON object, no prose, of the form:\n"
    "{\n"
    '  "steps": [\n'
    "    {\n"
    '      "title": "short imperative title",\n'
    '      "instruction": "what to do, 2-4 sentences, citing [n] where the material shows how",\n'
    '      "why": "which requirement this serves and why it comes at this point",\n'
    '      "requirement_ids": ["r1"],\n'
    '      "beyond_slides": false,\n'
    '      "beyond_reason": ""\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Order the steps the way the work should actually be done: understand the "
    "brief, set up, build, check, write up. Between 4 and 12 steps. If the brief "
    "requires a report, the final steps cover its structure and what evidence from "
    "the work goes in each part — without writing it."
)


def requirements_prompt(brief_title: str, brief_text: str) -> str:
    return f"Assignment brief: {brief_title}\n\n<brief>\n{brief_text}\n</brief>"


def plan_prompt(
    *,
    brief_title: str,
    brief_text: str,
    summary: str,
    requirements: list[dict[str, object]],
    snippets: list[EvidenceSnippet],
    integrity_policy: str = "",
) -> str:
    req_lines = "\n".join(
        f"- {r.get('id')}: [{r.get('kind')}] {r.get('text')}" for r in requirements
    )
    evidence_blocks: list[str] = []
    for snippet in snippets:
        header = (
            f"[{snippet.marker}] {snippet.document_title or 'Lecture'}, page {snippet.page_number}"
        )
        if snippet.section:
            header += f" — {snippet.section}"
        evidence_blocks.append(f"{header}\n{snippet.text}")
    evidence = (
        "\n\n".join(evidence_blocks) if evidence_blocks else "(no lecture material was found)"
    )

    policy = (
        f"\n\nCourse integrity policy, which you must respect:\n{integrity_policy}"
        if integrity_policy
        else ""
    )

    return (
        f"Assignment brief: {brief_title}\n\n"
        f"Summary of the brief:\n{summary}\n\n"
        f"Requirements:\n{req_lines}\n\n"
        f"<brief>\n{brief_text}\n</brief>\n\n"
        f"Lecture material from this course:\n\n{evidence}"
        f"{policy}\n\n"
        "Draft the plan as JSON."
    )
