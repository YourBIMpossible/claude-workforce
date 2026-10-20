"""Manifest lines: exactly three fields `<sha256>  <size>  <relpath>` (FB1.4 D7, repair R2)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable

from .canon import sha256_file

SEP = "  "
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DIGITS = re.compile(r"[0-9]+")


class ManifestMalformed(Exception):
    def __init__(self, line_no: int, reason: str, text: str):
        super().__init__(f"MALFORMED line {line_no}: {reason}: {text!r}")
        self.line_no = line_no
        self.reason = reason
        self.text = text


@dataclass(frozen=True)
class Entry:
    sha256: str
    size: int
    relpath: str


def format_line(e: Entry) -> str:
    if not _HEX64.match(e.sha256):
        raise ValueError("sha256 must be 64 lowercase hex")
    if e.size < 0:
        raise ValueError("size must be non-negative")
    if SEP in e.relpath or "\n" in e.relpath or not e.relpath:
        raise ValueError("relpath must be non-empty and contain no double space or newline")
    return f"{e.sha256}{SEP}{e.size}{SEP}{e.relpath}"


def render(entries: list[Entry]) -> str:
    """The manifest's text: one `format_line` per entry, each ending in LF."""
    lines = [format_line(e) for e in entries]
    return "\n".join(lines) + ("\n" if lines else "")


def write(path: str, entries: list[Entry], write_text: Callable[[str, str], None] | None = None) -> None:
    """Write the manifest. `export.refresh` passes its atomic, retried writer (FIX2 Y3: staged, fsynced and replaced
    like the other export files, inside the refresh's retry budget); without `write_text` the file is written in
    place."""
    text = render(entries)
    if write_text is not None:
        write_text(path, text)
        return
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def parse_line(line_no: int, line: str) -> Entry:
    text = line.rstrip("\r\n")
    fields = text.split(SEP)
    if len(fields) != 3:
        raise ManifestMalformed(line_no, f"expected exactly 3 fields, found {len(fields)}", text)
    sha, size, rel = fields
    if not _HEX64.match(sha):
        raise ManifestMalformed(line_no, "field 1 is not a 64-hex sha256", text)
    if not _DIGITS.fullmatch(size):  # PACKAGE-FIX1 E4: ASCII digits only (str.isdigit accepts e.g. U+00B2)
        raise ManifestMalformed(line_no, "field 2 is not a non-negative integer", text)
    if not rel or rel != rel.strip():
        raise ManifestMalformed(line_no, "field 3 is empty or has surrounding whitespace", text)
    return Entry(sha, int(size), rel)


def read(path: str) -> list[Entry]:
    out: list[Entry] = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            if line.strip() == "":
                if line.rstrip("\r\n") == "":
                    continue
                raise ManifestMalformed(i, "whitespace-only line", line)
            out.append(parse_line(i, line))
    return out


def build(root: str, relpaths: list[str]) -> list[Entry]:
    """Hash existing files under root; missing paths are skipped (caller decides what absence means)."""
    out: list[Entry] = []
    for rel in sorted(set(relpaths)):
        full = os.path.join(root, rel)
        if os.path.isfile(full):
            out.append(Entry(sha256_file(full), os.path.getsize(full), rel))
    return out


def entries_to_dicts(entries: list[Entry]) -> list[dict]:
    return [{"sha256": e.sha256, "size": e.size, "path": e.relpath} for e in entries]
