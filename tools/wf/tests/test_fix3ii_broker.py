"""PACKAGE-FIX3-II, lane F3-BRK (WF-195): the broker, `wf.run_brokered` and `wfbroker.py`. INTERFACE-CONTRACT-FIX3-II r2
sections 1.4, 1.8, 2.5, 3.5, 4.5 and 5; the 03:09Z NOTE (D27's rotation touches only the broker's own log files).

The broker runs in-process in the runner's own account, over fix3ii_fakes' MemoryTransport, with fake peers (the FP
harness). On F3-BRK's own head a brokered write uses `Broker(run=fakes.direct_run)`, which runs `wf.run` directly with
no caller (section 6.3): those tests cover the transport, the framing, re-send and serial dispatch, not the account
check, which is F3-ACC's and the E2E lane's. Reads, `whoami`, the routing refusals and every broker refusal run through
the production `run`, `wf.run_brokered`.

Rows: BRK-2, BRK-5, BRK-6, BRK-7, BRK-8, BRK-9, BRK-10, BRK-11, BRK-12, ID-8, ID-10 (no job), W-3, W-4, and section
2.5's U test. RA rows are declared skips.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import struct
import threading
import time
import unittest
from unittest import mock

import test_wf  # noqa: F401  (puts tools/wf on sys.path)

import fix3ii_fakes as fakes
import wf
import wfbroker
from wfcore import broker, client, commands, peer, wire
from wfcore import db as dbm

SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
RA_REASON = "real accounts: RA lane parked (PROTOCOL NOTE 2026-10-21T01:48Z)"
FAKE = fakes.FAKE
WFCORE = wire.wfcore_sha256(test_wf.WF_DIR)
CLIENTS = ("B1", "B2", "R1")
TS = re.compile(r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z\Z")


def caller(who: str) -> peer.Caller:
    return peer.Caller(peer=peer.PeerIdentity(account=FAKE[who], pid=4242, platform=fakes.PLATFORM),
                       broker_account=FAKE["own"], broker_wfcore=WFCORE)


def intent_content(note: str = "n", name: str = "a") -> str:
    return json.dumps({"path": f"jobs/j/work/{name}.txt", "sha_before": "", "note": note})


class _Env(unittest.TestCase):
    """Every new test scrubs the route and fault variables (contract section 6.3) and records WF_BROKER unset (O5)."""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        super().setUp()
        self.assertNotIn("WF_BROKER", os.environ)


class _Broker(_Env, test_wf.Base):
    def setUp(self) -> None:
        super().setUp()
        self.transport = fakes.MemoryTransport()
        self.address = fakes.address("broker")
        self.log_path = os.path.join(self.dir, "runtime", "broker", "broker-log.jsonl")
        self.ran: list[list[str]] = []

    def recording_run(self, reply=(0, {"ok": True, "outcome": "RAN"})):
        def run(argv, inputs, *, db, caller):
            self.ran.append(list(argv))
            return reply
        return run

    def make(self, run=None, listener=None) -> broker.Broker:
        clients = [FAKE[c] for c in CLIENTS]
        return broker.Broker(db=os.path.abspath(self.db),
                             listener=listener or self.transport.listen(self.address, clients),
                             own_account=FAKE["own"], wfcore=WFCORE, client_accounts=clients,
                             run=run or wf.run_brokered, log_path=self.log_path)

    def serving(self, run=None, listener=None) -> fakes.RunningBroker:
        return self.enterContext(fakes.RunningBroker(self.make(run, listener), self.transport, self.address))

    def as_(self, who: str, pid: int = 4242):
        return self.transport.connector((FAKE[who], pid))

    def call(self, who: str, argv: list[str], inputs=None, *, wfcore: str = WFCORE, op: str = "run"):
        return client.call(self.address, {"v": 1, "op": op, "argv": argv, "inputs": inputs or {}, "wfcore": wfcore},
                           connect=self.as_(who))

    def wf_as(self, who: str, *argv: str) -> tuple[int, dict]:
        """`wf --broker <address> …` as `who`: wf.run's own broker route, over the memory transport."""
        with mock.patch.object(peer, "connect", self.as_(who)):
            return wf.run(["--broker", self.address, *argv])

    def request_for(self, *argv: str) -> dict:
        """The request wf.run would send for `argv` (captured, not sent)."""
        sent = []
        with mock.patch.object(client, "call", lambda a, r, connect=None: (sent.append(r), (0, {}))[1]):
            wf.run(["--broker", self.address, *argv])
        return sent[0]

    def raw(self, who: str, data: bytes, *, read: bool = True) -> dict | None:
        """Send raw bytes as `who`; return the decoded response, or None when the broker closed without one."""
        ch = self.transport.connect(self.address, time.monotonic() + 5, (FAKE[who], 1))
        try:
            ch.send_all(data, time.monotonic() + 5)
            if not read:
                return None
            try:
                return wire.check_response(wire.decode(wire.read_frame(ch, time.monotonic() + 10,
                                                                         wire.MAX_RESPONSE_BYTES)))
            except EOFError:
                return None
        finally:
            ch.close()

    def log_lines(self, path: str | None = None) -> list[dict]:
        path = path or self.log_path
        if not os.path.exists(path):
            return []
        with open(path, encoding="ascii") as fh:
            return [json.loads(line) for line in fh]

    def wait_for(self, predicate, what: str, timeout: float = 10.0) -> None:
        end = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > end:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.02)

    def rows_with(self, msg_id: str) -> list[dict]:
        conn = dbm.connect(self.db, create=False)
        try:
            with dbm.read_snapshot(conn):
                return [r for r in dbm.rows(conn, "j") if r["msg_id"] == msg_id]
        finally:
            conn.close()


class ReSend(_Broker):
    """BRK-2: a lost response, then a re-send with the same msg_id; exactly one row either way."""

    def append_argv(self, msg_id: str) -> list[str]:
        return ["--role", "builder", "--session", "B1", "--msg-id", msg_id, "append", "j", "--type", "intent",
                "--content", intent_content(msg_id)]

    def test_brk2_the_client_closes_before_the_response(self):
        self.new_job()
        self.activate()
        self.serving(run=fakes.direct_run)
        req = self.request_for(*self.append_argv("m-brk2"))
        self.raw("B1", wire.frame(wire.encode_request(req)), read=False)
        self.wait_for(lambda: len(self.log_lines()) == 1, "the first connection's log line")
        self.assertEqual(len(self.rows_with("m-brk2")), 1)  # the broker committed the first send
        code, res = self.wf_as("B1", *self.append_argv("m-brk2"))
        self.assertEqual((code, res["outcome"]), (0, "DUPLICATE-IGNORED"), res)
        self.assertEqual(len(self.rows_with("m-brk2")), 1)

    def test_brk2_variant_the_client_closes_partway_through_the_frame(self):
        self.new_job()
        self.activate()
        self.serving(run=fakes.direct_run)
        data = wire.frame(wire.encode_request(self.request_for(*self.append_argv("m-brk2v"))))
        self.raw("B1", data[: len(data) // 2], read=False)
        self.wait_for(lambda: len(self.log_lines()) == 1, "the partial connection's log line")
        line = self.log_lines()[0]
        self.assertEqual((line["op"], line["exit"], line["code"], line["peer"]), (None, None, None, None))
        self.assertEqual(self.rows_with("m-brk2v"), [])  # nothing was dispatched
        code, res = self.wf_as("B1", *self.append_argv("m-brk2v"))
        self.assertEqual((code, res["outcome"]), (0, "COMMITTED"), res)
        self.assertEqual(len(self.rows_with("m-brk2v")), 1)

    def test_brk2_a_lost_response_while_the_client_lives(self):
        self.new_job()
        self.activate()
        running = self.serving(run=fakes.direct_run,
                               listener=fakes.BrokenSendListener(self.transport.listen(self.address, [FAKE["B1"]])))
        code, res = self.wf_as("B1", *self.append_argv("m-brk2l"))
        self.assertEqual((code, res["code"], res["msg_id"], res["action"]),
                         (1, "broker-response-lost", "m-brk2l", "re-send the same command with --msg-id m-brk2l"))
        self.wait_for(lambda: len(self.log_lines()) == 1, "the log line")
        line = self.log_lines()[0]
        self.assertEqual((line["exit"], line["code"]), (0, "response-unsent"))  # A1 item 1: the formed exit stands
        self.assertEqual(len(self.rows_with("m-brk2l")), 1)
        running.stop()  # its answer is lost too, but the broker stops
        self.assertFalse(running.alive)
        self.serving(run=fakes.direct_run)
        code, res = self.wf_as("B1", *self.append_argv("m-brk2l"))
        self.assertEqual((code, res["outcome"]), (0, "DUPLICATE-IGNORED"), res)
        self.assertEqual(len(self.rows_with("m-brk2l")), 1)


class Routing(_Broker):
    """BRK-5: the not-brokered refusals for a worker account; the own account runs every command."""

    NOT_BROKERED = (
        ("job abandon", ["job", "abandon", "j", "--reason", "r"]),
        ("resolve", ["resolve", "j", "1"]),
        ("export", ["export", "j"]),
        ("checkpoint", ["checkpoint", "j"]),
        ("observe", ["observe", "j"]),
        ("backup", ["backup", "j", "--to", "backup-never.db"]),
        ("verify", ["verify", "j", "--restored", "restored-never.db"]),
        ("verify", ["verify", "j", "--gate-file", "gate-never.py"]),
        ("verify", ["verify", "j", "--gate-sha", "a" * 64]),
        ("gate print-install", ["gate", "print-install", "--hooks-dir", "h", "--root", "r"]),
    )

    def test_brk5_a_worker_is_refused_each_not_brokered_command(self):
        self.new_job()
        self.activate()
        before = fakes.ledger_state(self.db)
        self.serving()
        for command, line in self.NOT_BROKERED:
            for who in ("B1", "R1"):
                with self.subTest(command=line, who=who):
                    code, res = self.wf_as(who, "--role", "builder", "--session", "B1", *line)
                    self.assertEqual((code, res), (1, {
                        "ok": False, "code": "rejected", "command": command,
                        "error": f"rejected(not-brokered): {command} is not served to a worker account; the owner "
                                 f"account runs it"}))
        self.assertEqual(fakes.ledger_state(self.db), before)
        self.assertFalse(os.path.exists("backup-never.db"))

    def test_brk5_the_own_account_runs_every_command(self):
        """On this head the own account's role-free commands run for real; the role-bearing writes reach `commands`
        with the caller (next test), where F3-ACC's check runs on the combined candidate."""
        self.new_job()
        self.activate()
        self.serving()
        for line in (["status", "j"], ["digest", "j"], ["verify", "j"]):
            with self.subTest(line=line):
                self.assertEqual(self.wf_as("own", "--role", "coordinator", "--session", "C1", *line),
                                 self.run_wf(*line, role="coordinator", session="C1"))
        for line in (["checkpoint", "j"], ["observe", "j"], ["export", "j"], ["resolve", "j", "1"],
                     ["gate", "print-install", "--hooks-dir", os.path.join(self.dir, "h"), "--root", self.dir],
                     ["backup", "j", "--to", os.path.join(self.dir, "b.db")], ["job", "abandon", "j", "--reason", "r"]):
            with self.subTest(line=line):
                code, res = self.wf_as("own", "--role", "coordinator", "--session", "C1", *line)
                self.assertNotIn("not-brokered", json.dumps(res))
                self.assertNotEqual(res.get("code"), "internal-error", res)
                if line[0] != "resolve":  # no pending marker: resolve answers as it does directly
                    self.assertEqual(code, 0, res)

    def test_brk5_role_bearing_writes_reach_commands_with_the_caller(self):
        g = ["--role=builder", "--session=B1", "--generation=1", "--msg-id=m1"]
        for who in ("B1", "own"):
            c = caller(who)
            with self.subTest(who=who), mock.patch.object(commands, "append", return_value={"outcome": "X"}) as m:
                code, res = wf.run_brokered(g + ["append", "j", "--type=intent"], {"--content": "{}"},
                                            db=os.path.abspath(self.db), caller=c)
                self.assertEqual(code, 0, res)
                self.assertIs(m.call_args.kwargs["caller"], c)
                self.assertEqual(m.call_args.args[3], "{}")  # the inline text, never a path
        with mock.patch.object(commands, "bind_cmd", return_value={"outcome": "X"}) as m:
            wf.run_brokered(g + ["bind", "j", "--bind-role=builder", "--bind-session=B2", f"--bind-account={FAKE['B2']}"],
                            {}, db=os.path.abspath(self.db), caller=caller("own"))
        self.assertEqual((m.call_args.kwargs["bind_account"], m.call_args.kwargs["caller"]), (FAKE["B2"], caller("own")))
        with mock.patch.object(commands, "status", return_value={"job": "j"}) as m:
            wf.run_brokered(g + ["status", "j"], {}, db=os.path.abspath(self.db), caller=caller("B1"))
        self.assertNotIn("caller", m.call_args.kwargs)  # reads take no caller (section 3.3)


class Refusals(_Broker):
    """Section 1.8's refusals, in order: BRK-7 (the frame), BRK-8 (the peer), admission, BRK-6 (the version)."""

    G = ["--role=builder", "--session=B1", "--generation=1", "--msg-id=m1", "whoami"]

    def good(self) -> bytes:
        return wire.frame(wire.encode_request({"v": 1, "op": "run", "argv": self.G, "inputs": {}, "wfcore": WFCORE}))

    def test_brk7_the_frame(self):
        self.new_job()
        before = fakes.ledger_state(self.db)
        self.serving(run=self.recording_run())
        bad = (
            ("not JSON", wire.frame(b"{nope"), "B1"),
            ("an oversize length", struct.pack(">I", wire.MAX_REQUEST_BYTES + 1), None),
            ("an unknown v", wire.frame(json.dumps({"v": 2, "op": "run", "argv": [], "inputs": {}, "wfcore": WFCORE}).encode()), "B1"),
            ("a missing field", wire.frame(json.dumps({"v": 1, "op": "run", "argv": [], "inputs": {}}).encode()), "B1"),
            ("not an object", wire.frame(b"[]"), "B1"),
            ("a duplicate key", wire.frame(b'{"v":1,"v":1}'), "B1"),
        )
        for name, data, peer_seen in bad:
            with self.subTest(case=name):
                resp = self.raw("B1", data)
                self.assertEqual((resp["exit"], resp["result"]["code"]), (1, "rejected"))
                self.assertTrue(resp["result"]["error"].startswith("rejected(wire): "), resp)
                self.assertEqual(resp["peer"], None if peer_seen is None else {"account": FAKE[peer_seen]})
        self.assertEqual(self.ran, [])
        self.assertEqual(fakes.ledger_state(self.db), before)
        self.assertEqual(self.call("B1", self.G), (0, {"ok": True, "outcome": "RAN"}))  # it keeps serving

    def test_brk8_the_peer_cannot_be_read(self):
        listener = fakes.ScriptedPeerListener(self.transport.listen(self.address, []),
                                              [OSError("ImpersonateNamedPipeClient failed"), (FAKE["own"], 1)])
        self.serving(run=self.recording_run(), listener=listener)
        resp = self.raw("B1", self.good())
        self.assertEqual((resp["exit"], resp["peer"], resp["result"]["account"]), (1, None, None))
        self.assertEqual(resp["result"]["error"], "rejected(caller-unattributed): the peer's account cannot be read: "
                                                  "ImpersonateNamedPipeClient failed")
        self.assertEqual(self.ran, [])

    def test_an_unlisted_account_is_not_admitted(self):
        self.serving(run=self.recording_run())
        resp = self.raw("unlisted", self.good())
        self.assertEqual((resp["exit"], resp["peer"]), (1, {"account": FAKE["unlisted"]}))
        self.assertEqual(resp["result"], {"ok": False, "code": "rejected", "account": FAKE["unlisted"],
                                          "error": "rejected(caller-unattributed): this account is not admitted by "
                                                   "the broker"})
        self.assertEqual(self.ran, [])

    def test_brk6_a_different_wfcore(self):
        self.serving(run=self.recording_run())
        self.assertEqual(self.call("B1", self.G, wfcore="b" * 64), (1, {
            "ok": False, "code": "rejected", "error": "rejected(version-mismatch): the client's wfcore differs from "
                                                      "the broker's",
            "client_wfcore": "b" * 64, "broker_wfcore": WFCORE}))
        self.assertEqual(self.ran, [])

    def test_the_read_bound(self):
        self.serving(run=self.recording_run())
        with mock.patch.object(broker, "READ_BOUND_S", 0.3):
            resp = self.raw("B1", struct.pack(">I", 100) + b"x" * 10)
        self.assertEqual(resp["result"]["error"], "rejected(wire): request incomplete within 0.3 s")
        self.assertEqual(self.ran, [])
        self.assertEqual(self.call("B1", self.G)[0], 0)

    def test_an_exception_in_run_is_an_internal_error(self):
        def run(argv, inputs, *, db, caller):
            raise ValueError("boom")
        self.serving(run=run)
        self.assertEqual(self.call("B1", self.G), (4, {"ok": False, "code": "internal-error", "error": "ValueError: boom"}))
        self.assertEqual(self.call("B1", self.G)[0], 4)  # still serving

    def test_an_exception_outside_step_7_closes_logs_and_keeps_serving(self):
        self.serving(run=self.recording_run())
        real = wire.response
        with mock.patch.object(wire, "response", side_effect=RuntimeError("encoder down")):
            self.assertIsNone(self.raw("B1", self.good()))  # closed with no answer
        self.wait_for(lambda: len(self.log_lines()) == 1, "the log line")
        line = self.log_lines()[0]
        self.assertEqual((line["exit"], line["code"], line["cmd"]), (None, "internal-error", "whoami"))
        self.assertIs(wire.response, real)
        self.assertEqual(self.call("B1", self.G), (0, {"ok": True, "outcome": "RAN"}))

    def test_a_malformed_run_result_is_an_internal_error(self):
        self.serving(run=self.recording_run(reply=None))
        code, res = self.call("B1", self.G)
        self.assertEqual((code, res["code"]), (4, "internal-error"))
        self.assertTrue(res["error"].startswith("TypeError: "), res)

    def test_the_response_is_truncated_past_8_mib(self):
        self.serving(run=self.recording_run(reply=(0, {"ok": True, "outcome": "COMMITTED", "seq": 3, "blob": "x" * 5000})))
        with mock.patch.object(wire, "MAX_RESPONSE_BYTES", 2000):
            resp = self.raw("B1", self.good())
        self.assertTrue(resp["truncated"])
        self.assertEqual(resp["result"], {"ok": True, "outcome": "COMMITTED", "seq": 3})


class Stop(_Broker):
    """Section 1.8 step 5 and D4."""

    def test_only_the_own_account_stops_it_and_a_stop_is_not_version_checked(self):
        running = self.serving(run=self.recording_run())
        self.assertEqual(self.call("B1", [], op="stop"), (1, {
            "ok": False, "code": "rejected", "account": FAKE["B1"],
            "error": "rejected(caller-mismatch): only the broker's own account may stop it"}))
        self.assertTrue(running.alive)
        self.assertEqual(self.call("own", [], op="stop", wfcore="c" * 64), (0, {"ok": True, "outcome": "STOPPING"}))
        running._thread.join(10)
        self.assertFalse(running.alive)
        self.assertEqual(running.served, 2)
        code, res = self.call("B1", ["--role=b"])
        self.assertEqual((code, res["code"], res["reason"]), (1, "broker-unavailable", "no-endpoint"))


class Uncertain(_Broker):
    """BRK-10: the uncertain-write path through the broker, with a commit error injected through the broker's own
    environment on a fixtures/tmp ledger."""

    def test_brk10(self):
        self.new_job()
        self.activate()
        returned = []

        def run(argv, inputs, *, db, caller):
            if "resolve" in argv:  # the routing step refuses it for a worker before any dispatch
                return wf.run_brokered(argv, inputs, db=db, caller=caller)
            out = fakes.direct_run(argv, inputs, db=db, caller=caller)
            returned.append(out)
            return out

        self.serving(run=run)
        w = ["--role", "builder", "--session", "B1"]
        os.environ["WF_TEST_FAULTS"] = "commit-error:IOERR"
        code, res = self.wf_as("B1", *w, "append", "j", "--type", "intent", "--content", intent_content("u1"))
        del os.environ["WF_TEST_FAULTS"]
        self.assertEqual((code, res), returned[-1])  # the client gets the result unchanged
        self.assertEqual((res["code"], res["auto_resolve"]["outcome"]), ("commit-uncertain", "SKIPPED"))
        self.assertTrue(res["action"].startswith("wf resolve j "), res)
        code, nxt = self.wf_as("B1", *w, "append", "j", "--type", "intent", "--content", intent_content("u2"))
        self.assertEqual((code, nxt["code"]), (1, "uncertain-write-pending"), nxt)
        code, rr = self.wf_as("B1", *w, "resolve", "j", str(res["seq"]), "--attempt", res["attempt_id"])
        self.assertEqual((code, rr["code"], rr["command"]), (1, "rejected", "resolve"))
        self.assertTrue(rr["error"].startswith("rejected(not-brokered): "))
        code, decided = wf.run(["--db", self.db, "resolve", "j", str(res["seq"]), "--attempt", res["attempt_id"]])
        self.assertEqual((code, decided["outcome"]), (0, "NOT-COMMITTED"), decided)
        code, ok = self.wf_as("B1", *w, "append", "j", "--type", "intent", "--content", intent_content("u3"))
        self.assertEqual((code, ok["outcome"]), (0, "COMMITTED"), ok)


class Serial(_Broker):
    """BRK-12: two worker clients write at the same time; one request runs at a time."""

    def test_brk12(self):
        self.new_job()
        self.activate()
        spans: list[tuple[float, float, str]] = []

        def run(argv, inputs, *, db, caller):
            start = time.monotonic()
            out = fakes.direct_run(argv, inputs, db=db, caller=caller)
            time.sleep(0.2)
            spans.append((start, time.monotonic(), next(a for a in argv if a.startswith("--msg-id="))[9:]))
            return out

        self.serving(run=run)
        results: dict[str, tuple[int, dict]] = {}
        # one builder session from both accounts: on this head no account check runs, and two builder sessions at one
        # generation would be a claimant conflict, which is not what this row tests
        requests = {who: self.request_for("--role", "builder", "--session", "B1", "--msg-id", f"m-{who}", "append", "j",
                                          "--type", "intent", "--content", intent_content(who, who))
                    for who in ("B1", "B2")}
        gate = threading.Barrier(2, timeout=10)

        def client_(who: str) -> None:
            gate.wait()
            results[who] = client.call(self.address, requests[who], connect=self.as_(who))

        threads = [threading.Thread(target=client_, args=(w,), daemon=True) for w in ("B1", "B2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual({w: (c, r.get("outcome")) for w, (c, r) in results.items()},
                         {"B1": (0, "COMMITTED"), "B2": (0, "COMMITTED")}, results)
        first, second = sorted(spans)
        self.assertLessEqual(first[1], second[0])  # no overlap
        seqs = {w: [r["seq"] for r in self.rows_with(f"m-{w}")] for w in ("B1", "B2")}
        self.assertEqual([len(v) for v in seqs.values()], [1, 1])
        self.assertLess(seqs[first[2][2:]][0], seqs[second[2][2:]][0])  # in the order the broker took them


class ReadsAndWhoami(_Broker):
    """ID-8 (reads are not attributed), ID-10 without a job, W-3 (W's start check)."""

    def test_id8_reads_from_a_worker_answer_as_directly(self):
        self.new_job()
        self.activate()
        self.assertEqual(self.intent()[0], 0)
        self.serving()
        for line in (["status", "j"], ["digest", "j"], ["verify", "j"], ["verify"],
                     ["grant-check", "j", "--check-role", "builder", "--action", "write", "--path", "jobs/j/work/a.txt"]):
            for who in ("B1", "R1"):
                with self.subTest(line=line, who=who):
                    self.assertEqual(self.wf_as(who, "--role", "builder", "--session", "B1", *line),
                                     self.run_wf(*line))

    def test_id10_whoami_through_the_broker(self):
        self.serving()
        self.assertEqual(self.wf_as("B1", "whoami"), (0, {
            "ok": True, "mode": "broker", "account": FAKE["B1"], "own": False,
            "broker": {"account": FAKE["own"], "wfcore": WFCORE}, "db": os.path.abspath(self.db), "msg_id": None}))
        self.assertFalse(os.path.exists(self.db))  # nothing written, no ledger opened

    def test_w3_the_owners_start_check(self):
        self.serving()
        code, res = self.wf_as("own", "whoami")
        self.assertEqual(code, 0, res)
        self.assertEqual((res["account"], res["own"], res["broker"]), (FAKE["own"], True,
                                                                      {"account": FAKE["own"], "wfcore": WFCORE}))


class Recovery(_Broker):
    """W-4: with the broker stopped, W's direct status, verify, resolve and job abandon run, even if W's shell still
    names the broker (`--db` wins, section 1.9)."""

    def test_w4(self):
        self.new_job()
        self.activate()
        running = self.serving()
        self.assertEqual(running.stop(), (0, {"ok": True, "outcome": "STOPPING"}))
        os.environ["WF_TEST_FAULTS"] = "commit-error:IOERR"
        code, res = self.intent()
        del os.environ["WF_TEST_FAULTS"]
        self.assertEqual(res["code"], "commit-uncertain")
        os.environ["WF_BROKER"] = self.address
        with mock.patch.object(client, "call", side_effect=AssertionError("the broker route was taken")):
            self.assertEqual(wf.run(["--db", self.db, "status", "j"])[0], 0)
            self.assertIn("clean", wf.run(["--db", self.db, "verify", "j"])[1])
            code, rr = wf.run(["--db", self.db, "resolve", "j", str(res["seq"]), "--attempt", res["attempt_id"]])
            self.assertEqual((code, rr["outcome"]), (0, "NOT-COMMITTED"), rr)
            code, ab = wf.run(["--db", self.db, "job", "abandon", "j", "--reason", "w4"])
            self.assertEqual(code, 0, ab)


class Log(_Broker):
    """Section 3.5's log: one line per connection, every key present, never an argv value or an input; D27's
    rotation touches only the broker's own log files (the 03:09Z NOTE)."""

    def test_the_lines(self):
        self.new_job()
        self.activate()
        running = self.serving(run=fakes.direct_run)
        secret = "SECRET-CONTENT-9f2c"
        code, res = self.wf_as("B1", "--role", "builder", "--session", "B1", "--msg-id", "m-log", "append", "j",
                               "--type", "intent", "--content", intent_content(secret), "--grant-ref", "SECRET-REF-1d7e")
        self.assertEqual(code, 0, res)
        self.raw("unlisted", wire.frame(b"{}"))
        self.raw("B1", wire.frame(b"{nope"))
        running.stop()
        lines = self.log_lines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            self.assertEqual(list(line), list(broker.LOG_KEYS))
            self.assertRegex(line["ts"], TS)
            self.assertIsInstance(line["ms"], int)
        self.assertEqual({k: lines[0][k] for k in ("peer", "pid", "op", "cmd", "job", "msg_id", "exit", "code")},
                         {"peer": FAKE["B1"], "pid": 4242, "op": "run", "cmd": "append", "job": "j", "msg_id": "m-log",
                          "exit": 0, "code": "COMMITTED"})
        self.assertEqual({k: lines[1][k] for k in ("peer", "op", "cmd", "exit", "code")},
                         {"peer": FAKE["unlisted"], "op": None, "cmd": None, "exit": 1, "code": "rejected"})
        self.assertEqual({k: lines[2][k] for k in ("peer", "op", "exit", "code")},
                         {"peer": FAKE["B1"], "op": None, "exit": 1, "code": "rejected"})
        self.assertEqual({k: lines[3][k] for k in ("peer", "op", "cmd", "exit", "code")},
                         {"peer": FAKE["own"], "op": "stop", "cmd": None, "exit": 0, "code": "STOPPING"})
        with open(self.log_path, encoding="ascii") as fh:
            text = fh.read()
        for value in (secret, "SECRET-REF-1d7e", "intent", "jobs/j/work"):
            self.assertNotIn(value, text)

    def _snapshot(self, skip: set[str]) -> dict[str, tuple[int, int, str]]:
        out = {}
        for d, _, files in os.walk(self.dir):
            for name in files:
                p = os.path.join(d, name)
                if p in skip:
                    continue
                st = os.stat(p)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, self.dir)] = (st.st_size, st.st_mtime_ns, hashlib.sha256(fh.read()).hexdigest())
        return out

    def test_d27_rotation_touches_only_the_brokers_log_files(self):
        self.new_job()
        self.activate()
        self.assertEqual(self.intent()[0], 0)
        broker_dir = os.path.dirname(self.log_path)
        os.makedirs(broker_dir)
        for name in ("broker-log.2.jsonl", "notes.txt", "broker-log.jsonl.bak"):
            with open(os.path.join(broker_dir, name), "w", encoding="ascii") as fh:
                fh.write(f"planted {name}\n")
        own_files = {self.log_path, broker.previous_log_path(self.log_path)}
        self.assertEqual(broker.previous_log_path(self.log_path), os.path.join(broker_dir, "broker-log.1.jsonl"))
        before = self._snapshot(own_files)
        self.assertTrue(any(k.startswith("ledger") for k in before), before)
        with mock.patch.object(broker, "LOG_MAX_BYTES", 400):
            running = self.serving()
            for _ in range(12):
                self.assertEqual(self.wf_as("B1", "--role", "builder", "--session", "B1", "status", "j")[0], 0)
            running.stop()
        current, previous = self.log_lines(), self.log_lines(broker.previous_log_path(self.log_path))
        self.assertTrue(current and previous)
        self.assertLess(len(current) + len(previous), 13)  # the older rotated file was replaced, not kept
        self.assertLess(os.path.getsize(self.log_path), 400 + 300)
        self.assertEqual(self._snapshot(own_files), before)  # the ledger, its jobs and the planted files untouched


class BrokerProcess(_Broker):
    """wfbroker.py: the start checks (BRK-11 and the rest), the ready and stopped lines, BRK-9."""

    def setUp(self) -> None:
        super().setUp()
        self.emitted: list[dict] = []
        for target, attr, value in ((wfbroker, "_emit", self.emitted.append), (peer, "listen", self.transport.listen),
                                    (peer, "own_account", lambda: FAKE["own"]), (peer, "connect", self.as_("own"))):
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def argv(self, db: str | None = None, *extra: str) -> list[str]:
        return ["--db", db or self.db, "--address", self.address, *extra]

    def config_check(self, argv: list[str]) -> str:
        self.emitted.clear()
        self.assertEqual(wfbroker.main(argv), 1)
        self.assertEqual(len(self.emitted), 1, self.emitted)
        out = self.emitted[0]
        self.assertEqual((out["ok"], out["code"], list(out)), (False, "broker-config", ["ok", "code", "check", "error"]))
        return out["check"]

    def start_thread(self, argv: list[str]) -> tuple[threading.Thread, list[int]]:
        exits: list[int] = []
        t = threading.Thread(target=lambda: exits.append(wfbroker.main(argv)), daemon=True)
        t.start()
        self.wait_for(lambda: any(e.get("code") == "broker-ready" for e in self.emitted), "the ready line")
        return t, exits

    def test_brk11_the_brokers_environment(self):
        self.new_job()
        for name in ("WF_ROLE", "WF_SESSION", "WF_DB", "WF_BROKER"):
            for value in ("", "x"):
                with self.subTest(name=name, value=value):
                    os.environ[name] = value
                    self.assertEqual(self.config_check(self.argv()), "env")
                    del os.environ[name]
        never = os.path.join(test_wf.REPO, "ledger", "never-created.db")
        os.environ["WF_TEST_FAULTS"] = "delay-precommit:1"
        self.assertEqual(self.config_check(self.argv(never)), "faults")
        self.assertFalse(os.path.exists(never))
        self.assertEqual(self.transport.listeners, {})  # nothing was served

    def test_the_other_start_checks_in_order(self):
        self.new_job()
        other = os.path.join(self.dir, "other", "l.db")  # a readable ledger whose directory is not named "ledger"
        os.makedirs(os.path.dirname(other))
        shutil.copyfile(self.db, other)
        missing = os.path.join(self.dir, "ledger", "missing.db")
        self.assertEqual(self.config_check(self.argv(missing)), "ledger")
        self.assertFalse(os.path.exists(missing))
        self.assertEqual(self.config_check(self.argv(other)), "root")
        with mock.patch.object(peer, "own_account", side_effect=OSError("no token")):
            self.assertEqual(self.config_check(self.argv()), "own-account")
        for extra in (["--client-account", "x"], ["--client-account", FAKE["B1"], "--client-account", FAKE["B1"]],
                      ["--client-account", FAKE["own"]]):
            with self.subTest(extra=extra):
                self.assertEqual(self.config_check(self.argv(None, *extra)), "client-account")
        self.assertEqual(self.config_check(["--db", self.db, "--address", "bad"]), "address")
        os.environ["WF_ROLE"] = "x"  # the env check comes first
        self.assertEqual(self.config_check(self.argv(missing)), "env")
        self.assertEqual(self.transport.listeners, {})

    def test_brk9_one_broker_per_address_then_a_stop_and_a_restart(self):
        self.new_job()
        before = fakes.ledger_state(self.db)
        t, exits = self.start_thread(self.argv(None, "--client-account", FAKE["B1"]))
        ready = next(e for e in self.emitted if e.get("code") == "broker-ready")
        self.assertEqual(list(ready), ["ok", "code", "address", "pid", "account", "wfcore", "db"])
        self.assertEqual((ready["address"], ready["pid"], ready["account"], ready["wfcore"], ready["db"]),
                         (self.address, os.getpid(), FAKE["own"], WFCORE, os.path.abspath(self.db)))
        self.assertEqual(self.config_check(self.argv()), "address-in-use")
        self.assertEqual(self.wf_as("B1", "whoami")[1]["mode"], "broker")  # the first keeps serving
        self.emitted.clear()
        self.assertEqual(wfbroker.main(["--stop", "--address", self.address]), 0)
        t.join(10)
        self.assertEqual(exits, [0])
        self.assertIn({"ok": True, "outcome": "STOPPING"}, self.emitted)
        self.assertIn({"ok": True, "code": "broker-stopped", "served": 2}, self.emitted)
        self.emitted.clear()
        t, exits = self.start_thread(self.argv(None, "--client-account", FAKE["B1"]))
        self.assertEqual(self.wf_as("B1", "whoami")[0], 0)
        self.assertEqual(wfbroker.main(["--stop", "--address", self.address]), 0)
        t.join(10)
        self.assertEqual(exits, [0])
        self.assertEqual(fakes.ledger_state(self.db), before)

    def test_the_log_location_and_the_production_run(self):
        self.new_job()
        t, _ = self.start_thread(self.argv())
        self.assertEqual(self.wf_as("own", "whoami")[0], 0)
        wfbroker.main(["--stop", "--address", self.address])
        t.join(10)
        self.assertEqual(len(self.log_lines()), 2)  # <root>/runtime/broker/broker-log.jsonl


class Seams(_Env):
    """Section 2.5's U test, and the fake ids."""

    def test_the_serve_path_uses_peer_listen_and_nothing_names_the_fakes(self):
        with open(wfbroker.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        listens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "listen"]
        self.assertEqual([ast.unparse(n.func) for n in listens], ["peer.listen"])
        names = [os.path.join(test_wf.WF_DIR, "wf.py"), wfbroker.__file__]
        core = os.path.join(test_wf.WF_DIR, "wfcore")
        names += [os.path.join(core, n) for n in os.listdir(core) if n.endswith(".py")]
        for path in names:
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn("fix3ii_fakes", fh.read(), path)

    def test_fake_ids_are_in_the_hosts_form_and_distinct(self):
        self.assertEqual(len(set(FAKE.values())), len(FAKE))
        for name, value in FAKE.items():
            with self.subTest(name=name):
                self.assertTrue(peer.is_account_id(value, fakes.PLATFORM))

    def test_fake_ids_differ_from_this_processs_account(self):
        try:
            own = peer.own_account()
        except OSError as exc:
            self.skipTest(f"the platform module is not on this head (lanes F3-OSI-W/L): {exc}")
        self.assertNotIn(own, FAKE.values())


class RunBrokered(_Env, test_wf.Base):
    """Section 3.5's run_brokered, steps 1-4, without a broker."""

    G = ["--role=builder", "--session=B1", "--generation=1", "--msg-id=m1"]

    def rb(self, argv, inputs=None, who="B1"):
        return wf.run_brokered(argv, inputs or {}, db=os.path.abspath(self.db), caller=caller(who))

    def assertWire(self, res, prefix):
        self.assertEqual((res[0], res[1]["code"]), (1, "rejected"), res)
        self.assertTrue(res[1]["error"].startswith(f"rejected(wire): {prefix}"), res)

    def test_the_parse(self):
        self.assertWire(self.rb(self.G + ["nope"]), "argv does not parse: ")
        self.assertWire(self.rb(self.G + ["-h"]), "argv does not parse: ")
        self.assertWire(self.rb(self.G + ["append", "j", "--type=intent"]), "argv does not parse: ")  # no --content

    def test_section_1_2(self):
        for extra, kept in ((["--db=x"], "--db"), ([f"--broker={fakes.address('x')}"], "--broker"), (["--json"], "--json")):
            with self.subTest(kept=kept):
                self.assertWire(self.rb(self.G + extra + ["status", "j"]), f"argv carries {kept}, which the client keeps")
        for i, opt in enumerate(("--role", "--session", "--generation", "--msg-id")):
            with self.subTest(missing=opt):
                self.assertWire(self.rb(self.G[:i] + self.G[i + 1:] + ["status", "j"]), f"argv lacks {opt}")
        self.assertEqual(self.rb(self.G + ["status", "j", "--role=x"]),
                         (2, {"ok": False, "code": "usage", "error": "rejected(usage): option --role given more than once"}))
        self.assertEqual(self.rb(self.G + ["status", "j\udc80"])[1]["code"], "usage")

    def test_section_1_5_and_a1(self):
        for opt in ("--content={}", "--cont={}"):
            with self.subTest(opt=opt):
                self.assertWire(self.rb(self.G + ["append", "j", "--type=intent", opt], {"--content": "{}"}),
                                f"argv names file option(s) {opt.split('=')[0]}")
        self.assertWire(self.rb(self.G + ["status", "j"], {"--content": "{}"}), "")
        self.assertWire(self.rb(self.G + ["append", "j", "--type=intent"], {"--content": "{}", "--results": "{}"}), "")

    def test_routing_for_a_worker_and_the_own_account(self):
        self.assertEqual(self.rb(self.G + ["verify", "j", "--restored", "r.db"])[1]["code"], "rejected")
        for who in ("B1", "own"):
            with self.subTest(who=who):
                code, res = self.rb(self.G + ["verify", "j"], who=who)
                self.assertNotIn("not-brokered", json.dumps(res))
        code, res = self.rb(self.G + ["export", "j"], who="own")
        self.assertNotIn("not-brokered", json.dumps(res))

    def test_whoami(self):
        for who, own in (("B1", False), ("own", True)):
            with self.subTest(who=who):
                code, res = self.rb(self.G + ["whoami"], who=who)
                self.assertEqual((code, res["mode"], res["account"], res["own"]), (0, "broker", FAKE[who], own))


class RealAccountRows(unittest.TestCase):
    """Contract section 7: an RA row is a declared skip in this build, never a pass."""

    def test_id8_ra_direct_reads_under_the_acl(self):
        self.skipTest(RA_REASON)

    def test_id10_ra_whoami_as_each_account(self):
        self.skipTest(RA_REASON)

    def test_w3_ra_the_start_check_as_the_owner_account(self):
        self.skipTest(RA_REASON)


if __name__ == "__main__":
    unittest.main()
