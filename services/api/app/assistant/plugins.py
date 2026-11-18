"""Reading a plugin, and what a connector may be.

A plugin is an archive in the layout Claude's own plugins use: a manifest in
``.claude-plugin/plugin.json``, skills in ``skills/<name>/SKILL.md``, commands
in ``commands/<name>.md``, and remote MCP servers in ``.mcp.json``. A single
skill packed on its own (a ``SKILL.md`` at the top of the archive) is taken as
a plugin of one skill.

Only text is read out of it, in memory, with limits on how much. Nothing is
unpacked to disk and nothing is run: a skill here is instructions and
reference pages for Claude to read, so a script inside one is listed as a
file Claude can look at, not something that executes. Agents, hooks and MCP
servers that start a local program have no meaning on a website and are
reported as left out rather than silently dropped.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 2000
#: Text kept per file, and across the whole plugin.
MAX_FILE_CHARS = 200_000
MAX_PLUGIN_CHARS = 3_000_000
MAX_SKILLS = 60
MAX_COMMANDS = 80
MAX_FILES_PER_SKILL = 40
MAX_CONNECTORS = 10

_TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".py", ".js", ".ts", ".sh", ".html", ".xml"}
)
_NAME = re.compile(r"[^a-z0-9_-]+")


class PluginError(ValueError):
    """An archive that cannot be used as a plugin; the message is for the user."""


@dataclass
class ParsedPlugin:
    name: str
    version: str = ""
    description: str = ""
    skills: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    #: Remote MCP servers it declares: [{"label", "url"}].
    connectors: list[dict[str, str]] = field(default_factory=list)
    #: What was in it that is not used here.
    notes: list[str] = field(default_factory=list)


def slug(value: str, fallback: str = "item") -> str:
    """A name safe to hand to the API: lower-case letters, digits, "-" and "_"."""
    cleaned = _NAME.sub("-", value.strip().lower()).strip("-_")
    return (cleaned or fallback)[:48]


def check_url(url: str) -> str:
    """A connector's address, or a refusal that says what is wrong with it."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("A connector's address must start with https://.")
    host = parts.hostname or ""
    if "." not in host or host.endswith(".local") or host == "localhost":
        raise ValueError(
            "That address is not reachable from the internet. A connector has to be a "
            "public MCP server; one running on your own computer cannot be used here."
        )
    if parts.username or parts.password:
        raise ValueError("Put the access token in its own box, not in the address.")
    if len(url) > 1000:
        raise ValueError("That address is too long.")
    return url


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    """The ``key: value`` header of a Markdown file, and the rest.

    Deliberately small: the two fields read from it are single values, plain
    or quoted, or a block written with ``>`` or ``|``. Anything richer in the
    header is ignored rather than guessed at.
    """
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    key = ""
    block: list[str] = []

    def close() -> None:
        if key and block:
            meta[key] = " ".join(part.strip() for part in block if part.strip())

    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match and not line.startswith((" ", "\t")):
            close()
            key, value = match.group(1).lower(), match.group(2).strip()
            block = []
            if value in {">", "|", ">-", "|-", ">+", "|+"}:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta[key] = value
            key = "" if value else key
        elif key:
            block.append(line)
    close()
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


def _decode(data: bytes) -> str | None:
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


class _Archive:
    """The text files of an archive, under limits, with paths made safe."""

    def __init__(self, data: bytes) -> None:
        if len(data) > MAX_ARCHIVE_BYTES:
            raise PluginError("That archive is larger than 8 MB.")
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise PluginError(
                "That is not a plugin archive. Add a .zip, .plugin or .skill file."
            ) from exc
        infos = [info for info in self._zip.infolist() if not info.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise PluginError("That archive holds too many files to be a plugin.")
        self._infos: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            path = PurePosixPath(info.filename.replace("\\", "/"))
            # A path that climbs out of the archive is not a file of the plugin.
            if path.is_absolute() or ".." in path.parts or "__MACOSX" in path.parts:
                continue
            self._infos[str(path)] = info
        self._read = 0

    @property
    def paths(self) -> list[str]:
        return sorted(self._infos)

    def text(self, path: str) -> str | None:
        info = self._infos.get(path)
        if info is None or info.file_size > MAX_FILE_CHARS * 4:
            return None
        if self._read + info.file_size > MAX_PLUGIN_CHARS * 4:
            raise PluginError("That plugin holds more text than can be kept here.")
        text = _decode(self._zip.read(info))
        if text is None:
            return None
        self._read += len(text)
        return text[:MAX_FILE_CHARS]


def _root_of(paths: list[str]) -> str:
    """The folder the plugin lives in, as a prefix ending in "/" (or "")."""
    for path in paths:
        if path.endswith(".claude-plugin/plugin.json"):
            return path[: -len(".claude-plugin/plugin.json")]
    for marker in ("SKILL.md", "skills/"):
        hits = sorted(
            (path for path in paths if f"/{marker}" in f"/{path}"),
            key=lambda item: item.count("/"),
        )
        if hits:
            first = hits[0]
            return first[: first.index(marker)] if marker in first else ""
    return ""


def _servers(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("mcpServers"), dict):
        return raw["mcpServers"]
    return raw if isinstance(raw, dict) else {}


def parse(filename: str, data: bytes) -> ParsedPlugin:
    """Read a plugin out of an uploaded archive."""
    archive = _Archive(data)
    paths = archive.paths
    root = _root_of(paths)
    inside = [path[len(root) :] for path in paths if path.startswith(root)]

    manifest: dict[str, Any] = {}
    raw_manifest = archive.text(f"{root}.claude-plugin/plugin.json")
    if raw_manifest:
        try:
            loaded = json.loads(raw_manifest)
            manifest = loaded if isinstance(loaded, dict) else {}
        except ValueError as exc:
            raise PluginError("The plugin's plugin.json is not valid JSON.") from exc

    stem = PurePosixPath(filename or "plugin").stem
    plugin = ParsedPlugin(
        name=str(manifest.get("name") or stem or "plugin")[:120],
        version=str(manifest.get("version") or "")[:40],
        description=str(manifest.get("description") or "")[:2000],
    )

    # --- skills ----------------------------------------------------------
    skill_dirs: list[str] = []
    if "SKILL.md" in inside:
        skill_dirs.append("")
    skill_dirs += sorted(
        {
            rel[: -len("SKILL.md")]
            for rel in inside
            if rel.startswith("skills/") and rel.count("/") == 2 and rel.endswith("/SKILL.md")
        }
    )
    for directory in skill_dirs[:MAX_SKILLS]:
        text = archive.text(f"{root}{directory}SKILL.md")
        if not text:
            continue
        meta, body = frontmatter(text)
        fallback = PurePosixPath(directory.rstrip("/")).name or plugin.name
        files: dict[str, str] = {}
        for rel in inside:
            if not rel.startswith(directory) or rel == f"{directory}SKILL.md":
                continue
            if directory == "" and rel.startswith((".claude-plugin/", "commands/", "skills/")):
                continue
            if PurePosixPath(rel).suffix.lower() not in _TEXT_SUFFIXES:
                continue
            if len(files) >= MAX_FILES_PER_SKILL:
                break
            content = archive.text(f"{root}{rel}")
            if content is not None:
                files[rel[len(directory) :]] = content
        plugin.skills.append(
            {
                "name": slug(meta.get("name") or fallback, "skill"),
                "description": (meta.get("description") or "")[:1200],
                "body": body,
                "files": files,
            }
        )
    if len(skill_dirs) > MAX_SKILLS:
        plugin.notes.append(f"Only the first {MAX_SKILLS} skills were added.")

    # --- commands ----------------------------------------------------------
    command_paths = sorted(
        rel
        for rel in inside
        if rel.startswith("commands/") and rel.endswith(".md") and rel.count("/") == 1
    )
    for rel in command_paths[:MAX_COMMANDS]:
        text = archive.text(f"{root}{rel}")
        if not text:
            continue
        meta, body = frontmatter(text)
        if not body.strip():
            continue
        plugin.commands.append(
            {
                "name": slug(PurePosixPath(rel).stem, "command"),
                "description": (meta.get("description") or "")[:400],
                "body": body,
            }
        )

    # --- connectors --------------------------------------------------------
    servers: dict[str, Any] = {}
    raw_mcp = archive.text(f"{root}.mcp.json")
    if raw_mcp:
        try:
            servers.update(_servers(json.loads(raw_mcp)))
        except ValueError:
            plugin.notes.append("Its .mcp.json could not be read, so no connectors were added.")
    servers.update(_servers({"mcpServers": manifest.get("mcpServers")}))
    local = 0
    for label, spec in sorted(servers.items()):
        if not isinstance(spec, dict):
            continue
        url = spec.get("url")
        if not isinstance(url, str) or not url:
            local += 1
            continue
        try:
            checked = check_url(url)
        except ValueError:
            local += 1
            continue
        if len(plugin.connectors) < MAX_CONNECTORS:
            plugin.connectors.append({"label": str(label)[:120], "url": checked})
    if local:
        noun = "connector starts" if local == 1 else "connectors start"
        plugin.notes.append(
            f"{local} {noun} a program on a computer, or is not on a public https address, "
            "and cannot be used on a website."
        )

    # --- what is not used here ---------------------------------------------
    if any(rel.startswith("agents/") for rel in inside):
        plugin.notes.append("Its agents are not used here.")
    if any(rel.startswith("hooks/") for rel in inside) or manifest.get("hooks"):
        plugin.notes.append("Its hooks are not used here.")
    if any(
        PurePosixPath(rel).suffix.lower() in {".py", ".sh", ".js", ".ts"}
        for rel in inside
        if rel.startswith("skills/") or rel.startswith("scripts/")
    ):
        plugin.notes.append("Scripts in it are not run here; Claude can only read them.")

    if not (plugin.skills or plugin.commands or plugin.connectors):
        raise PluginError(
            "Nothing usable was found in that archive. A plugin holds skills "
            "(skills/<name>/SKILL.md), commands (commands/<name>.md) or remote "
            "connectors (.mcp.json)."
        )
    return plugin
