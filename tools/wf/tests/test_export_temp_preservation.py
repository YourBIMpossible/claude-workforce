"""Export temporary-file preservation (owner grant 2026-10-15, Zen; correction round 1).

Round 0 (e03f466) used exclusive creation on the fixed name `<file>.tmp`, which preserved a planted temp but made
concurrent refreshes collide (T27/T31 regressions). Round 1: each writer stages to its own
`<file>.<pid>-<hex8>.tmp` (exclusive create, fsync, os.replace), so refreshes never share a temp file, and every
abandoned staging file is preserved and reported as `export temp file preserved: <name>` by both
`export.refresh` (key `preserved`) and `export.check` (problems). No cleanup deletion anywhere: artifacts stay
under WF_TEST_TMP for inspection.
"""
from __future__ import annotations

import os
import threading
import unittest

from test_wf import Base, dbm, paths  # noqa: E402
from wfcore import export  # noqa: E402

PLANTED = b"planted-by-test; must survive\n"
PRESERVED = "export temp file preserved: "


class ExportTempPreservation(Base):
    def _ledger_identity(self) -> tuple[int, str | None, int]:
        conn = dbm.connect(self.db, create=False)
        try:
            rows = dbm.rows(conn, "j")
        finally:
            conn.close()
        return (rows[-1]["seq"], rows[-1]["self"], len(rows)) if rows else (0, None, 0)

    def _out(self) -> str:
        return paths.export_dir(self.db, "j")

    def _plant(self, name: str = "events.jsonl.tmp") -> str:
        os.makedirs(self._out(), exist_ok=True)
        tmp = os.path.join(self._out(), name)
        with open(tmp, "xb") as fh:
            fh.write(PLANTED)
        return tmp

    def _assert_planted(self, tmp: str) -> None:
        with open(tmp, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)

    def _staging_files(self) -> list[str]:
        return sorted(n for n in os.listdir(self._out()) if n.endswith(".tmp"))

    # ---- 1 + 2: orphan preserved, refresh still succeeds and reports it
    def test_orphan_preserved_refresh_succeeds_and_reports(self):
        self.new_job(); self.intent()
        tmp = self._plant()
        note = export.refresh(self.db, "j")
        self.assertNotIn("error", note, note)
        self.assertEqual(note.get("preserved"), [PRESERVED + "events.jsonl.tmp"], note)
        self._assert_planted(tmp)
        self.assertEqual(self._staging_files(), ["events.jsonl.tmp"])  # refresh left no staging file of its own

    # ---- 3: check reports it on every run
    def test_check_reports_orphan_repeatedly(self):
        self.new_job(); self.intent()
        export.refresh(self.db, "j")
        tmp = self._plant("head.json.tmp")
        for _ in range(3):
            self.assertEqual(export.check(self.db, "j"), [PRESERVED + "head.json.tmp"])
        self._assert_planted(tmp)

    # ---- 4: orphan + stale events.jsonl -> both problems
    def test_check_lists_orphan_and_stale_together(self):
        self.new_job(); self.intent()
        export.refresh(self.db, "j")
        self._plant()
        with open(os.path.join(self._out(), "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("tampered\n")
        problems = export.check(self.db, "j")
        self.assertIn("export events.jsonl is stale relative to the ledger", problems)
        self.assertIn(PRESERVED + "events.jsonl.tmp", problems)
        self.assertTrue(any(p.startswith("export file differs from manifest") for p in problems), problems)

    # ---- 5: a successful refresh leaves and reports nothing of its own
    def test_successful_refresh_leaves_no_staging_and_reports_none(self):
        self.new_job(); self.intent()
        note = export.refresh(self.db, "j")
        self.assertNotIn("error", note, note)
        self.assertNotIn("preserved", note, note)
        self.assertEqual(self._staging_files(), [])
        self.assertEqual(export.check(self.db, "j"), [])

    # ---- 6: a failed refresh's leftover is an orphan on the next run
    def test_failed_refresh_leftover_reported_next_run(self):
        self.new_job(); self.intent()
        before = self._ledger_identity()
        real_replace = export.os.replace

        def fail_once(src: str, dst: str) -> None:
            if os.path.basename(dst) not in export.FILES:  # markers etc. share the os module: pass through
                return real_replace(src, dst)
            export.os.replace = real_replace
            raise PermissionError(32, "injected sharing violation", src)

        real_discard = export._discard_own_staging
        export.os.replace = fail_once
        export._discard_own_staging = lambda p: None  # writer dies before it can discard: the leftover case
        try:
            note = export.refresh(self.db, "j")
        finally:
            export.os.replace = real_replace
            export._discard_own_staging = real_discard
        self.assertIn("error", note, note)
        self.assertIn("PermissionError", note["error"])
        left = self._staging_files()
        self.assertEqual(len(left), 1, left)
        self.assertTrue(left[0].startswith(f"events.jsonl.{os.getpid()}-"), left)
        # the failing run itself already reports it: the write has ended, so the file is abandoned, not active
        self.assertEqual(note.get("preserved"), [PRESERVED + left[0]], note)
        note2 = export.refresh(self.db, "j")
        self.assertNotIn("error", note2, note2)
        self.assertEqual(note2.get("preserved"), [PRESERVED + left[0]], note2)
        self.assertEqual(export.check(self.db, "j"), [PRESERVED + left[0]])
        self.assertEqual(self._staging_files(), left)  # still there, untouched
        self.assertEqual(self._ledger_identity(), before)

    # ---- 7: concurrent writers never share a staging name
    def test_concurrent_writers_do_not_collide(self):
        target = os.path.join(self.dir, "concurrent.txt")
        n = 12
        errors: list[BaseException] = []
        start = threading.Barrier(n)

        def worker(i: int) -> None:
            try:
                start.wait(5)
                export._write_atomic(target, f"writer {i}\n")
            except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertFalse(any(isinstance(e, FileExistsError) for e in errors), errors)
        self.assertTrue(os.path.isfile(target))
        with open(target, encoding="utf-8") as fh:
            self.assertRegex(fh.read(), r"^writer \d+\n$")
        # a writer that lost the os.replace race fails visibly (OSError, never FileExistsError) and discards its
        # own staging file, so no staging file is left behind by any of the n writers
        self.assertTrue(all(isinstance(e, OSError) for e in errors), errors)
        leftovers = [n_ for n_ in os.listdir(self.dir) if n_.startswith("concurrent.txt.")]
        self.assertEqual(leftovers, [])

    # ---- a lost publish race leaves nothing behind (concurrent refreshes do not accumulate orphans)
    def test_lost_publish_race_discards_own_staging_and_reports_error(self):
        self.new_job(); self.intent()
        real_replace = export.os.replace

        def fail_once(src: str, dst: str) -> None:
            if os.path.basename(dst) not in export.FILES:
                return real_replace(src, dst)
            export.os.replace = real_replace
            raise PermissionError(32, "injected sharing violation", src)

        export.os.replace = fail_once
        try:
            note = export.refresh(self.db, "j")
        finally:
            export.os.replace = real_replace
        self.assertIn("error", note, note)
        self.assertNotIn("preserved", note, note)
        self.assertEqual(self._staging_files(), [])
        self.assertNotIn(PRESERVED, " ".join(export.check(self.db, "j")))

    # ---- active staging is not an orphan
    def test_active_staging_of_this_process_not_reported(self):
        self.new_job(); self.intent()
        export.refresh(self.db, "j")
        name = f"head.json.{os.getpid()}-deadbeef.tmp"
        tmp = self._plant(name)
        with export._ACTIVE_LOCK:
            export._ACTIVE.add(tmp)
        try:
            self.assertEqual(export.check(self.db, "j"), [])
        finally:
            with export._ACTIVE_LOCK:
                export._ACTIVE.discard(tmp)
        self.assertEqual(export.check(self.db, "j"), [PRESERVED + name])

    # ---- legacy fixed-name temp is never truncated or replaced (round-0 property kept)
    def test_write_atomic_never_touches_fixed_tmp(self):
        target = os.path.join(self.dir, "unit-target.txt")
        tmp = target + ".tmp"
        with open(tmp, "xb") as fh:
            fh.write(PLANTED)
        export._write_atomic(target, "new content\n")
        self._assert_planted(tmp)
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "new content\n")

    def test_symlink_at_fixed_tmp_not_followed_or_overwritten(self):
        victim = os.path.join(self.dir, "victim.txt")
        with open(victim, "xb") as fh:
            fh.write(PLANTED)
        target = os.path.join(self.dir, "link-target.txt")
        tmp = target + ".tmp"
        try:
            os.symlink(victim, tmp)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlink creation not supported in this environment: {exc}")
        export._write_atomic(target, "overwrite attempt\n")
        self.assertTrue(os.path.islink(tmp))
        self._assert_planted(victim)

    # ---- explicit export: orphan surfaces through the existing error behaviour, ledger untouched
    def test_explicit_export_reports_orphan_preserves_tmp_and_ledger(self):
        self.new_job(); self.intent()
        before = self._ledger_identity()
        tmp = self._plant()
        c, r = self.run_wf("export", "j")
        self.assertNotEqual(c, 0, r)
        self.assertEqual(r.get("code"), "export-error", r)
        self.assertNotIn("error", r["export"], r)  # the refresh itself succeeded
        self.assertEqual(r["export"].get("preserved"), [PRESERVED + "events.jsonl.tmp"], r)
        self.assertIn(PRESERVED + "events.jsonl.tmp", r["problems"], r)
        self._assert_planted(tmp)
        self.assertEqual(self._ledger_identity(), before)

    # ---- a refresh failure after a commit never turns the commit into an uncommitted claim
    def test_refresh_failure_after_commit_keeps_row_committed(self):
        self.new_job()
        before = self._ledger_identity()
        real_replace = export.os.replace

        def fail_once(src: str, dst: str) -> None:
            if os.path.basename(dst) not in export.FILES:  # markers etc. share the os module: pass through
                return real_replace(src, dst)
            export.os.replace = real_replace
            raise PermissionError(32, "injected sharing violation", src)

        export.os.replace = fail_once
        try:
            c, r = self.intent(note="committed despite export failure")
        finally:
            export.os.replace = real_replace
        self.assertEqual(c, 0, r)
        self.assertEqual(r.get("outcome"), "COMMITTED", r)
        self.assertIn("error", r.get("export", {}), r)
        after = self._ledger_identity()
        self.assertEqual(after[0], before[0] + 1)
        self.assertEqual(after[1], r["self"])


if __name__ == "__main__":
    unittest.main()
