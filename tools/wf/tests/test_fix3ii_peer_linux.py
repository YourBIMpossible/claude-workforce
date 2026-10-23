"""PACKAGE FIX3-II lane F3-OSI-L (WF-198, MyBuddy): `wfcore/peer_linux.py`, the Linux side of the peer seam.

Conformance tests for the interface contract r2 (`8d5ae9bf…`) §2.3 (the platform module API) and §2.4 (the Linux
rules), as §6.3 lists them for this lane: listen, connect, and `peer_account()` equal to `own_account()`; mode 0660
at creation; a stale socket replaced and a live socket refused; the `sun_path` limit; the deadlines; not found; busy.

Everything runs within the runner's own account. No test changes the owner, group or mode of anything; the only mode
set is the socket's own 0660, by `listen` at creation (contract §2.4, D24). Each test makes its sockets in a short
directory of its own under the system temp dir, because the WF_TEST_TMP paths are longer than `sun_path` allows
(contract §1.10: "Tests use short unique addresses of their own"), and removes it afterwards.

Off Linux every test skips with the reason "Linux only".
"""
from __future__ import annotations

import ast
import errno
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import test_wf  # noqa: F401  (sets sys.path to tools/wf)

try:
    from wfcore import peer_linux
    IMPORT_ERROR = None
except ImportError as exc:  # red at the registration commit: the module does not exist yet
    peer_linux = None
    IMPORT_ERROR = exc

LINUX = sys.platform.startswith("linux")
SKIP = "Linux only"
SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
SUN_PATH_MAX = 107  # contract §1.10: an absolute path of at most 107 bytes of UTF-8
UID_MAX = 4294967294  # contract §2.2a
SHORT = 0.3  # seconds: a deadline the tests expect to reach


def _uid_form(s: str) -> bool:
    """Contract §2.2a's Linux id form, written out here so the test does not lean on the module under test."""
    if not s or not s.isascii() or not s.isdigit() or (len(s) > 1 and s[0] == "0") or len(s) > 10:
        return False
    return int(s) <= UID_MAX


@unittest.skipUnless(LINUX, SKIP)
class PeerLinux(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in SCRUB:
            os.environ.pop(name, None)
        if peer_linux is None:
            self.fail(f"wfcore.peer_linux cannot be imported: {IMPORT_ERROR!r}")
        base = tempfile.gettempdir()
        if len(base.encode("utf-8")) > 40:
            base = "/tmp"
        self.dir = tempfile.mkdtemp(prefix="wfpl-", dir=base)
        self.addCleanup(self._remove_dir)
        self._closers: list = []

    def _remove_dir(self) -> None:
        for close in reversed(self._closers):
            close()
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def addr(self, name: str = "s") -> str:
        return os.path.join(self.dir, name)

    def listen(self, address: str, accounts=()):
        lis = peer_linux.listen(address, list(accounts))
        self._closers.append(lis.close)
        return lis

    def connect(self, address: str, within: float = 2.0):
        ch = peer_linux.connect(address, time.monotonic() + within)
        self._closers.append(ch.close)
        return ch

    def accept(self, lis, within: float = 2.0):
        sch = lis.accept(time.monotonic() + within)
        self._closers.append(sch.close)
        return sch

    def raw_listener(self, address: str, backlog: int = 4) -> socket.socket:
        """A plain AF_UNIX listener, not made by the module (for the live-socket and busy cases)."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._closers.append(s.close)
        s.bind(address)
        s.listen(backlog)
        return s

    def stale_socket(self, address: str) -> None:
        """A socket file with no listener behind it: bound, never listened on, closed, left on disk."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(address)
        s.close()
        self.assertTrue(stat.S_ISSOCK(os.lstat(address).st_mode))

    # ----------------------------------------------------------------------------------------- identity (§2.3)

    def test_platform_is_linux(self) -> None:
        self.assertEqual(peer_linux.PLATFORM, "linux")

    def test_own_account_is_the_effective_uid_in_canonical_form(self) -> None:
        own = peer_linux.own_account()
        self.assertIsInstance(own, str)
        self.assertEqual(own, str(os.geteuid()))
        self.assertTrue(_uid_form(own), own)

    def test_round_trip_and_peer_account_equals_own_account(self) -> None:
        lis = self.listen(self.addr())
        self.assertEqual(lis.address, self.addr())
        ch = self.connect(self.addr())
        sch = self.accept(lis)
        ch.send_all(b"\x00\x00\x00\x02hi", time.monotonic() + 2)
        self.assertEqual(sch.recv_exact(4, time.monotonic() + 2), b"\x00\x00\x00\x02")
        self.assertEqual(sch.recv_exact(2, time.monotonic() + 2), b"hi")
        account, pid = sch.peer_account()
        self.assertEqual(account, peer_linux.own_account())
        self.assertTrue(_uid_form(account), account)
        self.assertEqual(pid, os.getpid())
        sch.send_all(b"ok", time.monotonic() + 2)
        self.assertEqual(ch.recv_exact(2, time.monotonic() + 2), b"ok")

    def test_server_channel_has_the_channel_methods_plus_peer_account(self) -> None:
        lis = self.listen(self.addr())
        ch = self.connect(self.addr())
        sch = self.accept(lis)
        for name in ("recv_exact", "send_all", "close", "peer_account"):
            self.assertTrue(callable(getattr(sch, name, None)), name)
        for name in ("recv_exact", "send_all", "close"):
            self.assertTrue(callable(getattr(ch, name, None)), name)

    def test_peer_account_of_a_child_process_is_the_same_account_with_its_pid(self) -> None:
        lis = self.listen(self.addr())
        code = ("import socket,sys,time\n"
                "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.connect(sys.argv[1])\n"
                "s.sendall(b'x');s.recv(1)\n")
        proc = subprocess.Popen([sys.executable, "-I", "-c", code, self.addr()])
        try:
            sch = self.accept(lis, 10.0)
            self.assertEqual(sch.recv_exact(1, time.monotonic() + 10), b"x")
            account, pid = sch.peer_account()
            self.assertEqual(account, peer_linux.own_account())
            self.assertEqual(pid, proc.pid)
            sch.send_all(b"y", time.monotonic() + 2)
        finally:
            self.assertEqual(proc.wait(10), 0)

    # --------------------------------------------------------------------------------------- the socket (§2.4)

    def _check_mode(self, umask: int) -> None:
        old = os.umask(umask)
        try:
            lis = self.listen(self.addr(f"m{umask:o}"))
        finally:
            os.umask(old)
        st = os.lstat(lis.address)
        self.assertTrue(stat.S_ISSOCK(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660, oct(st.st_mode))
        self.assertEqual(st.st_uid, os.geteuid())

    def test_socket_mode_is_0660_at_creation_whatever_the_umask(self) -> None:
        for umask in (0o022, 0o077, 0o000, 0o002):
            with self.subTest(umask=oct(umask)):
                self._check_mode(umask)

    def test_owner_group_and_directory_are_not_changed(self) -> None:
        before = os.stat(self.dir)
        control = self.addr("control")
        with open(control, "x", encoding="utf-8"):
            pass
        lis = self.listen(self.addr())
        after = os.stat(self.dir)
        self.assertEqual((before.st_mode, before.st_uid, before.st_gid), (after.st_mode, after.st_uid, after.st_gid))
        st = os.lstat(lis.address)
        ctl = os.lstat(control)
        self.assertEqual((st.st_uid, st.st_gid), (ctl.st_uid, ctl.st_gid))

    def test_backlog_holds_at_least_four_waiting_clients(self) -> None:
        lis = self.listen(self.addr())
        clients = []
        for _ in range(4):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._closers.append(s.close)
            s.setblocking(False)
            s.connect(lis.address)  # raises BlockingIOError if the backlog were under 4
            clients.append(s)
        for _ in clients:
            self.accept(lis)

    def test_a_stale_socket_file_is_replaced(self) -> None:
        self.stale_socket(self.addr())
        old = os.lstat(self.addr()).st_ino
        lis = self.listen(self.addr())
        st = os.lstat(self.addr())
        self.assertNotEqual(st.st_ino, old)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)
        ch = self.connect(lis.address)
        sch = self.accept(lis)
        ch.send_all(b"z", time.monotonic() + 2)
        self.assertEqual(sch.recv_exact(1, time.monotonic() + 2), b"z")

    def test_a_live_socket_is_refused_and_left_serving(self) -> None:
        first = self.listen(self.addr())
        ino = os.lstat(self.addr()).st_ino
        with self.assertRaises(FileExistsError):
            peer_linux.listen(self.addr(), [])
        self.assertEqual(os.lstat(self.addr()).st_ino, ino)
        # The refusal's liveness probe connected and closed with nothing sent: the live listener sees one empty
        # connection first, which a broker closes with no answer (contract §1.8 step 1, EOF before a frame).
        probe = self.accept(first)
        with self.assertRaises(EOFError):
            probe.recv_exact(1, time.monotonic() + 2)
        ch = self.connect(self.addr())
        sch = self.accept(first)
        ch.send_all(b"1", time.monotonic() + 2)
        self.assertEqual(sch.recv_exact(1, time.monotonic() + 2), b"1")

    def test_a_live_listener_made_elsewhere_is_refused_too(self) -> None:
        self.raw_listener(self.addr())
        ino = os.lstat(self.addr()).st_ino
        with self.assertRaises(FileExistsError):
            peer_linux.listen(self.addr(), [])
        self.assertEqual(os.lstat(self.addr()).st_ino, ino)

    def test_a_file_that_is_not_a_socket_is_never_removed(self) -> None:
        with open(self.addr(), "x", encoding="utf-8") as fh:
            fh.write("keep")
        with self.assertRaises(OSError) as cm:
            peer_linux.listen(self.addr(), [])
        self.assertNotIsInstance(cm.exception, FileExistsError)
        with open(self.addr(), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "keep")

    def test_close_unlinks_its_own_socket_and_never_raises(self) -> None:
        lis = peer_linux.listen(self.addr(), [])
        lis.close()
        self.assertFalse(os.path.lexists(self.addr()))
        lis.close()

    def test_close_leaves_a_file_that_replaced_its_socket(self) -> None:
        lis = peer_linux.listen(self.addr(), [])
        os.unlink(self.addr())
        with open(self.addr(), "x", encoding="utf-8") as fh:
            fh.write("other")
        lis.close()
        with open(self.addr(), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "other")

    # ------------------------------------------------------------------------------- addresses and ids (§1.10)

    def _name_for(self, total_bytes: int, fill: str) -> str:
        room = total_bytes - len((self.dir + "/").encode("utf-8"))
        width = len(fill.encode("utf-8"))
        self.assertGreater(room, 2 * width)
        name = fill * (room // width) + "x" * (room % width)
        path = os.path.join(self.dir, name)
        self.assertEqual(len(path.encode("utf-8")), total_bytes)
        return path

    def test_sun_path_limit_107_bytes_works(self) -> None:
        for fill in ("a", "é", "€"):  # 1, 2 and 3 bytes of UTF-8: the limit counts bytes
            with self.subTest(fill=fill):
                path = self._name_for(SUN_PATH_MAX, fill)
                lis = self.listen(path)
                ch = self.connect(path)
                sch = self.accept(lis)
                ch.send_all(b"k", time.monotonic() + 2)
                self.assertEqual(sch.recv_exact(1, time.monotonic() + 2), b"k")
                self.assertEqual(sch.peer_account()[0], peer_linux.own_account())

    def test_sun_path_limit_108_bytes_is_refused_and_nothing_is_made(self) -> None:
        for fill in ("a", "é"):
            with self.subTest(fill=fill):
                path = self._name_for(SUN_PATH_MAX + 1, fill)
                with self.assertRaises(ValueError):
                    peer_linux.listen(path, [])
                self.assertFalse(os.path.lexists(path))
                t0 = time.monotonic()
                with self.assertRaises(OSError) as cm:
                    peer_linux.connect(path, t0 + 5)
                self.assertNotIsInstance(cm.exception, (FileNotFoundError, TimeoutError))
                self.assertLess(time.monotonic() - t0, 1.0)  # refused at once, not retried to the deadline

    def test_bad_addresses_are_refused(self) -> None:
        for bad in ("", "relative/s", "s", "\0abstract", self.addr("nul\0x"), None, b"/tmp/s", 7):
            with self.subTest(address=bad):
                with self.assertRaises(ValueError):
                    peer_linux.listen(bad, [])

    def test_client_account_ids_must_be_canonical_uids(self) -> None:
        for good in ([], ["0"], ["1000"], [str(UID_MAX)], ["0", "1000", "1001"]):
            with self.subTest(accounts=good), mock.patch.object(peer_linux, "_group_problem", return_value=None):
                lis = peer_linux.listen(self.addr("g"), good)  # the id form only: S2's group check is tested below
                lis.close()
        for bad in (["-1"], ["01"], [str(UID_MAX + 1)], ["4294967296"], ["12345678901"], ["S-1-5-18"], [""],
                    [" 1"], ["1 "], ["abc"], ["١"], [1000], [None], "1000"):
            with self.subTest(accounts=bad):
                with self.assertRaises(ValueError):
                    peer_linux.listen(self.addr("b"), bad)
                self.assertFalse(os.path.lexists(self.addr("b")))

    # -------------------------------------------------------------------------------- connect's retries (§2.3)

    def test_connect_not_found_is_retried_to_the_deadline(self) -> None:
        t0 = time.monotonic()
        with self.assertRaises(FileNotFoundError):
            peer_linux.connect(self.addr("none"), t0 + SHORT)
        took = time.monotonic() - t0
        self.assertGreaterEqual(took, SHORT - 0.06)
        self.assertLess(took, SHORT + 1.0)

    def test_connect_refused_is_retried_and_reported_as_not_found(self) -> None:
        self.stale_socket(self.addr())
        t0 = time.monotonic()
        with self.assertRaises(FileNotFoundError):
            peer_linux.connect(self.addr(), t0 + SHORT)
        self.assertGreaterEqual(time.monotonic() - t0, SHORT - 0.06)

    def test_connect_waits_for_a_listener_that_comes_up(self) -> None:
        made = []

        def later() -> None:
            time.sleep(0.25)
            made.append(peer_linux.listen(self.addr(), []))

        th = threading.Thread(target=later)
        th.start()
        try:
            ch = self.connect(self.addr(), 5.0)
        finally:
            th.join(5)
        self.assertEqual(len(made), 1)
        self._closers.append(made[0].close)
        sch = self.accept(made[0])
        ch.send_all(b"w", time.monotonic() + 2)
        self.assertEqual(sch.recv_exact(1, time.monotonic() + 2), b"w")

    def test_connect_busy_listener_times_out(self) -> None:
        raw = self.raw_listener(self.addr(), backlog=0)
        filled = 0
        for _ in range(64):  # fill the listener's queue; it never accepts
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._closers.append(s.close)
            s.setblocking(False)
            try:
                s.connect(raw.getsockname())
            except BlockingIOError:
                break
            filled += 1
        else:
            self.fail("the listener's queue never filled")
        self.assertGreaterEqual(filled, 1)
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            peer_linux.connect(self.addr(), t0 + SHORT)
        took = time.monotonic() - t0
        self.assertGreaterEqual(took, SHORT - 0.06)
        self.assertLess(took, SHORT + 1.0)

    def test_connect_access_denied_is_raised_at_once(self) -> None:
        calls = []

        class Denied:
            def __init__(self) -> None:
                self.closed = False

            def settimeout(self, _t) -> None:
                pass

            def setblocking(self, _b) -> None:
                pass

            def connect(self, _address) -> None:
                calls.append(1)
                raise PermissionError(errno.EACCES, "Permission denied")

            def close(self) -> None:
                self.closed = True

        with mock.patch.object(peer_linux, "_new_socket", Denied):
            t0 = time.monotonic()
            with self.assertRaises(PermissionError):
                peer_linux.connect(self.addr(), t0 + 5)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(len(calls), 1)

    def test_connect_other_errors_are_raised_as_they_are(self) -> None:
        class Broken:
            def settimeout(self, _t) -> None:
                pass

            def setblocking(self, _b) -> None:
                pass

            def connect(self, _address) -> None:
                raise OSError(errno.EIO, "I/O error")

            def close(self) -> None:
                pass

        with mock.patch.object(peer_linux, "_new_socket", Broken):
            with self.assertRaises(OSError) as cm:
                peer_linux.connect(self.addr(), time.monotonic() + 5)
        self.assertEqual(cm.exception.errno, errno.EIO)

    # ------------------------------------------------------------------------------------ the deadlines (§2.3)

    def test_accept_times_out_at_its_deadline(self) -> None:
        lis = self.listen(self.addr())
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            lis.accept(t0 + SHORT)
        self.assertGreaterEqual(time.monotonic() - t0, SHORT - 0.06)

    def test_recv_exact_collects_pieces_and_zero_is_empty(self) -> None:
        lis = self.listen(self.addr())
        ch = self.connect(self.addr())
        sch = self.accept(lis)
        self.assertEqual(sch.recv_exact(0, time.monotonic() + 1), b"")
        payload = os.urandom(300_000)
        th = threading.Thread(target=lambda: [ch.send_all(payload[i:i + 70_001], time.monotonic() + 5)
                                              for i in range(0, len(payload), 70_001)])
        th.start()
        try:
            self.assertEqual(sch.recv_exact(len(payload), time.monotonic() + 5), payload)
        finally:
            th.join(5)

    def test_recv_exact_raises_eof_when_the_peer_closes_first(self) -> None:
        lis = self.listen(self.addr())
        ch = self.connect(self.addr())
        sch = self.accept(lis)
        ch.send_all(b"ab", time.monotonic() + 2)
        ch.close()
        with self.assertRaises(EOFError):
            sch.recv_exact(4, time.monotonic() + 2)

    def test_recv_exact_times_out_at_its_deadline(self) -> None:
        lis = self.listen(self.addr())
        ch = self.connect(self.addr())
        sch = self.accept(lis)
        ch.send_all(b"a", time.monotonic() + 2)
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            sch.recv_exact(2, t0 + SHORT)
        self.assertGreaterEqual(time.monotonic() - t0, SHORT - 0.06)

    def test_send_all_to_a_closed_peer_is_a_broken_pipe(self) -> None:
        lis = self.listen(self.addr())
        ch = self.connect(self.addr())
        sch = self.accept(lis)
        sch.close()
        with self.assertRaises((BrokenPipeError, ConnectionError)):
            ch.send_all(b"x" * 65536, time.monotonic() + 2)

    def test_send_all_times_out_when_the_peer_does_not_read(self) -> None:
        lis = self.listen(self.addr())
        ch = self.connect(self.addr())
        self.accept(lis)
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            ch.send_all(b"x" * (8 * 1024 * 1024), t0 + SHORT)
        self.assertGreaterEqual(time.monotonic() - t0, SHORT - 0.06)

    def test_channel_close_is_idempotent_and_never_raises(self) -> None:
        lis = self.listen(self.addr())
        ch = self.connect(self.addr())
        sch = self.accept(lis)
        for c in (ch, sch, ch, sch):
            c.close()
        with self.assertRaises(OSError):
            sch.peer_account()

    # --------------------------------------------------------------------------------- the module's own rules

    def test_imports_are_stdlib_only_and_no_wfcore(self) -> None:
        tree = ast.parse(open(peer_linux.__file__, encoding="utf-8").read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "no relative import (no wfcore module)")
                names.add((node.module or "").split(".")[0])
        self.assertNotIn("wfcore", names)
        stdlib = set(sys.stdlib_module_names) | {"__future__"}
        self.assertEqual(names - stdlib, set())

    def test_never_reads_the_clients_environment_command_line_or_process_tree(self) -> None:
        src = open(peer_linux.__file__, encoding="utf-8").read()
        for token in ("/proc", "environ", "getenv", "cmdline", "psutil"):
            self.assertNotIn(token, src)

    # ------------------------------------------------------------------------- WF-232 S2: the socket's group

    def socket_gid(self) -> int:
        """The group a new file in self.dir takes: the directory's when it is setgid, else this process's."""
        st = os.stat(self.dir)
        return st.st_gid if st.st_mode & stat.S_ISGID else os.getegid()

    def test_s2_listen_refuses_a_group_that_admits_a_client_not(self) -> None:
        """The client's account has another primary group and is no member of the socket's: setup S8 is missing."""
        gid = self.socket_gid()
        entry = _Entry("wf-s2-outsider", gid + 1)
        with mock.patch("pwd.getpwuid", return_value=entry), \
                mock.patch("grp.getgrgid", return_value=_Group([])):
            with self.assertRaises(OSError) as cm:
                peer_linux.listen(self.addr(), ["3999101"])
        self.assertEqual(cm.exception.errno, errno.EACCES)
        self.assertIn(f"the socket's group {gid} does not admit client account(s) 3999101", str(cm.exception))
        self.assertFalse(os.path.lexists(self.addr()))  # the bound file is not left behind

    def test_s2_listen_accepts_a_group_that_admits_each_client(self) -> None:
        gid = self.socket_gid()
        entries = {3999101: _Entry("wf-s2-primary", gid), 3999102: _Entry("wf-s2-member", gid + 1)}
        with mock.patch("pwd.getpwuid", side_effect=entries.__getitem__), \
                mock.patch("grp.getgrgid", return_value=_Group(["wf-s2-member"])):
            lis = self.listen(self.addr(), ["3999101", "3999102"])
        self.assertEqual(os.lstat(lis.address).st_gid, gid)


class _Entry:
    """The two fields of a `pwd` entry that S2's check reads."""

    def __init__(self, name: str, gid: int) -> None:
        self.pw_name, self.pw_gid = name, gid


class _Group:
    """The one field of a `grp` entry that S2's check reads."""

    def __init__(self, members: list[str]) -> None:
        self.gr_mem = members


class GroupProblem(unittest.TestCase):
    """WF-232 S2: `_group_problem`'s rule, on every platform, with stand-in `pwd` and `grp` modules and the broker's
    uid fixed at 1000. Group 1000 stands for the broker's primary group, and group 5000 for `<wf-clients>`."""

    USERS = {2001: _Entry("b1", 1000 + 1001), 2002: _Entry("b2", 2002), 2003: _Entry("r1", 5000)}
    GROUPS = {1000: _Group([]), 5000: _Group(["b1", "b2"])}

    def setUp(self) -> None:
        if peer_linux is None:
            self.fail(f"wfcore.peer_linux cannot be imported: {IMPORT_ERROR!r}")
        pwd = type(sys)("pwd")
        pwd.getpwuid = self.USERS.__getitem__  # KeyError for an unknown uid, as pwd raises
        grp = type(sys)("grp")
        grp.getgrgid = self.GROUPS.__getitem__
        self.enterContext(mock.patch.dict(sys.modules, {"pwd": pwd, "grp": grp}))
        self.enterContext(mock.patch.object(os, "geteuid", return_value=1000, create=True))

    def test_s2_clients_outside_the_group_are_named(self) -> None:
        self.assertEqual(peer_linux._group_problem(1000, ("2001", "2002", "2003")),
                         "the socket's group 1000 does not admit client account(s) 2001, 2002, 2003, so its 0660 "
                         "group bits give them no access; setup S8 gives the socket's directory the client group "
                         "(SPEC-ii section 8.3)")

    def test_s2_a_member_or_a_primary_group_admits(self) -> None:
        self.assertIsNone(peer_linux._group_problem(5000, ("2001", "2002", "2003")))  # b1, b2 members; r1 primary

    def test_s2_one_outsider_is_enough(self) -> None:
        self.GROUPS[5001] = _Group(["b1"])
        self.addCleanup(self.GROUPS.pop, 5001)
        self.assertIn("client account(s) 2002, 2003,", peer_linux._group_problem(5001, ("2001", "2002", "2003")))

    def test_s2_the_own_uid_and_an_id_with_no_account_entry_are_not_checked(self) -> None:
        self.assertIsNone(peer_linux._group_problem(5000, ("1000", "3999101")))
        self.assertIsNone(peer_linux._group_problem(1000, ()))

    def test_s2_a_gid_with_no_group_entry_matches_only_primary_groups(self) -> None:
        self.assertIsNone(peer_linux._group_problem(2002, ("2002",)))
        self.assertIn("client account(s) 2001", peer_linux._group_problem(7777, ("2001",)))


@unittest.skipUnless(LINUX, SKIP)
class GroupThroughWfbroker(test_wf.Base):
    """WF-232 S2 end to end: wfbroker refuses to start as broker-config, and leaves no socket.

    WF-237: without the group check `wfbroker.main` starts serving and never returns, so it runs in a daemon thread
    and the test fails within BOUND_S instead of hanging; a broker that came up is stopped through its own `--stop`."""

    BOUND_S = 5.0

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in SCRUB:
            os.environ.pop(name, None)
        super().setUp()
        self.new_job()
        base = tempfile.gettempdir()
        self.sockdir = tempfile.mkdtemp(prefix="wfpl-", dir=base if len(base.encode("utf-8")) <= 40 else "/tmp")
        self.addCleanup(os.rmdir, self.sockdir)

    def main_within_bound(self, wfbroker, argv: list[str], emitted: list[dict], address: str) -> int:
        """`wfbroker.main(argv)`'s exit code, if it returns within BOUND_S without reporting `broker-ready`.
        Otherwise the test fails within the bound: a broker that reported ready is stopped first (its `--stop`,
        from this account, is the stop it serves), and a thread that is still running is a daemon, so it never
        keeps the run from ending."""
        exits: list[int] = []
        thread = threading.Thread(target=lambda: exits.append(wfbroker.main(argv)), daemon=True)
        thread.start()
        end = time.monotonic() + self.BOUND_S
        while thread.is_alive() and time.monotonic() < end:
            if any(e.get("code") == "broker-ready" for e in emitted):
                wfbroker.main(["--stop", "--address", address])
                thread.join(self.BOUND_S)
                self.fail("wfbroker.main started serving (broker-ready) instead of refusing as broker-config: the "
                          "socket's group was not checked; the broker was stopped")
            thread.join(0.02)
        if thread.is_alive():
            self.fail(f"wfbroker.main neither returned nor reported ready within {self.BOUND_S:g} s")
        return exits[0]

    def test_s2_a_group_that_admits_no_client_is_broker_config(self) -> None:
        import wfbroker
        emitted: list[dict] = []
        address = os.path.join(self.sockdir, "b.sock")
        with mock.patch.object(wfbroker, "_emit", emitted.append), \
                mock.patch("pwd.getpwuid", return_value=_Entry("wf-s2-outsider", os.getegid() + 1)), \
                mock.patch("grp.getgrgid", return_value=_Group([])):
            code = self.main_within_bound(
                wfbroker, ["--db", self.db, "--address", address, "--client-account", "3999101"], emitted, address)
        self.assertEqual(code, 1)
        self.assertEqual([(e["ok"], e["code"], e["check"]) for e in emitted], [(False, "broker-config", "address")])
        self.assertIn("does not admit client account(s) 3999101", emitted[0]["error"])
        self.assertFalse(os.path.lexists(address))


if __name__ == "__main__":
    unittest.main()
