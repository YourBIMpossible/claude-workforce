"""Retained-checkpoint comparison for `wf verify --checkpoint seq:hash` (spec lines 141, 153-156, 180; T36 (d)(e);
D-019 wording). Owned by lane C after the dispatch-ready seam commit.

Seam contract (frozen callers: wf.py and commands.verify):
  parse(text, job) -> (seq, self)       called by wf.py before verify. Malformed syntax or a missing job argument raise
                                        WfUsage (exit 2, code "usage"). This is the only place that validates the option,
                                        so lane C changes the accepted syntax here without touching wf.py.
  compare(result, checkpoint, chains)   called by commands.verify AFTER the ladder result is built and BEFORE `clean`
                                        is final. `result` is the verify dict (mutable; "jobs"[job]["problems"] and
                                        "clean" may be updated, a "checkpoint" key may be added). `checkpoint` is the
                                        (seq, self) pair for result["checkpoint_job"]. `chains` maps job id -> list of
                                        (seq, self) pairs for every stored event of that job, in seq order, read inside
                                        the same snapshot as the chain verification.

Owner behaviour decisions (2026-10-13), binding for lane C:
  - A retained checkpoint is a sequence/hash pair. Check whether that sequence exists in the chain and whether its
    recorded hash matches.
  - A valid older checkpoint must not fail merely because legitimate later events advanced the head.
  - A missing checkpointed sequence or a mismatching hash is detected: `clean` false, a reason naming the checkpoint
    and the ladder level reached.
  - An altered retained checkpoint that agrees with an altered chain is NOT detectable; the output states that the
    retained value is trusted. Never use the words "authentic" or "tamper-proof".
  - Who independently retains the checkpoint stays outside this build (G-28).

Seam state (before lane C): parse() validates `<int>:<64 hex>`; compare() REFUSES when a checkpoint is supplied,
so no caller can report a checkpoint verification that was not performed. Without --checkpoint nothing changes.
"""
from __future__ import annotations

import re

from .db import WfRefused, WfUsage

_SYNTAX = re.compile(r"^([0-9]+):([0-9a-f]{64})$")
NOT_IMPLEMENTED = ("--checkpoint comparison is not implemented at this revision (lane C deliverable); "
                   "no checkpoint verification was performed")


def parse(text: str, job: str | None) -> tuple[int, str]:
    """`seq:hash` -> (seq, self). Usage error on malformed syntax or when no job is named."""
    if job is None or not str(job).strip():
        raise WfUsage("--checkpoint requires a job argument (a checkpoint belongs to one job)")
    m = _SYNTAX.match(text.strip()) if isinstance(text, str) else None
    if not m:
        raise WfUsage("--checkpoint must be <seq>:<sha256 hex> (decimal sequence, 64 lowercase hex digits)")
    seq = int(m.group(1))
    if seq < 1:
        raise WfUsage("--checkpoint sequence must be >= 1")
    return seq, m.group(2)


def compare(result: dict, checkpoint: tuple[int, str] | None, chains: dict[str, list[tuple[int, str]]]) -> dict:
    """Apply the retained-checkpoint comparison to a verify result. Seam: refuse rather than pretend."""
    if checkpoint is None:
        return result
    raise WfRefused(NOT_IMPLEMENTED, checkpoint={"seq": checkpoint[0], "self": checkpoint[1]})
