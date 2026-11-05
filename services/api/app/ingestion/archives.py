"""Archives: a .zip or .rar of lecture files becomes its members, one by one.

Lecturers hand out "Week 3.zip"; nothing in it needs the archive kept. Each
member the pipeline can read is indexed as its own document, named by its
path inside the archive, and the members it cannot read are reported by
name with a reason — the same rule as a folder upload.

Safety: members are read into memory, never written under their own names;
a zip is read with the standard library, a rar with the `bsdtar` every Mac
and Linux ships (libarchive reads RAR 4 and most RAR 5), extracted into a
private temporary directory with path checks on the way back out. Bounds on
member count and total size keep a hostile archive from exhausting the
machine ("zip bombs" expand to terabytes from kilobytes).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

log = logging.getLogger(__name__)

ZIP_MEDIA_TYPE = "application/zip"
RAR_MEDIA_TYPE = "application/vnd.rar"
_ZIP_MAGIC = b"PK\x03\x04"
_RAR_MAGIC = b"Rar!\x1a\x07"

#: Members with these suffixes are offered to the pipeline; the rest are
#: reported as skipped with a reason.
READABLE_SUFFIXES = frozenset({".pdf", ".pptx", ".ppt"})
MODEL_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a"})
ARCHIVE_SUFFIXES = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"})
MAX_MEMBERS = 200


class UnreadableArchiveError(ValueError):
    pass


def archive_kind(data: bytes, filename: str = "") -> str:
    """ "zip", "rar", or "" when the bytes are neither."""
    if data.startswith(_RAR_MAGIC):
        return "rar"
    if data.startswith(_ZIP_MAGIC):
        return "zip"
    return ""


@dataclass
class Member:
    path: str
    data: bytes


@dataclass
class SkippedMember:
    path: str
    reason: str


@dataclass
class Unpacked:
    members: list[Member]
    skipped: list[SkippedMember]


def _classify(path: str) -> str:
    """ "" to read it, otherwise why not."""
    name = PurePosixPath(path).name
    if not name or name.startswith(".") or name.startswith("__MACOSX") or "/__MACOSX/" in path:
        return "hidden"
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in READABLE_SUFFIXES:
        return ""
    if suffix in MODEL_SUFFIXES:
        return "Images and audio need a model to read them; upload them one at a time."
    if suffix in ARCHIVE_SUFFIXES:
        return "An archive inside an archive; unpack it first."
    return "Not a supported type."


def _safe_relative(path: str) -> str | None:
    """A member path with no absolute or parent components, or None."""
    pure = PurePosixPath(path.replace("\\", "/"))
    parts = [part for part in pure.parts if part not in ("", ".", "/")]
    if not parts or any(part == ".." for part in parts) or pure.is_absolute():
        return None
    return "/".join(parts)


def unpack(data: bytes, kind: str, *, max_member_bytes: int, max_total_bytes: int) -> Unpacked:
    if kind == "zip":
        return _unpack_zip(data, max_member_bytes=max_member_bytes, max_total_bytes=max_total_bytes)
    if kind == "rar":
        return _unpack_rar(data, max_member_bytes=max_member_bytes, max_total_bytes=max_total_bytes)
    raise UnreadableArchiveError("Not a .zip or .rar archive.")


def _unpack_zip(data: bytes, *, max_member_bytes: int, max_total_bytes: int) -> Unpacked:
    import io

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnreadableArchiveError(f"The zip file could not be opened: {exc}") from exc
    members: list[Member] = []
    skipped: list[SkippedMember] = []
    total = 0
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_MEMBERS:
            skipped.append(
                SkippedMember("", f"Only the first {MAX_MEMBERS} of {len(infos)} files were read.")
            )
        for info in infos[:MAX_MEMBERS]:
            path = _safe_relative(info.filename)
            if path is None:
                skipped.append(SkippedMember(info.filename, "Unsafe path inside the archive."))
                continue
            reason = _classify(path)
            if reason == "hidden":
                continue
            if reason:
                skipped.append(SkippedMember(path, reason))
                continue
            if info.file_size > max_member_bytes:
                skipped.append(SkippedMember(path, "Larger than the upload limit."))
                continue
            if total + info.file_size > max_total_bytes:
                skipped.append(SkippedMember(path, "The archive's total size exceeds the limit."))
                continue
            try:
                # Trust the declared size no further than reading one byte past it.
                with archive.open(info) as handle:
                    content = handle.read(max_member_bytes + 1)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                skipped.append(SkippedMember(path, f"Could not be read: {exc}"))
                continue
            if len(content) > max_member_bytes:
                skipped.append(SkippedMember(path, "Larger than the upload limit."))
                continue
            total += len(content)
            members.append(Member(path=path, data=content))
    return Unpacked(members=members, skipped=skipped)


def _unpack_rar(data: bytes, *, max_member_bytes: int, max_total_bytes: int) -> Unpacked:
    tar = shutil.which("bsdtar") or shutil.which("tar")
    if tar is None:
        raise UnreadableArchiveError("No archive tool is available to read .rar files here.")
    members: list[Member] = []
    skipped: list[SkippedMember] = []
    with tempfile.TemporaryDirectory(prefix="studypilot-rar-") as scratch:
        root = Path(scratch)
        source = root / "archive.rar"
        source.write_bytes(data)
        target = root / "out"
        target.mkdir()
        try:
            listing = subprocess.run(
                [tar, "-tf", str(source)], capture_output=True, text=True, timeout=60, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnreadableArchiveError(f"The archive could not be listed: {exc}") from exc
        if listing.returncode != 0:
            detail = (listing.stderr or "").strip().splitlines()
            raise UnreadableArchiveError(
                "The .rar file could not be read"
                + (f": {detail[-1]}" if detail else ".")
                + " Encrypted or RAR5 solid archives are not supported."
            )
        names = [line for line in listing.stdout.splitlines() if line and not line.endswith("/")]
        if len(names) > MAX_MEMBERS:
            skipped.append(
                SkippedMember("", f"Only the first {MAX_MEMBERS} of {len(names)} files were read.")
            )
        wanted: list[str] = []
        for raw in names[:MAX_MEMBERS]:
            path = _safe_relative(raw)
            if path is None:
                skipped.append(SkippedMember(raw, "Unsafe path inside the archive."))
                continue
            reason = _classify(path)
            if reason == "hidden":
                continue
            if reason:
                skipped.append(SkippedMember(path, reason))
                continue
            wanted.append(raw)
        if wanted:
            try:
                extracted = subprocess.run(
                    [tar, "-xf", str(source), "-C", str(target), "--", *wanted],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise UnreadableArchiveError(f"The archive could not be unpacked: {exc}") from exc
            if extracted.returncode != 0:
                log.info("rar_extract_partial", extra={"detail": extracted.stderr[-300:]})
        total = 0
        resolved_root = target.resolve()
        for raw in wanted:
            path = _safe_relative(raw) or raw
            candidate = (target / path).resolve()
            if resolved_root not in candidate.parents or not candidate.is_file():
                skipped.append(SkippedMember(path, "Could not be unpacked."))
                continue
            size = candidate.stat().st_size
            if size > max_member_bytes:
                skipped.append(SkippedMember(path, "Larger than the upload limit."))
                continue
            if total + size > max_total_bytes:
                skipped.append(SkippedMember(path, "The archive's total size exceeds the limit."))
                continue
            total += size
            members.append(Member(path=path, data=candidate.read_bytes()))
    return Unpacked(members=members, skipped=skipped)
