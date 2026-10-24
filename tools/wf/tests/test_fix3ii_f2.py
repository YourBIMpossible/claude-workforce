"""PACKAGE-FIX3-II-F2, lane F2-A (WF-246): fix 1's follow-up for the D5 review's code items (Twin WF-242 S1, S5, S6, S9).

- S1: a brokered worker call that ends commit-uncertain runs no `resolve` (`resolve` is not brokered for workers);
  direct mode and the owner's account keep the automatic resolve.
- S5: the broker's log line records what was sent: `code` and `reason` from the encoded response, and its `truncated`.
- S6: `bind`'s account map and `check_caller`'s `map_in_force` name the same grant, across grant, re-grant, expiry and
  revoke (a guard: the code already holds it).
- S9: a send timeout's text says the broker received no complete request and ran nothing; the code stays
  `broker-unavailable`.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import test_wf  # noqa: F401  (puts tools/wf on sys.path)

import fix3ii_fakes as fakes
import test_fix3ii_broker as tb
import wf
from wfcore import accounts, bind, client, commands, peer, wire
from wfcore import db as dbm
from wfcore.db import WfCommitUncertain, WfError

FAKE = fakes.FAKE
WFCORE = wire.wfcore_sha256(test_wf.WF_DIR)
ADDR = fakes.address("f2")
G = ["--role=builder", "--session=B1", "--generation=1", "--msg-id=m1"]
INTENT = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"})
ATTEMPT = "a" * 32


def caller(who: str) -> peer.Caller:
    return peer.Caller(peer=peer.PeerIdentity(account=FAKE[who], pid=4242, platform=fakes.PLATFORM),
                       broker_account=FAKE["own"], broker_wfcore=WFCORE)


def _uncertain(*_a, **_k):
    raise WfCommitUncertain("injected: the commit's outcome is unknown", job="j", seq=3, attempt_id=ATTEMPT)


class S1NoResolveInBrokeredWorkerCalls(tb._Env, test_wf.Base):
    """D5 S1 (wf.py:538-545): `_dispatch` ran `commands.resolve` inside a brokered worker's call."""

    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def test_s1_a_brokered_worker_write_that_ends_uncertain_runs_no_resolve(self):
        for who in ("B1", "R1"):
            with self.subTest(who=who), mock.patch.object(commands, "append", side_effect=_uncertain), \
                    mock.patch.object(commands, "resolve") as resolve:
                code, res = wf.run_brokered(G + ["append", "j", "--type=intent"], {"--content": INTENT},
                                            db=os.path.abspath(self.db), caller=caller(who))
            resolve.assert_not_called()
            self.assertEqual((code, res["code"]), (1, "commit-uncertain"), res)
            self.assertNotIn("auto_resolve", res)
            self.assertEqual(res["action"], f"wf resolve j 3 --attempt {ATTEMPT}")

    def test_s1_the_owner_account_and_direct_mode_keep_the_automatic_resolve(self):
        reply = {"outcome": "NOT-COMMITTED"}
        with mock.patch.object(commands, "append", side_effect=_uncertain), \
                mock.patch.object(commands, "resolve", return_value=reply) as resolve:
            _, owner = wf.run_brokered(G + ["append", "j", "--type=intent"], {"--content": INTENT},
                                       db=os.path.abspath(self.db), caller=caller("own"))
            _, direct = wf.run(["--db", self.db, "--role", "builder", "--session", "B1", "append", "j", "--type",
                                "intent", "--content", INTENT])
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual((owner["auto_resolve"], direct["auto_resolve"]), (reply, reply))


class S5LogRecordsWhatWasSent(tb._Broker):
    """D5 S5 (broker.py:150-155): the log took `code` and `reason` from the result before encoding, and dropped
    `truncated`."""

    def good(self) -> bytes:
        return wire.frame(wire.encode_request({"v": 1, "op": "run", "argv": G + ["whoami"], "inputs": {},
                                               "wfcore": WFCORE}))

    def last_line(self) -> dict:
        self.wait_for(lambda: len(self.log_lines()) >= 1, "the log line")
        return self.log_lines()[-1]

    def test_s5_an_unencodable_result_is_logged_as_the_internal_error_sent(self):
        self.serving(run=self.recording_run(reply=(0, {"ok": True, "outcome": "COMMITTED", "x": float("nan")})))
        resp = self.raw("B1", self.good())
        self.assertEqual((resp["exit"], resp["result"]["code"]), (4, "internal-error"))
        line = self.last_line()
        self.assertEqual((line["exit"], line["code"], line["reason"], line.get("truncated")),
                         (4, "internal-error", None, False))

    def test_s5_the_log_carries_the_responses_truncated_flag(self):
        self.serving(run=self.recording_run(reply=(0, {"ok": True, "outcome": "COMMITTED", "seq": 3,
                                                       "blob": "x" * 5000})))
        with mock.patch.object(wire, "MAX_RESPONSE_BYTES", 2000):
            cut = self.raw("B1", self.good())
        self.assertTrue(cut["truncated"])
        line = self.last_line()
        self.assertEqual((line["code"], line.get("truncated")), (cut["result"]["outcome"], True))

    def test_s5_an_uncut_response_logs_truncated_false(self):
        self.serving(run=self.recording_run())
        resp = self.raw("B1", self.good())
        self.assertFalse(resp["truncated"])
        line = self.last_line()
        self.assertEqual((line["code"], line.get("truncated")), (resp["result"]["outcome"], False))

    def test_s5_a_refusal_is_logged_from_the_response_sent(self):
        self.serving(run=self.recording_run(reply=(1, {"ok": False, "code": "rejected",
                                                       "error": "rejected(caller-mismatch): no"})))
        resp = self.raw("B1", self.good())
        line = self.last_line()
        self.assertEqual((line["code"], line["reason"], line.get("truncated")),
                         (resp["result"]["code"], "caller-mismatch", False))


ROLES = {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
         "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
         "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["tools/wf/**"]}}


def _grant(grant_id: str, builders: list[str], reviewers: list[str]) -> dict:
    return dict(test_wf.GRANT, grant_id=grant_id,
                ars={"roles": ROLES, "accounts": {"builder": builders, "reviewer": reviewers}})


class S6BindAndCheckCallerNameTheSameGrant(tb._Env, test_wf.Base):
    """D5 S6 (commands.py:1198-1205; accounts.py:93-101): every `bind` that `validate_bind` accepts used the account map
    of the same grant as `map_in_force`; where no grant is in force, `bind` refuses. A guard: green at `16b4543`."""

    def setUp(self) -> None:
        super().setUp()
        self.calls: list[dict] = []
        real = bind.validate_bind

        def spy(conn, job, **kw):
            entry = {"bind_map": kw["account_map"], "in_force": accounts.map_in_force(dbm.rows(conn, job)),
                     "accepted": False}
            self.calls.append(entry)
            out = real(conn, job, **kw)
            entry["accepted"] = True
            return out

        self.enterContext(mock.patch.object(bind, "validate_bind", spy))

    def w(self, *argv: str) -> tuple[int, dict]:
        return wf.run(["--db", self.db, "--role", "coordinator", "--session", "C1", *argv])

    def grant_file(self, name: str, g: dict) -> str:
        path = os.path.join(self.dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(g, fh)
        return path

    def propose(self, name: str, g: dict) -> str:
        code, res = self.w("grant", "propose", "j", "--grant", self.grant_file(name, g))
        self.assertEqual(code, 0, res)
        return res["block_hash"][:8]

    def activate_hash(self, h: str) -> None:
        code, res = self.w("grant", "activate", "j", "--owner-hash", h, "--quote", f"approve {h}", "--turn", "owner turn")
        self.assertEqual(code, 0, res)

    def bind(self, name: str, role: str, session: str, account: str, accepted: bool) -> None:
        """One bind attempt, checked: accepted only with the map `map_in_force` names; refused otherwise."""
        before = len(self.calls)
        code, res = self.w("bind", "j", "--bind-role", role, "--bind-session", session, "--bind-account", account)
        mine = self.calls[before:]
        with self.subTest(step=name):
            self.assertEqual(code == 0, accepted, res)
            if accepted:
                self.assertEqual([c["accepted"] for c in mine], [True])
                self.assertIsNotNone(mine[0]["bind_map"])
                self.assertEqual(mine[0]["bind_map"], mine[0]["in_force"])  # the same grant's map
            else:
                self.assertFalse(any(c["accepted"] for c in mine))
                self.assertTrue(all(c["bind_map"] is None for c in mine), mine)  # no grant in force: no map is used

    def test_s6_bind_and_map_in_force_agree_across_grant_regrant_expiry_and_revoke(self):
        self.new_job()
        self.activate_hash(self.propose("g1", _grant("G1", [FAKE["B1"]], [FAKE["R1"]])))
        code, res = self.w("bind", "j", "--bind-role", "coordinator", "--bind-session", "C1")  # the first binding
        self.assertEqual(code, 0, res)
        self.bind("grant G1 in force", "builder", "B1", FAKE["B1"], True)
        h2 = self.propose("g2", _grant("G2", [FAKE["B1"], FAKE["B2"]], [FAKE["R1"]]))
        self.bind("G2 proposed: G1 voided", "reviewer", "R1", FAKE["R1"], False)
        self.activate_hash(h2)
        code, res = wf.run(["--db", self.db, "--role", "builder", "--session", "B1", "--generation", "1", "append", "j",
                            "--type", "delivered", "--content", '{"claims":[{"claim":"c","evidence":["e"]}]}'])
        self.assertEqual(code, 0, res)  # a reviewer is bound only after delivery
        self.bind("re-grant: G2 in force", "reviewer", "R1", FAKE["R1"], True)
        code, res = self.w("grant", "expire", "j", "--reason", "f2 s6", "--turn", "owner turn for job j: expire G2")
        self.assertEqual(code, 0, res)
        self.bind("G2 expired", "builder", "B2", FAKE["B2"], False)
        self.activate_hash(self.propose("g3", _grant("G3", [FAKE["B2"]], [FAKE["R1"]])))
        code, res = self.w("grant", "revoke", "j", "--reason", "f2 s6", "--turn", "owner turn for job j: revoke G3")
        self.assertEqual(code, 0, res)
        self.bind("G3 revoked", "builder", "B2", FAKE["B2"], False)
        self.assertEqual([c["accepted"] for c in self.calls], [True, True, True])  # C1, then B1 under G1, R1 under G2
        self.assertEqual(self.calls[0]["bind_map"], self.calls[0]["in_force"])
        self.assertNotEqual(self.calls[1]["bind_map"], self.calls[2]["bind_map"])  # the re-grant changed the map in force


class S9SendTimeoutText(tb._Env):
    """D5 S9 (client.py:64-65): a send timeout is `broker-unavailable`, but part of the frame may have gone out."""

    def test_s9_a_send_timeout_says_no_complete_request_and_nothing_ran(self):
        ch = fakes.ScriptChannel(send_error=TimeoutError("send: deadline reached"))
        code, res = client.call(ADDR, {"v": 1, "op": "run", "argv": G + ["whoami"], "inputs": {}, "wfcore": WFCORE},
                                connect=lambda address, deadline: ch)
        self.assertEqual((code, res["code"], res["reason"], res["address"]), (1, "broker-unavailable", "busy", ADDR))
        self.assertEqual(res["error"], f"the request was not sent in full within {client.CONNECT_BOUND_S:g} s: the "
                                       f"broker received no complete request and ran nothing "
                                       f"(TimeoutError: send: deadline reached)")
        self.assertTrue(ch.closed)


if __name__ == "__main__":
    unittest.main()
