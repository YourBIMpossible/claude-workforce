"""PACKAGE-FIX3-II, lane F3-BRK (WF-195): `wfcore/wire.py`, INTERFACE-CONTRACT-FIX3-II r2 sections 1.1-1.4, 1.7, 1.10.

U tests: the strict codec and the request and response shapes (BRK-7's codec part), the frame bounds, the response
truncation, the wfcore recipe against the gate hook's, the address forms, and what the broker log may name (the
command path and the job, never other argv text). No ledger and no transport.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import struct
import unittest
from unittest import mock

import test_wf  # noqa: F401  (puts tools/wf on sys.path)

import wf
import wfbroker
from wfcore import validate, wire

SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
HEX = "a" * 64


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


def request(**over) -> dict:
    base = {"v": 1, "op": "run", "argv": ["--role=builder"], "inputs": {}, "wfcore": HEX}
    base.update(over)
    return base


def response(**over) -> dict:
    base = {"v": 1, "exit": 0, "result": {"ok": True}, "peer": {"account": "1000"},
            "broker": {"account": "1000", "wfcore": HEX}, "truncated": False}
    base.update(over)
    return base


class Decode(_Env):
    """Section 1.1: what both sides refuse."""

    def test_each_refusal(self):
        cases = (
            (b"\xff{}", "not valid UTF-8"),
            (b'{"a":"\xed\xa0\x80"}', "not valid UTF-8"),  # an encoded surrogate is not UTF-8
            (b"[1]", "not an object"), (b'"x"', "not an object"), (b"null", "not an object"),
            (b'{"a":1,"a":2}', "duplicate key"), (b'{"a":{"b":1,"b":2}}', "duplicate key"),
            (b'{"a":[{"c":1,"c":1}]}', "duplicate key"),
            (b'{"a":NaN}', "NaN is not JSON"), (b'{"a":Infinity}', "Infinity is not JSON"),
            (b'{"a":-Infinity}', "-Infinity is not JSON"), (b'{"a":1e999}', "not finite"),
            (b'{"a":"\\ud800"}', "lone surrogate"), (b'{"\\udc00":1}', "lone surrogate"),
            (b'{"a":["x\\udfff"]}', "lone surrogate"),
            (b'{"a":', "not JSON"), (b"", "not JSON"), (b"{} {}", "not JSON"),
        )
        for body, needle in cases:
            with self.subTest(body=body):
                with self.assertRaises(wire.WireError) as cm:
                    wire.decode(body)
                self.assertIn(needle, str(cm.exception))

    def test_a_valid_object(self):
        self.assertEqual(wire.decode('{"a":[1,"é","\\ud83d\\ude00"],"b":null}'.encode("utf-8")),
                         {"a": [1, "é", "\U0001f600"], "b": None})


class Request(_Env):
    """Section 1.2's shape (what argv must hold is run_brokered's, section 3.5)."""

    def test_a_good_request_and_a_stop(self):
        self.assertEqual(wire.check_request(request()), request())
        stop = request(op="stop", argv=[], inputs={})
        self.assertEqual(wire.check_request(stop), stop)

    def test_fields(self):
        for k in sorted(wire.REQUEST_FIELDS):
            with self.subTest(missing=k):
                r = request()
                del r[k]
                with self.assertRaisesRegex(wire.WireError, f"missing {k}"):
                    wire.check_request(r)
        with self.assertRaisesRegex(wire.WireError, "unknown extra"):
            wire.check_request(request(extra=1))

    def test_types_and_values(self):
        bad = (
            dict(v=True), dict(v=2), dict(v="1"), dict(v=1.0), dict(v=0),
            dict(op="RUN"), dict(op=None), dict(argv="--role=b"), dict(argv=[1]), dict(argv=None),
            dict(inputs=[]), dict(inputs={"--content": 1}), dict(inputs={"--db": "x"}), dict(inputs={"content": "x"}),
            dict(wfcore="A" * 64), dict(wfcore="a" * 63), dict(wfcore=None),
            dict(op="stop", argv=["--role=b"], inputs={}), dict(op="stop", argv=[], inputs={"--content": "{}"}),
        )
        for over in bad:
            with self.subTest(over=over), self.assertRaises(wire.WireError):
                wire.check_request(request(**over))

    def test_every_file_option_is_an_input_key(self):
        r = request(inputs={opt: "{}" for opt in wire.FILE_OPTIONS})
        self.assertEqual(wire.check_request(r), r)
        self.assertEqual(wire.FILE_OPTIONS, ("--content", "--contract", "--grant", "--results", "--exceptions"))

    def test_encoding_round_trip(self):
        r = request(argv=["--role=builder", "--note=é ✓"], inputs={"--content": '{"k":"ü"}'})
        body = wire.encode_request(r)
        self.assertIn("é ✓".encode("utf-8"), body)  # ensure_ascii=False: the text travels as UTF-8
        self.assertNotIn(b" ", body.replace("é ✓".encode("utf-8"), b""))  # compact separators
        self.assertEqual(wire.check_request(wire.decode(body)), r)
        with self.assertRaises(ValueError):  # allow_nan=False
            wire.encode_request(request(argv=[float("nan")]))


class Response(_Env):
    """Section 1.3's shape, the 8 MiB bound and its truncation (section 1.4)."""

    def test_a_good_response(self):
        self.assertEqual(wire.check_response(response()), response())
        self.assertEqual(wire.check_response(response(peer=None, exit=4)), response(peer=None, exit=4))

    def test_bad_responses(self):
        bad = (dict(v=2), dict(v=True), dict(exit=5), dict(exit=-1), dict(exit=True), dict(exit="0"),
               dict(result=[]), dict(peer={}), dict(peer={"account": 1}), dict(peer={"account": "1", "x": 1}),
               dict(broker={"account": "1"}), dict(broker={"account": "1", "wfcore": "x"}), dict(truncated=0))
        for over in bad:
            with self.subTest(over=over), self.assertRaises(wire.WireError):
                wire.check_response(response(**over))
        r = response()
        del r["truncated"]
        with self.assertRaisesRegex(wire.WireError, "missing truncated"):
            wire.check_response(r)

    def test_response_bytes(self):
        data, exit_code, truncated = wire.response(1, {"ok": False, "code": "rejected", "z": "é"}, peer_account=None,
                                                   broker_account="1000", broker_wfcore=HEX)
        (n,) = struct.unpack(">I", data[:4])
        self.assertEqual(n, len(data) - 4)
        body = data[4:]
        body.decode("ascii")  # ensure_ascii=True
        self.assertEqual(json.loads(body), {"broker": {"account": "1000", "wfcore": HEX}, "exit": 1, "peer": None,
                                            "result": {"ok": False, "code": "rejected", "z": "é"},
                                            "truncated": False, "v": 1})
        self.assertEqual(list(json.loads(body)), sorted(json.loads(body)))  # sort_keys
        self.assertEqual((exit_code, truncated), (1, False))

    def test_truncation_keeps_only_the_named_keys(self):
        result = {k: k for k in wire.TRUNCATED_KEYS} | {"ok": True, "seq": 7, "blob": "x" * 5000, "rows": [1] * 500}
        with mock.patch.object(wire, "MAX_RESPONSE_BYTES", 2000):
            data, exit_code, truncated = wire.response(0, result, peer_account="1000", broker_account="1000",
                                                       broker_wfcore=HEX)
        obj = wire.check_response(wire.decode(data[4:]))
        self.assertTrue(truncated)
        self.assertTrue(obj["truncated"])
        self.assertEqual(obj["result"], {k: result[k] for k in wire.TRUNCATED_KEYS})
        partial = {"ok": False, "code": "x", "blob": "y" * 5000}
        with mock.patch.object(wire, "MAX_RESPONSE_BYTES", 2000):
            data, _, _ = wire.response(1, partial, peer_account=None, broker_account="1000", broker_wfcore=HEX)
        self.assertEqual(wire.decode(data[4:])["result"], {"ok": False, "code": "x"})

    def test_an_unencodable_result_is_an_internal_error(self):
        data, exit_code, _ = wire.response(0, {"ok": True, "x": float("inf")}, peer_account=None,
                                           broker_account="1000", broker_wfcore=HEX)
        obj = wire.decode(data[4:])
        self.assertEqual(exit_code, 4)
        self.assertEqual((obj["exit"], obj["result"]["code"]), (4, "internal-error"))

    def test_bounds(self):
        self.assertEqual((wire.MAX_REQUEST_BYTES, wire.MAX_RESPONSE_BYTES), (3 * 1024 * 1024, 8 * 1024 * 1024))


class _Recorder:
    def __init__(self, data: bytes) -> None:
        self.data, self.reads = data, []

    def recv_exact(self, n: int, deadline: float) -> bytes:
        self.reads.append(n)
        chunk, self.data = self.data[:n], self.data[n:]
        if len(chunk) < n:
            raise EOFError("short")
        return chunk


class Frames(_Env):
    def test_a_header_over_the_bound_is_refused_before_the_body(self):
        rec = _Recorder(struct.pack(">I", wire.MAX_REQUEST_BYTES + 1) + b"x" * 10)
        with self.assertRaises(wire.FrameTooLarge):
            wire.read_frame(rec, 0.0, wire.MAX_REQUEST_BYTES)
        self.assertEqual(rec.reads, [4])

    def test_a_frame_round_trip(self):
        body = wire.encode_request(request())
        rec = _Recorder(wire.frame(body))
        self.assertEqual(wire.read_frame(rec, 0.0, wire.MAX_REQUEST_BYTES), body)
        self.assertEqual(rec.reads, [4, len(body)])
        self.assertEqual(wire.read_frame(_Recorder(wire.frame(b"")), 0.0, 10), b"")

    def test_eof_partway_passes_through(self):
        with self.assertRaises(EOFError):
            wire.read_frame(_Recorder(struct.pack(">I", 100) + b"x" * 10), 0.0, wire.MAX_REQUEST_BYTES)


class Versions(_Env, test_wf.Base):
    """Section 1.7: the gate's recipe (wf_gate_hook.py:62-68)."""

    def test_the_recipe_equals_the_gate_hooks(self):
        spec = importlib.util.spec_from_file_location("wf_gate_hook_for_fix3ii",
                                                      os.path.join(test_wf.WF_DIR, "gate", "wf_gate_hook.py"))
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.assertEqual(wire.wfcore_sha256(test_wf.WF_DIR), hook.wfcore_sha256(test_wf.WF_DIR))

    def test_the_recipe_by_hand(self):
        core = os.path.join(self.dir, "tree", "wfcore")
        os.makedirs(core)
        files = {"b.py": b"print(2)\n", "a.py": b"print(1)\r\n", "notes.txt": b"ignored"}
        for name, data in files.items():
            with open(os.path.join(core, name), "wb") as fh:
                fh.write(data)
        text = "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in ("a.py", "b.py"))
        want = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(wire.wfcore_sha256(os.path.join(self.dir, "tree")), want)
        with open(os.path.join(core, "a.py"), "ab") as fh:
            fh.write(b"#")
        self.assertNotEqual(wire.wfcore_sha256(os.path.join(self.dir, "tree")), want)


class Addresses(_Env):
    """Section 1.10."""

    def test_win32(self):
        good = ("\\\\.\\pipe\\wf-broker-0123456789abcdef", "\\\\.\\pipe\\" + "a" * 200, "\\\\.\\pipe\\A.b_c-9")
        bad = ("\\\\.\\pipe\\", "\\\\.\\pipe\\" + "a" * 201, "\\\\.\\pipe\\a/b", "\\\\.\\pipe\\a b", "\\\\srv\\pipe\\x",
               "\\\\.\\pipe\\a\n", "/run/wf.sock", "", None, 5)
        for a in good:
            with self.subTest(a=a):
                self.assertTrue(wire.is_address(a, "win32"))
        for a in bad:
            with self.subTest(a=a):
                self.assertFalse(wire.is_address(a, "win32"))

    def test_linux(self):
        good = ("/run/wf/wf.sock", "/" + "a" * 106, "/" + "é" * 53)
        bad = ("run/wf.sock", "/" + "a" * 107, "/" + "é" * 54, "/a\0b", "", "/", "\\\\.\\pipe\\x", None)
        for a in good:
            with self.subTest(a=a):
                self.assertTrue(wire.is_address(a, "linux"))
        for a in bad:
            with self.subTest(a=a):
                self.assertFalse(wire.is_address(a, "linux"))
        self.assertFalse(wire.is_address("/run/x", "darwin"))

    def test_address_for_root(self):
        root = os.path.join(test_wf.TMP, "some-root")
        pipe = wfbroker.address_for_root(root, "win32")
        want = hashlib.sha256(wfbroker.paths.norm(root).encode("utf-8")).hexdigest()[:16]
        self.assertEqual(pipe, "\\\\.\\pipe\\wf-broker-" + want)
        self.assertTrue(wire.is_address(pipe, "win32"))
        self.assertEqual(wfbroker.address_for_root(root, "linux"),
                         os.path.join(os.path.abspath(root), "runtime", "broker", "wf.sock"))
        with self.assertRaises(ValueError):
            wfbroker.address_for_root(root, "darwin")


def _parser_commands() -> set[str]:
    def subs(p: argparse.ArgumentParser):
        return next((a for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)
    out = set()
    for name, sub in subs(wf._parser()).choices.items():
        group = subs(sub)
        out |= {f"{name} {s}" for s in group.choices} if group is not None else {name}
    return out


def _all_option_strings() -> set[str]:
    out: set[str] = set()

    def walk(p: argparse.ArgumentParser) -> None:
        for a in p._actions:
            out.update(s for s in a.option_strings if s.startswith("--"))
            if isinstance(a, argparse._SubParsersAction):
                for sub in a.choices.values():
                    walk(sub)
    walk(wf._parser())
    return out


class LogNames(_Env):
    """Section 3.5: the broker log names the command path and the job, and no other argv text."""

    def test_commands_are_the_parsers(self):
        self.assertEqual(wire.COMMANDS, _parser_commands())

    def test_the_job_rule_is_validates(self):
        self.assertEqual(wire._JOB.pattern, validate._ID.pattern)

    def test_describe(self):
        g = ["--role=builder", "--session=B1", "--generation=1", "--msg-id=m1", "--oplog"]
        cases = (
            (g + ["append", "j1", "--type=intent", "--reply-to=3"], ("append", "j1")),
            (g + ["grant", "activate", "j-2", "--owner-hash=abcd1234"], ("grant activate", "j-2")),
            (g + ["gate", "print-install", "--hooks-dir=/h", "--root=/r"], ("gate print-install", None)),
            (g + ["verify"], ("verify", None)),
            (g + ["whoami"], ("whoami", None)),
            (g + ["resolve", "j", "4"], ("resolve", "j")),
            (g + ["rm", "-rf", "x"], (None, None)),
            (g + ["grant"], (None, None)),
            (g + ["grant", "steal", "j"], (None, None)),
            (g + ["append", "../secret", "--type=x"], ("append", None)),
            (g + ["append", "j\n"], ("append", None)),
            (g, (None, None)),
            ([], (None, None)),
        )
        for argv, want in cases:
            with self.subTest(argv=argv):
                self.assertEqual(wire.describe(argv), want)

    def test_file_option_refs(self):
        cases = ((["--content=x"], ["--content"]), (["--cont=x"], ["--cont"]), (["--res=x", "--ex=y"], ["--res", "--ex"]),
                 (["--grant", "x"], ["--grant"]), (["--grant-ref=x"], []), (["--role=--content"], []), (["--"], []),
                 (["--c"], ["--c"]), (["content"], []), (["-c"], []))
        for argv, want in cases:
            with self.subTest(argv=argv):
                self.assertEqual(wire.file_option_refs(argv), want)

    def test_no_other_wf_option_prefixes_a_file_option(self):
        """So a conforming request argv (full option names, section 1.9) never trips the A1 refusal."""
        others = _all_option_strings() - set(wire.FILE_OPTIONS)
        self.assertTrue(others)
        clash = sorted(o for o in others if any(f.startswith(o) for f in wire.FILE_OPTIONS))
        self.assertEqual(clash, [])


class Rejected(_Env):
    def test_the_body(self):
        self.assertEqual(wire.rejected("wire", "bad", x=1),
                         {"ok": False, "code": "rejected", "error": "rejected(wire): bad", "x": 1})


if __name__ == "__main__":
    unittest.main()
