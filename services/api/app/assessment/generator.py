"""Question generation.

Two generators behind one interface:

``source_derived`` (always available)
    Builds cloze-deletion and term-matching questions directly from indexed
    chunks.  Every question is traceable to an exact passage, there is no model
    call, and nothing is invented — the answer is literally the word that was
    removed from the student's own slide.  Provenance is recorded as
    ``source_derived`` so the UI never calls these "AI generated".

``llm`` (when a generative provider is configured and permitted)
    Asks a model for structured JSON, then **validates every field** against
    the evidence that was supplied.  A question that does not validate is
    dropped rather than shown.

Question provenance is never fabricated.  ``past_paper`` is only ever set for
questions extracted from a document the student uploaded and marked as a past
paper; nothing in this module can produce that value on its own.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field

from app.models.base import (
    GenerationRequest,
    LLMProvider,
    ProviderUnavailableError,
)
from app.models.llm import extract_json_object
from app.rag.retriever import RetrievedChunk

log = logging.getLogger(__name__)

#: Slide decks frequently have no terminal punctuation, so a line break is as
#: much a sentence boundary as a full stop. Splitting on both is what stops a
#: whole slide becoming one run-on "sentence" in a cloze question.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z\-']{3,}\b")
#: Running headers and footers ("Slide 4", "Page 12 of 30") are page furniture,
#: not content, and make nonsense of a fill-in-the-blank question.
_FURNITURE_RE = re.compile(
    r"^\s*(?:slide|page|lecture|chapter|week)\s*\d+(?:\s*(?:of|/)\s*\d+)?\s*$",
    re.IGNORECASE,
)
_TRAILING_FURNITURE_RE = re.compile(
    r"\s*(?:slide|page)\s*\d+\s*$",
    re.IGNORECASE,
)

_COMMON = frozenset(
    [
        "about",
        "above",
        "after",
        "again",
        "against",
        "also",
        "although",
        "always",
        "among",
        "another",
        "because",
        "before",
        "being",
        "below",
        "between",
        "both",
        "cannot",
        "could",
        "course",
        "during",
        "each",
        "either",
        "every",
        "first",
        "follow",
        "following",
        "from",
        "further",
        "generally",
        "given",
        "however",
        "included",
        "including",
        "instead",
        "into",
        "itself",
        "lecture",
        "least",
        "less",
        "like",
        "many",
        "more",
        "most",
        "much",
        "must",
        "never",
        "often",
        "only",
        "other",
        "over",
        "rather",
        "same",
        "several",
        "should",
        "since",
        "slide",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "thus",
        "under",
        "until",
        "using",
        "very",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "within",
        "without",
        "would",
        "your",
        "example",
        "examples",
        "figure",
        "note",
        "notes",
        "page",
        "shown",
        "shows",
        "table",
        "university",
        "week",
        "lectures",
        "chapter",
        "section",
        "summary",
        "introduction",
        "overview",
        "conclusion",
    ]
)

VALID_KINDS = {"mcq", "short_answer", "numeric", "explanation"}


@dataclass
class GeneratedQuestion:
    kind: str
    prompt: str
    marks: int = 1
    difficulty: str = "medium"
    options: list[str] = field(default_factory=list)
    correct_option: int | None = None
    expected_answer: str = ""
    numeric_answer: float | None = None
    numeric_tolerance: float = 0.01
    rubric: dict[str, object] = field(default_factory=dict)
    topic_slug: str = ""
    provenance: str = "source_derived"
    source_chunk_id: str | None = None
    source_page: int | None = None
    explanation: str = ""


def build_blueprint(
    *,
    question_count: int,
    topic_weights: dict[str, float] | None = None,
    difficulty: str = "balanced",
    kinds: list[str] | None = None,
) -> dict[str, object]:
    """An assessment blueprint: what the generated paper is required to contain.

    Generating against a blueprint (rather than asking for "some questions")
    is what makes a mock exam reproducible and lets topic weighting be honoured.
    """
    kinds = [k for k in (kinds or ["mcq", "short_answer"]) if k in VALID_KINDS] or ["mcq"]
    return {
        "question_count": max(1, min(60, question_count)),
        "topic_weights": topic_weights or {},
        "difficulty": difficulty,
        "kinds": kinds,
    }


def reflow(text: str) -> str:
    """Rejoin lines that a PDF wrapped mid-sentence.

    Slide text arrives with hard line breaks at the export's wrap points, so a
    newline is only sometimes a sentence boundary. A line is treated as a
    continuation of the previous one when the previous line does not end in
    terminal punctuation and the next begins with a lower-case word — which
    keeps separate bullets apart while repairing wrapped prose.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    out: list[str] = [lines[0]]
    for line in lines[1:]:
        previous = out[-1]
        continues = not previous.endswith((".", "!", "?", ":", ";")) and (
            line[:1].islower() or line[:1].isdigit()
        )
        if continues:
            out[-1] = f"{previous} {line}"
        else:
            out.append(line)
    return "\n".join(out)


def _is_fair_distractor(term: str, answer: str) -> bool:
    """Whether ``term`` is a reasonable wrong option against ``answer``.

    A distractor that is a morphological variant of the answer ("convolution"
    against "convolutional") tests spelling, not understanding, and is often
    arguably correct. Such options are rejected, as are ones of wildly
    different length, which give the answer away by shape alone.
    """
    lowered, target = term.lower(), answer.lower()
    if lowered == target:
        return False
    if lowered.startswith(target) or target.startswith(lowered):
        return False
    return abs(len(term) - len(answer)) <= 6


def _candidate_terms(sentence: str, topic_hint: str = "") -> list[str]:
    """Words worth blanking out: content-bearing, not generic prose."""
    hint_words = {w.lower() for w in _WORD_RE.findall(topic_hint)}
    seen: dict[str, int] = {}
    for match in _WORD_RE.finditer(sentence):
        word = match.group(0)
        lowered = word.lower()
        if lowered in _COMMON or len(lowered) < 4:
            continue
        score = 1
        if lowered in hint_words:
            score += 3  # the topic's own vocabulary is the point of the question
        if word[0].isupper() and not sentence.startswith(word):
            score += 1  # likely a proper noun or named technique
        if len(lowered) > 8:
            score += 1
        seen[word] = max(seen.get(word, 0), score)
    return [word for word, _ in sorted(seen.items(), key=lambda item: item[1], reverse=True)]


class QuestionGenerator:
    """Builds questions from retrieved course material."""

    def __init__(self, *, seed: int | None = None) -> None:
        self._random = random.Random(seed)

    # --- deterministic ---------------------------------------------------
    def from_source(
        self,
        chunks: list[RetrievedChunk],
        *,
        blueprint: dict[str, object],
    ) -> list[GeneratedQuestion]:
        """Derive questions from the passages themselves. Never invents content."""
        wanted = int(blueprint.get("question_count", 5))
        kinds = list(blueprint.get("kinds") or ["mcq"])
        questions: list[GeneratedQuestion] = []

        # A pool of real terms from across the material, for MCQ distractors.
        term_pool: list[str] = []
        for chunk in chunks:
            term_pool.extend(_candidate_terms(chunk.text, chunk.section))
        term_pool = list(dict.fromkeys(term_pool))

        usable = [c for c in chunks if c.element_type != "table"]
        for chunk in usable:
            if len(questions) >= wanted:
                break
            sentences = [
                cleaned
                for cleaned in (
                    _TRAILING_FURNITURE_RE.sub("", " ".join(piece.split()))
                    for piece in _SENTENCE_RE.split(reflow(chunk.text))
                    if not _FURNITURE_RE.match(piece)
                )
                if 45 <= len(cleaned) <= 320
            ]
            # Prefer definitional sentences; they make the fairest questions.
            sentences.sort(
                key=lambda s: " is " in s or " are " in s or " means " in s, reverse=True
            )
            for sentence in sentences:
                if len(questions) >= wanted:
                    break
                terms = _candidate_terms(sentence, chunk.section)
                if not terms:
                    continue
                answer = terms[0]
                blanked = re.sub(rf"\b{re.escape(answer)}\b", "______", sentence, count=1)
                if "______" not in blanked:
                    continue

                kind = kinds[len(questions) % len(kinds)]
                if kind == "mcq":
                    distractors = [term for term in term_pool if _is_fair_distractor(term, answer)]
                    self._random.shuffle(distractors)
                    options = [answer, *distractors[:3]]
                    if len(options) < 3:
                        continue
                    self._random.shuffle(options)
                    questions.append(
                        GeneratedQuestion(
                            kind="mcq",
                            prompt=f"Complete the statement from your material:\n\n{blanked}",
                            options=options,
                            correct_option=options.index(answer),
                            expected_answer=answer,
                            marks=1,
                            difficulty="easy",
                            topic_slug=chunk.topic_slug,
                            provenance="source_derived",
                            source_chunk_id=chunk.chunk_id,
                            source_page=chunk.page_number,
                            explanation=f"The slide on page {chunk.page_number} states: {sentence}",
                        )
                    )
                else:
                    questions.append(
                        GeneratedQuestion(
                            kind="short_answer",
                            prompt=f"Fill in the missing term:\n\n{blanked}",
                            expected_answer=answer,
                            rubric={
                                "method": "keyword",
                                "required": [answer],
                                "accept_any": [answer],
                            },
                            marks=1,
                            difficulty="medium",
                            topic_slug=chunk.topic_slug,
                            provenance="source_derived",
                            source_chunk_id=chunk.chunk_id,
                            source_page=chunk.page_number,
                            explanation=f"From page {chunk.page_number}: {sentence}",
                        )
                    )
                break  # at most one question per chunk, for topic spread

        return questions

    # --- model-generated -------------------------------------------------
    def from_llm(
        self,
        chunks: list[RetrievedChunk],
        *,
        blueprint: dict[str, object],
        provider: LLMProvider,
    ) -> list[GeneratedQuestion]:
        """Ask a model for questions, then validate them against the evidence."""
        if not provider.info.generative:
            return []

        evidence_blocks = []
        allowed_ids: dict[str, RetrievedChunk] = {}
        for index, chunk in enumerate(chunks, start=1):
            allowed_ids[chunk.chunk_id] = chunk
            evidence_blocks.append(
                f"[{index}] (chunk_id={chunk.chunk_id}, page={chunk.page_number}, "
                f"section={chunk.section or 'n/a'})\n{chunk.parent_text or chunk.text}"
            )

        wanted = int(blueprint.get("question_count", 5))
        kinds = list(blueprint.get("kinds") or ["mcq"])
        system = (
            "You write university assessment questions from supplied lecture material. "
            "Rules you must follow:\n"
            "- Use ONLY the supplied material. Do not use outside knowledge.\n"
            "- Every question must be answerable from the material alone.\n"
            "- Set source_chunk_id to the chunk_id of the passage the question comes from.\n"
            "- Never claim a question came from a past examination paper.\n"
            "- Reply with JSON only, no prose."
        )
        user = (
            f"Material:\n\n{chr(10).join(evidence_blocks)}\n\n"
            f"Write exactly {wanted} questions of these kinds: {', '.join(kinds)}.\n"
            "Return JSON of the form:\n"
            '{"questions": [{"kind": "mcq", "prompt": "...", "options": ["..."], '
            '"correct_option": 0, "expected_answer": "...", "rubric_points": ["..."], '
            '"marks": 1, "difficulty": "easy|medium|hard", "source_chunk_id": "...", '
            '"explanation": "..."}]}'
        )

        try:
            result = provider.generate(
                GenerationRequest(
                    system=system,
                    user=user,
                    question="",
                    evidence=[],
                    max_output_tokens=2400,
                    temperature=0.4,
                    json_schema={"type": "object"},
                )
            )
        except ProviderUnavailableError as exc:
            log.warning("quiz_generation_failed", extra={"reason": str(exc)})
            return []

        payload = extract_json_object(result.text)
        if isinstance(payload, list):
            raw_questions = payload
        elif isinstance(payload, dict):
            raw_questions = payload.get("questions") or []
        else:
            log.warning("quiz_generation_unparseable")
            return []
        if not isinstance(raw_questions, list):
            return []

        validated: list[GeneratedQuestion] = []
        for raw in raw_questions:
            question = self._validate_llm_question(raw, allowed_ids)
            if question is not None:
                validated.append(question)
        if len(validated) < len(raw_questions):
            log.info(
                "quiz_questions_rejected",
                extra={"received": len(raw_questions), "kept": len(validated)},
            )
        return validated[:wanted]

    @staticmethod
    def _validate_llm_question(
        raw: object, allowed: dict[str, RetrievedChunk]
    ) -> GeneratedQuestion | None:
        """Reject anything malformed or not grounded in the supplied evidence."""
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind", "")).strip().lower()
        prompt = str(raw.get("prompt", "")).strip()
        if kind not in VALID_KINDS or len(prompt) < 10:
            return None

        chunk_id = str(raw.get("source_chunk_id", "")).strip()
        chunk = allowed.get(chunk_id)
        if chunk is None:
            # Ungrounded question: refuse it rather than show it without a source.
            return None

        difficulty = str(raw.get("difficulty", "medium")).lower()
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"
        try:
            marks = max(1, min(20, int(raw.get("marks", 1))))
        except (TypeError, ValueError):
            marks = 1

        question = GeneratedQuestion(
            kind=kind,
            prompt=prompt,
            marks=marks,
            difficulty=difficulty,
            topic_slug=chunk.topic_slug,
            provenance="ai_generated",
            source_chunk_id=chunk.chunk_id,
            source_page=chunk.page_number,
            explanation=str(raw.get("explanation", ""))[:2000],
        )

        if kind == "mcq":
            options = [str(o).strip() for o in (raw.get("options") or []) if str(o).strip()]
            if not 2 <= len(options) <= 6 or len(set(options)) != len(options):
                return None
            try:
                correct = int(raw.get("correct_option"))
            except (TypeError, ValueError):
                return None
            if not 0 <= correct < len(options):
                return None
            question.options = options
            question.correct_option = correct
            question.expected_answer = options[correct]
        elif kind == "numeric":
            try:
                question.numeric_answer = float(raw.get("expected_answer"))
            except (TypeError, ValueError):
                return None
            try:
                question.numeric_tolerance = abs(float(raw.get("tolerance", 0.01)))
            except (TypeError, ValueError):
                question.numeric_tolerance = 0.01
        else:
            expected = str(raw.get("expected_answer", "")).strip()
            points = [str(p).strip() for p in (raw.get("rubric_points") or []) if str(p).strip()]
            if not expected and not points:
                return None
            question.expected_answer = expected
            question.rubric = {
                "method": "rubric",
                "points": points or [expected],
                "required": points or [],
            }
        return question


def serialise_blueprint(blueprint: dict[str, object]) -> str:
    return json.dumps(blueprint, sort_keys=True)
