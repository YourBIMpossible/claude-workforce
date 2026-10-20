"""WF-030 (MyBuddy): backup edge cases, the five "later batch" items White recorded at WF-010 VERIFIED (2026-10-17).

Tests only; product code is unchanged. Contract: INTERFACE-CONTRACT-S5.md section 1 (`dest-inside-ledger`, D-S5-3:
"`wf backup` refuses any destination inside the ledger directory"; `head_in_source` per job). Where current behaviour
is safe the test pins it. Where it contradicts the contract or White's note the test asserts the intended behaviour,
is marked `expectedFailure`, and its docstring records the observed behaviour as a finding (W30-Fn).

  1 symlinked ledger path        where symlinks can be made    pins 1a/1b; 1c is finding W30-F1 (POSIX and Windows)
  2 `..` in the destination      2a any platform; 2b POSIX     pins 2a; 2b (`..` after a symlink) is finding W30-F2
  3 UNC alias / junction         Windows only (skip elsewhere) 3a is finding W30-F3 (confirmed on Windows); 3b/3c pins
  4 gap below the head           any platform                  pin: a gap is a distinct chain problem
  5 head_in_source lookup order  any platform                  pin 5a; 5b (same connection as the copy) is W30-F4

Fixtures live under the test's own WF_TEST_TMP directory (test_wf.Base); nothing outside it is touched.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from unittest import mock

from test_wf import Base  # noqa: E402  (sets sys.path; fresh WF_TEST_TMP)

import wf  # noqa: E402
from wfcore import backup, db  # noqa: E402

POSIX = os.name == "posix"
WINDOWS = os.name == "nt"
SKIP_POSIX = "POSIX `..` resolution after a symlink (Win32 collapses `..` lexically; see 3c)"
SKIP_WINDOWS = "UNC admin-share aliases and junctions exist on Windows only"


class _Backup(Base):
    """A ledger with one job (contract, grant proposed, grant activated: seq 1..3) and helpers."""

    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()
        self.ledger = os.path.dirname(self.db)

    def wf_backup(self, dest: str, *, db_path: str | None = None, stopped: bool = True) -> tuple[int, dict]:
        argv = ["--db", db_path or self.db, "--role", "coordinator", "--session", "C1", "backup", "j", "--to", dest]
        return wf.run(argv + (["--writer-stopped"] if stopped else []))

    def assertRefusedInsideLedger(self, dest: str, *, db_path: str | None = None) -> None:
        code, res = self.wf_backup(dest, db_path=db_path)
        self.assertEqual((code, res.get("ok"), res.get("error")), (1, False, "dest-inside-ledger"), res)
        self.assertFalse(os.path.lexists(dest), "a refusal must write nothing")

    def ledger_files(self) -> list[str]:
        return sorted(os.listdir(self.ledger))


# ------------------------------------------------------------------------------------------------ 1 symlinked ledger

class SymlinkedLedger(_Backup):
    """Runs wherever this account can create symlinks (capability-probed in setUp, review F2 W30-R1): Linux, and
    Windows with Developer Mode or the symlink privilege. Skipped only when the probe symlink cannot be created."""

    def setUp(self) -> None:
        super().setUp()
        probe = os.path.join(self.dir, "symlink-probe")
        try:
            os.symlink(self.ledger, probe, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlinks here: {type(exc).__name__}: {exc}")
        (os.rmdir if WINDOWS else os.unlink)(probe)   # removes the link itself, never the ledger directory

    def test_1a_dest_through_a_symlink_to_the_ledger_directory_is_refused(self):
        """Pin: `<root>/alias -> <root>/ledger`; `--to <root>/alias/b.db` is inside the ledger (realpath)."""
        alias = os.path.join(self.dir, "alias")
        os.symlink(self.ledger, alias, target_is_directory=True)
        self.assertRefusedInsideLedger(os.path.join(alias, "b.db"))

    def test_1b_db_given_through_a_symlinked_directory_is_compared_by_realpath(self):
        """Pin: `--db <root>/alias/l.db` with `alias -> ledger`; a dest in the real ledger directory is refused."""
        alias = os.path.join(self.dir, "alias")
        os.symlink(self.ledger, alias, target_is_directory=True)
        self.assertRefusedInsideLedger(os.path.join(self.ledger, "b.db"), db_path=os.path.join(alias, "l.db"))

    def test_1c_db_file_symlink_elsewhere_still_protects_the_real_ledger_directory(self):  # FIX2 batch C fixed this finding; decorator removed by the integrator (AMENDMENT FIX2-A5 section 3)
        """W30-F1 (finding, fail-open, POSIX AND Windows): `--db <root>/elsewhere/l-alias.db` is a symlink to
        `<root>/ledger/l.db`. The ledger directory is taken as `dirname(abspath(db))` = `<root>/elsewhere`
        (backup._refuse_dest, backup.py:100), not the directory of the resolved ledger file. OBSERVED on Ubuntu, and
        on Windows by Plexy's review WF-033 with the platform skip lifted: `--to <root>/ledger/b.db` is not refused;
        the backup is written into the real ledger directory (D-S5-3 says any destination inside the ledger
        directory is refused). Intended: compare against `dirname(realpath(db))` as well."""
        elsewhere = os.path.join(self.dir, "elsewhere")
        os.makedirs(elsewhere)
        link = os.path.join(elsewhere, "l-alias.db")
        os.symlink(self.db, link)
        self.assertRefusedInsideLedger(os.path.join(self.ledger, "b.db"), db_path=link)


# ------------------------------------------------------------------------------------------------ 2 `..` in --to

class DotDotDestination(_Backup):
    def test_2a_dotdot_that_resolves_inside_the_ledger_is_refused(self):
        """Pin: plain `..` forms that land in the ledger directory are refused (lexical and real forms agree)."""
        os.makedirs(os.path.join(self.dir, "x"))
        for dest in (os.path.join(self.dir, "x", "..", "ledger", "b.db"),
                     os.path.join(self.ledger, "..", "ledger", "b.db"),
                     os.path.join(self.ledger, "pending", "..", "b.db")):
            with self.subTest(dest=dest):
                self.assertRefusedInsideLedger(dest)

    def test_2a_dotdot_that_resolves_outside_the_ledger_is_allowed(self):
        """Pin (no false refusal): `<root>/ledger/../b.db` is outside the ledger and is written there."""
        code, res = self.wf_backup(os.path.join(self.ledger, "..", "b.db"))
        self.assertEqual((code, res.get("clean")), (0, True), res)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "b.db")))

    @unittest.skipUnless(POSIX, SKIP_POSIX)
    def test_2b_dotdot_after_a_symlink_into_the_ledger_is_refused(self):  # FIX2 batch C fixed this finding; decorator removed by the integrator (AMENDMENT FIX2-A5 section 3)
        """W30-F2 (finding, fail-open on POSIX): `<root>/lnk -> <root>/ledger/sub`, `--to <root>/lnk/../b.db`.
        backup._norm runs abspath (which collapses `..` lexically: `<root>/b.db`, outside) BEFORE realpath, but the
        kernel resolves `lnk` before `..` (as realpath alone would: `<root>/ledger/b.db`). OBSERVED on Ubuntu: no
        refusal; os.open and the engine copy write the full backup to `<root>/ledger/b.db` (inside the ledger
        directory), then verification reopens the lexical path `<root>/b.db` (_open_ro uses abspath for the URI),
        which does not exist: an uncaught `sqlite3.OperationalError: unable to open database file`, no result.
        Intended: dest-inside-ledger, nothing written. Fix direction: compare `realpath(dest)` without a prior
        abspath (and build the URI from the same form), or refuse any `..` in `--to`."""
        sub = os.path.join(self.ledger, "sub")
        os.makedirs(sub)
        lnk = os.path.join(self.dir, "lnk")
        os.symlink(sub, lnk)
        before = self.ledger_files()
        try:
            outcome = self.wf_backup(lnk + "/../b.db")
        except sqlite3.Error as exc:                   # observed today: escapes wf.run
            outcome = (None, {"exception": f"{type(exc).__name__}: {exc}"})
        landed = sorted(set(self.ledger_files()) - set(before))
        self.assertEqual((outcome[0], outcome[1].get("error"), landed), (1, "dest-inside-ledger", []),
                         {"outcome": outcome, "written_inside_ledger": landed})


# ------------------------------------------------------------------------------------------------ 3 Windows aliases

@unittest.skipUnless(WINDOWS, SKIP_WINDOWS)
class WindowsAliases(_Backup):
    def _unc(self, path: str) -> str:
        drive, rest = os.path.splitdrive(os.path.abspath(path))
        return f"\\\\localhost\\{drive[0]}$" + rest

    def test_3a_admin_share_alias_of_the_ledger_directory_is_refused(self):  # FIX2 batch C fixed this finding; decorator removed by the integrator (AMENDMENT FIX2-A5 section 3)
        """W30-F3 (finding, fail-open plus a crash; CONFIRMED on Windows by Zen, WF-030 review F1, 2026-10-19):
        `--to \\\\localhost\\C$\\...\\ledger\\b.db` names the ledger directory through the admin share. realpath keeps
        the UNC form, so commonpath raises ValueError (different drives) and `_inside` answers False: no refusal.
        OBSERVED on Zen (account can reach the share): the engine copy writes `b.db` INSIDE the ledger directory
        through the share; then verification reopens it and `_open_ro` (backup.py:63, from backup.py:130) raises
        `sqlite3.OperationalError: unable to open database file`, so `wf backup` ends in an error, not a result. The
        Windows twin of W30-F2. Intended: dest-inside-ledger, nothing written. Review F2 (W30-R3): the outcome is one
        assertion over (refusal reason, b.db present in the ledger), so it holds for any product fix; any sqlite or
        OS error from the backup is recorded in it, never turned into a skip. Skipped (not counted) only when the
        admin share is not reachable at all from this account."""
        unc_ledger = self._unc(self.ledger)
        if not os.path.isdir(unc_ledger):
            raise unittest.SkipTest(f"admin share not reachable: {unc_ledger}")
        dest = unc_ledger + "\\b.db"
        try:
            code, res = self.wf_backup(dest)
            reason = res.get("error") if code == 1 else f"exit {code}, not refused"
        except (sqlite3.Error, OSError) as exc:      # observed on Windows: the finding, not the environment
            reason = f"not refused; {type(exc).__name__}: {exc}"
        written = os.path.exists(os.path.join(self.ledger, "b.db"))
        self.assertEqual((reason, written), ("dest-inside-ledger", False),
                         "W30-F3: (refusal reason, b.db written inside the ledger directory)")

    def _junction(self, link: str, target: str) -> None:
        import _winapi  # noqa: PLC0415 - Windows only
        _winapi.CreateJunction(target, link)

    def test_3b_dest_through_a_junction_to_the_ledger_directory_is_refused(self):
        """Pin: `<root>\\jn` is a junction to `<root>\\ledger`; realpath resolves junctions, so it is refused."""
        jn = os.path.join(self.dir, "jn")
        self._junction(jn, self.ledger)
        self.assertRefusedInsideLedger(os.path.join(jn, "b.db"))

    def test_3c_dotdot_after_a_junction_is_collapsed_lexically_and_stays_outside(self):
        """Pin (passes on Windows, review F1/F2 runs on Zen and Plexy; the Windows twin of 2b): `<root>\\jn2` is a
        junction to `<root>\\ledger\\sub`. Win32
        collapses `..` lexically before any reparse point is followed, so `<root>\\jn2\\..\\b.db` is `<root>\\b.db`
        for both the check and the OS. The backup is allowed and lands outside the ledger."""
        sub = os.path.join(self.ledger, "sub")
        os.makedirs(sub)
        jn2 = os.path.join(self.dir, "jn2")
        self._junction(jn2, sub)
        code, res = self.wf_backup(jn2 + "\\..\\b.db")
        self.assertEqual(code, 0, res)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "b.db")))
        self.assertFalse(os.path.exists(os.path.join(self.ledger, "b.db")))


# ------------------------------------------------------------------------------------------------ 4 gap below head

class _GapConn(sqlite3.Connection):
    """Destination connection that removes seq 2 of job j from the finished copy just before it closes, so the
    backup's own verification sees a copy with a gap below its head (simulates a copy that lost an event)."""

    def close(self) -> None:
        for (name,) in self.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='events'"):
            self.execute(f'DROP TRIGGER "{name}"')
        self.execute("DELETE FROM events WHERE job='j' AND seq=2")
        super().close()


class GapBelowHead(_Backup):
    def test_4_gap_below_the_head_is_a_distinct_chain_problem_not_head_in_source_false(self):
        """Pin (safe): a copy missing seq 2 below its head (seq 3) reports `chains[j]` = ["sequence gap: expected 2,
        found 3", ...]; `head_in_source[j]` stays True and `head_equal[j]` True, so the gap is distinguishable from a
        head missing in the source (`head_in_source` False with clean chains). `clean` is False. There is no
        dedicated status key; the distinct signal is the chain problem text. The copy is left in place."""
        real_connect = sqlite3.connect
        dest = os.path.join(self.dir, "gap.db")

        def connect(target, *a, **kw):
            if target == dest:
                kw["factory"] = _GapConn
            return real_connect(target, *a, **kw)

        with mock.patch.object(backup.sqlite3, "connect", connect):
            code, res = self.wf_backup(dest)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["heads"]["j"]["seq"], 3)
        self.assertTrue(any(p.startswith("sequence gap: expected 2, found 3") for p in res["chains"]["j"]),
                        res["chains"])
        self.assertEqual((res["head_in_source"], res["head_equal"]), ({"j": True}, {"j": True}))
        self.assertEqual((res["all_chains_clean"], res["clean"]), (False, False))
        self.assertTrue(os.path.isfile(dest), "a failed verification never deletes the backup")


# ------------------------------------------------------------------------------------------------ 5 lookup timing

class _TracedConn(sqlite3.Connection):
    log: list[tuple] = []

    def close(self) -> None:
        _TracedConn.log.append(("close", self.n, self.path))
        super().close()

    def backup(self, target, *a, **kw):  # noqa: D401 - engine copy from this (source) connection
        _TracedConn.log.append(("copy", self.n, self.path))
        return super().backup(target, *a, **kw)


class LookupTiming(_Backup):
    def _traced_backup(self) -> tuple[dict, list[tuple]]:
        """Run backup.backup with every read-only connection traced (open, copy, close) and every db.row_at /
        db.head call recorded with its connection and whether that connection was still open. Connections are
        numbered in open order (`n`), never identified by id(), which CPython may reuse after a close."""
        _TracedConn.log = []
        log = _TracedConn.log
        closed: set[int] = set()

        def open_ro(path: str) -> sqlite3.Connection:
            import pathlib  # noqa: PLC0415
            uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None, factory=_TracedConn)
            conn.path, conn.n = os.path.abspath(path), sum(1 for e in log if e[0] == "open") + 1
            log.append(("open", conn.n, conn.path))
            return conn

        orig_close = _TracedConn.close

        def close(conn) -> None:
            orig_close(conn)
            closed.add(conn.n)

        def traced(name, fn):
            def wrapper(conn, *a, **kw):
                n = getattr(conn, "n", None)
                log.append((name, n, getattr(conn, "path", None), n is not None and n not in closed))
                return fn(conn, *a, **kw)
            return wrapper

        dest = os.path.join(self.dir, "b.db")
        with mock.patch.object(backup, "_open_ro", open_ro), mock.patch.object(_TracedConn, "close", close), \
                mock.patch.object(db, "row_at", traced("row_at", db.row_at)), \
                mock.patch.object(db, "head", traced("head", db.head)):
            res = backup.backup(self.db, dest, writer_stopped=True)
        return res, list(log)

    def _source_lookups(self, log: list[tuple]) -> list[tuple]:
        src = os.path.abspath(self.db)
        return [e for e in log if e[0] in ("row_at", "head") and e[2] == src]

    def test_5a_head_in_source_lookups_run_on_an_open_source_connection(self):
        """Pin (safe): every source lookup behind `head_in_source` / `head_equal` (db.row_at, db.head on the ledger)
        runs while its connection is open, and that connection is closed only afterwards."""
        res, log = self._traced_backup()
        self.assertEqual(res["head_in_source"], {"j": True})
        lookups = self._source_lookups(log)
        self.assertTrue(lookups, log)
        for e in lookups:
            self.assertTrue(e[3], f"lookup on a closed connection: {e}")
            close_at = log.index(("close", e[1], e[2]))
            self.assertLess(log.index(e), close_at, log)

    @unittest.expectedFailure
    def test_5b_head_in_source_lookups_use_the_copy_connection_before_it_closes(self):
        """W30-F4 (finding vs White's WF-010 note "head_in_source lookups before the source closes"): OBSERVED
        order: open source -> copy -> close source -> open/verify the copy -> open a SECOND source connection ->
        row_at/head -> close. The lookups do not run on the connection that made the copy, so they are not
        before that source closes; a writer may append in between (harmless for an append-only chain: the head
        row still exists), and a ledger made unreadable in between turns a written backup into an error result.
        Intended: keep the copy's source connection (and snapshot) open through the head_in_source lookups."""
        _, log = self._traced_backup()
        copies = [e for e in log if e[0] == "copy"]
        self.assertEqual(len(copies), 1, log)
        copy_conn = copies[0][1]
        for e in self._source_lookups(log):
            self.assertEqual(e[1], copy_conn, f"lookup on another source connection: {e}\n{log}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
