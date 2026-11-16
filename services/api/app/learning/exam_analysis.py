"""What a course's past papers say about its next exam.

Past papers are the best evidence a student has of what an exam will ask and
how. This reads every paper marked as one in a course and produces an *exam
profile*: the paper's fixed format, the mix of question forms, and a ranked
list of topics with how likely each is to appear next and why. Quizzes, mock
exams and flashcards then follow that profile instead of a generic mix.

Three things are decided here rather than left to the model.

**A prediction is a likelihood with its evidence, never a promise.**  Each
topic carries the question references it was drawn from, and the likelihoods
are clamped: nothing is certain, and one paper cannot support a confident
forecast.  The profile always says how many papers it rests on.

**The method is spelled out.**  A model asked "what will come up?" guesses.
Asked to inventory every question first, then count, then look for rotation
and for what this year's lectures cover that the papers have not, it reasons
the way a tutor does — and the inventory makes the counts checkable.

**It works without a model.**  The format of a paper can be counted: option
letters mean multiple choice, "[10 marks]" means written parts.  That much is
returned when no model may read the papers, and it is labelled as counted.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.agents.prompts import _INJECTION_GUARD
from app.models.base import (
    ConsentRequiredError,
    GenerationRequest,
    ProviderUnavailableError,
)
from app.models.llm import extract_json_object

log = logging.getLogger(__name__)

QUESTION_KINDS = ("mcq", "short_answer", "numeric", "explanation")
#: Characters of past-paper text put in front of the analyst, across all papers.
PAPER_BUDGET = 110_000
MAX_TOPICS = 14

ANALYST_SYSTEM = (
    "You are the most careful exam analyst a student could hire: a senior tutor who has "
    "marked this module for years. You are given every past paper the student has for "
    "one university module, sometimes with solutions, and the topics this year's "
    "lectures cover. Work out what the next paper will most likely ask and in what form, "
    "and be exact about how sure the evidence lets you be.\n\n"
    f"{_INJECTION_GUARD}\n\n"
    "Do the analysis in this order. Do the work at each step before moving on; the "
    "later steps are only as good as the inventory.\n\n"
    "1. INVENTORY. For every paper, list every question and sub-part: its reference "
    '(the paper\'s year or title plus the question number, like "2023 Q2(b)"), the '
    "topic it tests, the skill it demands (recall, explain, derive, calculate, design, "
    "compare, critique, apply to a case), its marks, and its form.\n"
    "2. FORMAT. From the inventory, state the paper's fixed shape: sections, which are "
    "compulsory and where there is a choice, how many questions, marks per question, "
    "time allowed, and which question form dominates. Exam formats rarely change: "
    "predict that the next paper matches unless the papers disagree with each other, "
    "and if they do, trust the most recent.\n"
    "3. FREQUENCY. For each topic, count the papers it appears in out of the papers "
    "supplied, and the marks it carried.\n"
    "4. ROTATION AND RECENCY. Examiners return to core topics every year but avoid "
    "repeating last year's exact question. Look for: topics present in every paper "
    "(core, near-certain); topics that alternate years (work out whether this year is "
    "on or off); topics that appeared in a run and then missed the latest paper (due "
    "back); topics seen once, long ago (possible but unlikely); and pairs where one "
    "appears when the other does not.\n"
    "5. THIS YEAR'S TEACHING. Compare with the lecture topics supplied. A topic taught "
    "at length this year that has never been examined is a real candidate for a first "
    "appearance. A topic examined before that is absent from this year's lectures "
    "should be marked far down: it has probably left the syllabus.\n"
    "6. THE NEXT VARIATION. For each likely topic, look at how its question changed "
    "from paper to paper (new numbers, a proof one year and an application the next, "
    "a sub-part added) and predict the next variation, not a repeat of the last one.\n"
    "7. HOW MARKS ARE EARNED. If solutions or mark schemes are included, record what "
    "earns marks: steps shown, definitions stated, diagrams, units, a final comment.\n"
    "8. CALIBRATE. Give each topic a likelihood from 0 to 1 of appearing in the next "
    "paper. Every likelihood must follow from counts you can state. With a single "
    "paper, give nothing above 0.7 and say confidence is low. Never give 1.\n\n"
    "Rules:\n"
    "- Use only what the papers and the lecture topics support. Do not bring in what "
    "other universities examine.\n"
    "- Name topics with this year's lecture topic names wherever one fits.\n"
    "- Plain text in every string: no LaTeX and no backslashes. Write x^2, sqrt(x), "
    "pi.\n"
    "- Reply with one JSON object and nothing else, in exactly this shape:\n"
    '{"papers_read": [{"title": "...", "year": "2023 or empty", "questions": 6}],\n'
    ' "format": {"style": "mcq | written | mixed", "summary": "two sentences",\n'
    '            "duration_minutes": 120, "total_marks": 100,\n'
    '            "sections": [{"name": "Section A", "instructions": "answer all",\n'
    '                          "questions": 4, "marks_each": 10, "form": "short_answer"}]},\n'
    ' "question_mix": {"mcq": 0.0, "short_answer": 0.3, "numeric": 0.3, "explanation": 0.4},\n'
    ' "command_words": ["derive", "explain", "calculate"],\n'
    ' "topics": [{"topic": "...", "likelihood": 0.85, "pattern": "every paper",\n'
    '             "appearances": ["2021 Q1", "2022 Q2(a)", "2023 Q1"],\n'
    '             "typical_marks": 15, "form": "numeric",\n'
    '             "rationale": "the counts and the rotation that give this likelihood",\n'
    '             "next_variation": "what the question will most plausibly ask this time"}],\n'
    ' "patterns": ["one sentence each: a regularity across the papers"],\n'
    ' "marking_habits": ["one sentence each: what earns marks"],\n'
    ' "advice": ["one sentence each: how to spend revision time given all this"],\n'
    ' "confidence": "low | medium | high",\n'
    ' "caveat": "one sentence on what this rests on and what could make it wrong"}\n'
    f"Give at most {MAX_TOPICS} topics, most likely first. question_mix must sum to 1 "
    "and use exactly those four keys: mcq (choose an option), short_answer (a few "
    "lines), numeric (a calculated value), explanation (a long written or derived "
    "answer)."
)


@dataclass
class Paper:
    document_id: str
    title: str
    text: str
    content_hash: str = ""


@dataclass
class ProfileDraft:
    profile: dict[str, Any]
    #: "llm:<provider>" when a model analysed the papers, "counted" for the fallback.
    generated_by: str
    model: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def analysed(self) -> bool:
        return self.generated_by.startswith("llm:")


# --- reading a paper without a model -----------------------------------------------------
# The third option of a multiple-choice question: every such question has
# one, and it is short. Counting "(a)" lines would count the sub-parts of a
# written question as well.
_OPTION_LINE = re.compile(r"^\s*\(?[Cc][\).:]\s+(?!.*\bmarks?\b).{1,90}$", re.MULTILINE)
_MARKS = re.compile(r"[\[(]\s*(\d{1,3})\s*marks?\s*[\])]", re.IGNORECASE)
_QUESTION_START = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?(\d{1,2})[\).:]\s+\S", re.MULTILINE)
_NUMERIC_CUE = re.compile(
    r"\b(calculate|compute|determine the value|find the value|evaluate)\b", re.I
)
_LONG_CUE = re.compile(
    r"\b(derive|prove|discuss|explain|describe|compare|show that|design)\b", re.I
)
_DURATION = re.compile(r"(\d(?:\.\d)?)\s*hours?|(\d{2,3})\s*minutes", re.IGNORECASE)


def count_profile(papers: list[Paper]) -> dict[str, Any]:
    """The format of the papers, counted from their text. No topics are predicted."""
    options = marks_parts = numeric = long_form = questions = 0
    marks: list[int] = []
    minutes = 0
    for paper in papers:
        text = paper.text
        options += len(_OPTION_LINE.findall(text))
        found = [int(value) for value in _MARKS.findall(text)]
        marks.extend(found)
        marks_parts += len(found)
        numeric += len(_NUMERIC_CUE.findall(text))
        long_form += len(_LONG_CUE.findall(text))
        questions += len(_QUESTION_START.findall(text))
        duration = _DURATION.search(text[:3000])
        if duration and not minutes:
            minutes = (
                int(float(duration.group(1)) * 60) if duration.group(1) else int(duration.group(2))
            )

    mcq_questions = float(options)
    written = max(marks_parts, long_form + numeric)
    total = mcq_questions + written
    if total <= 0:
        mix = {"mcq": 0.25, "short_answer": 0.25, "numeric": 0.25, "explanation": 0.25}
        style = "mixed"
    else:
        mcq_share = mcq_questions / total
        rest = 1 - mcq_share
        cue_total = max(1, numeric + long_form)
        numeric_share = rest * 0.8 * (numeric / cue_total)
        long_share = rest * 0.8 * (long_form / cue_total)
        mix = {
            "mcq": round(mcq_share, 2),
            "short_answer": round(rest * 0.2, 2),
            "numeric": round(numeric_share, 2),
            "explanation": round(long_share, 2),
        }
        style = "mcq" if mcq_share >= 0.75 else "written" if mcq_share <= 0.2 else "mixed"

    described = {
        "mcq": "multiple-choice questions",
        "written": "written questions with marks per part",
        "mixed": "a mix of multiple-choice and written questions",
    }[style]
    return {
        "papers_read": [{"title": paper.title, "year": "", "questions": 0} for paper in papers],
        "format": {
            "style": style,
            "summary": f"Counted from the text of {len(papers)} paper"
            f"{'' if len(papers) == 1 else 's'}: mostly {described}.",
            "duration_minutes": minutes,
            "total_marks": sum(marks) // max(1, len(papers)) if marks else 0,
            "sections": [],
        },
        "question_mix": _normalised_mix(mix),
        "command_words": [],
        "topics": [],
        "patterns": [],
        "marking_habits": [],
        "advice": [],
        "confidence": "low",
        "caveat": "Only the format was counted; no model read the papers, so no topics "
        "are predicted.",
    }


# --- validating what a model returned ----------------------------------------------------
def _text(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str | int | float) else ""


def _texts(value: object, limit: int, count: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(item, limit) for item in value[:count]) if text]


def _number(value: object, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if math.isnan(number):
        return default
    return max(low, min(high, number))


def _normalised_mix(raw: object) -> dict[str, float]:
    mix = dict.fromkeys(QUESTION_KINDS, 0.0)
    if isinstance(raw, dict):
        for kind in QUESTION_KINDS:
            mix[kind] = _number(raw.get(kind), 0.0, 1.0)
    total = sum(mix.values())
    if total <= 0:
        return dict.fromkeys(QUESTION_KINDS, 0.25)
    return {kind: round(value / total, 3) for kind, value in mix.items()}


def clean_profile(raw: object, *, paper_count: int) -> dict[str, Any] | None:
    """Keep what is well-formed, clamp what is not believable, drop the rest."""
    if not isinstance(raw, dict):
        return None
    raw_format = raw.get("format") if isinstance(raw.get("format"), dict) else {}
    style = _text(raw_format.get("style"), 16).lower()
    sections = []
    for section in (raw_format.get("sections") or [])[:8]:
        if not isinstance(section, dict):
            continue
        form = _text(section.get("form"), 24).lower()
        sections.append(
            {
                "name": _text(section.get("name"), 80),
                "instructions": _text(section.get("instructions"), 200),
                "questions": int(_number(section.get("questions"), 0, 200)),
                "marks_each": int(_number(section.get("marks_each"), 0, 200)),
                "form": form if form in QUESTION_KINDS else "",
            }
        )

    # One paper cannot carry a confident forecast, whatever the model says.
    ceiling = 0.7 if paper_count <= 1 else 0.95
    topics = []
    for entry in (raw.get("topics") or [])[: MAX_TOPICS * 2]:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("topic"), 120)
        if not name:
            continue
        form = _text(entry.get("form"), 24).lower()
        topics.append(
            {
                "topic": name,
                "likelihood": round(_number(entry.get("likelihood"), 0.02, ceiling, 0.3), 2),
                "pattern": _text(entry.get("pattern"), 120),
                "appearances": _texts(entry.get("appearances"), 40, 12),
                "typical_marks": int(_number(entry.get("typical_marks"), 0, 200)),
                "form": form if form in QUESTION_KINDS else "",
                "rationale": _text(entry.get("rationale"), 500),
                "next_variation": _text(entry.get("next_variation"), 500),
            }
        )
    topics.sort(key=lambda topic: topic["likelihood"], reverse=True)

    confidence = _text(raw.get("confidence"), 12).lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    if paper_count <= 1:
        confidence = "low"
    elif paper_count == 2 and confidence == "high":
        confidence = "medium"

    papers_read = []
    for entry in (raw.get("papers_read") or [])[:30]:
        if isinstance(entry, dict) and _text(entry.get("title"), 200):
            papers_read.append(
                {
                    "title": _text(entry.get("title"), 200),
                    "year": _text(entry.get("year"), 12),
                    "questions": int(_number(entry.get("questions"), 0, 500)),
                }
            )

    profile = {
        "papers_read": papers_read,
        "format": {
            "style": style if style in {"mcq", "written", "mixed"} else "mixed",
            "summary": _text(raw_format.get("summary"), 600),
            "duration_minutes": int(_number(raw_format.get("duration_minutes"), 0, 600)),
            "total_marks": int(_number(raw_format.get("total_marks"), 0, 1000)),
            "sections": sections,
        },
        "question_mix": _normalised_mix(raw.get("question_mix")),
        "command_words": _texts(raw.get("command_words"), 30, 16),
        "topics": topics[:MAX_TOPICS],
        "patterns": _texts(raw.get("patterns"), 400, 8),
        "marking_habits": _texts(raw.get("marking_habits"), 400, 8),
        "advice": _texts(raw.get("advice"), 400, 8),
        "confidence": confidence,
        "caveat": _text(raw.get("caveat"), 500),
    }
    if not profile["topics"] and not profile["format"]["summary"]:
        return None
    return profile


# --- the analysis --------------------------------------------------------------------------
def _papers_block(papers: list[Paper]) -> str:
    share = max(4000, PAPER_BUDGET // max(1, len(papers)))
    blocks = []
    for index, paper in enumerate(papers, start=1):
        text = paper.text.strip()
        if len(text) > share:
            # The front of a paper holds its rubric and first questions; the
            # back holds the last questions and, often, the solutions.
            text = f"{text[: share * 2 // 3]}\n[... middle left out ...]\n{text[-share // 3 :]}"
        blocks.append(f"=== PAPER {index}: {paper.title} ===\n{text}")
    return "\n\n".join(blocks)


def analyse(
    *,
    provider: Any,
    course_title: str,
    papers: list[Paper],
    lecture_topics: list[str],
    lecture_titles: list[str],
) -> ProfileDraft:
    """Read the papers and forecast the next one; count the format if no model may read them."""
    counted = count_profile(papers)
    if not provider.info.generative:
        return ProfileDraft(
            profile=counted,
            generated_by="counted",
            warnings=[
                "No AI model may read your papers, so only their format was counted. "
                "Choose a model in Settings and allow it to read your material to get "
                "the topic forecast."
            ],
        )

    topics = "\n".join(f"- {topic}" for topic in lecture_topics[:90]) or "(none indexed yet)"
    lectures = "\n".join(f"- {title}" for title in lecture_titles[:60]) or "(none)"
    user = (
        f"Module: {course_title or 'n/a'}\n"
        f"Past papers supplied: {len(papers)}\n\n"
        f"This year's lectures:\n{lectures}\n\n"
        f"Topics this year's lectures cover:\n{topics}\n\n"
        f"The past papers:\n<<<\n{_papers_block(papers)}\n>>>\n\n"
        "Analyse them and reply with the JSON object."
    )
    try:
        result = provider.generate(
            GenerationRequest(
                system=ANALYST_SYSTEM,
                user=user,
                question="",
                max_output_tokens=6000,
                temperature=0.1,
                json_schema={"type": "object"},
                timeout_seconds=240.0,
            )
        )
    except (ProviderUnavailableError, ConsentRequiredError) as exc:
        log.warning("exam_analysis_failed", extra={"reason": str(exc)})
        return ProfileDraft(
            profile=counted,
            generated_by="counted",
            warnings=[f"The AI model could not be used: {exc}"],
        )

    profile = clean_profile(extract_json_object(result.text), paper_count=len(papers))
    if profile is None:
        return ProfileDraft(
            profile=counted,
            generated_by="counted",
            warnings=["The model's analysis could not be read, so only the format was counted."],
        )
    if not profile["papers_read"]:
        profile["papers_read"] = counted["papers_read"]
    return ProfileDraft(
        profile=profile,
        generated_by=f"llm:{result.provider}",
        model=result.model,
        warnings=list(result.warnings),
    )


# --- using a profile when writing questions ----------------------------------------------
def kinds_from_profile(profile: dict[str, Any]) -> list[str]:
    """The question forms to write, most common first, leaving out the rare ones."""
    mix = _normalised_mix(profile.get("question_mix"))
    kinds = [
        kind for kind, share in sorted(mix.items(), key=lambda item: -item[1]) if share >= 0.12
    ]
    return kinds or ["short_answer"]


def style_brief(profile: dict[str, Any], *, sample: str = "") -> str:
    """The exam, described for whoever writes practice questions for it."""
    exam_format = profile.get("format") or {}
    mix = _normalised_mix(profile.get("question_mix"))
    lines = ["THE REAL EXAM, from the student's past papers:"]
    if exam_format.get("summary"):
        lines.append(f"Format: {exam_format['summary']}")
    for section in exam_format.get("sections") or []:
        described = ", ".join(
            part
            for part in (
                f"{section['questions']} questions" if section.get("questions") else "",
                f"{section['marks_each']} marks each" if section.get("marks_each") else "",
                section.get("form", ""),
                section.get("instructions", ""),
            )
            if part
        )
        lines.append(f"- {section.get('name') or 'Section'}: {described}")
    lines.append(
        "Mix of question forms: "
        + ", ".join(f"{kind} {round(share * 100)}%" for kind, share in mix.items() if share > 0)
    )
    if profile.get("command_words"):
        lines.append("Command words the examiner uses: " + ", ".join(profile["command_words"]))
    topics = profile.get("topics") or []
    if topics:
        lines.append("Topics by likelihood of appearing next:")
        for topic in topics:
            detail = f"- {topic['topic']} ({round(topic['likelihood'] * 100)}%)"
            if topic.get("typical_marks"):
                detail += f", usually {topic['typical_marks']} marks"
            if topic.get("form"):
                detail += f", as {topic['form']}"
            if topic.get("next_variation"):
                detail += f". Likely next: {topic['next_variation']}"
            lines.append(detail)
    for habit in profile.get("marking_habits") or []:
        lines.append(f"Marks are earned by: {habit}")
    if sample.strip():
        lines.append(
            "\nReal questions from the past papers, to match in wording, depth and marks "
            f"(do not copy them):\n<<<\n{sample.strip()}\n>>>"
        )
    return "\n".join(lines)


def paper_sample(papers: list[Paper], budget: int = 5000) -> str:
    """A slice of each paper's questions, skipping the cover page."""
    if not papers:
        return ""
    share = max(600, budget // len(papers))
    parts = []
    for paper in papers[:6]:
        text = paper.text.strip()
        first = _QUESTION_START.search(text)
        start = (
            first.start() if first and first.start() < len(text) // 2 else min(600, len(text) // 5)
        )
        parts.append(f"[{paper.title}]\n{text[start : start + share]}")
    return "\n\n".join(parts)[:budget]


_TERM = re.compile(r"[a-z][a-z0-9\-]{3,}")
_GENERIC = frozenset(
    [
        "analysis",
        "method",
        "methods",
        "model",
        "models",
        "theory",
        "introduction",
        "basic",
        "basics",
        "general",
        "using",
        "based",
        "system",
        "systems",
        "processing",
        "techniques",
        "technique",
        "algorithm",
        "algorithms",
        "application",
        "applications",
        "problem",
        "problems",
        "example",
        "examples",
        "properties",
    ]
)


def topic_terms(topic: str) -> set[str]:
    return {term for term in _TERM.findall(topic.lower()) if term not in _GENERIC}


def exam_weight(text: str, profile: dict[str, Any]) -> float:
    """How much a passage is about what the exam is likely to ask, 0 upward."""
    lowered = text.lower()
    weight = 0.0
    for topic in profile.get("topics") or []:
        terms = topic_terms(topic.get("topic", ""))
        if not terms:
            continue
        hits = sum(1 for term in terms if term in lowered)
        if hits and hits / len(terms) >= 0.5:
            weight += float(topic.get("likelihood", 0.0)) * (hits / len(terms))
    return weight
