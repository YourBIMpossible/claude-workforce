"""Canonical JSON and hashing (FB1.4 D2)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical(obj))


def event_self_hash(row: dict[str, Any]) -> str:
    """self = SHA-256 of the canonical event header (everything except self)."""
    header = {
        "job": row["job"],
        "seq": row["seq"],
        "type": row["type"],
        "msg_id": row["msg_id"],
        "author_role": row["author_role"],
        "session_id": row["session_id"],
        "generation": row["generation"],
        "reply_to": row["reply_to"],
        "grant_ref": row["grant_ref"],
        "ts": row["ts"],
        "content_sha256": row["content_sha256"],
        "prev": row["prev"],
    }
    return sha256_obj(header)
