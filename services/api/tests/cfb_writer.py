"""A minimal OLE compound file writer, so tests can make a real .ppt.

`olefile` reads compound files but does not create them, and nothing else
in the toolchain writes the old PowerPoint container. Writing one is small
when every stream is at least 4096 bytes (so the mini-stream is never
needed): a header, one FAT sector, one directory sector, and the streams in
consecutive sectors. MS-CFB version 3, 512-byte sectors.
"""

from __future__ import annotations

import struct

SECTOR = 512
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
FREESECT = 0xFFFFFFFF
NOSTREAM = 0xFFFFFFFF
MINI_CUTOFF = 4096


def _pad(data: bytes, size: int = SECTOR) -> bytes:
    remainder = len(data) % size
    return data + (b"\0" * (size - remainder) if remainder else b"")


def _entry(
    name: str, kind: int, *, left: int, right: int, child: int, start: int, size: int
) -> bytes:
    encoded = name.encode("utf-16-le") + b"\0\0"
    return (
        encoded.ljust(64, b"\0")
        + struct.pack("<HBB", len(encoded), kind, 1)
        + struct.pack("<III", left, right, child)
        + b"\0" * 16  # CLSID
        + struct.pack("<I", 0)  # state bits
        + b"\0" * 16  # creation and modification times
        + struct.pack("<II", start, size)
        + b"\0" * 4  # the high half of the 8-byte size, unused in version 3
    )


def write_compound_file(streams: dict[str, bytes]) -> bytes:
    """Streams by name; each is padded to the mini-stream cutoff."""
    names = sorted(streams, key=lambda name: (len(name), name.upper()))
    if len(names) > 3:
        raise ValueError("this tiny writer holds at most three streams")
    payloads = [_pad(streams[name].ljust(MINI_CUTOFF, b"\0")) for name in names]

    # Sector 0: FAT. Sector 1: directory. Then the streams, back to back.
    fat: list[int] = [FATSECT, ENDOFCHAIN]
    starts: list[int] = []
    for payload in payloads:
        count = len(payload) // SECTOR
        starts.append(len(fat))
        fat.extend(range(len(fat) + 1, len(fat) + count))
        fat.append(ENDOFCHAIN)
    if len(fat) > SECTOR // 4:
        raise ValueError("streams too large for a single FAT sector")
    fat_sector = _pad(b"".join(struct.pack("<I", entry) for entry in fat))

    # Directory: a red-black tree by (length, name); the middle name is the
    # root's child, the others its siblings. olefile accepts a plain tree.
    ids = {name: index + 1 for index, name in enumerate(names)}
    middle = names[len(names) // 2]
    entries = [
        _entry(
            "Root Entry",
            5,
            left=NOSTREAM,
            right=NOSTREAM,
            child=ids[middle],
            start=ENDOFCHAIN,
            size=0,
        )
    ]
    for name in names:
        left = right = NOSTREAM
        if name == middle:
            before = [n for n in names if n != middle and names.index(n) < names.index(middle)]
            after = [n for n in names if n != middle and names.index(n) > names.index(middle)]
            if before:
                left = ids[before[0]]
            if after:
                right = ids[after[0]]
        entries.append(
            _entry(
                name,
                2,
                left=left,
                right=right,
                child=NOSTREAM,
                start=starts[names.index(name)],
                size=len(streams[name]),
            )
        )
    directory = _pad(b"".join(entries))

    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\0" * 16  # CLSID
        + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
        + b"\0" * 6
        + struct.pack("<IIIIIIII", 0, 1, 1, 0, MINI_CUTOFF, ENDOFCHAIN, 0, ENDOFCHAIN)
        + struct.pack("<I", 0)
        + struct.pack("<I", 0)  # first FAT sector in DIFAT...
    )
    # ...the DIFAT proper: 109 entries, the first names sector 0, rest free.
    difat = struct.pack("<I", 0) + struct.pack("<I", FREESECT) * 108
    header = header[: 512 - len(difat)] + difat
    assert len(header) == SECTOR, len(header)
    return header + fat_sector + directory + b"".join(payloads)
