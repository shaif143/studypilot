"""Bringing a GitHub issue or pull request into a question, as text.

Read-only and deliberately narrow: one request for the issue, one for its
comments, both to ``api.github.com`` and nowhere else. The address the
student types only ever supplies an owner, a repository and a number, so it
cannot point this server at another host.

Public repositories need nothing. A private one needs a token with read
access in ``GITHUB_TOKEN`` on the backend; without it GitHub answers "not
found", and that is what the student is told.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

API = "https://api.github.com"
TIMEOUT = 12.0
MAX_COMMENTS = 60
MAX_CHARS = 60_000

#: Tests replace this to answer without a network.
TRANSPORT: httpx.BaseTransport | None = None

_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/"
    r"(?:issues|pull)/(\d+)(?:[/?#].*)?$"
)
_SHORT = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)$")


class GitHubImportError(ValueError):
    """An issue could not be brought in; the message is for the user."""


@dataclass
class ImportedIssue:
    title: str
    #: A name to show it under, as if it were an attached file.
    filename: str
    text: str
    url: str
    comment_count: int


def parse_reference(reference: str) -> tuple[str, str, int]:
    """``owner``, ``repo`` and number from a link or from ``owner/repo#123``."""
    value = reference.strip()
    match = _URL.match(value) or _SHORT.match(value)
    if not match:
        raise GitHubImportError(
            "Paste the issue's link from GitHub, or write it as owner/repository#123."
        )
    owner, repo, number = match.group(1), match.group(2), int(match.group(3))
    return owner, repo.removesuffix(".git"), number


def _get(path: str, params: dict[str, str] | None = None) -> httpx.Response:
    headers = {"accept": "application/vnd.github+json", "user-agent": "StudyPilot"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT) as client:
            return client.get(f"{API}{path}", params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise GitHubImportError("GitHub could not be reached. Try again in a moment.") from exc


def fetch_issue(reference: str) -> ImportedIssue:
    owner, repo, number = parse_reference(reference)
    response = _get(f"/repos/{owner}/{repo}/issues/{number}")
    if response.status_code == 404:
        raise GitHubImportError(
            f"{owner}/{repo}#{number} was not found. It may not exist, or the repository "
            "is private."
        )
    if response.status_code in {403, 429}:
        raise GitHubImportError(
            "GitHub is limiting requests from here for now. Try again in a few minutes."
        )
    if response.status_code != 200:
        raise GitHubImportError(f"GitHub answered with status {response.status_code}.")
    issue = response.json()
    kind = "Pull request" if issue.get("pull_request") else "Issue"
    title = str(issue.get("title") or f"{owner}/{repo}#{number}")
    author = (issue.get("user") or {}).get("login", "someone")
    labels = ", ".join(str(label.get("name")) for label in issue.get("labels") or [])
    parts = [
        f"# {kind} {owner}/{repo}#{number}: {title}",
        f"State: {issue.get('state', 'unknown')} · opened by {author}"
        + (f" · labels: {labels}" if labels else ""),
        str(issue.get("body") or "(no description)").strip(),
    ]

    comments: list[dict] = []
    if int(issue.get("comments") or 0) > 0:
        listed = _get(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            {"per_page": str(MAX_COMMENTS)},
        )
        if listed.status_code == 200 and isinstance(listed.json(), list):
            comments = listed.json()
    for comment in comments:
        who = (comment.get("user") or {}).get("login", "someone")
        when = str(comment.get("created_at") or "")[:10]
        parts.append(f"## Comment by {who} on {when}\n{str(comment.get('body') or '').strip()}")
    total = int(issue.get("comments") or 0)
    if total > len(comments):
        parts.append(f"(Only the first {len(comments)} of {total} comments are included.)")

    text = "\n\n".join(parts)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n(The rest was too long to include.)"
    return ImportedIssue(
        title=title,
        filename=f"{repo}-{number}.md",
        text=text,
        url=str(issue.get("html_url") or f"https://github.com/{owner}/{repo}/issues/{number}"),
        comment_count=len(comments),
    )
