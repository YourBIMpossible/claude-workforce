"""FIX3-II lane F3-OSI-W (WF-197): wfcore/peer_win.py, the Windows named-pipe endpoint and the client token's account
(interface contract r2 §2.3-2.4, §6.3). Every test runs within one account: the listener's own account is the only
account that connects, so `peer_account()` must equal `own_account()`. A second account, any ACL other than the pipe's
own at creation, admin and services are out of scope for this lane. Off Windows every test skips ("Windows only")."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
if WF_DIR not in sys.path:
    sys.path.insert(0, WF_DIR)

WINDOWS = sys.platform == "win32"
if WINDOWS:
    from wfcore import peer_win  # noqa: E402  (imported only where the module is meant to run)

SID = re.compile(r"\AS-1-(0|[1-9][0-9]{0,9})(-(0|[1-9][0-9]{0,9})){1,15}\Z")
# A well-formed domain-account SID that names no real account, for the DACL read-back: it grants nothing to anyone.
UNUSED_SID = "S-1-5-21-0-0-0-1001"


def _addr() -> str:
    return r"\\.\pipe\wf-test-" + uuid.uuid4().hex[:16]


def _soon(seconds: float = 5.0) -> float:
    return time.monotonic() + seconds


@unittest.skipUnless(WINDOWS, "Windows only")
class PeerWin(unittest.TestCase):
    def setUp(self) -> None:
        self.own = peer_win.own_account()
        self.addr = _addr()
        self.listener = peer_win.listen(self.addr, [self.own])
        self.addCleanup(self.listener.close)

    def _pair(self):
        client = peer_win.connect(self.addr, _soon())
        try:
            server = self.listener.accept(_soon())
        except BaseException:
            client.close()
            raise
        self.addCleanup(server.close)
        # Cleanups run last-in first: the client leaves first, so a server close that lingers ends at once.
        self.addCleanup(client.close)
        return client, server

    # -- identity --------------------------------------------------------------------------------------------------
    def test_platform_constant(self) -> None:
        self.assertEqual(peer_win.PLATFORM, "win32")

    def test_own_account_is_a_canonical_sid(self) -> None:
        self.assertRegex(self.own, SID)

    def test_round_trip_and_peer_account_equals_own_account(self) -> None:
        client, server = self._pair()
        client.send_all(b"request-frame", _soon())
        self.assertEqual(server.recv_exact(len(b"request-frame"), _soon()), b"request-frame")
        account, pid = server.peer_account()
        self.assertEqual(account, self.own)
        self.assertEqual(pid, os.getpid())
        server.send_all(b"response", _soon())
        self.assertEqual(client.recv_exact(len(b"response"), _soon()), b"response")

    def test_recv_exact_gathers_split_writes(self) -> None:
        client, server = self._pair()
        client.send_all(b"ab", _soon())
        client.send_all(b"cdef", _soon())
        self.assertEqual(server.recv_exact(6, _soon()), b"abcdef")

    def test_large_frame_crosses_the_pipe_buffer(self) -> None:
        import threading

        client, server = self._pair()
        payload = os.urandom(3 * 1024 * 1024)  # the contract's request bound, larger than the 64 KiB pipe buffer
        sender = threading.Thread(target=client.send_all, args=(payload, _soon(30)))
        sender.start()
        got = server.recv_exact(len(payload), _soon(30))
        sender.join(30)
        self.assertEqual(got, payload)

    # -- the endpoint and its access list ------------------------------------------------------------------------
    def test_second_listener_on_the_same_address_is_refused(self) -> None:
        with self.assertRaises(FileExistsError):
            peer_win.listen(self.addr, [self.own])

    def test_dacl_read_back_is_exactly_the_owner_and_the_listed_accounts(self) -> None:
        addr = _addr()
        lst = peer_win.listen(addr, [self.own, UNUSED_SID])
        self.addCleanup(lst.close)
        sddl = lst._dacl_sddl()
        self.assertTrue(sddl.startswith("D:P"), sddl)
        aces = re.findall(r"\(([^)]*)\)", sddl)
        parsed = {}
        for ace in aces:
            kind, flags, rights, _obj, _inh, sid = ace.split(";")
            self.assertEqual((kind, flags), ("A", ""), ace)
            parsed[sid] = rights
        self.assertEqual(set(parsed), {self.own, UNUSED_SID}, sddl)
        self.assertIn(parsed[self.own].upper(), {"FA", "0X1F01FF"}, sddl)
        # FILE_GENERIC_READ | FILE_WRITE_DATA, and no FILE_CREATE_PIPE_INSTANCE (0x4) for a listed client.
        self.assertEqual(int(parsed[UNUSED_SID], 16), 0x0012008B, sddl)

    def test_closing_a_channel_re_arms_the_single_instance(self) -> None:
        client, server = self._pair()
        client.send_all(b"x", _soon())
        server.recv_exact(1, _soon())
        client.close()
        server.close()
        client2 = peer_win.connect(self.addr, _soon())
        self.addCleanup(client2.close)
        server2 = self.listener.accept(_soon())
        self.addCleanup(server2.close)
        client2.send_all(b"again", _soon())
        self.assertEqual(server2.recv_exact(5, _soon()), b"again")
        self.assertEqual(server2.peer_account()[0], self.own)

    def test_close_is_idempotent_and_never_raises(self) -> None:
        client, server = self._pair()
        client.close()
        client.close()
        server.close()
        server.close()
        self.listener.close()
        self.listener.close()

    def test_a_closed_channel_never_reaches_the_pipe(self) -> None:
        client, server = self._pair()
        client.close()
        with self.assertRaises(OSError):
            client.send_all(b"x", _soon())
        with self.assertRaises(OSError):
            client.recv_exact(1, _soon(0.2))
        server.close()
        for call in (lambda: server.recv_exact(1, _soon(0.2)), lambda: server.send_all(b"x", _soon()),
                     server.peer_account):
            with self.assertRaises(OSError):
                call()

    def test_a_server_channel_is_unusable_after_its_listener_closes(self) -> None:
        client, server = self._pair()
        self.listener.close()
        with self.assertRaises(OSError):
            server.recv_exact(1, _soon(0.2))
        server.close()  # still never raises

    def test_address_property(self) -> None:
        self.assertEqual(self.listener.address, self.addr)

    # -- bad input -----------------------------------------------------------------------------------------------
    def test_bad_address_or_id_is_value_error(self) -> None:
        for bad in ("wf-broker", r"\\server\pipe\wf", r"\\.\pipe\\", r"\\.\pipe\a b", r"\\.\pipe\\" + "a" * 201):
            with self.subTest(addr=bad):
                with self.assertRaises(ValueError):
                    peer_win.listen(bad, [self.own])
                with self.assertRaises(ValueError):
                    peer_win.connect(bad, _soon(0.1))
        for bad_id in ("", "S-1", "S-01-5-21", "1000", "S-1-5-21-x"):
            with self.subTest(account=bad_id):
                with self.assertRaises(ValueError):
                    peer_win.listen(_addr(), [bad_id])

    # -- the bounds: not found, busy, timeouts, end of file ----------------------------------------------------
    def test_not_found_retries_until_the_deadline(self) -> None:
        start = time.monotonic()
        with self.assertRaises(FileNotFoundError):
            peer_win.connect(_addr(), start + 0.3)
        self.assertGreaterEqual(time.monotonic() - start, 0.25)

    def test_busy_single_instance_is_timeout_at_the_deadline(self) -> None:
        self._pair()  # the only instance is now taken
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            peer_win.connect(self.addr, start + 0.3)
        self.assertGreaterEqual(time.monotonic() - start, 0.25)

    def test_accept_times_out_with_no_client(self) -> None:
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.listener.accept(start + 0.3)
        self.assertGreaterEqual(time.monotonic() - start, 0.25)
        # The listener stays usable after a timed-out accept.
        client, server = self._pair()
        client.send_all(b"ok", _soon())
        self.assertEqual(server.recv_exact(2, _soon()), b"ok")

    def test_recv_times_out_and_the_channel_stays_usable(self) -> None:
        client, server = self._pair()
        with self.assertRaises(TimeoutError):
            server.recv_exact(1, time.monotonic() + 0.2)
        client.send_all(b"late", _soon())
        self.assertEqual(server.recv_exact(4, _soon()), b"late")

    def test_peer_close_before_n_bytes_is_eof(self) -> None:
        client, server = self._pair()
        client.send_all(b"ab", _soon())
        client.close()
        with self.assertRaises(EOFError):
            server.recv_exact(4, _soon())

    def test_send_after_peer_close_is_broken_pipe(self) -> None:
        client, server = self._pair()
        client.send_all(b"x", _soon())
        server.recv_exact(1, _soon())
        client.close()
        with self.assertRaises((BrokenPipeError, ConnectionError)):
            server.send_all(b"response", _soon())

    def test_server_close_does_not_lose_a_written_response(self) -> None:
        client, server = self._pair()
        client.send_all(b"q", _soon())
        server.recv_exact(1, _soon())
        server.send_all(b"answer", _soon())
        import threading

        closer = threading.Thread(target=server.close)
        closer.start()
        self.assertEqual(client.recv_exact(6, _soon()), b"answer")
        client.close()
        closer.join(15)
        self.assertFalse(closer.is_alive())

    def test_server_close_linger_is_bounded_and_re_arms(self) -> None:
        from unittest import mock

        client, server = self._pair()
        client.send_all(b"q", _soon())
        server.recv_exact(1, _soon())
        server.send_all(b"answer", _soon())
        with mock.patch.object(peer_win, "_LINGER_S", 0.3):
            start = time.monotonic()
            server.close()  # the client never closes: the linger ends at its bound
            self.assertLess(time.monotonic() - start, 5.0)
        client.close()
        client2 = peer_win.connect(self.addr, _soon())
        self.addCleanup(client2.close)
        server2 = self.listener.accept(_soon())
        self.addCleanup(server2.close)
        client2.send_all(b"next", _soon())
        self.assertEqual(server2.recv_exact(4, _soon()), b"next")

    # -- a failed RevertToSelf ends the process with exit 4 ----------------------------------------------------
    def test_failed_revert_exits_4(self) -> None:
        script = textwrap.dedent(
            f"""
            import sys, time
            sys.path.insert(0, {WF_DIR!r})
            from wfcore import peer_win
            addr = {_addr()!r}
            lst = peer_win.listen(addr, [peer_win.own_account()])
            c = peer_win.connect(addr, time.monotonic() + 5)
            s = lst.accept(time.monotonic() + 5)
            c.send_all(b"f", time.monotonic() + 5)
            s.recv_exact(1, time.monotonic() + 5)
            peer_win._revert_to_self = lambda: False
            s.peer_account()
            print("NOT EXITED")
            """
        )
        r = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertNotIn("NOT EXITED", r.stdout)


if __name__ == "__main__":
    unittest.main()
