"""T33 replacement bind at generation+1 (B2 slice 2, lane S2B, Plexy): INTERFACE-CONTRACT-S2.md §2 (ARS replacement
rules, exact types, expiry, malformed / fail closed), §3 (generation >= 2 preconditions in order, `replaces_seq`,
first-event rule), §6 (`parse_replacement`, `replacement_count`, `delegation_summary`, `active_roles` latest per role,
`digest_rows` fields), §8 S2B. Contract copy: WorkForce collab folder `contracts/b2s2` (SHA256SUMS verified).
Spec: FIRST-RELEASE-SPEC.md §2B l.44-46, §6.2 steps 2-5, T33 l.308.

Evidence path: the real `wf` CLI on main (`wf bind --bind-generation`, `wf append --type stop-confirmed`,
`wf reconcile`, `wf ack-reconcile`, `wf digest`) through `commands._guard` and `validate.build_row`, on fixture ledgers
built with `test_wf.Base` (fresh WF_TEST_TMP, refuse-existing, never delete). The one exception: `recovery-decision`
(S2 §1) is an S2R type that is not registered on main yet, so it is inserted as a fully hashed, chained row in the
§1 shape (`put_decision`); that row is evidence input, not a claim about `wf recover decide`.

T33 subcases (§8 S2B): (a) replacement allowed at g+1 with the ARS unchanged; the new session's first event must be
`ack-reconcile` (any other -> unreconciled) - `T33a_*`; (b) allowed false - `T33b_*`; (c) count exceeded - `T33c_*`;
(d) wider tools/paths - `T33d_*`; (e) unconfirmed termination - `T33e_*`; (f) generation-2 event without a bind ->
unbound-session - `T33f_*`; expiry past; malformed replacement object.
"""
from __future__ import annotations

import json
import sqlite3
import unittest
from typing import Any

from test_wf import Base, CONTRACT, GRANT  # noqa: E402
from wfcore import bind, db as dbm  # noqa: E402
from wfcore.canon import canonical, event_self_hash, sha256_text  # noqa: E402
from wfcore.db import WfRejected  # noqa: E402

FAR = "2999-12-31T23:59:59Z"
ROLES = {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
         "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
         "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["tools/wf/**"]}}
INTENT = '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}'


def _grant(replacement: Any = ..., **extra: Any) -> dict:
    ars: dict[str, Any] = {"roles": ROLES}
    if replacement is not ...:
        ars["replacement"] = replacement
    return dict(GRANT, ars=dict(ars, **extra))


def _stop(session: str, generation: int) -> str:
    return json.dumps({"role": "builder", "session": session, "generation": generation, "how": "process exit observed",
                       "when": "2026-10-16T22:00:00Z", "owner_check": "no child process, no outstanding operation",
                       "owner_turn": "owner turn: builder session stopped in job j"})


class _Flow(Base):
    grant: dict = _grant({"allowed": True, "max": 1, "expiry": FAR})

    def setUp(self) -> None:
        super().setUp()
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(self.grant, fh)

    # ---- reads
    def rows(self) -> list[dict]:
        c = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(c, "j")
        finally:
            c.close()

    def head(self) -> int:
        rs = self.rows()
        return rs[-1]["seq"] if rs else 0

    def last(self) -> tuple[dict, dict]:
        r = self.rows()[-1]
        return r, json.loads(r["content"])

    def table(self) -> dict[str, bind.Binding]:
        c = dbm.connect(self.db, create=False)
        try:
            return bind.active_roles(c, "j")
        finally:
            c.close()

    # ---- CLI steps (real wiring)
    def ok(self, res: tuple[int, dict]) -> dict:
        code, r = res
        self.assertEqual(code, 0, r)
        return r

    def refused(self, res: tuple[int, dict], reason: str) -> dict:
        code, r = res
        self.assertEqual(code, 1, r)
        self.assertIn(f"rejected({reason})", r.get("error", ""), r)
        return r

    def bind(self, brole: str, bsession: str, *extra: str, generation: int = 1) -> tuple[int, dict]:
        return self.run_wf("bind", "j", "--bind-role", brole, "--bind-session", bsession,
                           "--bind-generation", str(generation), *extra, role="coordinator", session="C1")

    def intent(self, session: str, generation: int = 1) -> tuple[int, dict]:
        return self.run_wf("append", "j", "--type", "intent", "--content", INTENT, "--generation", str(generation),
                           role="builder", session=session)

    def stop(self, session: str, generation: int) -> tuple[int, dict]:
        return self.run_wf("append", "j", "--type", "stop-confirmed", "--content", _stop(session, generation),
                           role="coordinator", session="C1")

    def reconcile(self) -> dict:
        return self.ok(self.run_wf("reconcile", "j", role="coordinator", session="C1"))

    def ack_reconcile(self, session: str, generation: int, report_seq: int, ids: list[str]) -> tuple[int, dict]:
        return self.run_wf("ack-reconcile", "j", "--report", str(report_seq), "--exceptions", json.dumps(ids),
                           "--generation", str(generation), role="builder", session=session)

    def report_ids(self, report_seq: int) -> list[str]:
        r = [x for x in self.rows() if x["seq"] == report_seq][0]
        self.assertEqual(r["type"], "reconcile-report")
        return [e["id"] for e in json.loads(r["content"])["exceptions"]]

    def put_row(self, etype: str, content: dict, *, role: str = "coordinator", session: str = "C1",
                generation: int = 1) -> int:
        """A fully hashed, chained row (types not registered on main: S2R's recovery-decision; or a state fixture)."""
        c = dbm.connect(self.db, create=False)
        try:
            with c:
                hs, hself = dbm.head(c, "j")
                csha = dbm.contract(c, "j")["sha256"]
                text = canonical(dict(content, contract_sha256=csha))
                row = {"job": "j", "seq": hs + 1, "type": etype, "msg_id": f"fixture-{etype}-{hs + 1}",
                       "author_role": role, "session_id": session, "generation": generation, "reply_to": None,
                       "grant_ref": None, "ts": "2026-10-16T22:30:00.000Z", "content": text,
                       "content_sha256": sha256_text(text), "prev": hself}
                row["self"] = event_self_hash(row)
                c.execute("INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,"
                          "grant_ref,ts,content,content_sha256,prev,self) VALUES (:job,:seq,:type,:msg_id,"
                          ":author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,"
                          ":prev,:self)", row)
                return row["seq"]
        finally:
            c.close()

    def put_decision(self, report_seq: int, stop_seq: int, decision: str = "resume") -> int:
        return self.put_row("recovery-decision", {"decision": decision, "role": "builder", "report_seq": report_seq,
                                                  "stop_seq": stop_seq, "restore_paths": [], "unresolved": [],
                                                  "owner_turn": "owner turn: resume job j with a new builder"})

    # ---- composite fixture
    def ready_for_replacement(self, *b1_extra: str) -> dict[str, int]:
        """contract (1), grant (2, 3), C1 self-bind (4), bind builder B1 (5), B1 intent (6), stop B1 (7),
        reconcile (8), resume decision (9). Returns the seqs."""
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.ok(self.bind("builder", "B1", *b1_extra))
        self.ok(self.intent("B1"))
        self.ok(self.stop("B1", 1))
        rep = self.reconcile()
        report_seq = self.head()
        self.assertEqual(rep.get("seq", report_seq), report_seq)
        decision = self.put_decision(report_seq, 7)
        return {"b1_bind": 5, "stop": 7, "report": report_seq, "decision": decision}


# ===================================================================================================== (a)

class T33a(_Flow):
    def test_T33a_replacement_at_g2_and_first_event_must_be_ack_reconcile(self):
        s = self.ready_for_replacement()
        self.assertEqual((s["report"], s["decision"]), (8, 9))
        self.ok(self.bind("builder", "B2", generation=2))
        r, c = self.last()
        self.assertEqual((r["seq"], r["type"], r["author_role"], r["session_id"]), (10, "bind", "coordinator", "C1"))
        self.assertEqual((c["role"], c["session"], c["generation"], c["replaces_seq"], c["tools"], c["paths"]),
                         ("builder", "B2", 2, 5, ["Write"], ["jobs/j/work/**"]))
        b = self.table()["builder"]
        self.assertEqual((b.session, b.generation, b.bind_seq, b.replaces_seq, b.stopped_seq), ("B2", 2, 10, 5, None))
        # first event rule (§3, existing unreconciled rule): any other event by B2 -> unreconciled
        self.refused(self.intent("B2", 2), "unreconciled")
        self.refused(self.run_wf("append", "j", "--type", "ack-assignment", "--content",
                                 json.dumps({"role": "builder", "session": "B2", "bind_seq": 10}),
                                 "--generation", "2", role="builder", session="B2"), "unreconciled")
        self.assertEqual(self.head(), 10)
        ids = self.report_ids(8)
        self.assertTrue(ids)
        self.ok(self.ack_reconcile("B2", 2, 8, ids))
        r, c = self.last()
        self.assertEqual((r["seq"], r["type"], r["session_id"], r["generation"], c["report_seq"]),
                         (11, "ack-reconcile", "B2", 2, 8))
        self.ok(self.intent("B2", 2))                          # resumed
        self.refused(self.intent("B1", 1), "stale")            # the replaced generation is stale (check_actor)
        self.ok(self.run_wf("append", "j", "--type", "ack-assignment", "--content",
                            json.dumps({"role": "builder", "session": "B2", "bind_seq": 10}),
                            "--generation", "2", role="builder", session="B2"))
        self.assertEqual(self.table()["builder"].ack_seq, self.head())

    def test_T33a_digest_rows_show_replacement(self):
        self.ready_for_replacement()
        self.ok(self.bind("builder", "B2", generation=2))
        d = self.ok(self.run_wf("digest", "j"))
        row = {x["role"]: x for x in d["bindings"]["rows"]}["builder"]
        self.assertEqual((row["session"], row["generation"], row["replacements"], row["replaces_seq"], row["tools"],
                          row["paths"]), ("B2", 2, 1, 5, ["Write"], ["jobs/j/work/**"]))
        c = dbm.connect(self.db, create=False)
        try:
            self.assertEqual(bind.replacement_count(c, "j", "builder"), 1)
            self.assertEqual(bind.replacement_count(c, "j", "coordinator"), 0)
        finally:
            c.close()

    def test_T33a_foreign_session_at_the_new_generation_is_a_claimant_conflict(self):
        self.ready_for_replacement()
        self.ok(self.bind("builder", "B2", generation=2))
        code, r = self.intent("B1", 2)
        self.assertEqual(code, 1, r)
        self.assertIn("claimant-conflict", r["error"])
        last, c = self.last()
        self.assertEqual((last["type"], c["holder_session"], c["claimant_session"]), ("blocker", "B2", "B1"))

    def test_T33a_same_decision_cannot_be_cited_twice(self):
        """§4 / T16: after the replacement the role is at g=2; a second g=2 bind -> bound; a g=3 bind for the same
        stop -> termination-unconfirmed (the new binding is not stopped)."""
        self.ready_for_replacement()
        self.ok(self.bind("builder", "B2", generation=2))
        n = self.head()
        self.refused(self.bind("builder", "B3", generation=2), "bound")
        self.refused(self.bind("builder", "B3", generation=3), "termination-unconfirmed")
        self.assertEqual(self.head(), n)


# ===================================================================================================== (b) (c) expiry malformed

class T33Delegation(_Flow):
    def _refuse(self, grant: dict, reason: str = "delegation", **kw: Any) -> dict:
        self.grant = grant
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(grant, fh)
        self.ready_for_replacement()
        n = self.head()
        r = self.refused(self.bind("builder", "B2", generation=2, **kw), reason)
        self.assertEqual(self.head(), n)
        return r

    def test_T33b_allowed_false(self):
        r = self._refuse(_grant({"allowed": False, "max": 5, "expiry": FAR}))
        self.assertIn("allowed: false", r["error"])

    def test_T33b_replacement_absent_is_not_permitted(self):
        r = self._refuse(_grant())
        self.assertIn("no replacement block", r["error"])

    def test_T33c_count_at_max_zero(self):
        r = self._refuse(_grant({"allowed": True, "max": 0, "expiry": FAR}))
        self.assertIn("reached max 0", r["error"])

    def test_T33c_count_exceeded_after_one_replacement(self):
        """max 1: generation 2 succeeds; the generation-3 replacement (after its own stop, reconcile and decision) is
        refused because one replacement bind already exists."""
        self.ready_for_replacement()
        self.ok(self.bind("builder", "B2", generation=2))
        self.ok(self.ack_reconcile("B2", 2, 8, self.report_ids(8)))
        self.ok(self.intent("B2", 2))
        self.ok(self.stop("B2", 2))
        stop2 = self.head()
        self.reconcile()
        report2 = self.head()
        self.put_decision(report2, stop2)
        n = self.head()
        r = self.refused(self.bind("builder", "B3", generation=3), "delegation")
        self.assertIn("replacement count 1 for builder has reached max 1", r["error"])
        self.assertEqual(self.head(), n)

    def test_expiry_past(self):
        r = self._refuse(_grant({"allowed": True, "max": 3, "expiry": "2000-01-01T00:00:00Z"}))
        self.assertIn("expired at 2000-01-01T00:00:00Z", r["error"])

    def test_malformed_replacement_g1_ok_g2_refused_summary_invalid(self):
        """§2: a malformed block never breaks initial assignment; generation >= 2 is refused; the digest view reports
        valid False (delegation: malformed)."""
        cases = [{"allowed": True, "max": 1}, {"allowed": "yes", "max": 1, "expiry": FAR},
                 {"allowed": True, "max": True, "expiry": FAR}, {"allowed": True, "max": -1, "expiry": FAR},
                 {"allowed": True, "max": 1, "expiry": "2999-12-31"}, {"allowed": True, "max": 1, "expiry": FAR, "x": 1},
                 {"allowed": True, "max": 1, "expiry": "2999-02-30T00:00:00Z"}, ["allowed"]]
        for i, rep in enumerate(cases):
            with self.subTest(replacement=rep):
                g = _grant(rep)
                summary = bind.delegation_summary(dict(g, state="activated"))
                self.assertEqual((summary["present"], summary["valid"]), (True, False))
                self.assertIn("rejected(delegation)", summary["problem"])
                with self.assertRaises(WfRejected) as cm:
                    bind.parse_replacement(g["ars"])
                self.assertEqual(cm.exception.reason, "delegation")
                bind.parse_ars(g["ars"])                     # parse_ars ignores the block (g1 unaffected)
        # end to end with one malformed block: every generation-1 bind works, generation 2 refused
        self._refuse(_grant({"allowed": True, "max": 1}))
        self.assertEqual({k: v.generation for k, v in self.table().items()}, {"coordinator": 1, "builder": 1})

    def test_parse_replacement_and_summary_valid_forms(self):
        self.assertIsNone(bind.parse_replacement({"roles": ROLES}))
        self.assertIsNone(bind.parse_replacement(None))
        self.assertEqual(bind.parse_replacement({"replacement": {"allowed": True, "max": 2, "expiry": FAR}}),
                         {"allowed": True, "max": 2, "expiry": FAR})
        self.assertEqual(bind.delegation_summary(None), {"present": False, "valid": True, "allowed": None, "max": None,
                                                         "expiry": None, "problem": None})
        self.assertEqual(bind.delegation_summary(_grant({"allowed": False, "max": 0, "expiry": FAR})),
                         {"present": True, "valid": True, "allowed": False, "max": 0, "expiry": FAR, "problem": None})
        self.assertEqual(bind.REPLACEMENT_KEYS, ("allowed", "max", "expiry"))


# ===================================================================================================== (d)

class T33d(_Flow):
    def test_T33d_wider_tool_refused(self):
        self.ready_for_replacement()
        r = self.refused(self.bind("builder", "B2", "--tool", "Edit", generation=2), "delegation")
        self.assertIn("tool 'Edit'", r["error"])

    def test_T33d_wider_path_refused_and_narrower_allowed(self):
        self.ready_for_replacement("--path", "jobs/j/work/sub/**")
        r = self.refused(self.bind("builder", "B2", generation=2), "delegation")          # default = write_scope
        self.assertIn("path 'jobs/j/work/**'", r["error"])
        self.ok(self.bind("builder", "B2", "--path", "jobs/j/work/sub/x/**", generation=2))
        _, c = self.last()
        self.assertEqual((c["paths"], c["replaces_seq"]), (["jobs/j/work/sub/x/**"], 5))


# ===================================================================================================== (e) (f) and order

class T33Preconditions(_Flow):
    def test_T33e_unconfirmed_termination(self):
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.ok(self.bind("builder", "B1"))
        self.ok(self.intent("B1"))
        n = self.head()
        r = self.refused(self.bind("builder", "B2", generation=2), "termination-unconfirmed")
        self.assertIn("no override", r["error"])
        self.assertEqual(self.head(), n)

    def test_T33f_generation_2_event_without_a_bind(self):
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.ok(self.bind("builder", "B1"))
        self.refused(self.intent("B1", 2), "unbound-session")
        self.refused(self.intent("B2", 2), "unbound-session")

    def test_order_generation_checks(self):
        """(1) only builder is replaceable; a g-1 binding must be current (no jump, no binding)."""
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.refused(self.bind("builder", "B1", generation=2), "generation")              # no binding at all
        self.ok(self.bind("builder", "B1"))
        self.refused(self.bind("builder", "B2", generation=3), "generation")              # jump g1 -> g3
        self.refused(self.bind("coordinator", "C2", generation=2), "generation")          # D-S2-2
        self.refused(self.bind("reviewer", "R1", generation=2), "generation")

    def test_no_reconcile_cases(self):
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.ok(self.bind("builder", "B1"))
        self.ok(self.intent("B1"))
        self.reconcile()                                       # report BEFORE the stop
        early = self.head()
        self.ok(self.stop("B1", 1))
        stop = self.head()
        r = self.refused(self.bind("builder", "B2", generation=2), "no-reconcile")
        self.assertIn(f"latest report seq {early} precedes the stop", r["error"])
        self.reconcile()
        report = self.head()
        self.put_row("ack-reconcile", {"role": "builder", "session": "B1", "report_seq": report, "exceptions": []},
                     role="builder", session="B1")              # state fixture: the post-stop report acknowledged
        self.put_decision(report, stop)
        r = self.refused(self.bind("builder", "B2", generation=2), "no-reconcile")
        self.assertIn("is acknowledged", r["error"])

    def test_no_report_at_all(self):
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.ok(self.bind("builder", "B1"))
        self.ok(self.stop("B1", 1))
        r = self.refused(self.bind("builder", "B2", generation=2), "no-reconcile")
        self.assertIn("no reconcile-report", r["error"])

    def test_no_decision_cases(self):
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.ok(self.bind("builder", "B1"))
        self.ok(self.intent("B1"))
        self.ok(self.stop("B1", 1))
        stop = self.head()
        self.reconcile()
        report = self.head()
        self.refused(self.bind("builder", "B2", generation=2), "no-decision")             # none
        self.put_decision(report, stop, decision="abandon")
        self.refused(self.bind("builder", "B2", generation=2), "no-decision")             # not resume
        self.put_decision(report - 1, stop)
        self.refused(self.bind("builder", "B2", generation=2), "no-decision")             # wrong report
        self.put_decision(report, stop - 1)
        self.refused(self.bind("builder", "B2", generation=2), "no-decision")             # wrong stop
        self.put_decision(report, stop)
        self.ok(self.bind("builder", "B2", generation=2))

    def test_same_session_refused(self):
        self.ready_for_replacement()
        r = self.refused(self.bind("builder", "B1", generation=2), "delegation")
        self.assertIn("must differ", r["error"])

    def test_precondition_order_stop_before_reconcile_before_decision_before_delegation(self):
        """With allowed false AND no stop, the stop is reported first; with a stop but no report, no-reconcile wins
        over no-decision and delegation (§3 order)."""
        self.grant = _grant({"allowed": False, "max": 0, "expiry": FAR})
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(self.grant, fh)
        self.new_job(); self.activate()
        self.ok(self.bind("coordinator", "C1"))
        self.ok(self.bind("builder", "B1"))
        self.refused(self.bind("builder", "B2", generation=2), "termination-unconfirmed")
        self.ok(self.stop("B1", 1))
        self.refused(self.bind("builder", "B2", generation=2), "no-reconcile")
        self.reconcile()
        self.refused(self.bind("builder", "B2", generation=2), "no-decision")


if __name__ == "__main__":
    unittest.main()
