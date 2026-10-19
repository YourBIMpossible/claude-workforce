"""Gate decision core (B2 slice 4a, lane S4G, Plexy): INTERFACE-CONTRACT-S4A.md §1 payload, §2 rules 0-8, §6
`gatecore` signatures, §7 S4G (T13, T15, T17, T18a, T25, shell rule + SHELL_LIMITATION, fail closed), §8 decisions.
Contract copy: WorkForce collab folder `contracts/b2s4a` (SHA256SUMS verified).

Fixture ledgers are built with the real `wf` CLI (`test_wf.Base`: fresh WF_TEST_TMP, refuse-existing, never delete);
payloads copy the recorded PreToolUse shape (sample-hook-payloads.jsonl: session_id, hook_event_name, tool_name,
tool_input, cwd). Hard limits honoured: no hook is registered or invoked, nothing is written to a user profile or a
settings file; `decide` is called in-process on fixture payloads only. Claim limit: decisions on fixtures; no claim
that any write was prevented in Claude Code (slice 4b).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unittest
from typing import Any

from test_wf import Base, CONTRACT, GRANT  # noqa: E402
from wfcore import db as dbm, gatecore, grant  # noqa: E402

FAR = "2999-12-31T23:59:59Z"
NOW = "2026-10-17T12:00:00Z"
ROLES = {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
         "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
         "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["tools/wf/**"]}}
GATE_GRANT = dict(GRANT, actions=["write"], resources=["jobs/j/work/**"], exclusions=["jobs/j/work/secret/**"],
                  ars={"roles": ROLES, "replacement": {"allowed": True, "max": 1, "expiry": FAR}}, expiry=FAR)
TURN = "owner turn for job j"


class _Gate(Base):
    grant_obj: dict = GATE_GRANT

    def setUp(self) -> None:
        super().setUp()
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(self.grant_obj, fh)
        self.hook = os.path.join(os.path.dirname(self.dir), f"{self._testMethodName}-hooks", "wf_gate_hook.py")
        self.cfg = os.path.join(os.path.dirname(self.hook), "wf_gate.json")

    # ---- payloads and decisions
    def p(self, rel: str | None = None, *, session: str = "B1", tool: str = "Write", absolute: str | None = None,
          command: str | None = None, cwd: str | None = None) -> dict:
        ti: dict[str, Any] = {}
        if tool in ("Write", "Edit"):
            ti = {"file_path": absolute if absolute is not None else os.path.join(self.dir, *rel.split("/")),
                  "content": "x"}
        elif tool == "NotebookEdit":
            ti = {"notebook_path": absolute if absolute is not None else os.path.join(self.dir, *rel.split("/"))}
        elif tool in ("Bash", "PowerShell"):
            ti = {"command": command if command is not None else "echo hi"}
        else:
            ti = {"file_path": os.path.join(self.dir, "x")}
        return {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti,
                "cwd": cwd or self.dir, "transcript_path": "fixture", "permission_mode": "auto"}

    def d(self, payload: dict, now: str = NOW, **kw: Any) -> dict:
        return gatecore.decide(payload, root=self.dir, db_path=self.db, now=now,
                               gate_files=[self.hook, self.cfg], **kw)

    def deny(self, payload: dict, reason: str, **kw: Any) -> dict:
        r = self.d(payload, **kw)
        self.assertEqual((r["decision"], r["reason"]), ("deny", reason), r)
        self.assertIn(reason, gatecore.REASONS)
        return r

    def allow(self, payload: dict, reason: str, **kw: Any) -> dict:
        r = self.d(payload, **kw)
        self.assertEqual((r["decision"], r["reason"]), ("allow", reason), r)
        self.assertIn(reason, gatecore.REASONS)
        return r

    # ---- CLI fixture steps
    def ok(self, res: tuple[int, dict]) -> dict:
        self.assertEqual(res[0], 0, res[1])
        return res[1]

    def bind(self, role: str, session: str, generation: int = 1, job: str = "j") -> None:
        self.ok(self.run_wf("bind", job, "--bind-role", role, "--bind-session", session, "--bind-generation",
                            str(generation), role="coordinator", session="C1"))

    def explicit(self) -> None:
        """contract, activated grant with ARS, coordinator C1, builder B1 (explicit mode)."""
        self.new_job(); self.activate()
        self.bind("coordinator", "C1"); self.bind("builder", "B1")

    def stop(self, session: str, generation: int = 1, job: str = "j") -> None:
        self.ok(self.run_wf("append", job, "--type", "stop-confirmed", "--content", json.dumps(
            {"role": "builder", "session": session, "generation": generation, "how": "exit observed", "when": NOW,
             "owner_check": "no child process", "owner_turn": TURN}), role="coordinator", session="C1"))

    def job_k(self) -> None:
        """A second job k with its own contract (write scope jobs/k/work/**) and activated grant; coordinator C1."""
        roles = dict(ROLES, builder=dict(ROLES["builder"], paths=["jobs/k/work/**"]))
        contract = dict(CONTRACT, write_scope=["jobs/k/work/**"],
                        acceptance_criteria=[{"id": "AC1", "check": "wf verify k --json", "expected": "clean true"}])
        g = dict(GATE_GRANT, resources=["jobs/k/work/**"], exclusions=[], ars=dict(GATE_GRANT["ars"], roles=roles))
        cpath, gpath = os.path.join(self.dir, "contract-k.json"), os.path.join(self.dir, "grant-k.json")
        for path, obj in ((cpath, contract), (gpath, g)):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(obj, fh)
        self.ok(self.run_wf("job", "new", "k", "--contract", cpath, role="coordinator", session="C1"))
        h = self.ok(self.run_wf("grant", "propose", "k", "--grant", gpath, role="coordinator", session="C1"))["block_hash"]
        self.ok(self.run_wf("grant", "activate", "k", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                            "--turn", "owner turn", role="coordinator", session="C1"))
        self.bind("coordinator", "C1", job="k")

    def ledger_rows(self) -> list[dict]:
        c = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(c, "j")
        finally:
            c.close()


# ===================================================================================================== rule 0 / 8

class PayloadAndChannels(_Gate):
    def test_rule0_malformed_payloads(self):
        self.explicit()
        good = self.p("jobs/j/work/a.txt")
        bad = [None, [], "x", {}, dict(good, session_id=""), dict(good, session_id=3),
               dict(good, hook_event_name="PostToolUse"), dict(good, tool_name=None), dict(good, tool_input="x"),
               dict(good, cwd=None), {k: v for k, v in good.items() if k != "cwd"},
               dict(good, tool_input={"content": "no path"}), dict(good, tool_input={"file_path": ""}),
               dict(self.p(tool="Bash"), tool_input={"command": ["echo"]}), dict(self.p(tool="Bash"), tool_input={})]
        for payload in bad:
            with self.subTest(payload=payload if not isinstance(payload, dict) else sorted(payload)):
                r = self.deny(payload, "malformed-payload")
                self.assertEqual(r["decision"], "deny")

    def test_other_tools_are_not_mediated_and_need_no_ledger(self):
        """D-S4-5: Read/Glob/... are recorded allow `not-mediated`; no ledger is opened (a missing ledger is fine)."""
        for tool in ("Read", "Glob", "Grep", "WebFetch"):
            with self.subTest(tool=tool):
                r = self.allow(self.p(tool=tool), "not-mediated")
                self.assertEqual((r["channel"], r["limitation"], r["paths"]), ("other", None, []))
        self.assertFalse(os.path.exists(self.db))

    def test_result_shape(self):
        self.explicit()
        r = self.d(self.p("jobs/j/work/a.txt"))
        self.assertEqual(set(r), {"decision", "reason", "channel", "session", "tool_name", "jobs", "paths",
                                  "command_sha256", "command_length", "limitation"})
        self.assertEqual((r["channel"], r["session"], r["tool_name"], r["jobs"], r["paths"]),
                         ("preventable", "B1", "Write", ["j"], ["jobs/j/work/a.txt"]))

    def test_T25_bound_session_outside_boundary_is_out_of_scope(self):
        """T25 (FIRST-RELEASE-SPEC allow-list, AMENDMENT S4A-A4): a session bound to a job is denied `out-of-scope` for a
        target outside the boundary root (e.g. a sibling AIEP checkout); its grant names boundary paths only. An
        unbound session's target outside the boundary is allow `outside-boundary`: not a Workforce path, no claim."""
        self.explicit()
        aiep = os.path.join(os.path.dirname(self.dir), "ai-engineering-platform", "pkg3", "x.py")
        for session in ("B1", "C1"):
            for tool in ("Write", "Edit", "NotebookEdit"):
                with self.subTest(session=session, tool=tool):
                    r = self.deny(self.p(absolute=aiep, session=session, tool=tool), "out-of-scope")
                    self.assertEqual((r["jobs"], r["paths"]), (["j"], []))
        r = self.allow(self.p(absolute=aiep, session="U9"), "outside-boundary")
        self.assertEqual((r["jobs"], r["paths"]), ([], []))
        self.ok(self.run_wf("grant", "revoke", "j", "--reason", "r", "--turn", TURN, role="owner", session="O1"))
        self.deny(self.p(absolute=aiep, session="B1"), "authority-revoked")   # rule 4 now reaches outside targets
        self.allow(self.p(absolute=aiep, session="U9"), "outside-boundary")

    def test_in_root_path_in_no_job_by_unbound_session(self):
        self.explicit()
        self.allow(self.p("README.md", session="U9"), "not-mediated")


# ===================================================================================================== rule 1

class ProtectedPaths(_Gate):
    def test_rule1_protected_paths_everyone(self):
        """T17 second half: ledger / exports / runtime / the configured gate files are denied to everyone, bound or
        not, through the preventable channel."""
        self.explicit()
        for rel in ("ledger/l.db", "ledger/l.db-journal", "ledger/pending/j/m.json", "jobs/j/export/head.json",
                    "runtime/gate/gate.db", "runtime/observe/j.log", "Ledger/L.DB", "runtime"):
            for session in ("B1", "C1", "U9"):
                with self.subTest(rel=rel, session=session):
                    self.deny(self.p(rel, session=session), "protected-path")
        dotted = os.path.join(self.dir, "..", os.path.basename(os.path.dirname(self.hook)), "wf_gate_hook.py")
        for gf in (self.hook, self.cfg, dotted, self.hook.upper() if self.dir[1:2] == ":" else self.hook):
            with self.subTest(gate_file=gf):
                self.deny(self.p(absolute=gf, session="B1"), "protected-path")
                self.deny(self.p(absolute=gf, session="U9", tool="Edit"), "protected-path")

    def test_protected_beats_ledger_health(self):
        """Rule 1 precedes rule 2: a write at the ledger is protected-path even when the ledger cannot be opened."""
        self.deny(self.p("ledger/l.db"), "protected-path")


# ===================================================================================================== rule 2

class LedgerHealth(_Gate):
    def test_missing_and_not_sqlite(self):
        self.deny(self.p("jobs/j/work/a.txt"), "ledger-unreadable")
        self.deny(self.p(tool="Bash"), "ledger-unreadable")
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        with open(self.db, "wb") as fh:
            fh.write(b"not a sqlite database at all" * 10)
        self.deny(self.p("jobs/j/work/a.txt"), "ledger-unreadable")

    def test_locked_past_the_timeout(self):
        self.explicit()
        holder = sqlite3.connect(self.db, timeout=0.1, isolation_level=None)
        try:
            holder.execute("BEGIN EXCLUSIVE")
            self.deny(self.p("jobs/j/work/a.txt"), "ledger-locked", read_timeout_s=0.2)
        finally:
            holder.execute("ROLLBACK"); holder.close()
        self.allow(self.p("jobs/j/work/a.txt"), "allowed", read_timeout_s=0.2)

    def test_identity(self):
        self.explicit()
        ident = gatecore.ledger_identity(self.db)
        self.assertEqual(set(ident), {"dev", "ino", "size"})
        self.allow(self.p("jobs/j/work/a.txt"), "allowed", expected_identity=ident)
        self.deny(self.p("jobs/j/work/a.txt"), "ledger-identity", expected_identity=dict(ident, ino=ident["ino"] + 1))
        self.deny(self.p("jobs/j/work/a.txt"), "ledger-identity", expected_identity=dict(ident, dev=ident["dev"] + 1))
        self.deny(self.p("jobs/j/work/a.txt"), "ledger-identity", expected_identity=dict(ident, size=ident["size"] + 1))
        self.allow(self.p("jobs/j/work/a.txt"), "allowed", expected_identity=dict(ident, size=ident["size"] - 1))

    def test_chain_tail_tamper(self):
        self.explicit()
        c = sqlite3.connect(self.db)
        try:
            c.execute("DROP TRIGGER events_no_update")
            # the activation row's quote text (binding rows untouched: B1 stays bound)
            c.execute("UPDATE events SET content = replace(content, 'approve G1', 'approve GX') WHERE job='j' AND seq=3")
            c.commit()
        finally:
            c.close()
        r = self.deny(self.p("jobs/j/work/a.txt"), "chain-tail")
        self.assertEqual(r["jobs"], ["j"])
        self.deny(self.p(tool="Bash"), "chain-tail")                 # B1 bound: its job is touched on the shell channel
        self.allow(self.p(tool="Bash", session="U9"), "shell-not-preventable")   # unbound shell touches no job

    def test_decide_never_writes_the_ledger(self):
        self.explicit()
        before = (hashlib.sha256(open(self.db, "rb").read()).hexdigest(), os.path.getmtime(self.db),
                  sorted(os.listdir(os.path.dirname(self.db))))
        for payload in (self.p("jobs/j/work/a.txt"), self.p("tools/wf/x.py"), self.p(tool="Bash"),
                        self.p("jobs/j/work/a.txt", session="U9")):
            self.d(payload)
        after = (hashlib.sha256(open(self.db, "rb").read()).hexdigest(), os.path.getmtime(self.db),
                 sorted(os.listdir(os.path.dirname(self.db))))
        self.assertEqual(before, after)


# ===================================================================================================== rules 3-4

class JobState(_Gate):
    def test_T15_revoked_grant_denies_hooked_write(self):
        self.explicit()
        self.allow(self.p("jobs/j/work/a.txt"), "allowed")
        self.ok(self.run_wf("grant", "revoke", "j", "--reason", "owner", "--turn", TURN, role="owner", session="O1"))
        r = self.deny(self.p("jobs/j/work/a.txt"), "authority-revoked")
        self.assertEqual(r["jobs"], ["j"])
        self.deny(self.p("jobs/j/work/a.txt", tool="Edit"), "authority-revoked")
        self.deny(self.p(tool="Bash"), "authority-revoked")          # rule 4 applies to both channels
        self.deny(self.p("jobs/j/work/a.txt", session="U9"), "authority-revoked")  # touched by path

    def test_expired_explicit_and_time(self):
        self.explicit()
        self.deny(self.p("jobs/j/work/a.txt"), "authority-expired", now="3000-01-01T00:00:00Z")
        self.ok(self.run_wf("grant", "expire", "j", "--reason", "owner", "--turn", TURN, role="owner", session="O1"))
        self.deny(self.p("jobs/j/work/a.txt"), "authority-expired")

    def test_voided_by_new_proposal(self):
        self.explicit()
        self.ok(self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1"))
        self.deny(self.p("jobs/j/work/a.txt"), "authority-voided")

    def test_malformed_authority(self):
        self.explicit()
        self.deny(self.p("jobs/j/work/a.txt"), "authority-malformed", now="2026-10-17T12:00:00.5Z")

    def test_cancelled(self):
        self.explicit()
        self.ok(self.run_wf("cancel", "j", "--reason", "stop", "--turn", TURN, role="owner", session="O1"))
        for payload in (self.p("jobs/j/work/a.txt"), self.p(tool="Bash"), self.p("jobs/j/work/a.txt", session="U9")):
            self.deny(payload, "cancelled")

    def test_recovery_open(self):
        self.explicit()
        self.ok(self.run_wf("recover", "begin", "j", "--reason", "owner-reported interruption", "--turn", TURN,
                            role="coordinator", session="C1"))
        self.deny(self.p("jobs/j/work/a.txt"), "recovery-open")
        self.deny(self.p(tool="PowerShell"), "recovery-open")

    def test_D_S4_1_bound_session_without_authority(self):
        """Gate denies ungranted work for a bound session (none / proposed) although the CLI records it (D-S3-1)."""
        self.new_job()
        self.ok(self.run_wf("append", "j", "--type", "intent", "--content",
                            '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'))  # B1 implicit builder
        self.deny(self.p("jobs/j/work/a.txt"), "authority-none")
        self.deny(self.p(tool="Bash"), "authority-none")
        self.ok(self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1"))
        self.deny(self.p("jobs/j/work/a.txt"), "authority-proposed")
        # an unbound session touching the job by path is not denied by rule 4 here: rule 6 decides
        self.deny(self.p("jobs/j/work/a.txt", session="U9"), "unbound-session")
        self.allow(self.p(tool="Bash", session="U9"), "shell-not-preventable")


# ===================================================================================================== rules 5-6

class BoundAndUnbound(_Gate):
    def test_bound_session_allowed_inside_its_grant(self):
        self.explicit()
        r = self.allow(self.p("jobs/j/work/sub/a.txt"), "allowed")
        self.assertEqual(r["paths"], ["jobs/j/work/sub/a.txt"])
        self.allow(self.p("jobs/j/work/n.ipynb", tool="NotebookEdit"), "allowed")
        self.allow(self.p("jobs/j/work/a.txt", tool="Edit"), "allowed")

    def test_implicit_builder_session_is_bound(self):
        self.new_job(); self.activate()
        self.ok(self.run_wf("append", "j", "--type", "intent", "--content",
                            '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'))
        self.allow(self.p("jobs/j/work/a.txt"), "allowed")
        self.deny(self.p("jobs/j/work/a.txt", session="B2"), "unbound-session")

    def test_T18a_bound_session_outside_its_write_scope(self):
        """T18a: a write by the bound builder outside its write scope (inside the boundary) -> deny via grant.check."""
        self.explicit()
        self.deny(self.p("tools/wf/wfcore/db.py"), "path-not-granted")
        self.deny(self.p("README.md"), "path-not-granted")
        self.deny(self.p("jobs/j/work/secret/key.txt"), "path-excluded")

    def test_bound_role_decision_is_exactly_grant_check(self):
        """Rule 5 reuses grant.check (owner item 4): a bound reviewer writing into the builder's work scope gets
        exactly grant.check's answer. Since AMENDMENT S4A-A2 grant.check compares the target with the role's ARS
        actions/paths and the binding's paths, so the reviewer (ARS actions ["review"]) is denied
        `action-outside-role`, passed through by rule 5; the gate adds no second authority logic."""
        self.explicit()
        self.ok(self.run_wf("append", "j", "--type", "delivered", "--content",
                            '{"claims":[{"claim":"c","evidence":["e"]}]}', session="B1"))
        self.bind("reviewer", "R1")
        r = self.d(self.p("jobs/j/work/a.txt", session="R1"))
        g = grant.check(self.ledger_rows(), job="j", now=NOW, role="reviewer", action="write", path="jobs/j/work/a.txt",
                        contract=CONTRACT, binding={"role": "reviewer", "tools": ["Read"], "paths": ["tools/wf/**"]})
        self.assertEqual((r["decision"] == "allow", r["reason"]), (g["allow"], g["reason"]))
        self.assertEqual((r["decision"], r["reason"]), ("deny", "action-outside-role"))     # S4A-A2
        self.assertIn(r["reason"], gatecore.REASONS)
        self.allow(self.p("jobs/j/work/a.txt", session="B1"), "allowed")       # the builder's own scope is unchanged

    def test_T17_unbound_session_into_write_scope_or_job_tree(self):
        self.explicit()
        for rel in ("jobs/j/work/a.txt", "jobs/j/notes.md", "JOBS/J/WORK/A.TXT"):
            with self.subTest(rel=rel):
                self.deny(self.p(rel, session="U9"), "unbound-session")
        self.deny(self.p("ledger/l.db", session="U9"), "protected-path")

    def test_T13_superseded_session_after_replacement(self):
        """T13: after a generation-2 replacement the old builder B1 is `stale-session`; B2 writes."""
        self.explicit()
        self.ok(self.run_wf("append", "j", "--type", "intent", "--content",
                            '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'))
        self.stop("B1")
        self.deny(self.p("jobs/j/work/a.txt"), "not-current-binding")       # stopped, not yet replaced
        self.ok(self.run_wf("reconcile", "j", role="coordinator", session="C1"))
        self.ok(self.run_wf("recover", "decide", "j", "--decision", "resume", "--turn", TURN, role="coordinator",
                            session="C1"))
        self.bind("builder", "B2", generation=2)
        self.deny(self.p("jobs/j/work/a.txt", session="B1"), "stale-session")
        self.allow(self.p("jobs/j/work/a.txt", session="B2"), "allowed")
        self.allow(self.p(tool="Bash", session="B1"), "shell-not-preventable")   # not bound: rule 7

    def test_T13_replaced_or_stopped_session_writes_nowhere(self):
        """T13 ("attempts a hooked write after replacement -> Denied"), not boundary-limited: a stopped, then
        replaced, builder B1 is denied for a target outside the boundary and for an in-root path in no job, not only
        for its old job's paths; the job it held is reported. Bound B2 outside is T25 `out-of-scope`; unbound U9
        stays `outside-boundary`, `not-mediated` in root."""
        self.explicit()
        self.ok(self.run_wf("append", "j", "--type", "intent", "--content",
                            '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'))
        aiep = os.path.join(os.path.dirname(self.dir), "ai-engineering-platform", "pkg3", "x.py")
        self.stop("B1")
        for tool in ("Write", "Edit", "NotebookEdit"):
            with self.subTest(state="stopped", tool=tool):
                r = self.deny(self.p(absolute=aiep, tool=tool), "not-current-binding")
                self.assertEqual((r["jobs"], r["paths"]), (["j"], []))
        r = self.deny(self.p("README.md"), "not-current-binding")
        self.assertEqual((r["jobs"], r["paths"]), (["j"], ["README.md"]))
        self.ok(self.run_wf("reconcile", "j", role="coordinator", session="C1"))
        self.ok(self.run_wf("recover", "decide", "j", "--decision", "resume", "--turn", TURN, role="coordinator",
                            session="C1"))
        self.bind("builder", "B2", generation=2)
        for tool in ("Write", "Edit", "NotebookEdit"):
            with self.subTest(state="replaced", tool=tool):
                r = self.deny(self.p(absolute=aiep, session="B1", tool=tool), "stale-session")
                self.assertEqual((r["jobs"], r["paths"]), (["j"], []))
        self.deny(self.p("README.md", session="B1"), "stale-session")
        self.deny(self.p("jobs/j/work/a.txt", session="B1"), "stale-session")
        self.allow(self.p(tool="Bash", session="B1"), "shell-not-preventable")   # shell: SHELL_LIMITATION
        self.deny(self.p(absolute=aiep, session="B2"), "out-of-scope")             # T25
        self.allow(self.p(absolute=aiep, session="U9"), "outside-boundary")
        self.allow(self.p("README.md", session="U9"), "not-mediated")
        self.ok(self.run_wf("grant", "revoke", "j", "--reason", "r", "--turn", TURN, role="owner", session="O1"))
        self.deny(self.p(absolute=aiep, session="B1"), "authority-revoked")       # rule 4 first on the held job
        self.allow(self.p(absolute=aiep, session="U9"), "outside-boundary")

    def test_T13_held_session_into_another_jobs_scope(self):
        """T13 variant, second job: B1's only binding (builder on j) is stopped, then replaced; a write into job k's
        write scope (k current, builder K1) is `not-current-binding`, then `stale-session`, not rule 6
        `unbound-session`; the held job and the target's job are both reported. Unbound U9 there stays
        `unbound-session`; K1 is unaffected."""
        self.explicit()
        self.job_k()
        self.bind("builder", "K1", job="k")
        self.ok(self.run_wf("append", "j", "--type", "intent", "--content",
                            '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'))
        self.stop("B1")
        r = self.deny(self.p("jobs/k/work/a.txt"), "not-current-binding")
        self.assertEqual((r["jobs"], r["paths"]), (["j", "k"], ["jobs/k/work/a.txt"]))
        self.ok(self.run_wf("reconcile", "j", role="coordinator", session="C1"))
        self.ok(self.run_wf("recover", "decide", "j", "--decision", "resume", "--turn", TURN, role="coordinator",
                            session="C1"))
        self.bind("builder", "B2", generation=2)
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                r = self.deny(self.p("jobs/k/work/a.txt", tool=tool), "stale-session")
                self.assertEqual((r["jobs"], r["paths"]), (["j", "k"], ["jobs/k/work/a.txt"]))
        r = self.deny(self.p("jobs/k/work/a.txt", session="U9"), "unbound-session")
        self.assertEqual(r["jobs"], ["k"])
        self.allow(self.p("jobs/k/work/a.txt", session="K1"), "allowed")

    def test_T13_stale_wins_over_stopped(self):
        """T13 variant: B1 holds a replaced binding on j and a stop-confirmed binding on k and is bound to neither;
        `stale-session` wins over `not-current-binding` for every target. While B1 is still bound on j, its stopped
        binding on k does not apply (rule 5 decides)."""
        self.explicit()
        self.job_k()
        self.bind("builder", "B1", job="k")
        aiep = os.path.join(os.path.dirname(self.dir), "ai-engineering-platform", "pkg3", "x.py")
        self.stop("B1", job="k")
        self.allow(self.p("jobs/j/work/a.txt"), "allowed")                         # still bound on j
        self.ok(self.run_wf("append", "j", "--type", "intent", "--content",
                            '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'))
        self.stop("B1")
        r = self.deny(self.p("README.md"), "not-current-binding")                  # stopped on both
        self.assertEqual(r["jobs"], ["j", "k"])
        self.ok(self.run_wf("reconcile", "j", role="coordinator", session="C1"))
        self.ok(self.run_wf("recover", "decide", "j", "--decision", "resume", "--turn", TURN, role="coordinator",
                            session="C1"))
        self.bind("builder", "B2", generation=2)
        for rel, absolute in (("README.md", None), (None, aiep), ("jobs/j/work/a.txt", None),
                              ("jobs/k/work/a.txt", None)):
            with self.subTest(target=rel or "outside"):
                r = self.deny(self.p(rel, absolute=absolute), "stale-session")    # stale on j, stopped on k
                self.assertEqual(r["jobs"], ["j", "k"])

    def test_T13_rule2_before_stale_on_a_held_job(self):
        """T13 variant: the held job is touched, so rule 2 (chain tail) decides before rule 5: after j's chain tail
        is tampered, replaced B1 is denied `chain-tail` (job j), not `stale-session`, for a target outside the
        boundary and an in-root path in no job. Unbound U9 outside touches no job."""
        self.explicit()
        aiep = os.path.join(os.path.dirname(self.dir), "ai-engineering-platform", "pkg3", "x.py")
        self.ok(self.run_wf("append", "j", "--type", "intent", "--content",
                            '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'))
        self.stop("B1")
        self.ok(self.run_wf("reconcile", "j", role="coordinator", session="C1"))
        self.ok(self.run_wf("recover", "decide", "j", "--decision", "resume", "--turn", TURN, role="coordinator",
                            session="C1"))
        self.bind("builder", "B2", generation=2)
        self.deny(self.p(absolute=aiep), "stale-session")
        c = sqlite3.connect(self.db)
        try:
            c.execute("DROP TRIGGER events_no_update")
            c.execute("UPDATE events SET content = replace(content, 'approve G1', 'approve GX') WHERE job='j' AND seq=3")
            c.commit()
        finally:
            c.close()
        for rel, absolute in ((None, aiep), ("README.md", None)):
            with self.subTest(target=rel or "outside"):
                r = self.deny(self.p(rel, absolute=absolute), "chain-tail")
                self.assertEqual(r["jobs"], ["j"])
        self.allow(self.p(absolute=aiep, session="U9"), "outside-boundary")


# ===================================================================================================== rule 7

class ShellChannel(_Gate):
    def test_shell_rule_and_limitation(self):
        self.explicit()
        cmd = "Remove-Item ledger\\l.db -Force; echo jobs/j/work/a.txt"
        for session in ("U9", "B1"):
            for tool in ("Bash", "PowerShell"):
                with self.subTest(session=session, tool=tool):
                    r = self.allow(self.p(tool=tool, session=session, command=cmd), "shell-not-preventable")
                    self.assertEqual(r["channel"], "shell")
                    self.assertEqual(r["limitation"], gatecore.SHELL_LIMITATION)
                    self.assertEqual(r["command_sha256"], hashlib.sha256(cmd.encode("utf-8")).hexdigest())
                    self.assertEqual(r["command_length"], len(cmd))
                    self.assertEqual(r["paths"], [])                          # never parsed for paths (D-S4-2)
                    self.assertNotIn(cmd, json.dumps(r))                      # the text itself is not stored
        self.assertEqual(self.d(self.p(tool="Bash", session="U9"))["jobs"], [])
        self.assertEqual(self.d(self.p(tool="Bash", session="B1"))["jobs"], ["j"])

    def test_shell_limitation_exact_text(self):
        self.assertEqual(gatecore.SHELL_LIMITATION,
                         "shell commands are recorded as invocations only; their filesystem effects are not parsed, "
                         "prevented or comprehensively detected; changes inside the monitored boundary are detected "
                         "later by wf reconcile/accept only")
        self.assertEqual(gatecore.PREVENTABLE_TOOLS, ("Write", "Edit", "NotebookEdit"))
        self.assertEqual(gatecore.SHELL_TOOLS, ("Bash", "PowerShell"))
        self.assertEqual(gatecore.PROTECTED_GLOBS, ("ledger/**", "jobs/*/export/**", "runtime/**"))


# ===================================================================================================== paths, fail closed

class PathsAndFailClosed(_Gate):
    def test_target_paths(self):
        self.explicit()
        p = self.p("jobs/j/work/a.txt")
        self.assertEqual(gatecore.target_paths(p, self.dir), (["jobs/j/work/a.txt"], None))
        dotted = dict(p, tool_input={"file_path": os.path.join(self.dir, "jobs", "j", ".", "work", "c.txt")})
        self.assertEqual(gatecore.target_paths(dotted, self.dir), (["jobs/j/work/c.txt"], None))
        out = dict(p, tool_input={"file_path": os.path.join(os.path.dirname(self.dir), "x.txt")})
        self.assertEqual(gatecore.target_paths(out, self.dir), ([], "outside-boundary"))
        self.assertEqual(gatecore.target_paths(self.p(tool="Bash"), self.dir), ([], "malformed-payload"))

    # AMENDMENT FIX1-A2 I8 (WF-021 F7): the relative-path case split by platform; its meaning on Windows is unchanged.
    @unittest.skipUnless(os.sep == "\\", "Windows path form")
    def test_target_paths_relative_windows(self):
        self.explicit()
        rel = dict(self.p("jobs/j/work/a.txt"), tool_input={"file_path": "jobs\\j\\work\\b.txt"})  # joined to cwd
        self.assertEqual(gatecore.target_paths(rel, self.dir), (["jobs/j/work/b.txt"], None))

    @unittest.skipIf(os.sep == "\\", "POSIX path form")
    def test_target_paths_relative_posix(self):
        self.explicit()
        p = self.p("jobs/j/work/a.txt")
        rel = dict(p, tool_input={"file_path": "jobs/j/work/b.txt"})
        self.assertEqual(gatecore.target_paths(rel, self.dir), (["jobs/j/work/b.txt"], None))
        back = dict(p, tool_input={"file_path": "jobs\\j\\work\\b.txt"})     # a backslash is a name character here
        self.assertEqual(gatecore.target_paths(back, self.dir), ([], "unresolvable-path"))

    def test_unresolvable_paths(self):
        self.explicit()
        esc = os.path.join(self.dir, "jobs", "..", "..", "elsewhere", "x.txt")
        cases = [esc, os.path.join(self.dir, "jobs", "j", "work", "CON"),
                 os.path.join(self.dir, "jobs", "j", "work", "a.txt:stream"),
                 os.path.join(self.dir, "jobs", "j", "work", "a.txt.")]
        if self.dir[1:2] == ":":
            other = ("Q" if self.dir[0].upper() != "Q" else "R") + self.dir[1:]
            cases.append(os.path.join(other, "jobs", "j", "work", "a.txt"))   # drive mismatch
        for absolute in cases:
            with self.subTest(path=absolute):
                r = self.deny(self.p(absolute=absolute), "unresolvable-path")
                self.assertEqual(r["paths"], [])
        inside = os.path.join(self.dir, "jobs", "j", "x", "..", "work", "a.txt")   # `..` that stays inside
        self.allow(self.p(absolute=inside), "allowed")

    def test_link_component_is_unresolvable(self):
        self.explicit()
        outside = os.path.join(os.path.dirname(self.dir), f"{self._testMethodName}-outside")
        os.makedirs(outside, exist_ok=True)
        os.makedirs(os.path.join(self.dir, "jobs", "j", "work"), exist_ok=True)
        link = os.path.join(self.dir, "jobs", "j", "work", "lnk")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink not creatable on this host ({type(exc).__name__}); platform-dependent")
        self.deny(self.p("jobs/j/work/lnk/x.txt"), "unresolvable-path")

    def test_fail_closed_on_internal_errors(self):
        self.explicit()
        real = grant.check

        def boom(*a: Any, **k: Any) -> dict:
            raise RuntimeError("injected")
        grant.check = boom
        try:
            r = self.deny(self.p("jobs/j/work/a.txt"), "gate-error")
        finally:
            grant.check = real
        self.assertEqual(r["decision"], "deny")
        real_auth = grant.authority
        grant.authority = boom
        try:
            self.deny(self.p(tool="Bash"), "gate-error")
        finally:
            grant.authority = real_auth
        r = gatecore.decide(self.p("jobs/j/work/a.txt"), root=self.dir, db_path=self.db, now=NOW, gate_files=None)
        self.assertEqual(r["decision"], "allow")                                # None gate list tolerated

    def test_reasons_tuple_covers_every_rule(self):
        for reason in ("malformed-payload", "unresolvable-path", "protected-path", "ledger-unreadable",
                       "ledger-locked", "ledger-identity", "chain-tail", "recovery-open", "cancelled",
                       "authority-none", "authority-proposed", "authority-expired", "authority-revoked",
                       "authority-voided", "authority-malformed", "stale-session", "not-current-binding",
                       "path-not-granted", "path-excluded", "unbound-session", "shell-not-preventable",
                       "out-of-scope", "outside-boundary", "not-mediated", "gate-error", "allowed",
                       "gate-config", "gate-integrity", "audit-unavailable"):  # FIX1 I6: hook-only reasons
            self.assertIn(reason, gatecore.REASONS)
        self.assertEqual(len(set(gatecore.REASONS)), len(gatecore.REASONS))


if __name__ == "__main__":
    unittest.main()
