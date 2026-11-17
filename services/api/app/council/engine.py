"""A discussion between models, run to agreement.

One model answering an assignment can be confidently wrong, and nothing in
its answer says so. Several models make different mistakes, so they are made
to check each other. The discussion has five stages, and each exists to
catch something the one before it cannot:

1. **Draft.**  Every model answers alone, so that one model's mistake is not
   copied by the rest.
2. **Review.**  Every model reads the others' drafts and raises issues: a
   mistake, or a better approach, each with its reason and a reference.
3. **Answer and judge.**  The author of a draft accepts each issue or defends
   the draft with its own reason. A defended issue goes to a model that is
   neither its author nor its reviewer, which rules on it. This is what stops
   a confident reviewer overturning a correct answer, and a stubborn author
   keeping a wrong one.
4. **The agreed version.**  The model whose draft was voted the best to build
   on writes one version with every settled correction applied.
5. **Vote.**  Every other model accepts it or objects, with a reason and the
   fix. Objections go back for revision and another vote, a limited number
   of times.

What is decided here rather than left to a model: who may judge what (never
a party to the dispute), what counts as settled (accepted by its author or
upheld by a neutral judge), when it is over (everyone agrees, or the rounds
run out), and what is reported (if they did not all agree, that is said and
the remaining objections are kept beside the answer).

Agreement is not proof. Models can share a mistake, and the result says how
it was reached so that it can be checked, rather than claiming to be right.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from app.council import prompts
from app.models.base import GenerationRequest

log = logging.getLogger(__name__)

#: Votes on the agreed version before it is given as it stands, objections and all.
MAX_VOTE_ROUNDS = 3
MAX_ISSUES_PER_REVIEW = 8
#: Characters of brief, guide and references a model is given.
CLOUD_BUDGET = 60_000
LOCAL_BUDGET = 9_000

Emit = Callable[[dict[str, Any]], None]


class DiscussionStoppedError(Exception):
    """The student stopped the discussion."""


class NoAnswerError(Exception):
    """No model produced a draft, so there is nothing to discuss."""


@dataclass
class Member:
    """One model at the table."""

    key: str  # "anthropic" | "openai" | "gemini" | "ollama"
    name: str  # what it is called in the discussion: "Claude", "ChatGPT", ...
    provider: Any
    model: str = ""
    #: Runs on this machine: slower, with a small context window.
    local: bool = False

    @property
    def budget(self) -> int:
        return LOCAL_BUDGET if self.local else CLOUD_BUDGET

    @property
    def timeout(self) -> float:
        return 900.0 if self.local else 300.0


@dataclass
class Task:
    kind: str  # "guide" | "solution"
    course_title: str
    brief_title: str
    brief_text: str
    #: Numbered reference passages: {"marker", "text", "document_title", "page_number"}.
    passages: list[dict[str, Any]]
    #: The agreed guide, when the task is to complete the assignment by it.
    guide: str = ""

    @property
    def instructions(self) -> str:
        return prompts.SOLUTION_TASK if self.kind == "solution" else prompts.GUIDE_TASK

    def block(self, member: Member) -> str:
        return prompts.task_block(
            course_title=self.course_title,
            brief_title=self.brief_title,
            brief_text=self.brief_text,
            passages=self.passages,
            guide=self.guide,
            budget=member.budget,
        )


@dataclass
class Issue:
    id: int
    reviewer: str
    author: str
    kind: str  # "mistake" | "better"
    where: str
    problem: str
    reason: str
    fix: str
    #: open → accepted | defended → upheld | dismissed | contested
    status: str = "open"
    response: str = ""
    judge: str = ""
    ruling: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def stated(self) -> str:
        return (
            f"Issue {self.id} (raised by {self.reviewer} about {self.author}'s draft, "
            f"{self.kind})\n"
            f"where: {self.where}\nproblem: {self.problem}\nreason: {self.reason}\n"
            f"fix: {self.fix}"
        )


@dataclass
class Outcome:
    text: str
    lead: str
    unanimous: bool
    rounds: int
    agreed: list[str]
    objections: list[dict[str, str]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    took_part: list[str] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)

    def consensus(self) -> dict[str, Any]:
        settled = Counter(issue.status for issue in self.issues)
        return {
            "unanimous": self.unanimous,
            "rounds": self.rounds,
            "lead": self.lead,
            "agreed": self.agreed,
            "took_part": self.took_part,
            "dropped": self.dropped,
            "objections": self.objections,
            "issues_raised": len(self.issues),
            "issues_accepted": settled["accepted"],
            "issues_upheld": settled["upheld"],
            "issues_dismissed": settled["dismissed"],
            "issues_contested": settled["contested"],
        }


# --- reading the delimited replies -------------------------------------------------------
_BLOCK = re.compile(r"^===\s*([A-Za-z ]+?)\s*===\s*$", re.MULTILINE)


def parse_blocks(text: str, fields: tuple[str, ...]) -> list[tuple[str, dict[str, str]]]:
    """Split a reply into its ``=== NAME ===`` blocks and each block into its fields.

    A field's value runs until the next known field name starts a line, so a
    reason may span several lines and contain colons of its own.
    """
    field_line = re.compile(
        r"^\s*(" + "|".join(re.escape(name) for name in fields) + r")\s*:\s*(.*)$", re.IGNORECASE
    )
    found: list[tuple[str, dict[str, str]]] = []
    marks = list(_BLOCK.finditer(text))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        values: dict[str, str] = {}
        current = ""
        for line in text[mark.end() : end].splitlines():
            match = field_line.match(line)
            if match:
                current = match.group(1).lower()
                values[current] = match.group(2).strip()
            elif current and line.strip():
                values[current] = f"{values[current]}\n{line.strip()}".strip()
        found.append((mark.group(1).strip().upper(), values))
    return found


def match_name(value: str, names: list[str]) -> str:
    """The member a reply means, however it capitalised or decorated the name."""
    cleaned = re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()
    for name in names:
        if cleaned == name.lower():
            return name
    for name in names:
        if name.lower() in cleaned or (cleaned and cleaned in name.lower()):
            return name
    return ""


def _first_word(value: str) -> str:
    words = re.findall(r"[a-z]+", value.lower())
    return words[0] if words else ""


def _number(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


# --- the discussion ----------------------------------------------------------------------
class Discussion:
    def __init__(
        self,
        task: Task,
        members: list[Member],
        *,
        emit: Emit | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.task = task
        self.members = list(members)
        self._emit = emit or (lambda event: None)
        self._should_stop = should_stop or (lambda: False)
        self.dropped: list[dict[str, str]] = []

    # -- plumbing ---------------------------------------------------------------------
    def _check(self) -> None:
        if self._should_stop():
            raise DiscussionStoppedError

    def _phase(self, phase: str, detail: str) -> None:
        self._check()
        self._emit({"type": "phase", "phase": phase, "detail": detail})

    def _ask(
        self, member: Member, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> str:
        result = member.provider.generate(
            GenerationRequest(
                system=system,
                user=user,
                question=self.task.brief_title,
                max_output_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=member.timeout,
            )
        )
        text = (result.text or "").strip()
        if not text:
            raise ValueError("it returned nothing")
        return text

    def _ask_each(
        self,
        phase: str,
        calls: dict[str, tuple[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, str]:
        """Ask several members at once. One that fails is reported and left out of the stage."""
        by_name = {member.name: member for member in self.members}
        replies: dict[str, str] = {}
        if not calls:
            return replies
        with ThreadPoolExecutor(max_workers=len(calls), thread_name_prefix="council") as pool:
            futures = {
                name: pool.submit(
                    self._ask,
                    by_name[name],
                    system,
                    user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                for name, (system, user) in calls.items()
            }
            for name, future in futures.items():
                try:
                    replies[name] = future.result()
                except Exception as exc:
                    reason = str(exc)[:300] or exc.__class__.__name__
                    log.warning("council_member_failed", extra={"member": name, "phase": phase})
                    self._emit({"type": "error", "phase": phase, "member": name, "text": reason})
                    self.dropped.append({"member": name, "phase": phase, "reason": reason})
        return replies

    def _drafts_block(self, drafts: dict[str, str], reader: Member) -> str:
        room = max(1500, reader.budget // max(1, len(drafts)))
        parts = []
        for name, text in drafts.items():
            label = "your own draft" if name == reader.name else f"draft by {name}"
            shown = text if len(text) <= room else f"{text[:room]}\n[... cut for length ...]"
            parts.append(f"##### {label.upper()} #####\n{shown}")
        return "\n\n".join(parts)

    # -- stage 1 ----------------------------------------------------------------------
    def _propose(self) -> dict[str, str]:
        self._phase("propose", "Each model is writing its own draft.")
        system = prompts.PROPOSE_SYSTEM.replace("{task}", self.task.instructions)
        calls = {
            member.name: (system, f"{self.task.block(member)}\n\nWrite your draft.")
            for member in self.members
        }
        long = self.task.kind == "solution"
        drafts = self._ask_each(
            "propose", calls, max_tokens=7000 if long else 3600, temperature=0.3
        )
        for name, text in drafts.items():
            self._emit({"type": "draft", "phase": "propose", "member": name, "markdown": text})
        if not drafts:
            raise NoAnswerError("None of the models produced a draft.")
        # Only a model that drafted has a position to argue from.
        self.members = [member for member in self.members if member.name in drafts]
        return drafts

    # -- stage 2 ----------------------------------------------------------------------
    def _review(self, drafts: dict[str, str]) -> tuple[list[Issue], Counter[str]]:
        self._phase("review", "Each model is checking the others' drafts.")
        names = [member.name for member in self.members]
        calls = {
            member.name: (
                prompts.REVIEW_SYSTEM,
                f"{self.task.block(member)}\n\nYou are {member.name}. The drafts:\n\n"
                f"{self._drafts_block(drafts, member)}\n\nWrite your review.",
            )
            for member in self.members
        }
        replies = self._ask_each("review", calls, max_tokens=3200, temperature=0.2)

        issues: list[Issue] = []
        best: Counter[str] = Counter()
        for reviewer, reply in replies.items():
            raised: list[Issue] = []
            choice = ""
            for block, values in parse_blocks(
                reply, ("draft", "kind", "where", "problem", "reason", "fix", "because")
            ):
                if block == "BEST":
                    choice = match_name(values.get("draft", ""), names)
                elif block == "ISSUE" and len(raised) < MAX_ISSUES_PER_REVIEW:
                    author = match_name(values.get("draft", ""), names)
                    problem = values.get("problem", "").strip()
                    # An issue about no one's draft, about the reviewer's own, or
                    # with no reason given, is not an issue.
                    if not author or author == reviewer or not problem:
                        continue
                    if not values.get("reason", "").strip():
                        continue
                    issue = Issue(
                        id=len(issues) + len(raised) + 1,
                        reviewer=reviewer,
                        author=author,
                        kind="better"
                        if _first_word(values.get("kind", "")) == "better"
                        else "mistake",
                        where=values.get("where", "")[:400],
                        problem=problem[:1500],
                        reason=values.get("reason", "")[:1500],
                        fix=values.get("fix", "")[:2500],
                    )
                    raised.append(issue)
            issues.extend(raised)
            if choice:
                best[choice] += 1
            self._emit(
                {
                    "type": "review",
                    "phase": "review",
                    "member": reviewer,
                    "issues": [issue.to_dict() for issue in raised],
                    "best": choice,
                }
            )
        return issues, best

    # -- stage 3 ----------------------------------------------------------------------
    def _defend(self, drafts: dict[str, str], issues: list[Issue]) -> None:
        by_author: dict[str, list[Issue]] = {}
        for issue in issues:
            by_author.setdefault(issue.author, []).append(issue)
        if not by_author:
            return
        self._phase("defend", "Each model is answering the issues raised about its draft.")
        by_name = {member.name: member for member in self.members}
        calls = {}
        for author, raised in by_author.items():
            member = by_name[author]
            listed = "\n\n".join(issue.stated() for issue in raised)
            calls[author] = (
                prompts.DEFEND_SYSTEM,
                f"{self.task.block(member)}\n\nYou are {author}. Your draft:\n<<<\n"
                f"{drafts[author][: member.budget]}\n>>>\n\nThe issues raised about it:\n\n"
                f"{listed}\n\nAnswer each one.",
            )
        replies = self._ask_each("defend", calls, max_tokens=2600, temperature=0.2)

        for author, raised in by_author.items():
            answered: dict[int, tuple[str, str]] = {}
            for block, values in parse_blocks(
                replies.get(author, ""), ("issue", "stance", "reason")
            ):
                number = _number(values.get("issue", ""))
                if block == "RESPONSE" and number is not None:
                    answered[number] = (
                        _first_word(values.get("stance", "")),
                        values.get("reason", "")[:1500],
                    )
            for issue in raised:
                stance, reason = answered.get(issue.id, ("", ""))
                issue.response = reason
                if stance == "accept":
                    issue.status = "accepted"
                else:
                    # Defended, or not answered at all: either way it is not
                    # the author who gets to settle it.
                    issue.status = "defended"
                    issue.response = reason or "(the author gave no answer)"
            self._emit(
                {
                    "type": "defence",
                    "phase": "defend",
                    "member": author,
                    "responses": [
                        {"issue": issue.id, "stance": issue.status, "reason": issue.response}
                        for issue in raised
                    ],
                }
            )

    def _judge(self, drafts: dict[str, str], issues: list[Issue]) -> None:
        defended = [issue for issue in issues if issue.status == "defended"]
        if not defended:
            return
        # A judge is never a party: not the author, not the reviewer.
        load: Counter[str] = Counter()
        docket: dict[str, list[Issue]] = {}
        for issue in defended:
            neutral = [
                member.name
                for member in self.members
                if member.name not in (issue.author, issue.reviewer)
            ]
            if not neutral:
                issue.status = "contested"  # two models, no third to rule
                continue
            judge = min(neutral, key=lambda name: (load[name], neutral.index(name)))
            load[judge] += 1
            issue.judge = judge
            docket.setdefault(judge, []).append(issue)
        if not docket:
            return
        self._phase("judge", "A model that took neither side is ruling on each disagreement.")
        by_name = {member.name: member for member in self.members}
        calls = {}
        for judge, cases in docket.items():
            member = by_name[judge]
            authors = {issue.author for issue in cases}
            shown = self._drafts_block({name: drafts[name] for name in authors}, member)
            listed = "\n\n".join(
                f"{issue.stated()}\nthe author's defence: {issue.response}" for issue in cases
            )
            calls[judge] = (
                prompts.JUDGE_SYSTEM,
                f"{self.task.block(member)}\n\nYou are {judge}. The drafts in question:\n\n"
                f"{shown}\n\nThe disagreements:\n\n{listed}\n\nRule on each one.",
            )
        replies = self._ask_each("judge", calls, max_tokens=2600, temperature=0.1)

        for judge, cases in docket.items():
            ruled: dict[int, tuple[str, str]] = {}
            for block, values in parse_blocks(
                replies.get(judge, ""), ("issue", "ruling", "reason")
            ):
                number = _number(values.get("issue", ""))
                if block == "RULING" and number is not None:
                    ruled[number] = (
                        _first_word(values.get("ruling", "")),
                        values.get("reason", "")[:1500],
                    )
            for issue in cases:
                ruling, reason = ruled.get(issue.id, ("", ""))
                issue.ruling = reason
                if ruling == "uphold":
                    issue.status = "upheld"
                elif ruling == "dismiss":
                    issue.status = "dismissed"
                else:
                    issue.status = "contested"  # no ruling came back
            self._emit(
                {
                    "type": "ruling",
                    "phase": "judge",
                    "member": judge,
                    "rulings": [
                        {"issue": issue.id, "ruling": issue.status, "reason": issue.ruling}
                        for issue in cases
                    ],
                }
            )

    # -- stage 4 ----------------------------------------------------------------------
    def _lead_order(self, best: Counter[str]) -> list[str]:
        """Who writes the agreed version: most votes first, then the order they sit in."""
        names = [member.name for member in self.members]
        return sorted(names, key=lambda name: (-best[name], names.index(name)))

    def _synthesise(
        self, drafts: dict[str, str], issues: list[Issue], order: list[str]
    ) -> tuple[str, str]:
        def listed(statuses: tuple[str, ...]) -> str:
            chosen = [issue for issue in issues if issue.status in statuses]
            if not chosen:
                return "(none)"
            return "\n\n".join(
                issue.stated()
                + (f"\nthe judge's reason: {issue.ruling}" if issue.ruling else "")
                + (
                    f"\nthe author's defence: {issue.response}"
                    if issue.status == "contested" and issue.response
                    else ""
                )
                for issue in chosen
            )

        self._phase("synthesis", "The agreed version is being written.")
        by_name = {member.name: member for member in self.members}
        for lead in order:
            member = by_name[lead]
            user = (
                f"{self.task.block(member)}\n\nYou are {lead}. All the drafts:\n\n"
                f"{self._drafts_block(drafts, member)}\n\n"
                f"SETTLED corrections, to apply:\n{listed(('accepted', 'upheld'))}\n\n"
                f"DISMISSED points, not to apply:\n{listed(('dismissed',))}\n\n"
                f"CONTESTED points, for you to decide:\n{listed(('contested',))}\n\n"
                "Write the agreed version."
            )
            system = prompts.SYNTHESIS_SYSTEM.replace("{task}", self.task.instructions)
            reply = self._ask_each(
                "synthesis",
                {lead: (system, user)},
                max_tokens=8000 if self.task.kind == "solution" else 4200,
                temperature=0.2,
            )
            if lead in reply:
                self._emit(
                    {
                        "type": "synthesis",
                        "phase": "synthesis",
                        "member": lead,
                        "markdown": reply[lead],
                    }
                )
                return lead, reply[lead]
            self._check()
        # No one could write it: fall back to the draft that was voted best.
        lead = order[0]
        return lead, drafts[lead]

    # -- stage 5 ----------------------------------------------------------------------
    def _vote(
        self, lead: str, text: str, round_number: int
    ) -> tuple[list[str], list[dict[str, str]]]:
        voters = [member for member in self.members if member.name != lead]
        calls = {
            member.name: (
                prompts.VOTE_SYSTEM,
                f"{self.task.block(member)}\n\nYou are {member.name}. The agreed version:\n<<<\n"
                f"{text[: max(member.budget, 6000)]}\n>>>\n\nVote.",
            )
            for member in voters
        }
        replies = self._ask_each("vote", calls, max_tokens=2200, temperature=0.1)
        agreed: list[str] = []
        objections: list[dict[str, str]] = []
        for name, reply in replies.items():
            vote = ""
            raised: list[dict[str, str]] = []
            for block, values in parse_blocks(reply, ("vote", "where", "problem", "reason", "fix")):
                if block == "VOTE":
                    vote = _first_word(values.get("vote", ""))
                elif block == "OBJECTION" and values.get("problem", "").strip():
                    raised.append(
                        {
                            "member": name,
                            "where": values.get("where", "")[:400],
                            "problem": values["problem"][:1500],
                            "reason": values.get("reason", "")[:1500],
                            "fix": values.get("fix", "")[:2500],
                        }
                    )
            # An objection with nothing to object to is not one; a vote that
            # cannot be read is not agreement.
            accepts = vote == "agree" and not raised
            if accepts:
                agreed.append(name)
            elif not raised:
                raised.append(
                    {
                        "member": name,
                        "where": "",
                        "problem": "It did not agree, and gave no objection that could be read.",
                        "reason": "",
                        "fix": "",
                    }
                )
            objections.extend(raised)
            self._emit(
                {
                    "type": "vote",
                    "phase": "vote",
                    "member": name,
                    "round": round_number,
                    "vote": "agree" if accepts else "object",
                    "objections": raised,
                }
            )
        return agreed, objections

    def _revise(
        self, lead: str, text: str, objections: list[dict[str, str]], round_number: int
    ) -> str:
        member = next(m for m in self.members if m.name == lead)
        listed = "\n\n".join(
            f"Objection from {o['member']}\nwhere: {o['where']}\nproblem: {o['problem']}\n"
            f"reason: {o['reason']}\nfix: {o['fix']}"
            for o in objections
        )
        system = prompts.REVISE_SYSTEM.replace("{task}", self.task.instructions)
        reply = self._ask_each(
            "revise",
            {
                lead: (
                    system,
                    f"{self.task.block(member)}\n\nYou are {lead}. The agreed version:\n<<<\n"
                    f"{text}\n>>>\n\nThe objections:\n\n{listed}\n\nWrite the revised version.",
                )
            },
            max_tokens=8000 if self.task.kind == "solution" else 4200,
            temperature=0.2,
        )
        if lead not in reply:
            return text
        self._emit(
            {
                "type": "revision",
                "phase": "revise",
                "member": lead,
                "round": round_number,
                "markdown": reply[lead],
            }
        )
        return reply[lead]

    # -- the whole thing --------------------------------------------------------------
    def run(self) -> Outcome:
        drafts = self._propose()
        took_part = [member.name for member in self.members]

        if len(self.members) < 2:
            # One model answered. There is no one to check it, and that is said.
            only = self.members[0].name
            return Outcome(
                text=drafts[only],
                lead=only,
                unanimous=False,
                rounds=0,
                agreed=[only],
                took_part=took_part,
                dropped=self.dropped,
            )

        issues, best = self._review(drafts)
        self._defend(drafts, issues)
        self._judge(drafts, issues)
        lead, text = self._synthesise(drafts, issues, self._lead_order(best))

        agreed: list[str] = []
        objections: list[dict[str, str]] = []
        rounds = 0
        for round_number in range(1, MAX_VOTE_ROUNDS + 1):
            self._phase(
                "vote",
                "Every model is checking the agreed version."
                if round_number == 1
                else f"The revised version is being checked (round {round_number}).",
            )
            rounds = round_number
            agreed, objections = self._vote(lead, text, round_number)
            if not objections:
                break
            if round_number == MAX_VOTE_ROUNDS:
                break
            self._phase("revise", "The objections are being worked into the agreed version.")
            text = self._revise(lead, text, objections, round_number)

        voters = [member.name for member in self.members if member.name != lead]
        return Outcome(
            text=text,
            lead=lead,
            # Everyone who could vote did, and all of them agreed.
            unanimous=not objections and set(agreed) == set(voters) and bool(voters),
            rounds=rounds,
            agreed=[lead, *agreed],
            objections=objections,
            issues=issues,
            took_part=took_part,
            dropped=self.dropped,
        )
