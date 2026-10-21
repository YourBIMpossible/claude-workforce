"""FIX3-II lane F3-ACC (WF-196): the account check in `commands` (interface contract r2 §3.3 the `caller` keyword,
§3.4 the entry check and the in-transaction check, §4.1 the map at propose and activate, §4.2 the bind account,
§4.5 `whoami_job`, §1.5 `inline_only`). ACCEPTANCE-ii rows owned by F3-ACC (§7): ID-1, ID-2 (the request is refused),
ID-3, ID-4, ID-5, ID-7, ID-9 (a)(b)(c), ID-10 (`whoami_job`), ID-13, MAP-1 to MAP-5, BRK-4 (`inline_only`), W-1 and
W-2 (an own `Caller`).

Harness (§6.3): ledgers are built by direct calls (`wf.run` with `--db`, and `commands.bind_cmd(…, bind_account=…)`
for the bind account, whose CLI option is F3-BRK's); each claim then calls the `commands` function with
`caller=Caller(…)`, as `run_brokered` will. The caller's account is a fake id in the host's form, so every test runs
in the runner's own account. `wfcore.peer.own_account` is patched to the fake owner account; the environment is
scrubbed of WF_DB, WF_BROKER, WF_ROLE, WF_SESSION and WF_TEST_FAULTS.

A refusal leaves the ledger unchanged: the same row count and chain head before and after (ACCEPTANCE-ii)."""
from __future__ import annotations

import builtins
import json
import os
import unittest
from typing import Any, Callable
from unittest import mock

from test_wf import Base, GRANT  # noqa: E402
from test_fix3ii_accounts import B1, B2, FOREIGN, OWN, PLAT, R1, ROLES, WFCORE, X  # noqa: E402
from wfcore import commands, db as dbm, grant as grantm, peer  # noqa: E402
from wfcore.canon import canonical, event_self_hash, sha256_text  # noqa: E402
from wfcore.db import OpLog, WfError, WfRejected, WfUsage  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
FAR = "2999-12-31T23:59:59Z"
INTENT = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"})
MAP = {"builder": [B1, B2], "reviewer": [R1]}
REPLACEMENT = {"allowed": True, "max": 1, "expiry": FAR}
GRANT_MAP = dict(GRANT, ars={"roles": ROLES, "replacement": REPLACEMENT, "accounts": MAP})
GRANT_NOMAP = dict(GRANT, ars={"roles": ROLES, "replacement": REPLACEMENT})


def caller(account: str) -> peer.Caller:
    return peer.Caller(peer=peer.PeerIdentity(account=account, pid=None, platform=PLAT), broker_account=OWN,
                       broker_wfcore=WFCORE)


def stop_content(session: str, generation: int) -> str:
    return json.dumps({"role": "builder", "session": session, "generation": generation,
                       "how": "process exit observed", "when": "2026-10-21T04:00:00Z",
                       "owner_check": "no child process, no outstanding operation",
                       "owner_turn": "owner turn: builder session stopped in job j"})


class _Guard(Base):
    grant: dict = GRANT_MAP

    def setUp(self) -> None:
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        own = mock.patch.object(peer, "own_account", return_value=OWN)
        own.start()
        self.addCleanup(own.stop)
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(self.grant, fh)
        self._n = 0

    # ---- calls
    def call(self, fn: Callable[..., Any], *a: Any, who: str | None = None, role: str, session: str,
             generation: int = 1, **kw: Any) -> dict[str, Any]:
        """`fn(db, *a, …)` with the claim; `who` is the caller's account (None: direct mode, no caller keyword)."""
        self._n += 1
        kw.update(msg_id=f"g{self._n}", role=role, session=session, generation=generation, faults=Faults(),
                  oplog=OpLog())
        if who is not None:
            kw["caller"] = caller(who)
        return fn(self.db, *a, **kw)

    def intent(self, who: str | None, session: str, *, role: str = "builder", generation: int = 1) -> dict[str, Any]:
        return self.call(commands.append, "j", "intent", INTENT, who=who, role=role, session=session,
                         generation=generation, reply_to=None, grant_ref=None)

    def bind(self, brole: str, bsession: str, account: str | None = None, *, generation: int = 1) -> dict[str, Any]:
        kw: dict[str, Any] = {} if account is None else {"bind_account": account}
        return self.call(commands.bind_cmd, "j", bind_role=brole, bind_session=bsession, bind_generation=generation,
                         tools=None, paths_=None, role="coordinator", session="C1", **kw)

    def ok(self, res: tuple[int, dict]) -> dict:
        code, r = res
        self.assertEqual(code, 0, r)
        return r

    # ---- ledger reads
    def rows(self, job: str = "j") -> list[dict[str, Any]]:
        c = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(c, job)
        finally:
            c.close()

    def state(self) -> tuple[int, str | None]:
        rs = self.rows()
        return len(rs), (rs[-1]["self"] if rs else None)

    def refused(self, want: str, fn: Callable[..., Any], /, *a: Any, **k: Any) -> WfError:
        """`fn` raises rejected(<want>) (or `usage`), and the ledger is unchanged."""
        before = self.state() if os.path.exists(self.db) else None
        with self.assertRaises(WfError) as cm:
            fn(*a, **k)
        e = cm.exception
        if want == "usage":
            self.assertIsInstance(e, WfUsage, str(e))
        else:
            self.assertIsInstance(e, WfRejected, str(e))
            self.assertEqual(e.reason, want, str(e))
            self.assertTrue(str(e).startswith(f"rejected({want})"), str(e))
        if before is not None:
            self.assertEqual(self.state(), before, "a refusal must leave the ledger unchanged")
        return e

    # ---- fixtures (direct mode)
    def explicit(self) -> None:
        """contract (1), proposed grant (2), activated grant (3), C1 self-bind (4), builder B1 with B1's account (5)."""
        self.new_job()
        self.activate()
        self.bind("coordinator", "C1")
        self.bind("builder", "B1", B1)

    def deliver_and_bind_reviewer(self) -> int:
        self.ok(self.run_wf("append", "j", "--type", "delivered", "--content",
                            '{"claims":[{"claim":"c","evidence":["e"]}]}', role="builder", session="B1"))
        self.bind("reviewer", "R1", R1)
        return self.rows()[-1]["seq"]

    def put_row(self, etype: str, content: dict) -> int:
        """A fully hashed, chained coordinator row (as test_t33_replacement: a recovery-decision fixture)."""
        c = dbm.connect(self.db, create=False)
        try:
            with c:
                hs, hself = dbm.head(c, "j")
                csha = dbm.contract(c, "j")["sha256"]
                text = canonical(dict(content, contract_sha256=csha))
                row = {"job": "j", "seq": hs + 1, "type": etype, "msg_id": f"fixture-{etype}-{hs + 1}",
                       "author_role": "coordinator", "session_id": "C1", "generation": 1, "reply_to": None,
                       "grant_ref": None, "ts": "2026-10-21T04:00:00.000Z", "content": text,
                       "content_sha256": sha256_text(text), "prev": hself}
                row["self"] = event_self_hash(row)
                c.execute("INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,"
                          "grant_ref,ts,content,content_sha256,prev,self) VALUES (:job,:seq,:type,:msg_id,"
                          ":author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,"
                          ":prev,:self)", row)
                return row["seq"]
        finally:
            c.close()

    def ready_for_replacement(self) -> dict[str, int]:
        """explicit() (1-5), B1 intent (6), stop B1 (7), reconcile (8), resume decision (9)."""
        self.explicit()
        self.intent(None, "B1")
        self.ok(self.run_wf("append", "j", "--type", "stop-confirmed", "--content", stop_content("B1", 1),
                            role="coordinator", session="C1"))
        self.ok(self.run_wf("reconcile", "j", role="coordinator", session="C1"))
        report = self.rows()[-1]["seq"]
        decision = self.put_row("recovery-decision", {"decision": "resume", "role": "builder", "report_seq": report,
                                                      "stop_seq": 7, "restore_paths": [], "unresolved": [],
                                                      "owner_turn": "owner turn: resume job j with a new builder"})
        return {"stop": 7, "report": report, "decision": decision}

    def replaced(self) -> dict[str, int]:
        """ready_for_replacement() (1-9), B2 bound at generation 2 to B2's account (10), B2's ack-reconcile (11)."""
        s = self.ready_for_replacement()
        self.bind("builder", "B2", B2, generation=2)
        s["b2_bind"] = self.rows()[-1]["seq"]
        report = [r for r in self.rows() if r["seq"] == s["report"]][0]
        ids = [e["id"] for e in json.loads(report["content"])["exceptions"]]
        self.call(commands.ack_reconcile, "j", report_seq=s["report"], exceptions_src=json.dumps(ids), note=None,
                  who=B2, role="builder", session="B2", generation=2)
        return s


# ===================================================================================================== §3.3, §3.4

ENTRY_POINTS: list[tuple[str, Callable[["_Guard"], tuple[Callable[..., Any], tuple, dict]]]] = [
    ("job_new", lambda t: (commands.job_new, ("j2", t.cpath), {})),
    ("grant_propose", lambda t: (commands.grant_propose, ("j", t.gpath), {})),
    ("grant_activate", lambda t: (commands.grant_activate, ("j", "deadbeef", "approve G1 deadbeef", "owner turn"), {})),
    ("append", lambda t: (commands.append, ("j", "intent", INTENT), {"reply_to": None, "grant_ref": None})),
    ("accept", lambda t: (commands.accept, ("j", "{}"), {})),
    ("close", lambda t: (commands.close, ("j", "none"), {})),
    ("bind_cmd", lambda t: (commands.bind_cmd, ("j",), {"bind_role": "builder", "bind_session": "B9",
                                                       "bind_generation": 1, "tools": None, "paths_": None})),
    ("reconcile_cmd", lambda t: (commands.reconcile_cmd, ("j",), {"target_role": "builder"})),
    ("ack_reconcile", lambda t: (commands.ack_reconcile, ("j",), {"report_seq": 1, "exceptions_src": "[]",
                                                                 "note": None})),
    ("recover_begin", lambda t: (commands.recover_begin, ("j",), {"reason": "r", "turn": "owner turn j"})),
    ("recover_reconcile", lambda t: (commands.recover_reconcile, ("j",), {"turn": "owner turn j"})),
    ("recover_close", lambda t: (commands.recover_close, ("j",), {"turn": "owner turn j"})),
    ("recover_decide", lambda t: (commands.recover_decide, ("j",), {"decision": "resume", "restore_paths": None,
                                                                  "unresolved": None, "turn": "owner turn j",
                                                                  "note": None})),
    ("grant_revoke", lambda t: (commands.grant_revoke, ("j",), {"reason": "r", "turn": "owner turn j"})),
    ("grant_expire", lambda t: (commands.grant_expire, ("j",), {"reason": "r", "turn": "owner turn j"})),
    ("cancel", lambda t: (commands.cancel, ("j",), {"reason": "r", "turn": "owner turn j"})),
    ("log_intervention", lambda t: (commands.log_intervention, ("j",), {"label": "l", "necessity": "necessary",
                                                                      "minutes": "1", "note": ""})),
]


class EntryCheck(_Guard):
    """§3.3: the 17 entry points take `caller`; §3.4: the entry check is first, before every other check (C1, C2)."""

    def test_17_entry_points(self):
        self.assertEqual(len(ENTRY_POINTS), 17)

    def test_a_worker_claiming_the_coordinator_is_refused_first_everywhere(self):
        self.explicit()
        for name, spec in ENTRY_POINTS:
            with self.subTest(entry=name):
                fn, a, kw = spec(self)
                # job j2 has no rows, so no map is in force for B1 there: §4.4 row 4
                reason = "caller-unattributed" if name == "job_new" else "caller-mismatch"
                self.refused(reason, self.call, fn, *a, who=B1, role="coordinator", session="C1", **kw)

    def test_the_own_account_claiming_builder_is_refused_first_everywhere(self):
        self.explicit()
        for name, spec in ENTRY_POINTS:
            with self.subTest(entry=name):
                fn, a, kw = spec(self)
                e = self.refused("caller-mismatch", self.call, fn, *a, who=OWN, role="builder", session="B1", **kw)
                self.assertEqual(e.info, {"account": OWN, "claimed_role": "builder", "claimed_session": "B1"})

    def test_C1_before_the_role_name_refusal(self):
        self.explicit()
        self.refused("caller-mismatch", self.call, commands.accept, "j", "{}", who=B1, role="coordinator",
                     session="C1")
        self.refused("role", self.call, commands.accept, "j", "{}", role="coordinator", session="C1")

    def test_C2_recover_reconcile_with_nothing_open(self):
        self.explicit()
        self.refused("caller-mismatch", self.call, commands.recover_reconcile, "j", turn="owner turn j", who=B1,
                     role="coordinator", session="C1")
        self.refused("no-recovery", self.call, commands.recover_reconcile, "j", turn="owner turn j",
                     role="coordinator", session="C1")

    def test_the_in_transaction_check_runs_before_the_recovery_gate(self):
        self.explicit()
        self.ok(self.run_wf("recover", "begin", "j", "--reason", "r", "--turn", "owner turn j", role="coordinator",
                            session="C1"))
        c = dbm.connect(self.db, create=False)
        try:
            csha = dbm.contract(c, "j")["sha256"]
            g = dict(etype="intent", generation=1, msg_id="x", contract_sha=csha)
            for who, role, session in ((B1, "coordinator", "C1"), (OWN, "builder", "B1"), (B2, "builder", "B2")):
                with self.subTest(who=who, role=role):
                    with self.assertRaises(WfRejected) as cm:
                        commands._guard(c, "j", role=role, session=session, caller=caller(who), **g)
                    self.assertEqual(cm.exception.reason, "caller-mismatch")
            with self.assertRaises(WfRejected) as cm:
                commands._guard(c, "j", role="builder", session="B1", **g)
            self.assertEqual(cm.exception.reason, "recovery-open")
        finally:
            c.close()

    def test_the_in_transaction_check_holds_without_the_entry_check(self):
        """§3.4: `_guard`'s check is authoritative, on the write's own snapshot."""
        self.explicit()
        with mock.patch.object(commands, "_caller_entry", lambda *a, **k: None):
            self.refused("caller-mismatch", self.intent, B1, "C1", role="coordinator")
            self.refused("caller-mismatch", self.intent, B2, "B2")
            self.refused("caller-mismatch", self.call, commands.log_intervention, "j", label="l", necessity="necessary",
                         minutes="1", note="", who=B1, role="owner", session="O-x")


# ===================================================================================================== ID rows

class Spoofs(_Guard):
    def test_ID1_the_coordinator_spoof_literal(self):
        self.explicit()
        e = self.refused("caller-mismatch", self.intent, B1, "C1", role="coordinator")
        self.assertEqual(e.info, {"account": B1, "claimed_role": "coordinator", "claimed_session": "C1"})

    def test_ID2_the_one_request_of_every_indirect_form_is_refused(self):
        """The env-default, built-string, encoded and script forms give one request (F3-BRK, U); it is refused here,
        because the check sees the account, not the form."""
        self.explicit()
        role, session = "".join(["coord", "inator"]), "C" + str(1)
        e = self.refused("caller-mismatch", self.intent, B1, session, role=role)
        self.assertEqual(e.info, {"account": B1, "claimed_role": "coordinator", "claimed_session": "C1"})

    def test_ID3_the_owner_spoof(self):
        self.explicit()
        e = self.refused("caller-mismatch", self.call, commands.log_intervention, "j", label="l", necessity="necessary",
                         minutes="1", note="", who=B1, role="owner", session="O-x")
        self.assertEqual(e.info, {"account": B1, "claimed_role": "owner", "claimed_session": "O-x"})

    def test_ID4_a_nested_session_claims_the_bound_reviewer(self):
        self.explicit()
        bind_seq = self.deliver_and_bind_reviewer()
        ack = json.dumps({"role": "reviewer", "session": "R1", "bind_seq": bind_seq})
        e = self.refused("caller-mismatch", self.call, commands.append, "j", "ack-assignment", ack, reply_to=None,
                         grant_ref=None, who=B1, role="reviewer", session="R1")
        self.assertEqual(e.info, {"account": B1, "claimed_role": "reviewer", "claimed_session": "R1"})

    def test_ID5_out_of_the_tree_the_account_decides(self):
        self.explicit()
        for fn, a, kw in ((commands.grant_propose, ("j", json.dumps(GRANT_NOMAP)), {}),
                          (commands.recover_begin, ("j",), {"reason": "r", "turn": "owner turn j"}),
                          (commands.cancel, ("j",), {"reason": "r", "turn": "owner turn j"})):
            with self.subTest(fn=fn.__name__):
                self.refused("caller-mismatch", self.call, fn, *a, who=B1, role="coordinator", session="C1", **kw)

    def test_ID13_a_builder_claims_the_other_builders_session(self):
        self.replaced()
        for who in (B1, R1):
            with self.subTest(who=who):
                e = self.refused("caller-mismatch", self.intent, who, "B2", generation=2)
                self.assertEqual(e.info, {"account": who, "claimed_role": "builder", "claimed_session": "B2"})
        self.assertNotIn("blocker", [r["type"] for r in self.rows()], "no claimant-conflict row")
        # direct mode is unchanged (W-1): the typed values match B2's binding, as at main
        res = self.intent(None, "B2", generation=2)
        self.assertEqual(res.get("outcome"), "COMMITTED", res)


class GreenPaths(_Guard):
    def test_ID7_W_direct_claims_the_coordinator(self):
        self.explicit()
        res = self.call(commands.log_intervention, "j", label="l", necessity="necessary", minutes="1", note="",
                        role="coordinator", session="C1")
        self.assertEqual(res.get("outcome"), "COMMITTED", res)

    def test_ID7_B1_claims_B1_and_R1_claims_R1(self):
        self.explicit()
        self.assertEqual(self.intent(B1, "B1").get("outcome"), "COMMITTED")
        bind_seq = self.deliver_and_bind_reviewer()
        res = self.call(commands.append, "j", "ack-assignment",
                        json.dumps({"role": "reviewer", "session": "R1", "bind_seq": bind_seq}), reply_to=None,
                        grant_ref=None, who=R1, role="reviewer", session="R1")
        self.assertEqual(res.get("outcome"), "COMMITTED", res)

    def test_ID7_B2_claims_B2_at_generation_2(self):
        s = self.replaced()
        last = self.rows()[-1]
        self.assertEqual((last["type"], last["session_id"], last["generation"]), ("ack-reconcile", "B2", 2))
        self.assertEqual(self.intent(B2, "B2", generation=2).get("outcome"), "COMMITTED")
        self.assertEqual(s["b2_bind"], 10)

    def test_W2_the_own_account_through_the_broker(self):
        self.explicit()
        for role, session in (("coordinator", "C1"), ("owner", "O-1")):
            with self.subTest(role=role):
                res = self.call(commands.log_intervention, "j", label="l", necessity="necessary", minutes="1", note="",
                                who=OWN, role=role, session=session)
                self.assertEqual(res.get("outcome"), "COMMITTED", res)
        self.refused("caller-mismatch", self.intent, OWN, "B1")
        self.refused("caller-mismatch", self.call, commands.append, "j", "blocker", '{"reason":"r"}',
                     reply_to=None, grant_ref=None, who=OWN, role="reviewer", session="R1")
        # the own account's other claims then meet the existing checks, which refuse an unknown role
        self.refused("unbound-session", self.call, commands.log_intervention, "j", label="l", necessity="necessary",
                     minutes="1", note="", who=OWN, role="nobody", session="N1")


class ImplicitAndUnmapped(_Guard):
    """ID-9: (a) direct mode, no bindings: as today; (c) a mapped worker claims a session no bind ties to it."""

    def test_ID9a_direct_mode_job_without_bindings(self):
        self.new_job()
        self.activate()
        self.assertEqual(self.intent(None, "B1").get("outcome"), "COMMITTED")

    def test_ID9c_a_session_no_bind_ties_to_the_account(self):
        self.new_job()
        self.activate()
        self.refused("caller-mismatch", self.intent, B1, "B1")  # implicit mode: the map in force, no bind
        self.bind("coordinator", "C1")
        self.bind("builder", "B1", B1)
        self.refused("caller-mismatch", self.intent, B2, "B2")  # B2 is mapped, not bound
        self.refused("caller-mismatch", self.intent, B2, "B1")  # B1's session


class Unattributed(_Guard):
    grant = GRANT_NOMAP

    def test_ID9b_a_worker_on_a_job_whose_grant_has_no_map(self):
        self.new_job()
        self.activate()
        self.bind("coordinator", "C1")
        self.bind("builder", "B1")
        for who in (B1, X):
            with self.subTest(who=who):
                e = self.refused("caller-unattributed", self.intent, who, "B1")
                self.assertEqual(e.info, {"account": who})
        # direct mode is as today
        self.assertEqual(self.intent(None, "B1").get("outcome"), "COMMITTED")


# ===================================================================================================== ID-10

class WhoamiJob(_Guard):
    def test_ID10_a_job_with_no_rows(self):
        want = {"job": "nojob", "map_in_force": False, "roles": [], "sessions": []}
        self.assertEqual(commands.whoami_job(self.db, "nojob", B1), want)  # no ledger yet
        self.new_job()
        self.assertEqual(commands.whoami_job(self.db, "nojob", B1), want)

    def test_ID10_the_map_in_force_and_the_bound_sessions(self):
        self.new_job()
        h = self.ok(self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator",
                                session="C1"))["block_hash"]
        self.assertFalse(commands.whoami_job(self.db, "j", B1)["map_in_force"])  # a proposed map has no force
        self.ok(self.run_wf("grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                            "--turn", "owner turn", role="coordinator", session="C1"))
        self.bind("coordinator", "C1")
        self.bind("builder", "B1", B1)
        before = self.state()
        self.assertEqual(commands.whoami_job(self.db, "j", B1),
                         {"job": "j", "map_in_force": True, "roles": ["builder"],
                          "sessions": [{"role": "builder", "session": "B1", "generation": 1, "bind_seq": 5}]})
        self.assertEqual(commands.whoami_job(self.db, "j", R1),
                         {"job": "j", "map_in_force": True, "roles": ["reviewer"], "sessions": []})
        for who in (OWN, X):
            self.assertEqual(commands.whoami_job(self.db, "j", who),
                             {"job": "j", "map_in_force": True, "roles": [], "sessions": []})
        self.assertEqual(self.state(), before, "whoami writes nothing")


# ===================================================================================================== MAP rows

class MapRows(_Guard):
    def propose(self, g: Any) -> dict[str, Any]:
        return self.call(commands.grant_propose, "j", json.dumps(g), role="coordinator", session="C1")

    def activate_hash(self, h: str) -> dict[str, Any]:
        return self.call(commands.grant_activate, "j", h[:8], f"approve G1 {h[:8]}", "owner turn",
                         role="coordinator", session="C1")

    def test_MAP1_the_map_comes_into_force_at_activation(self):
        self.new_job()
        h = self.propose(GRANT_MAP)["block_hash"]
        self.refused("caller-unattributed", self.intent, B1, "B1")
        self.activate_hash(h)
        self.assertTrue(commands.whoami_job(self.db, "j", B1)["map_in_force"])
        self.refused("caller-mismatch", self.intent, B1, "B1")
        self.bind("coordinator", "C1")
        self.bind("builder", "B1", B1)
        self.assertEqual(self.intent(B1, "B1").get("outcome"), "COMMITTED")

    def test_MAP2_the_hash_covers_the_map(self):
        self.new_job()
        h1 = self.propose(GRANT_MAP)["block_hash"]
        changed = dict(GRANT_MAP, ars=dict(GRANT_MAP["ars"], accounts={"builder": [B1, X], "reviewer": [R1]}))
        h2 = self.propose(changed)["block_hash"]
        self.assertNotEqual(h1[:8], h2[:8])
        self.refused("unbound-approval", self.activate_hash, h1)

    def test_MAP3_a_malformed_map_at_propose(self):
        self.new_job()
        for bad in ({}, "B1", {"coordinator": [B1]}, {"builder": B1}, {"builder": []}, {"builder": [B1, 5]},
                    {"builder": [FOREIGN]}, {"builder": [B1], "reviewer": [B1]}, {"builder": [B1, B1]},
                    {"builder": [B1], "reviewer": [OWN]}):
            with self.subTest(accounts=bad):
                g = dict(GRANT_MAP, ars=dict(GRANT_MAP["ars"], accounts=bad))
                self.refused("ars", self.propose, g)

    def test_MAP3_a_malformed_map_at_activate(self):
        self.new_job()
        h = self.propose(GRANT_MAP)["block_hash"]
        # the activating process's own account is in the map (rule 7, read from the process in both modes)
        with mock.patch.object(peer, "own_account", return_value=B1):
            self.refused("ars", self.activate_hash, h)
        # a stored proposal whose map breaks a rule (as written before this build)
        bad = dict(GRANT_MAP, ars=dict(GRANT_MAP["ars"], accounts={"owner": [B1]}), state="proposed")
        res = self.call(commands._append, "j", etype="grant", content=bad, role="coordinator", session="C1")
        h2 = grantm.block("j", json.loads(self.rows()[-1]["content"]), res["seq"])[1]
        self.refused("ars", self.activate_hash, h2)

    def test_an_unreadable_own_account_is_rejected_ars(self):
        self.new_job()
        h = self.propose(GRANT_MAP)["block_hash"]
        with mock.patch.object(peer, "own_account", side_effect=OSError("no token")):
            e = self.refused("ars", self.propose, GRANT_MAP)
            self.assertIn("cannot read this process's account", str(e))
            self.refused("ars", self.activate_hash, h)

    def test_a_grant_without_a_map_never_reads_the_account(self):
        self.new_job()
        with mock.patch.object(peer, "own_account", side_effect=AssertionError("own_account read")), \
                mock.patch.object(peer, "host_platform", side_effect=AssertionError("host_platform read")):
            h = self.propose(GRANT_NOMAP)["block_hash"]
            self.assertEqual(self.activate_hash(h).get("outcome"), "COMMITTED")

    def test_MAP4_one_account_per_session_at_bind(self):
        self.new_job()
        self.activate()
        self.bind("coordinator", "C1")
        self.refused("ars", self.bind, "builder", "B1", R1)    # (a) not in ars.accounts[builder]
        self.refused("ars", self.bind, "builder", "B1")        # (c) omitted while the grant has a map
        self.refused("malformed", self.bind, "coordinator", "C2", B1)  # (d) an account on a coordinator bind
        self.bind("builder", "B1", B1)
        content = json.loads(self.rows()[-1]["content"])
        self.assertEqual(content["account"], B1)
        self.assertEqual(content["session"], "B1")

    def test_MAP4b_an_account_already_bound_to_another_session(self):
        self.ready_for_replacement()
        self.refused("ars", self.bind, "builder", "B2", B1, generation=2)  # (b) B1's account names session B1
        self.bind("builder", "B2", B2, generation=2)

    def test_MAP5_after_revoke_the_existing_refusal(self):
        self.explicit()
        self.ok(self.run_wf("grant", "revoke", "j", "--reason", "r", "--turn", "owner turn j", role="coordinator",
                            session="C1"))
        self._after_end_the_existing_refusal()

    def test_MAP5_after_expire_the_existing_refusal(self):
        self.explicit()
        self.ok(self.run_wf("grant", "expire", "j", "--reason", "r", "--turn", "owner turn j", role="coordinator",
                            session="C1"))
        self._after_end_the_existing_refusal()

    def _after_end_the_existing_refusal(self) -> None:
        direct = self.raised(lambda: self.intent(None, "B1"))
        brokered = self.raised(lambda: self.intent(B1, "B1"))
        self.assertEqual((type(brokered), getattr(brokered, "reason", None), str(brokered)),
                         (type(direct), getattr(direct, "reason", None), str(direct)))
        self.assertNotIn("caller-", str(brokered))
        self.refused("caller-mismatch", self.intent, B2, "B1")  # the map still decides the account

    def raised(self, fn: Callable[[], Any]) -> WfError:
        """The refusal a claim gets; it writes nothing."""
        before = self.state()
        with self.assertRaises(WfError) as cm:
            fn()
        self.assertEqual(self.state(), before)
        return cm.exception


class BindWithoutMap(_Guard):
    grant = GRANT_NOMAP

    def test_MAP4_an_account_without_a_map_is_rejected_ars(self):
        self.new_job()
        self.activate()
        self.bind("coordinator", "C1")
        self.refused("ars", self.bind, "builder", "B1", B1)
        self.bind("builder", "B1")
        self.assertNotIn("account", json.loads(self.rows()[-1]["content"]))


# ===================================================================================================== BRK-4, W-1

class InlineOnly(_Guard):
    """BRK-4 (F3-ACC): with a caller, inputs are JSON text only; nothing is opened or stat'ed (§1.5)."""

    def setUp(self) -> None:
        super().setUp()
        self.explicit()
        self.file = os.path.join(self.dir, "content.json")
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write(INTENT)

    def append(self, who: str | None, content: str) -> dict[str, Any]:
        return self.call(commands.append, "j", "intent", content, reply_to=None, grant_ref=None, who=who,
                         role="builder", session="B1")

    def test_a_file_name_is_not_read_and_is_usage(self):
        seen: list[str] = []
        real_isfile, real_getsize, real_open = os.path.isfile, os.path.getsize, builtins.open

        def spy(real: Callable[..., Any]) -> Callable[..., Any]:
            def f(p: Any, *a: Any, **k: Any) -> Any:
                seen.append(str(p))
                return real(p, *a, **k)
            return f

        with mock.patch("os.path.isfile", spy(real_isfile)), mock.patch("os.path.getsize", spy(real_getsize)), \
                mock.patch("builtins.open", spy(real_open)):
            e = self.refused("usage", self.append, B1, self.file)
        self.assertIn("not a file and not JSON", str(e))
        self.assertNotIn(self.file, seen)

    def test_non_json_text_is_usage_and_bad_json_is_malformed(self):
        e = self.refused("usage", self.append, B1, "hello")
        self.assertIn("not a file and not JSON", str(e))
        self.refused("malformed", self.append, B1, '{"a": 1e999}')
        self.refused("malformed", self.append, B1, '{"a": NaN}')
        self.refused("malformed", self.append, B1, '{"a": 1, "a": 2}')
        self.refused("malformed", self.append, B1, json.dumps({"note": "x" * commands.MAX_JSON_INPUT_BYTES}))

    def test_inline_json_is_accepted(self):
        self.assertEqual(self.append(B1, INTENT).get("outcome"), "COMMITTED")

    def test_W1_direct_mode_still_reads_the_file(self):
        self.assertEqual(self.append(None, self.file).get("outcome"), "COMMITTED")


class DirectMode(_Guard):
    """W-1: `caller=None` runs no check; behaviour equals ff3cd2f (the existing suite is the rest of the evidence)."""

    def test_W1_the_spoofs_commit_in_direct_mode_as_today(self):
        self.explicit()
        res = self.call(commands.log_intervention, "j", label="l", necessity="necessary", minutes="1", note="",
                        role="owner", session="O-x")
        self.assertEqual(res.get("outcome"), "COMMITTED", res)
        self.assertEqual(self.intent(None, "B1").get("outcome"), "COMMITTED")

    def test_W1_no_caller_reads_no_account(self):
        self.explicit()
        with mock.patch.object(peer, "own_account", side_effect=AssertionError("own_account read")):
            self.assertEqual(self.intent(None, "B1").get("outcome"), "COMMITTED")


if __name__ == "__main__":
    unittest.main()
