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

Lane C (T36 (d)/(e)): compare() looks the checkpoint seq up in chains[job] and compares the stored `self` with the
retained value. The result gains a "checkpoint" key (supplied value, found value, status, ladder levels, limits).
A missing seq or a different hash adds one problem to result["jobs"][job]["problems"] and makes `clean` false; the
exit code is left to the caller (verify reports, it does not raise). Level 3 is reported only as "compared against
the value supplied": the retained value is trusted as supplied, so an altered checkpoint that agrees with an
altered chain is not detected here. Level 4 is never claimed.
"""
from __future__ import annotations

import re

from .db import WfUsage

_SYNTAX = re.compile(r"^([0-9]+):([0-9a-f]{64})$")


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


LEVEL_3 = "level 3 (retained-checkpoint comparison)"
TRUST = ("the retained checkpoint is trusted as supplied: this comparison only shows whether the current chain "
         "contains that seq with that self hash")
UNDETECTABLE = ("an altered checkpoint that agrees with an altered chain is not detectable by this comparison; "
                "events after the checkpoint seq are not covered by it")
DISCLAIMER = "not an authenticity claim"


def _levels(result: dict, pre_problems: list[str]) -> dict[str, str]:
    """Ladder levels 1-2 as already decided by verify (before this comparison added anything)."""
    l1 = "passed" if not pre_problems else "failed"
    ledger = result.get("ledger_problems") or {}
    l2 = "failed" if ledger.get("integrity") else "passed"
    return {"1": f"chain consistency: {l1}", "2": f"accidental-corruption check (integrity_check): {l2}"}


def compare(result: dict, checkpoint: tuple[int, str] | None, chains: dict[str, list[tuple[int, str]]]) -> dict:
    """Apply the retained-checkpoint comparison (spec 3C level 3) to a verify result.

    Without a checkpoint the result is returned unchanged. With one, result["checkpoint"] records the comparison
    and a missing seq or a different hash is a problem of the checkpoint's job (clean false)."""
    if checkpoint is None:
        return result
    seq, expected = checkpoint
    job = result.get("checkpoint_job")
    stored = dict(chains.get(job, [])) if job is not None else {}
    found = stored.get(seq)
    head_seq = max(stored) if stored else 0
    jobs = result.setdefault("jobs", {})
    jres = jobs.setdefault(job, {"problems": []})
    problems = jres.setdefault("problems", [])
    levels = _levels(result, list(problems))
    status = "missing" if found is None else ("match" if found == expected else "mismatch")
    found_text = f"no stored event at seq {seq} (current head seq {head_seq})" if found is None else f"self {found}"
    levels["3"] = (f"retained-checkpoint comparison: {status}"
                   + (" (against the value as supplied)" if status == "match" else ""))
    levels["4"] = "authorship: not checked (R1 makes no such claim)"
    if status != "match":
        problems.append(f"retained checkpoint {job} seq {seq}: expected self {expected}, found {found_text}; "
                        f"ladder level reached: {LEVEL_3}, {status}")
        result["clean"] = False
    result["checkpoint"] = {
        "job": job, "seq": seq, "expected_self": expected, "found_self": found, "head_seq": head_seq,
        "status": status, "level_reached": LEVEL_3, "levels": levels,
        "statement": (f"the current chain contains seq {seq} with the retained self hash" if status == "match" else
                      f"the current chain does not contain seq {seq} with the retained self hash ({status})"),
        "trust": TRUST, "limits": UNDETECTABLE, "disclaimer": DISCLAIMER}
    return result
