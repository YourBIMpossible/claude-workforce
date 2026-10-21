"""T32 bind + ARS (B2 slice 1 lane S1B, slice 2 lane S2B; Plexy): module tests for `wfcore.bind` against the frozen
contracts: slice 1 INTERFACE-CONTRACT.md (v1 §1 schemas, §2 ARS, §3 preconditions / actor check / bootstrap, §6, §8)
and slice 2 INTERFACE-CONTRACT-S2.md (A-1 implicit-claimant; §6 Binding / digest fields), both in the WorkForce
collab folder `contracts/b2s1`, `contracts/b2s2`. Replacement (T33) is tested in test_t33_replacement.py.
Spec: FIRST-RELEASE-SPEC.md §2B l.34-46, §4.1 l.169, §5 rules 4-5, §6.2 step 2, T32 l.307.

Fixture ledgers are built through `wf.run` with `test_wf.Base` (fresh WF_TEST_TMP, refuse-existing, never delete).
`_Engine._write` writes through the real `txn.write_event` and runs the v1 §6 wrapper steps (3) `bind.check_actor`,
(5) the bind type-specific check, (6) `validate.build_row`; the S1R §7 dispatch of the bind types in validate.py is on
main, so the real `validate.validate_content` routes them to `bind.validate_content` (the slice-1 test-local mock is
gone). It is a module-level harness, not the wiring (`commands._guard`), and is not evidence for the wiring.
The one `recovery-opened` row used (recovery gate input) is written as a fully hashed row in S1R's §1 shape.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import sqlite3
import unittest
from typing import Any

from test_wf import Base, CONTRACT, GRANT  # noqa: E402
from test_fix3ii_accounts import B1, B2, R1  # noqa: E402
from wfcore import bind, db as dbm, txn, validate  # noqa: E402
from wfcore.canon import canonical, event_self_hash, sha256_text  # noqa: E402
from wfcore.db import OpLog, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

ARS = {"roles": {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
                 "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
                 "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["tools/wf/**"]}},
       "session_requirements": {"one_live_session_per_role": True, "reviewer_fresh": True},
       "replacement": {"allowed": False, "max": 0, "expiry": "2999-12-31T23:59:59Z"}}
GRANT_ARS = dict(GRANT, ars=ARS)
# Declared tools/paths the wiring would default to (contract allowed_tools / scope, contract §3 last sentence).
DEFAULTS = {"coordinator": ([], list(CONTRACT["read_scope"])),
            "builder": (list(CONTRACT["allowed_tools"]["builder"]), list(CONTRACT["write_scope"])),
            "reviewer": (list(CONTRACT["allowed_tools"]["reviewer"]), list(CONTRACT["read_scope"]))}



class _Engine(Base):
    """Base + ledger helpers. Job "j": contract by coordinator C1 (test_wf.Base.new_job), grant with ARS."""

    def setUp(self) -> None:
        super().setUp()
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(GRANT_ARS, fh)
        self._n = 0

    # -- ledger reads
    def conn(self) -> sqlite3.Connection:
        return dbm.connect(self.db, create=False)

    def table(self) -> dict[str, bind.Binding]:
        c = self.conn()
        try:
            return bind.active_roles(c, "j")
        finally:
            c.close()

    def head(self) -> int:
        c = self.conn()
        try:
            return dbm.head(c, "j")[0]
        finally:
            c.close()

    def types(self) -> list[str]:
        c = self.conn()
        try:
            return [r["type"] for r in dbm.rows(c, "j")]
        finally:
            c.close()

    # -- writes through the real txn.write_event (contract §6 steps 3, 5, 6)
    def _write(self, *, etype: str, role: str, session: str, generation: int, content_fn: Any,
               msg_id: str | None = None) -> Any:
        self._n += 1
        msg_id = msg_id or f"m{self._n}-{etype}"

        def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict:
            c = dbm.contract(conn, "j")
            assert c is not None
            csha, contract = c["sha256"], json.loads(c["content"])
            bind.check_actor(conn, "j", role=role, session=session, generation=generation, etype=etype,
                             msg_id=msg_id, contract_sha=csha)
            content = content_fn(conn)
            return validate.build_row(conn, "j", head_seq, head_self, etype=etype, msg_id=msg_id, author_role=role,
                                      session_id=session, generation=generation, reply_to=None, grant_ref=None,
                                      content=content, contract_sha=csha, contract_content=contract)
        return txn.write_event(self.db, "j", Faults(), OpLog(), build=build, session=session)

    def do_bind(self, role: str, session: str, *, author: str = "C1", tools: list[str] | None = None,
                paths: list[str] | None = None, generation: int = 1) -> Any:
        dt, dp = DEFAULTS[role]

        def content(conn: sqlite3.Connection) -> dict:
            st = validate.existing_state(conn, "j")
            grant_rows = [r for r in dbm.rows(conn, "j") if r["type"] == "grant"]
            grant_seq = grant_rows[-1]["seq"] if grant_rows else None
            return bind.validate_bind(conn, "j", role=role, session=session, generation=generation,
                                      tools=dt if tools is None else tools, paths=dp if paths is None else paths,
                                      author_session=author, grant=st["grant"], grant_seq=grant_seq, state=st)
        return self._write(etype="bind", role="coordinator", session=author, generation=1, content_fn=content)

    def do_ack(self, role: str, session: str, *, content: dict | None = None) -> Any:
        def fn(conn: sqlite3.Connection) -> dict:
            b = bind.active_roles(conn, "j").get(role)
            c = content if content is not None else {"role": role, "session": session,
                                                     "bind_seq": b.bind_seq if b else 0}
            bind.check_ack_assignment(conn, "j", role=role, session=session, content=c)
            return c
        return self._write(etype="ack-assignment", role=role, session=session, generation=1, content_fn=fn)

    def do_stop(self, role: str, session: str, *, generation: int = 1, author: str = "C1") -> Any:
        c = {"role": role, "session": session, "generation": generation, "how": "owner closed the session",
             "when": "2026-10-16T06:00Z", "owner_check": "no child process and no outstanding operation",
             "owner_turn": "owner turn: stop builder session in job j"}

        def fn(conn: sqlite3.Connection) -> dict:
            bind.check_stop_confirmed(conn, "j", content=c)
            return c
        return self._write(etype="stop-confirmed", role="coordinator", session=author, generation=1, content_fn=fn)

    def do_intent(self, session: str, generation: int = 1) -> Any:
        content = {"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}
        return self._write(etype="intent", role="builder", session=session, generation=generation,
                           content_fn=lambda conn: content)

    def put_recovery_opened(self) -> None:
        """A `recovery-opened` row in S1R's §1 shape (type owned by S1R), fully hashed and chained."""
        c = self.conn()
        try:
            with c:
                hs, hself = dbm.head(c, "j")
                csha = dbm.contract(c, "j")["sha256"]
                text = canonical({"reason": "owner-reported interruption", "owner_turn": "recover job j",
                                  "exceptions": [], "chain_head": {"seq": hs, "self": hself},
                                  "contract_sha256": csha})
                row = {"job": "j", "seq": hs + 1, "type": "recovery-opened", "msg_id": "rec-1",
                       "author_role": "coordinator", "session_id": "C1", "generation": 1, "reply_to": None,
                       "grant_ref": None, "ts": "2026-10-16T06:00:00.000Z", "content": text,
                       "content_sha256": sha256_text(text), "prev": hself}
                row["self"] = event_self_hash(row)
                c.execute("INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,"
                          "grant_ref,ts,content,content_sha256,prev,self) VALUES (:job,:seq,:type,:msg_id,"
                          ":author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,"
                          ":prev,:self)", row)
        finally:
            c.close()

    def explicit(self) -> None:
        """Contract, activated ARS grant, coordinator self-bind (bootstrap), builder bound to B1."""
        self.new_job(); self.activate()
        self.do_bind("coordinator", "C1")
        self.do_bind("builder", "B1")

    def assertConflict(self, fn: Any, *a: Any, **k: Any) -> dict:
        """§5 rule 5 through txn: the attempted row is never written; the conflict blocker row is (head+1), and
        txn reports rejected(claimant-conflict). Returns the blocker content."""
        before = self.head()
        with self.assertRaises(WfRejected) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.reason, "claimant-conflict", str(cm.exception))
        self.assertEqual(self.head(), before + 1)
        c = self.conn()
        try:
            last = dbm.rows(c, "j")[-1]
        finally:
            c.close()
        self.assertEqual(last["type"], "blocker")
        return json.loads(last["content"])

    def assertRejected(self, reason: str, fn: Any, *a: Any, **k: Any) -> WfRejected:
        before = self.head() if os.path.exists(self.db) else None
        with self.assertRaises(WfRejected) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.reason, reason, str(cm.exception))
        if before is not None:
            self.assertEqual(self.head(), before, "a rejected write must append nothing")
        return cm.exception


# ===================================================================================================== §2 ARS

class ArsParseAndSubset(unittest.TestCase):
    def test_parse_valid_ars(self):
        p = bind.parse_ars(ARS)
        self.assertEqual(set(p), {"coordinator", "builder", "reviewer"})
        self.assertEqual(p["builder"], {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]})

    def test_parse_rejects_malformed_ars(self):
        cases = [GRANT["ars"], None, [], "x", {}, {"roles": {}}, {"roles": []},
                 {"roles": {"owner": {"tools": [], "actions": [], "paths": []}}},
                 {"roles": {"builder": []}},
                 {"roles": {"builder": {"tools": ["W"], "actions": []}}},
                 {"roles": {"builder": {"tools": "W", "actions": [], "paths": []}}},
                 {"roles": {"builder": {"tools": [1], "actions": [], "paths": []}}}]
        for ars in cases:
            with self.subTest(ars=ars):
                with self.assertRaises(WfRejected) as cm:
                    bind.parse_ars(ars)
                self.assertEqual(cm.exception.reason, "ars")

    def test_subset_rule(self):
        spec = bind.parse_ars(ARS)["builder"]
        self.assertEqual(bind.declared_subset(["Write"], ["jobs/j/work/**", "jobs/j/work/sub/*.txt"], spec), [])
        self.assertEqual(bind.declared_subset([], [], spec), [])
        r = bind.declared_subset(["Bash"], ["jobs/j/other/**", "jobs/**"], spec)
        self.assertEqual(len(r), 3, r)
        self.assertTrue(any("'Bash'" in x for x in r) and any("'jobs/**'" in x for x in r))
        self.assertTrue(bind.declared_subset([None], [3], spec))  # non-strings never subset


# ===================================================================================================== §1 schemas

class Schemas(unittest.TestCase):
    GOOD = {"bind": {"role": "builder", "session": "B1", "generation": 1, "tools": ["Write"], "paths": ["a/**"],
                     "grant_seq": 3},
            "ack-assignment": {"role": "builder", "session": "B1", "bind_seq": 4},
            "stop-confirmed": {"role": "builder", "session": "B1", "generation": 1, "how": "h", "when": "w",
                               "owner_check": "no child process", "owner_turn": "turn j"}}

    def test_good_contents_pass_with_injected_pin(self):
        for etype, c in self.GOOD.items():
            with self.subTest(etype=etype):
                bind.validate_content(etype, dict(c, contract_sha256="0" * 64), CONTRACT, None)

    def test_malformed_contents(self):
        bad = [("bind", {"role": "owner"}), ("bind", {"generation": 0}), ("bind", {"generation": True}),
               ("bind", {"session": " "}), ("bind", {"tools": "Write"}), ("bind", {"grant_seq": None}),
               ("ack-assignment", {"bind_seq": "4"}), ("ack-assignment", {"session": ""}),
               ("stop-confirmed", {"owner_check": ""}), ("stop-confirmed", {"owner_turn": "  "}),
               ("stop-confirmed", {"how": 1}), ("stop-confirmed", {"generation": 0})]
        for etype, patch in bad:
            with self.subTest(etype=etype, patch=patch):
                with self.assertRaises(WfRejected) as cm:
                    bind.validate_content(etype, dict(self.GOOD[etype], **patch), CONTRACT, None)
                self.assertEqual(cm.exception.reason, "malformed")
        for etype, c in self.GOOD.items():
            with self.subTest(etype=etype, case="missing+extra"):
                missing = dict(c); missing.pop("role")
                for content in (missing, dict(c, extra=1), "x"):
                    with self.assertRaises(WfRejected):
                        bind.validate_content(etype, content, CONTRACT, None)
        with self.assertRaises(WfRejected):
            bind.validate_content("intent", {}, CONTRACT, None)

    def test_bind_replaces_seq_schema(self):
        """S2 §6: `replaces_seq` absent/None at generation 1, int >= 1 at generation >= 2; anything else malformed."""
        g1 = self.GOOD["bind"]
        bind.validate_content("bind", dict(g1, replaces_seq=None), CONTRACT, None)
        bind.validate_content("bind", dict(g1, generation=2, replaces_seq=5), CONTRACT, None)
        for bad in (dict(g1, replaces_seq=5), dict(g1, generation=2), dict(g1, generation=2, replaces_seq=None),
                    dict(g1, generation=2, replaces_seq=0), dict(g1, generation=2, replaces_seq=True),
                    dict(g1, generation=2, replaces_seq="5")):
            with self.subTest(content=bad):
                with self.assertRaises(WfRejected) as cm:
                    bind.validate_content("bind", bad, CONTRACT, None)
                self.assertEqual(cm.exception.reason, "malformed")


# ===================================================================================================== §3 bind preconditions

class BindPreconditions(_Engine):
    def test_bootstrap_self_bind_then_builder_then_reviewer(self):
        """T32: coordinator self-bind (contract author C1), builder B1, reviewer R1 after delivery; table and rows."""
        self.new_job(); self.activate()
        self.assertEqual(self.table(), {})                       # implicit mode until the first bind
        self.do_bind("coordinator", "C1")
        self.do_bind("builder", "B1")
        self.do_intent("B1")
        self._write(etype="delivered", role="builder", session="B1", generation=1,
                    content_fn=lambda conn: {"claims": [{"claim": "c", "evidence": ["e"]}]})
        self.do_bind("reviewer", "R1")
        t = self.table()
        self.assertEqual({k: (v.session, v.generation) for k, v in t.items()},
                         {"coordinator": ("C1", 1), "builder": ("B1", 1), "reviewer": ("R1", 1)})
        c = self.conn()
        try:
            rs = dbm.rows(c, "j")
            brow = [r for r in rs if r["type"] == "bind" and json.loads(r["content"])["role"] == "builder"][0]
            content = json.loads(brow["content"])
            grant_seq = [r["seq"] for r in rs if r["type"] == "grant"][-1]
            self.assertEqual({k: content[k] for k in ("role", "session", "generation", "tools", "paths", "grant_seq")},
                             {"role": "builder", "session": "B1", "generation": 1, "tools": ["Write"],
                              "paths": ["jobs/j/work/**"], "grant_seq": grant_seq})
            self.assertEqual(brow["author_role"], "coordinator"); self.assertEqual(brow["session_id"], "C1")
            self.assertEqual(dbm.verify_chain(c, "j"), [])
        finally:
            c.close()

    def test_bootstrap_rule(self):
        """First bind must be coordinator == invoking session == contract author session (D-S1-1)."""
        self.new_job(); self.activate()
        self.assertRejected("bootstrap", self.do_bind, "builder", "B1")
        self.assertRejected("bootstrap", self.do_bind, "coordinator", "C9", author="C9")   # not the contract author
        self.assertRejected("bootstrap", self.do_bind, "coordinator", "C9", author="C1")   # binds someone else
        self.assertEqual(self.table(), {})

    def test_no_grant_and_proposed_grant(self):
        self.new_job()
        self.assertRejected("no-grant", self.do_bind, "coordinator", "C1")
        self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        self.assertRejected("no-grant", self.do_bind, "coordinator", "C1")

    def test_free_form_ars_fails_only_at_bind(self):
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(GRANT, fh)                                  # ars {"a": "owner"}: propose/activate still work
        self.new_job(); self.activate()
        self.assertRejected("ars", self.do_bind, "coordinator", "C1")

    def test_role_not_in_ars_and_declarations_outside_ars(self):
        ars = copy.deepcopy(ARS); del ars["roles"]["reviewer"]
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(dict(GRANT, ars=ars), fh)
        self.new_job(); self.activate()
        self.do_bind("coordinator", "C1")
        e = self.assertRejected("ars", self.do_bind, "builder", "B1", tools=["Bash"])
        self.assertIn("Bash", str(e))
        e = self.assertRejected("ars", self.do_bind, "builder", "B1", paths=["jobs/j/**"])
        self.assertIn("jobs/j/**", str(e))
        self.assertRejected("ars", self.do_bind, "reviewer", "R1")       # role absent from the ARS
        self.do_bind("builder", "B1", paths=["jobs/j/work/sub/*.md"])   # narrower glob matches an ARS glob

    def test_duplicate_and_foreign_bind(self):
        self.explicit()
        e = self.assertRejected("bound", self.do_bind, "builder", "B2")
        self.assertIn("replacement binds generation 2", str(e))          # S2 §3: replacement is generation+1
        self.assertRejected("bound", self.do_bind, "builder", "B1")         # same session twice: still bound
        self.assertRejected("bound", self.do_bind, "coordinator", "C1")
        # foreign coordinator session: claimant conflict at the actor check (conflict row written, role blocked)
        c = self.assertConflict(self.do_bind, "reviewer", "R1", author="C2")
        self.assertEqual((c["role"], c["holder_session"], c["claimant_session"]), ("coordinator", "C1", "C2"))
        # the coordinator role is now blocked for every session (§5 rule 5, build_row; §6 runs type checks first,
        # so a plain coordinator event shows the block)
        self.assertRejected("role-blocked", self._write, etype="blocker", role="coordinator", session="C1",
                            generation=1, content_fn=lambda conn: {"reason": "r"})

    def test_reviewer_preconditions(self):
        self.explicit()
        self.assertRejected("reviewer-precondition", self.do_bind, "reviewer", "R1")     # nothing delivered
        self.do_intent("B1")
        self._write(etype="delivered", role="builder", session="B1", generation=1,
                    content_fn=lambda conn: {"claims": [{"claim": "c", "evidence": ["e"]}]})
        e = self.assertRejected("reviewer-precondition", self.do_bind, "reviewer", "B1")  # builder session
        self.assertIn("builder session", str(e))
        self.do_bind("reviewer", "R1")

    def test_reviewer_precondition_uses_implicit_builder_session(self):
        """Builder never bound (explicit mode began after implicit builder work): the implicit builder_session counts."""
        self.new_job(); self.activate()
        self.run_wf("append", "j", "--type", "delivered", "--content", '{"claims":[{"claim":"c","evidence":["e"]}]}')
        self.do_bind("coordinator", "C1")
        self.assertRejected("reviewer-precondition", self.do_bind, "reviewer", "B1")
        self.do_bind("reviewer", "R1")

    def test_generation_without_a_current_binding(self):
        """S2 §3 (1): generation >= 2 needs a current binding at g-1; with none -> rejected(generation)."""
        self.new_job(); self.activate()
        self.do_bind("coordinator", "C1")
        e = self.assertRejected("generation", self.do_bind, "builder", "B1", generation=2)
        self.assertIn("current binding at generation 1", str(e))

    def test_recovery_open_refuses_bind(self):
        self.new_job(); self.activate()
        self.put_recovery_opened()
        self.assertRejected("recovery-open", self.do_bind, "coordinator", "C1")

    def test_stopped_coordinator_cannot_bind(self):
        self.explicit()
        self.do_stop("coordinator", "C1")
        self.assertRejected("stopped-session", self.do_bind, "reviewer", "R1")

    def test_closed_and_shape_checks_at_module_level(self):
        self.new_job(); self.activate()
        c = self.conn()
        try:
            st = validate.existing_state(c, "j")
            g = st["grant"]
            kw = dict(generation=1, tools=[], paths=["tools/wf/**"], author_session="C1", grant=g, grant_seq=3)
            with self.assertRaises(WfRejected) as cm:
                bind.validate_bind(c, "j", role="coordinator", session="C1", state=dict(st, closed=True), **kw)
            self.assertEqual(cm.exception.reason, "closed")
            for bad in ({"role": "owner"}, {"session": ""}, {"tools": "Read"}, {"generation": "1"},
                        {"author_session": " "}):
                args = dict(role="coordinator", session="C1", state=st, **kw); args.update(bad)
                with self.subTest(bad=bad):
                    with self.assertRaises(WfRejected) as cm:
                        bind.validate_bind(c, "j", **args)
                    self.assertEqual(cm.exception.reason, "malformed")
            with self.assertRaises(WfRejected) as cm:
                bind.validate_bind(c, "nojob", role="coordinator", session="C1", state=st, **kw)
            self.assertEqual(cm.exception.reason, "malformed")
            ok = bind.validate_bind(c, "j", role="coordinator", session="C1", state=st, **kw)
            self.assertEqual(ok, {"role": "coordinator", "session": "C1", "generation": 1, "tools": [],
                                  "paths": ["tools/wf/**"], "grant_seq": 3,
                                  "replaces_seq": None})
        finally:
            c.close()


# ===================================================================================================== §3 actor check

class ActorCheck(_Engine):
    def test_implicit_mode_is_a_no_op(self):
        """No bind ever ran: check_actor accepts anything; the existing implicit rules (build_row) still apply."""
        self.new_job(); self.activate()
        c = self.conn()
        try:
            for role, session, gen in (("builder", "B9", 7), ("reviewer", "R1", 1), ("coordinator", "X", 1)):
                bind.check_actor(c, "j", role=role, session=session, generation=gen, etype="intent", msg_id="m",
                                 contract_sha="0" * 64)
        finally:
            c.close()
        self.do_intent("B1")
        self.assertConflict(self.do_intent, "B2")                # implicit claimant rule unchanged

    def test_matrix_in_explicit_mode(self):
        self.explicit()
        self.do_intent("B1")                                      # bound session works
        self.assertRejected("unbound-session", self._write, etype="blocker", role="reviewer", session="R1",
                            generation=1, content_fn=lambda conn: {"reason": "r"})          # unbound role
        self.assertRejected("unbound-session", self.do_intent, "B1", generation=2)          # higher generation
        c = self.conn()
        try:
            with self.assertRaises(WfRejected) as cm:             # below the bound generation (module level)
                bind.check_actor(c, "j", role="builder", session="B1", generation=0, etype="intent", msg_id="m",
                                 contract_sha="0" * 64)
            self.assertEqual(cm.exception.reason, "stale")
            bind.check_actor(c, "j", role="owner", session="anyone", generation=1, etype="intent", msg_id="m",
                             contract_sha="0" * 64)               # owner is never bound
        finally:
            c.close()
        c = self.assertConflict(self.do_intent, "B2")             # foreign session at the bound generation
        self.assertEqual((c["holder_session"], c["claimant_session"], c["attempted_type"]), ("B1", "B2", "intent"))
        # the module raises the WfClaimantConflict that txn turns into that row (module level, no write)
        c2 = self.conn()
        try:
            with self.assertRaises(validate.WfClaimantConflict) as cm:
                bind.check_actor(c2, "j", role="coordinator", session="C2", generation=1, etype="bind",
                                 msg_id="x", contract_sha="0" * 64)
        finally:
            c2.close()
        self.assertEqual((cm.exception.holder_session, cm.exception.claimant_session), ("C1", "C2"))
        self.assertRejected("role-blocked", self.do_intent, "B1")  # both sessions blocked (§5 rule 5)

    def test_stopped_session(self):
        self.explicit()
        self.do_stop("builder", "B1")
        self.assertRejected("stopped-session", self.do_intent, "B1")
        self.assertEqual(self.table()["builder"].stopped_seq, self.head())
        self.assertConflict(self.do_intent, "B2")                # a new session is no replacement (T33)


# ===================================================================================================== implicit -> explicit

class ImplicitThenExplicit(_Engine):
    """White's review of PR #29 (S1B cycle 1/3), cases (a) and (b): explicit binding after implicit builder work.
    Contract-determined (§3 precondition list exhaustive; owner Q6 keeps build_row's implicit rules). Asserted on the
    real rows. Fixture seqs: 1 contract, 2 grant proposed, 3 grant activated."""

    def rows(self) -> list[dict]:
        c = self.conn()
        try:
            return dbm.rows(c, "j")
        finally:
            c.close()

    def last(self) -> tuple[dict, dict]:
        r = self.rows()[-1]
        return r, json.loads(r["content"])

    def test_case_a_same_session_implicit_then_bound(self):
        """(a) B1 acts implicitly -> C1 self-bind -> bind builder B1 -> B1 intent and ack: all committed, no conflict."""
        self.new_job(); self.activate()
        self.do_intent("B1")
        r, _ = self.last()
        self.assertEqual((r["seq"], r["type"], r["session_id"]), (4, "intent", "B1"))
        self.do_bind("coordinator", "C1")
        self.do_bind("builder", "B1")
        r, c = self.last()
        self.assertEqual((r["seq"], r["type"], r["author_role"], r["session_id"]), (6, "bind", "coordinator", "C1"))
        self.assertEqual((c["role"], c["session"], c["generation"]), ("builder", "B1", 1))
        self.do_intent("B1")
        r, _ = self.last()
        self.assertEqual((r["seq"], r["type"], r["author_role"], r["session_id"]), (7, "intent", "builder", "B1"))
        self.do_ack("builder", "B1")
        r, c = self.last()
        self.assertEqual((r["seq"], r["type"], r["session_id"]), (8, "ack-assignment", "B1"))
        self.assertEqual(c["bind_seq"], 6)
        self.assertEqual([x["type"] for x in self.rows()].count("blocker"), 0)
        b = self.table()["builder"]
        self.assertEqual((b.session, b.bind_seq, b.ack_seq, b.stopped_seq), ("B1", 6, 8, None))

    def test_case_b_different_session_refused_implicit_claimant(self):
        """S2 A-1 (owner item 1): B1 acts implicitly (4) -> C1 self-bind (5) -> bind builder B2 -> rejected(implicit-
        claimant) naming B1, nothing written; no conflict row, no block. Same-session bind (case a) stays allowed."""
        self.new_job(); self.activate()
        self.do_intent("B1")
        self.do_bind("coordinator", "C1")
        r, _ = self.last()
        self.assertEqual((r["seq"], r["type"]), (5, "bind"))
        e = self.assertRejected("implicit-claimant", self.do_bind, "builder", "B2")
        self.assertIn("'B1'", str(e))
        self.assertEqual(self.head(), 5)
        self.assertEqual([x["type"] for x in self.rows()].count("blocker"), 0)
        self.assertNotIn("builder", self.table())
        self.do_bind("builder", "B1")                      # the implicit holder itself may still be bound (case a)
        r, c = self.last()
        self.assertEqual((r["seq"], r["type"], c["session"]), (6, "bind", "B1"))
        self.do_intent("B1")

    def test_case_b_allowed_after_the_implicit_holder_is_stop_confirmed(self):
        """S2 A-1 exception: a stop-confirmed naming the implicit builder session (written in implicit mode) lets the
        coordinator bind a different session at generation 1."""
        self.new_job(); self.activate()
        self.do_intent("B1")
        self.do_stop("builder", "B1")                       # implicit mode: check_stop_confirmed implicit rule
        r, c = self.last()
        self.assertEqual((r["seq"], r["type"], c["session"]), (5, "stop-confirmed", "B1"))
        self.do_bind("coordinator", "C1")
        self.do_bind("builder", "B2")
        r, c = self.last()
        self.assertEqual((r["seq"], r["type"], c["role"], c["session"], c["replaces_seq"]), (7, "bind", "builder", "B2", None))

    def test_implicit_claimant_applies_to_builder_at_generation_one_only(self):
        """A-1 reads builder_session for role builder only: binding a reviewer is governed by its own precondition."""
        self.new_job(); self.activate()
        self.run_wf("append", "j", "--type", "delivered", "--content", '{"claims":[{"claim":"c","evidence":["e"]}]}')
        self.do_bind("coordinator", "C1")
        self.do_bind("reviewer", "R1")
        self.assertEqual(self.table()["reviewer"].session, "R1")

# ===================================================================================================== ack / stop

class AckAssignment(_Engine):
    def test_valid_ack_and_duplicate(self):
        self.explicit()
        self.do_ack("builder", "B1")
        b = self.table()["builder"]
        self.assertEqual(b.ack_seq, self.head())
        self.assertRejected("invalid-ack", self.do_ack, "builder", "B1")      # duplicate acknowledgement

    def test_invalid_acks(self):
        self.explicit()
        bseq = self.table()["builder"].bind_seq
        for content in ({"role": "builder", "session": "B1", "bind_seq": bseq + 99},
                        {"role": "builder", "session": "B2", "bind_seq": bseq},
                        {"role": "reviewer", "session": "B1", "bind_seq": bseq}):
            with self.subTest(content=content):
                self.assertRejected("invalid-ack", self.do_ack, "builder", "B1", content=content)
        c = self.conn()
        try:
            for role, session in (("reviewer", "R1"), ("builder", "B2")):
                with self.subTest(role=role, session=session):
                    with self.assertRaises(WfRejected) as cm:
                        bind.check_ack_assignment(c, "j", role=role, session=session,
                                                  content={"role": role, "session": session, "bind_seq": bseq})
                    self.assertEqual(cm.exception.reason, "invalid-ack")
        finally:
            c.close()
        self.assertIsNone(self.table()["builder"].ack_seq)

    def test_ack_is_not_a_precondition_for_work(self):
        """D-S1-2: the bound builder works before acknowledging; the ack is recorded and shown."""
        self.explicit()
        self.do_intent("B1")
        self.do_ack("builder", "B1")
        self.assertIsNotNone(self.table()["builder"].ack_seq)

    def test_implicit_mode_has_nothing_to_ack(self):
        self.new_job(); self.activate()
        c = self.conn()
        try:
            with self.assertRaises(WfRejected) as cm:
                bind.check_ack_assignment(c, "j", role="builder", session="B1",
                                          content={"role": "builder", "session": "B1", "bind_seq": 1})
            self.assertEqual(cm.exception.reason, "invalid-ack")
        finally:
            c.close()


class StopConfirmed(_Engine):
    def test_explicit_stop_valid_wrong_and_twice(self):
        self.explicit()
        self.assertRejected("invalid-stop", self.do_stop, "builder", "B2")            # not the bound session
        self.assertRejected("invalid-stop", self.do_stop, "builder", "B1", generation=2)
        self.assertRejected("invalid-stop", self.do_stop, "reviewer", "R1")           # role not bound
        self.do_stop("builder", "B1")
        self.assertRejected("invalid-stop", self.do_stop, "builder", "B1")            # already stopped

    def test_implicit_stop_uses_the_builder_session(self):
        self.new_job(); self.activate()
        c = self.conn()
        content = {"role": "builder", "session": "B1", "generation": 1}
        try:
            with self.assertRaises(WfRejected) as cm:                                # no builder observed yet
                bind.check_stop_confirmed(c, "j", content=content)
            self.assertEqual(cm.exception.reason, "invalid-stop")
        finally:
            c.close()
        self.do_intent("B1")
        self.assertRejected("invalid-stop", self.do_stop, "builder", "B2")
        self.assertRejected("invalid-stop", self.do_stop, "coordinator", "C1")
        self.do_stop("builder", "B1")
        self.assertRejected("invalid-stop", self.do_stop, "builder", "B1")
        self.assertEqual(self.table(), {})                    # a stop does not create explicit mode


# ===================================================================================================== digest rows

class DigestRows(_Engine):
    def test_rows_reflect_bind_ack_stop(self):
        self.explicit()
        self.do_ack("builder", "B1")
        self.do_stop("builder", "B1")
        t = self.table()
        c = self.conn()
        try:
            rows = bind.digest_rows(c, "j", {"builder": False})
        finally:
            c.close()
        self.assertEqual([r["role"] for r in rows], ["coordinator", "builder"])
        self.assertEqual(rows[1], {"role": "builder", "session": "B1", "generation": 1,
                                   "bind_seq": t["builder"].bind_seq, "acknowledged": True,
                                   "ack_seq": t["builder"].ack_seq, "stopped": True,
                                   "stopped_seq": t["builder"].stopped_seq, "reconciled": False,
                                   "replacements": 0, "replaces_seq": None, "tools": ["Write"],
                                   "paths": ["jobs/j/work/**"]})
        self.assertEqual((rows[0]["acknowledged"], rows[0]["stopped"], rows[0]["reconciled"]), (False, False, None))

    def test_implicit_mode_rows_empty(self):
        self.new_job(); self.activate(); self.do_intent("B1")
        c = self.conn()
        try:
            self.assertEqual(bind.digest_rows(c, "j", {}), [])
        finally:
            c.close()


# ===================================================================================== FIX3-II bind account

ACC_MAP = {"builder": (B1, B2), "reviewer": (R1,)}


class BindAccount(_Engine):
    """FIX3-II lane F3-ACC (WF-196): interface contract r2 §4.2 at module level (ACCEPTANCE-ii MAP-4). `validate_bind`
    takes `account` and `account_map` (passed in by `commands.bind_cmd`, D17). A coordinator bind with an account is
    an input-shape refusal (malformed); after the ARS checks and before the generation tails, a mapped builder or
    reviewer bind needs an account in `account_map[role]` that no other session's `bind` row names (ars); with no map,
    any account is refused (ars). `Binding.account` is the last field, kept through the ack and stop updates."""

    def acct_bind(self, role: str, session: str, account: str | None, account_map: dict | None = ACC_MAP, *,
                  author: str = "C1", generation: int = 1, tools: list[str] | None = None) -> object:
        dt, dp = DEFAULTS[role]

        def content(conn: sqlite3.Connection) -> dict:
            st = validate.existing_state(conn, "j")
            grant_rows = [r for r in dbm.rows(conn, "j") if r["type"] == "grant"]
            grant_seq = grant_rows[-1]["seq"] if grant_rows else None
            return bind.validate_bind(conn, "j", role=role, session=session, generation=generation,
                                      tools=dt if tools is None else tools, paths=dp, author_session=author,
                                      grant=st["grant"], grant_seq=grant_seq, state=st, account=account,
                                      account_map=account_map)
        return self._write(etype="bind", role="coordinator", session=author, generation=1, content_fn=content)

    def last_content(self) -> dict:
        c = self.conn()
        try:
            return json.loads(dbm.rows(c, "j")[-1]["content"])
        finally:
            c.close()

    def test_a_mapped_bind_records_the_account_and_the_binding_keeps_it(self):
        self.new_job(); self.activate()
        self.acct_bind("coordinator", "C1", None)
        self.assertNotIn("account", self.last_content())
        self.assertIsNone(self.table()["coordinator"].account)
        self.acct_bind("builder", "B1", B1)
        self.assertEqual(self.last_content()["account"], B1)
        self.assertEqual(self.table()["builder"].account, B1)
        self.do_ack("builder", "B1")
        b = self.table()["builder"]
        self.assertEqual((b.account, b.ack_seq is not None), (B1, True))
        self.do_stop("builder", "B1")
        b = self.table()["builder"]
        self.assertEqual((b.account, b.stopped_seq is not None), (B1, True))

    def test_MAP4_refusals(self):
        self.new_job(); self.activate()
        self.acct_bind("coordinator", "C1", None)
        self.assertRejected("ars", self.acct_bind, "builder", "B1", R1)              # (a) not in map[builder]
        self.assertRejected("ars", self.acct_bind, "builder", "B1", None)            # (c) omitted with a map
        self.assertRejected("malformed", self.acct_bind, "coordinator", "C2", B1)    # (d) coordinator + account
        self.assertRejected("ars", self.acct_bind, "builder", "B1", B1, None)        # an account with no map
        self.assertRejected("ars", self.acct_bind, "reviewer", "R1", R1, {"builder": (B1,)})  # no reviewer list
        self.acct_bind("builder", "B1", B1)
        # (b) another session's bind names B1; the account check runs before the generation tails ("bound")
        e = self.assertRejected("ars", self.acct_bind, "builder", "B9", B1)
        self.assertIn("B1", str(e))
        self.assertRejected("bound", self.acct_bind, "builder", "B9", B2)

    def test_an_account_on_a_coordinator_bind_is_an_input_shape_refusal(self):
        self.new_job()                                                              # no grant yet
        self.assertRejected("no-grant", self.acct_bind, "coordinator", "C1", None, None)
        self.assertRejected("malformed", self.acct_bind, "coordinator", "C1", B1, None)

    def test_the_ars_checks_come_before_the_account(self):
        self.new_job(); self.activate()
        self.acct_bind("coordinator", "C1", None)
        e = self.assertRejected("ars", self.acct_bind, "builder", "B1", R1, tools=["Bash"])
        self.assertIn("Bash", str(e))

    def test_schema_account(self):
        good = dict(Schemas.GOOD["bind"], contract_sha256="0" * 64)
        bind.validate_content("bind", dict(good, account=B1), CONTRACT, None)
        for bad in ("", "  ", 5, None, [B1]):
            with self.subTest(account=bad):
                with self.assertRaises(WfRejected) as cm:
                    bind.validate_content("bind", dict(good, account=bad), CONTRACT, None)
                self.assertEqual(cm.exception.reason, "malformed")

    def test_binding_account_is_the_last_field_with_a_default(self):
        self.assertEqual([f.name for f in dataclasses.fields(bind.Binding)][-1], "account")
        b = bind.Binding("builder", "B1", 1, 5, None, None, (), (), None)  # the positional form of bind.py
        self.assertIsNone(b.account)


if __name__ == "__main__":
    unittest.main()
