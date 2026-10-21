"""PACKAGE-FIX3-II, lane F3-BRK (WF-195): the client side. INTERFACE-CONTRACT-FIX3-II r2 sections 1.4-1.6, 1.9, 2.3,
3.3, 4.2, 4.5 and 5.

wf.py's route table and the broker route (BRK-3, BRK-1), the request it builds and the argv rebuild's binding property
(section 1.9), inline inputs (BRK-4's client part), msg_id (section 1.6), ID-2's U part (the env-default and explicit
forms give one request), `client.call` and its refusals, MAP-4's U part (`--bind-account` parsed and passed), direct
mode calling `commands` exactly as at ff3cd2f (S1, W-1), and `whoami` in direct mode (ID-10 without a job).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import sys
import time
import unittest
from unittest import mock

import test_wf  # noqa: F401  (puts tools/wf on sys.path)

import fix3ii_fakes as fakes
import wf
from wfcore import client, commands, peer, wire
from wfcore.db import WfRejected

SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
RA_REASON = "real accounts: RA lane parked (PROTOCOL NOTE 2026-10-21T01:48Z)"
ADDR = fakes.address("client")
FAKE = fakes.FAKE
WFCORE = wire.wfcore_sha256(test_wf.WF_DIR)
DB_REQUIRED = "rejected(usage): --db required (give --db <path> or set WF_DB)"


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


class _Capture:
    """Stands in for `client.call`: records each (address, request) and answers a fixed reply."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.reply = (0, {"ok": True, "outcome": "CAPTURED"})

    def __call__(self, address: str, request: dict, *, connect=None) -> tuple[int, dict]:
        self.requests.append((address, request))
        return self.reply


class _Captured(_Env, test_wf.Base):
    def setUp(self) -> None:
        super().setUp()
        self.cap = _Capture()
        patcher = mock.patch.object(client, "call", self.cap)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self, *argv: str) -> dict:
        before = len(self.cap.requests)
        code, res = wf.run(["--broker", ADDR, *argv])
        self.assertEqual((code, res), self.cap.reply)
        self.assertEqual(len(self.cap.requests), before + 1)
        address, request = self.cap.requests[-1]
        self.assertEqual(address, ADDR)
        return request


class Route(_Captured):
    """BRK-3 and section 1.9's table."""

    def test_db_and_broker_together(self):
        for argv in (["--db", self.db, "--broker", ADDR, "status", "j"], ["--broker", ADDR, "status", "j", "--db", self.db]):
            with self.subTest(argv=argv):
                self.assertEqual(wf.run(argv), (2, {"ok": False, "code": "usage",
                                                    "error": "rejected(usage): give --db or --broker, not both"}))
        self.assertEqual(self.cap.requests, [])
        self.assertFalse(os.path.exists(self.db))

    def test_wf_db_and_wf_broker_together(self):
        os.environ.update(WF_DB=self.db, WF_BROKER=ADDR)
        self.assertEqual(wf.run(["status", "j"]), (2, {"ok": False, "code": "usage", "error":
                                                       "rejected(usage): WF_DB and WF_BROKER are both set (give --db or --broker)"}))
        self.assertEqual(self.cap.requests, [])

    def test_neither_is_todays_refusal(self):
        self.assertEqual(wf.run(["status", "j"]), (2, {"ok": False, "code": "usage", "error": DB_REQUIRED}))
        os.environ.update(WF_DB=" ", WF_BROKER="")  # empty and blank values count as unset (D20)
        self.assertEqual(wf.run(["status", "j"]), (2, {"ok": False, "code": "usage", "error": DB_REQUIRED}))
        self.assertEqual(self.cap.requests, [])

    def test_broker_given_twice(self):
        for argv in (["--broker", ADDR, "--broker", ADDR, "status", "j"], ["--broker", ADDR, "status", "j", f"--broker={ADDR}"]):
            with self.subTest(argv=argv):
                self.assertEqual(wf.run(argv), (2, {"ok": False, "code": "usage",
                                                    "error": "rejected(usage): option --broker given more than once"}))
        self.assertEqual(self.cap.requests, [])

    def test_wf_broker_alone_is_the_route(self):
        os.environ["WF_BROKER"] = ADDR
        self.assertEqual(wf.run(["status", "j"]), self.cap.reply)
        os.environ["WF_DB"] = "   "
        self.assertEqual(wf.run(["status", "j"]), self.cap.reply)
        self.assertEqual([a for a, _ in self.cap.requests], [ADDR, ADDR])

    def test_the_db_option_beats_wf_broker(self):
        self.new_job()
        os.environ["WF_BROKER"] = ADDR
        code, res = wf.run(["--db", self.db, "status", "j"])
        self.assertEqual(code, 0, res)
        self.assertEqual(self.cap.requests, [])

    def test_an_empty_db_option_falls_back_to_wf_db_as_before(self):
        self.new_job()
        os.environ["WF_DB"] = self.db
        code, res = wf.run(["--db", "", "status", "j"])
        self.assertEqual(code, 0, res)
        self.assertEqual(self.cap.requests, [])

    def test_a_bad_address_is_usage(self):
        other = "/run/wf.sock" if fakes.PLATFORM == "win32" else "relative/wf.sock"
        for bad in ("pipe-x", "", other):
            with self.subTest(bad=bad):
                code, res = wf.run(["--broker", bad, "status", "j"])
                self.assertEqual((code, res["code"]), (2, "usage"))
                self.assertIn(f"rejected(usage): --broker {bad!r} is not a ", res["error"])
        self.assertEqual(self.cap.requests, [])

    def test_the_broker_route_opens_no_ledger_and_calls_no_command(self):
        boom = mock.Mock(side_effect=AssertionError("called in the broker route"))
        with mock.patch.object(wf.paths, "db_path", boom), mock.patch.object(wf, "load_faults", boom), \
                mock.patch.object(commands, "append", boom), mock.patch.object(commands, "status", boom):
            self.sent("--role", "builder", "--session", "B1", "append", "j", "--type", "intent", "--content", '{"a":1}')
            self.sent("status", "j")
        boom.assert_not_called()
        self.assertFalse(os.path.exists(os.path.dirname(self.db)))


class RequestForm(_Captured):
    """Sections 1.2, 1.6 and 1.9: the request the client sends."""

    def test_the_request(self):
        r = self.sent("--role", "builder", "--session", "B1", "--msg-id", "m1", "append", "j", "--type", "intent",
                      "--content", '{"a":1}', "--reply-to", "5")
        self.assertEqual(r, {"v": 1, "op": "run", "inputs": {"--content": '{"a":1}'}, "wfcore": WFCORE,
                             "argv": ["--role=builder", "--session=B1", "--generation=1", "--msg-id=m1", "append", "j",
                                      "--type=intent", "--reply-to=5"]})

    def test_json_stays_with_the_client_and_oplog_is_sent(self):
        r = self.sent("--json", "--oplog", "--role", "b", "--session", "s", "status", "j")
        self.assertNotIn("--json", r["argv"])
        self.assertIn("--oplog", r["argv"])
        self.assertFalse([a for a in r["argv"] if a.startswith(("--db", "--broker", "--json"))])

    def test_msg_id_is_fixed_before_sending(self):
        r = self.sent("status", "j")
        ids = [a for a in r["argv"] if a.startswith("--msg-id=")]
        self.assertEqual(len(ids), 1)
        self.assertRegex(ids[0], r"\A--msg-id=[0-9a-f]{32}\Z")
        self.assertIn("--msg-id=abc", self.sent("--msg-id", "abc", "status", "j")["argv"])

    def test_every_command_round_trips(self):
        """Section 1.9 (binding): the request argv, with the inputs put back, parses with wf._parser() to the client's
        namespace, less db, broker and json, with msg_id as sent and each file option holding the text sent."""
        lines = [
            ["job", "new", "j", "--contract", self.cpath], ["job", "abandon", "j", "--reason", "r"],
            ["grant", "propose", "j", "--grant", self.gpath],
            ["grant", "activate", "j", "--owner-hash", "abcd1234", "--quote", "q", "--turn", "t"],
            ["grant", "revoke", "j", "--reason", "r", "--turn", "t"], ["grant", "expire", "j", "--reason", "r", "--turn", "t"],
            ["append", "j", "--type", "intent", "--content", '{"a":1}', "--reply-to", "3", "--grant-ref", "G1"],
            ["status", "j"], ["digest", "j"], ["checkpoint", "j"], ["verify"], ["verify", "j", "--checkpoint", "3:abc"],
            ["verify", "--restored", "r.db", "--job", "j", "--checkpoint", "1:a"],
            ["verify", "j", "--gate-file", "g.py", "--gate-sha", "a" * 64],
            ["backup", "--all", "--to", "b.db", "--writer-stopped"], ["backup", "j", "--to", "b.db"],
            ["export", "j"], ["accept", "j", "--results", '{"r":1}'], ["close", "j", "--intervention", "none"],
            ["resolve", "j", "4", "--attempt", "a1", "--disposition", "d"],
            ["bind", "j", "--bind-role", "builder", "--bind-session", "B1", "--bind-generation", "2", "--tool", "Write",
             "--tool", "Read", "--path", "a/**", "--bind-account", FAKE["B1"]],
            ["reconcile", "j", "--target-role", "reviewer"],
            ["ack-reconcile", "j", "--report", "5", "--exceptions", '["e1"]', "--note", "n"],
            ["grant-check", "j", "--check-role", "builder", "--action", "write", "--path", "a"],
            ["cancel", "j", "--reason", "r", "--turn", "t"],
            ["log-intervention", "j", "--label", "l", "--necessity", "unknown", "--minutes", "3",
             "--note=--reads-like-an-option"],
            ["gate", "print-install", "--hooks-dir", "h", "--root", "r", "--wf-dir", "w"], ["observe", "j"],
            ["recover", "begin", "j", "--reason", "r", "--turn", "t"], ["recover", "reconcile", "j", "--turn", "t"],
            ["recover", "close", "j", "--turn", "t"],
            ["recover", "decide", "j", "--decision", "restore", "--restore-path", "a", "--restore-path", "b",
             "--unresolved", "p=abandon-path", "--turn", "t", "--note", "n"],
            ["whoami"], ["whoami", "j"],
        ]
        covered = set()
        dests = {"--content": "content", "--contract": "contract", "--grant": "grant", "--results": "results",
                 "--exceptions": "exceptions"}
        for line in lines:
            full = ["--role", "builder", "--session", "B1", "--generation", "2", "--oplog", *line]
            with self.subTest(line=line):
                _, typed, _ = wf._parse(full)
                r = self.sent(*full)
                covered.add(wire.describe(r["argv"])[0])
                _, back, _ = wf._parse(r["argv"] + [f"{o}={t}" for o, t in r["inputs"].items()])
                want = {k: v for k, v in vars(typed).items() if k not in ("db", "broker", "json")}
                want["msg_id"] = next(a.split("=", 1)[1] for a in r["argv"] if a.startswith("--msg-id="))
                for opt, text in r["inputs"].items():
                    value = getattr(typed, dests[opt])
                    if os.path.isfile(value):
                        with open(value, "rb") as fh:
                            self.assertEqual(text, fh.read().decode("utf-8"))
                    else:
                        self.assertEqual(text, value)
                    want[dests[opt]] = text
                self.assertEqual({k: v for k, v in vars(back).items() if k not in ("db", "broker", "json")}, want)
                self.assertEqual(wire.file_option_refs(r["argv"]), [])
        self.assertEqual(covered, set(wire.COMMANDS))

    def test_a_positional_that_reads_like_an_option_is_not_sent(self):
        code, res = wf.run(["--broker", ADDR, "status", "--", "-j"])
        self.assertEqual((code, res["code"]), (1, "rejected"))
        self.assertTrue(res["error"].startswith("rejected(wire): the command does not parse back unchanged"), res)
        self.assertEqual(self.cap.requests, [])

    def test_id2_the_env_default_and_explicit_forms_give_one_request(self):
        """ID-2's U part (contract section 7): however the role and session are given, the broker gets one request."""
        content = '{"path":"jobs/j/work/a.txt"}'
        cfile = os.path.join(self.dir, "content.json")
        with open(cfile, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        tail = ["append", "j", "--type", "intent", "--content", content]
        forms = {
            "explicit": self.sent("--role", "coordinator", "--session", "C", "--msg-id", "m", *tail),
            "equals": self.sent("--role=coordinator", "--session=C", "--msg-id=m", "append", "j", "--type=intent",
                                f"--content={content}"),
            "after the command": self.sent(*tail, "--role", "coordinator", "--session", "C", "--msg-id", "m"),
            "a content file": self.sent("--role", "coordinator", "--session", "C", "--msg-id", "m", *tail[:-1], cfile),
        }
        os.environ.update(WF_ROLE="coordinator", WF_SESSION="C")
        forms["WF_ROLE and WF_SESSION"] = self.sent("--msg-id", "m", *tail)
        for name, r in forms.items():
            with self.subTest(form=name):
                self.assertEqual(r, forms["explicit"])


class Inputs(_Captured):
    """Section 1.5, the client: a file the caller names is read in its own account and sent as text."""

    def test_a_file_is_read_and_sent_as_its_text(self):
        r = self.sent("--role", "coordinator", "--session", "C1", "job", "new", "j", "--contract", self.cpath)
        with open(self.cpath, "rb") as fh:
            self.assertEqual(r["inputs"], {"--contract": fh.read().decode("utf-8")})
        self.assertFalse([a for a in r["argv"] if a.startswith("--contract")])
        self.assertNotIn(self.cpath, json.dumps(r))

    def test_inline_text_is_sent_unchanged(self):
        for text in ('{"a": [1, 2]}', "not json at all", os.path.join(self.dir, "no-such-file.json")):
            with self.subTest(text=text):
                self.assertEqual(self.sent("append", "j", "--type", "intent", "--content", text)["inputs"],
                                 {"--content": text})

    def _refused_like_load_json(self, path: str) -> None:
        with self.assertRaises(WfRejected) as cm:
            commands._load_json(path)
        code, res = wf.run(["--broker", ADDR, "append", "j", "--type", "intent", "--content", path])
        self.assertEqual((code, res), (1, {"ok": False, "code": "rejected", "error": str(cm.exception)}))
        self.assertEqual(self.cap.requests, [])

    def test_a_file_over_the_bound_is_malformed_and_nothing_is_sent(self):
        path = os.path.join(self.dir, "big.json")
        with open(path, "wb") as fh:
            fh.write(b'"' + b"x" * commands.MAX_JSON_INPUT_BYTES + b'"')
        self._refused_like_load_json(path)

    def test_an_invalid_file_keeps_todays_text(self):
        for name, data in (("bad.json", b'{"a":'), ("bad-utf8.json", b'{"a":"\xff"}'), ("nan.json", b'{"a":NaN}'),
                           ("dup.json", b'{"a":1,"a":2}')):
            with self.subTest(name=name):
                path = os.path.join(self.dir, name)
                with open(path, "wb") as fh:
                    fh.write(data)
                self._refused_like_load_json(path)

    def test_a_file_that_changes_after_the_check(self):
        real = commands._load_json
        for name, new, needle in (("utf8.json", b'{"a":"\xff"}', "changed while being read: not valid UTF-8"),
                                  ("grown.json", b"1" * (commands.MAX_JSON_INPUT_BYTES + 1),
                                   f"changed while being read: over {commands.MAX_JSON_INPUT_BYTES} bytes")):
            path = os.path.join(self.dir, name)
            with open(path, "wb") as fh:
                fh.write(b'{"a":1}')

            def then_change(p, new=new):
                out = real(p)
                with open(p, "wb") as fh:
                    fh.write(new)
                return out

            with self.subTest(name=name), mock.patch.object(commands, "_load_json", side_effect=then_change):
                code, res = wf.run(["--broker", ADDR, "append", "j", "--type", "intent", "--content", path])
                self.assertEqual((code, res["code"]), (1, "rejected"))
                self.assertTrue(res["error"].startswith(f"rejected(malformed): {path}: {needle}"), res)
        self.assertEqual(self.cap.requests, [])


def _response(exit_code=0, result=None, **over) -> bytes:
    obj = {"v": 1, "exit": exit_code, "result": {"ok": True} if result is None else result,
           "peer": {"account": FAKE["B1"]}, "broker": {"account": FAKE["own"], "wfcore": WFCORE}, "truncated": False}
    obj.update(over)
    return wire.frame(json.dumps(obj).encode("utf-8"))


class Call(_Env):
    """client.call: sections 1.4, 2.3's mapping and 5's bodies."""

    REQ = {"v": 1, "op": "run", "argv": ["--role=b", "--session=s", "--generation=1", "--msg-id=m1", "status", "j"],
           "inputs": {}, "wfcore": WFCORE}

    def call(self, channel_or_exc):
        def connect(address, deadline):
            self.connected = (address, deadline - time.monotonic())
            if isinstance(channel_or_exc, BaseException):
                raise channel_or_exc
            return channel_or_exc
        self.connected = None
        return client.call(ADDR, self.REQ, connect=connect)

    def test_a_good_response(self):
        ch = fakes.ScriptChannel(_response(1, {"ok": False, "code": "rejected", "error": "rejected(x): y"}))
        self.assertEqual(self.call(ch), (1, {"ok": False, "code": "rejected", "error": "rejected(x): y"}))
        self.assertEqual(ch.sent, wire.frame(wire.encode_request(self.REQ)))
        self.assertTrue(ch.closed)
        self.assertAlmostEqual(self.connected[1], client.CONNECT_BOUND_S, delta=5)
        recv = [d for kind, d in ch.deadlines if kind == "recv"]
        self.assertAlmostEqual(recv[0] - time.monotonic(), client.RESPONSE_BOUND_S, delta=5)
        self.assertEqual((client.CONNECT_BOUND_S, client.RESPONSE_BOUND_S), (60.0, 120.0))

    def test_broker_unavailable(self):
        for exc, reason in ((FileNotFoundError("not found"), "no-endpoint"), (TimeoutError("busy"), "busy"),
                            (PermissionError("denied"), "access-denied"), (OSError(22, "other"), "no-endpoint"),
                            (ConnectionRefusedError("refused"), "no-endpoint")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self.call(exc), (1, {"ok": False, "code": "broker-unavailable", "reason": reason,
                                                      "address": ADDR, "error": f"{type(exc).__name__}: {exc}"}))
        code, res = self.call(fakes.ScriptChannel(send_error=TimeoutError("send")))
        self.assertEqual((code, res["code"], res["reason"]), (1, "broker-unavailable", "busy"))

    def test_broker_response_lost(self):
        lost = {"ok": False, "code": "broker-response-lost", "msg_id": "m1",
                "action": "re-send the same command with --msg-id m1"}
        cases = (
            ("no answer", fakes.ScriptChannel(b"")),
            ("a short body", fakes.ScriptChannel(_response()[:-3])),
            ("an oversize header", fakes.ScriptChannel(struct.pack(">I", wire.MAX_RESPONSE_BYTES + 1) + b"{}")),
            ("not JSON", fakes.ScriptChannel(wire.frame(b"{nope"))),
            ("a bad field", fakes.ScriptChannel(_response(exit_code=9))),
            ("a missing field", fakes.ScriptChannel(wire.frame(b'{"v":1,"exit":0,"result":{}}'))),
            ("a timeout", fakes.ScriptChannel(recv_error=TimeoutError("slow"))),
            ("a read error", fakes.ScriptChannel(recv_error=ConnectionResetError("reset"))),
            ("a broken send", fakes.ScriptChannel(send_error=BrokenPipeError("broken"))),
        )
        for name, ch in cases:
            with self.subTest(case=name):
                code, res = self.call(ch)
                self.assertEqual(code, 1)
                self.assertEqual({k: v for k, v in res.items() if k != "error"}, lost)
                self.assertIsInstance(res["error"], str)
                self.assertTrue(ch.closed)

    def test_a_request_over_3_mib_is_wire_and_nothing_is_sent(self):
        big = dict(self.REQ, inputs={"--content": "x" * wire.MAX_REQUEST_BYTES})
        connect = mock.Mock(side_effect=AssertionError("connected"))
        code, res = client.call(ADDR, big, connect=connect)
        self.assertEqual((code, res["code"]), (1, "rejected"))
        self.assertTrue(res["error"].startswith("rejected(wire): the request is "), res)
        connect.assert_not_called()

    def test_connect_none_is_peer_connect_at_call_time(self):
        rec = mock.Mock(side_effect=FileNotFoundError("none"))
        with mock.patch.object(peer, "connect", rec):
            code, res = client.call(ADDR, self.REQ)
        self.assertEqual((code, res["reason"]), (1, "no-endpoint"))
        self.assertEqual(rec.call_args.args[0], ADDR)


class NoBroker(_Env, test_wf.Base):
    """BRK-1: no broker at the address; WF_DB is also set; nothing is sent or written, and there is no fallback."""

    def test_brk1_in_memory(self):
        self.new_job()
        before = fakes.ledger_state(self.db)
        os.environ["WF_DB"] = self.db
        with mock.patch.object(peer, "connect", fakes.MemoryTransport().connect):
            code, res = wf.run(["--broker", ADDR, "--role", "builder", "--session", "B1", "append", "j", "--type",
                                "intent", "--content", '{"a":1}'])
        self.assertEqual((code, res["code"], res["reason"], res["address"]), (1, "broker-unavailable", "no-endpoint", ADDR))
        self.assertEqual(fakes.ledger_state(self.db), before)

    def test_brk1_the_real_transport_with_no_server(self):
        """`peer.connect` unpatched. On a head without the platform module that is an OSError; with it, the connect
        retries until the (shortened) bound and raises FileNotFoundError. Both are no-endpoint."""
        self.new_job()
        before = fakes.ledger_state(self.db)
        os.environ["WF_DB"] = self.db
        address = fakes.address(f"none-{os.getpid()}-{time.monotonic_ns()}")
        with mock.patch.object(client, "CONNECT_BOUND_S", 0.3):
            code, res = wf.run(["--broker", address, "--role", "builder", "--session", "B1", "status", "j"])
        self.assertEqual((code, res["code"], res["reason"]), (1, "broker-unavailable", "no-endpoint"))
        self.assertEqual(fakes.ledger_state(self.db), before)


class BindAccount(_Env, test_wf.Base):
    """MAP-4's U part, and section 3.3 (S1): `--bind-account` is parsed and passed only when given."""

    def _bind(self, *extra: str) -> mock.Mock:
        with mock.patch.object(commands, "bind_cmd", return_value={"outcome": "COMMITTED"}) as m:
            code, res = self.run_wf("bind", "j", "--bind-role", "builder", "--bind-session", "B1", *extra,
                                    role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        return m

    def test_map4_bind_account_is_passed(self):
        kwargs = self._bind("--bind-account", FAKE["B1"]).call_args.kwargs
        self.assertEqual(kwargs["bind_account"], FAKE["B1"])
        self.assertNotIn("caller", kwargs)

    def test_without_it_the_call_is_ff3cd2fs(self):
        kwargs = self._bind().call_args.kwargs
        self.assertEqual(set(kwargs), {"bind_role", "bind_session", "bind_generation", "tools", "paths_", "faults",
                                       "msg_id", "role", "session", "generation", "oplog"})


class DirectMode(_Env, test_wf.Base):
    """Section 3.3 (S1), W-1: direct mode calls `commands` exactly as at ff3cd2f, with no `caller`."""

    def test_s1_direct_writes_pass_no_caller(self):
        names = ("job_new", "grant_propose", "grant_activate", "append", "status")
        spies = {n: mock.patch.object(commands, n, wraps=getattr(commands, n)) for n in names}
        started = {n: p.start() for n, p in spies.items()}
        for p in spies.values():
            self.addCleanup(p.stop)
        self.new_job()
        self.activate()
        code, res = self.intent()
        self.assertEqual(code, 0, res)
        code, res = self.run_wf("status", "j")
        self.assertEqual(code, 0, res)
        for name, spy in started.items():
            with self.subTest(name=name):
                self.assertTrue(spy.called)
                for call in spy.call_args_list:
                    self.assertNotIn("caller", call.kwargs)


class WhoamiDirect(_Env, test_wf.Base):
    """Section 4.5, direct mode; ID-10 without a job. Nothing is written and no ledger is opened."""

    def test_direct(self):
        with mock.patch.object(peer, "own_account", return_value=FAKE["own"]):
            code, res = wf.run(["--db", self.db, "whoami"])
        self.assertEqual((code, res), (0, {"ok": True, "mode": "direct", "account": FAKE["own"],
                                           "note": "direct mode: no account check", "db": os.path.abspath(self.db),
                                           "msg_id": None}))
        self.assertFalse(os.path.exists(self.db))

    def test_an_unreadable_own_account_is_refused(self):
        with mock.patch.object(peer, "own_account", side_effect=OSError("no token")):
            code, res = wf.run(["--db", self.db, "whoami"])
        self.assertEqual((code, res["code"]), (1, "refused"))
        self.assertIn("cannot read this process's account: no token", res["error"])

    def test_the_plain_text_keys(self):
        out = io.StringIO()
        with mock.patch.object(peer, "own_account", return_value=FAKE["own"]), \
                mock.patch.object(sys, "argv", ["wf", "--db", self.db, "whoami"]), contextlib.redirect_stdout(out):
            self.assertEqual(wf.main(), 0)
        self.assertEqual(out.getvalue(), f'mode="direct" account="{FAKE["own"]}" note="direct mode: no account check"\n')


class RealAccountRows(unittest.TestCase):
    """Contract section 7: an RA row is a declared skip in this build, never a pass."""

    def test_id2_ra_each_shell_form_run_as_b1(self):
        self.skipTest(RA_REASON)


if __name__ == "__main__":
    unittest.main()
