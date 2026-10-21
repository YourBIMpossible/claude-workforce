"""PACKAGE-FIX3-II, lane F3-BRK, P0 (WF-195): `wfcore/peer.py`, INTERFACE-CONTRACT-FIX3-II r2 sections 2.1, 2.2 and 3.1.

U tests, no ledger and no OS account: the id forms (2.2a), host_platform, the lazy dispatch to the platform module
and its failures (own_account, listen, connect), identify's checks, the frozen PeerIdentity and Caller, and the module's
imports. The platform modules (`peer_win`, `peer_linux`) belong to the OSI lanes; every test here stands in for them,
so the file's results do not depend on whether those modules exist yet.
"""
from __future__ import annotations

import ast
import dataclasses
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import test_wf  # noqa: F401  (puts tools/wf on sys.path)

from wfcore import peer

PEER_PY = os.path.join(test_wf.WF_DIR, "wfcore", "peer.py")
SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
SID = "S-1-5-21-1004336348-1177238915-682003330-1001"
SID2 = "S-1-5-21-1004336348-1177238915-682003330-1002"


class _Env(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)


def _fake_module(**attrs):
    return SimpleNamespace(**attrs)


class IdForms(_Env):
    """Section 2.2a."""

    def test_win32_canonical_sids(self):
        for s in ("S-1-5-18", "S-1-5-32-544", SID, "S-1-0-0", "S-1-5" + "-1" * 15):
            with self.subTest(s=s):
                self.assertTrue(peer.is_account_id(s, "win32"))

    def test_win32_refuses_other_forms(self):
        for s in ("S-1-5", "s-1-5-18", "S-1-05-18", "S-1-5-018", "S-2-5-18", "S-1-5-18 ", " S-1-5-18", "S-1-5-18\n",
                  "S-1-5-" + "1" * 11, "S-1-5" + "-1" * 16, "S-1-5--18", "S-1-5-18-", "", "1000", "S-1-٥-18"):
            with self.subTest(s=s):
                self.assertFalse(peer.is_account_id(s, "win32"))

    def test_linux_canonical_uids(self):
        for s in ("0", "1", "1000", "4294967294"):
            with self.subTest(s=s):
                self.assertTrue(peer.is_account_id(s, "linux"))

    def test_linux_refuses_other_forms(self):
        for s in ("4294967295", "99999999999", "01", "-1", "+1", " 1000", "1000\n", "1e3", "", "١٠٠٠", SID):
            with self.subTest(s=s):
                self.assertFalse(peer.is_account_id(s, "linux"))

    def test_non_strings_and_unknown_platforms(self):
        for s, plat in ((1000, "linux"), (None, "win32"), (b"S-1-5-18", "win32"), ("1000", "darwin"),
                        ("S-1-5-18", "")):
            with self.subTest(s=s, plat=plat):
                self.assertFalse(peer.is_account_id(s, plat))


class HostPlatform(_Env):
    def test_names(self):
        for value, want in (("win32", "win32"), ("linux", "linux"), ("linux2", "linux")):
            with self.subTest(value=value), mock.patch.object(peer.sys, "platform", value):
                self.assertEqual(peer.host_platform(), want)

    def test_other_hosts_raise_oserror(self):
        for value in ("darwin", "cygwin", "freebsd14"):
            with self.subTest(value=value), mock.patch.object(peer.sys, "platform", value):
                with self.assertRaises(OSError):
                    peer.host_platform()


class Dispatch(_Env):
    """Section 2.2: listen, connect and own_account go to the host's platform module, imported at call time."""

    def _patch_import(self, module=None, error: Exception | None = None):
        calls = []

        def fake_import(name, package=None):
            calls.append((name, package))
            if error is not None:
                raise error
            return module

        patcher = mock.patch.object(peer.importlib, "import_module", side_effect=fake_import)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_the_module_is_chosen_by_host_and_imported_at_call_time(self):
        for plat, name in (("win32", ".peer_win"), ("linux", ".peer_linux")):
            with self.subTest(plat=plat), mock.patch.object(peer.sys, "platform", plat):
                calls = self._patch_import(_fake_module(listen=lambda a, c: ("L", a, tuple(c)),
                                                        connect=lambda a, d: ("C", a, d)))
                self.assertEqual(peer.listen("addr", ["1001"]), ("L", "addr", ("1001",)))
                self.assertEqual(peer.connect("addr", 12.5), ("C", "addr", 12.5))
                self.assertEqual(calls, [(name, "wfcore"), (name, "wfcore")])

    def test_own_account_returns_the_modules_id(self):
        with mock.patch.object(peer.sys, "platform", "win32"):
            self._patch_import(_fake_module(own_account=lambda: SID))
            self.assertEqual(peer.own_account(), SID)

    def test_a_missing_platform_module_is_a_chained_oserror(self):
        self._patch_import(error=ModuleNotFoundError("No module named 'wfcore.peer_x'"))
        for call in (peer.own_account, lambda: peer.listen("a", []), lambda: peer.connect("a", 0.0)):
            with self.subTest(call=call):
                with self.assertRaises(OSError) as cm:
                    call()
                self.assertIsInstance(cm.exception.__cause__, ImportError)

    def test_own_account_failures_are_oserrors(self):
        def boom():
            raise ValueError("token read failed")

        for module, cause in ((_fake_module(own_account=boom), ValueError),
                              (_fake_module(own_account=lambda: (_ for _ in ()).throw(PermissionError("denied"))),
                               None)):
            with self.subTest(cause=cause), mock.patch.object(peer.sys, "platform", "win32"):
                self._patch_import(module)
                with self.assertRaises(OSError) as cm:
                    peer.own_account()
                if cause is not None:
                    self.assertIsInstance(cm.exception.__cause__, cause)

    def test_own_account_refuses_an_id_not_in_the_host_form(self):
        for plat, bad in (("win32", "1000"), ("linux", SID), ("linux", "01")):
            with self.subTest(plat=plat, bad=bad), mock.patch.object(peer.sys, "platform", plat):
                self._patch_import(_fake_module(own_account=lambda b=bad: b))
                with self.assertRaises(OSError):
                    peer.own_account()

    def test_listen_and_connect_errors_pass_through_unchanged(self):
        # section 2.3's exceptions are the platform module's; the client and the broker map them (2.2, 2.3)
        for exc in (FileExistsError("in use"), FileNotFoundError("none"), TimeoutError("busy"),
                    PermissionError("denied"), ValueError("bad address")):
            def raiser(*_a, e=exc):
                raise e

            with self.subTest(exc=type(exc).__name__):
                self._patch_import(_fake_module(listen=raiser, connect=raiser))
                with self.assertRaises(type(exc)):
                    peer.listen("a", [])
                with self.assertRaises(type(exc)):
                    peer.connect("a", 0.0)


class Identify(_Env):
    """Section 2.2: identify reads (account, pid) from the server channel and checks the account's form."""

    def _channel(self, result=None, error: Exception | None = None):
        def peer_account():
            if error is not None:
                raise error
            return result

        return SimpleNamespace(peer_account=peer_account)

    def test_a_valid_peer(self):
        with mock.patch.object(peer.sys, "platform", "win32"):
            got = peer.identify(self._channel((SID, 4242)))
        self.assertEqual(got, peer.PeerIdentity(account=SID, pid=4242, platform="win32"))
        with mock.patch.object(peer.sys, "platform", "linux"):
            got = peer.identify(self._channel(("1001", None)))
        self.assertEqual(got, peer.PeerIdentity(account="1001", pid=None, platform="linux"))

    def test_an_unusable_pid_is_dropped_not_decisive(self):
        for pid in (True, -1, "4242", 4.2):
            with self.subTest(pid=pid), mock.patch.object(peer.sys, "platform", "win32"):
                self.assertIsNone(peer.identify(self._channel((SID, pid))).pid)

    def test_a_bad_account_is_an_oserror(self):
        for plat, account in (("win32", "1001"), ("win32", ""), ("linux", SID), ("linux", "4294967295"),
                              ("win32", None)):
            with self.subTest(plat=plat, account=account), mock.patch.object(peer.sys, "platform", plat):
                with self.assertRaises(OSError):
                    peer.identify(self._channel((account, 1)))

    def test_read_failures_are_oserrors(self):
        cases = ((OSError("ImpersonateNamedPipeClient failed"), None), (ValueError("too many values"), ValueError))
        for error, cause in cases:
            with self.subTest(error=type(error).__name__), mock.patch.object(peer.sys, "platform", "win32"):
                with self.assertRaises(OSError) as cm:
                    peer.identify(self._channel(error=error))
                if cause is not None:
                    self.assertIsInstance(cm.exception.__cause__, cause)
        with mock.patch.object(peer.sys, "platform", "win32"):
            with self.assertRaises(OSError):  # a malformed answer, not a 2-tuple
                peer.identify(self._channel((SID,)))


class Dataclasses(_Env):
    """Sections 2.1 and 3.1."""

    def test_caller_own_is_derived(self):
        me = peer.PeerIdentity(account=SID, pid=1, platform="win32")
        other = peer.PeerIdentity(account=SID2, pid=2, platform="win32")
        self.assertTrue(peer.Caller(peer=me, broker_account=SID, broker_wfcore="a" * 64).own)
        self.assertFalse(peer.Caller(peer=other, broker_account=SID, broker_wfcore="a" * 64).own)

    def test_both_are_frozen(self):
        ident = peer.PeerIdentity(account=SID, pid=None, platform="win32")
        caller = peer.Caller(peer=ident, broker_account=SID, broker_wfcore="a" * 64)
        for obj, field in ((ident, "account"), (caller, "broker_account")):
            with self.subTest(field=field), self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(obj, field, "x")
        with self.assertRaises(AttributeError):
            caller.own = False  # a read-only property

    def test_fields_and_order(self):
        self.assertEqual([f.name for f in dataclasses.fields(peer.PeerIdentity)], ["account", "pid", "platform"])
        self.assertEqual([f.name for f in dataclasses.fields(peer.Caller)], ["peer", "broker_account", "broker_wfcore"])


class Imports(_Env):
    """Section 6.1: peer.py imports the stdlib only; the platform modules only lazily (2.2), and no fake (2.5)."""

    def test_stdlib_only_and_no_platform_module_at_import(self):
        with open(PEER_PY, encoding="utf-8") as fh:
            text = fh.read()
        tree = ast.parse(text)
        names = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, f"a module-level relative import: {ast.dump(node)}")
                names.append(node.module)
        self.assertEqual(sorted(names), ["__future__", "dataclasses", "importlib", "re", "sys", "typing"])
        self.assertNotIn("fix3ii_fakes", text)


if __name__ == "__main__":
    unittest.main()
