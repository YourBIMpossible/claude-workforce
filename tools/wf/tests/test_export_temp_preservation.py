"""Export temporary-file preservation (owner grant 2026-10-15, Zen).

`export._write_atomic` writes `<target>.tmp` and renames it over the target. A pre-existing `<target>.tmp`
(left by a crashed or concurrent writer, or planted) must never be truncated, replaced or followed: the
export refresh reports an error through its existing error path, the temp artifact keeps its bytes, and a
committed ledger row stays committed. No cleanup deletion anywhere: artifacts stay under WF_TEST_TMP.
"""
from __future__ import annotations

import json
import os
import unittest

from test_wf import Base, dbm, paths  # noqa: E402
from wfcore import export  # noqa: E402

PLANTED = b"planted-by-test; must survive\n"


class ExportTempPreservation(Base):
    def _ledger_identity(self) -> tuple[int, str | None, int]:
        conn = dbm.connect(self.db, create=False)
        try:
            rows = dbm.rows(conn, "j")
        finally:
            conn.close()
        return (rows[-1]["seq"], rows[-1]["self"], len(rows)) if rows else (0, None, 0)

    def _plant(self, name: str = "events.jsonl") -> str:
        out = paths.export_dir(self.db, "j")
        os.makedirs(out, exist_ok=True)
        tmp = os.path.join(out, name + ".tmp")
        with open(tmp, "xb") as fh:
            fh.write(PLANTED)
        return tmp

    def test_write_atomic_refuses_existing_tmp_and_preserves_bytes(self):
        target = os.path.join(self.dir, "unit-target.txt")
        tmp = target + ".tmp"
        with open(tmp, "xb") as fh:
            fh.write(PLANTED)
        with self.assertRaises(OSError):
            export._write_atomic(target, "new content\n")
        with open(tmp, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)
        self.assertFalse(os.path.exists(target))

    def test_write_atomic_normal_path_still_works(self):
        target = os.path.join(self.dir, "unit-ok.txt")
        export._write_atomic(target, "ok\n")
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"ok\n")
        self.assertFalse(os.path.exists(target + ".tmp"))

    def test_symlink_at_tmp_path_not_followed_or_overwritten(self):
        victim = os.path.join(self.dir, "victim.txt")
        with open(victim, "xb") as fh:
            fh.write(PLANTED)
        target = os.path.join(self.dir, "link-target.txt")
        tmp = target + ".tmp"
        try:
            os.symlink(victim, tmp)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlink creation not supported in this environment: {exc}")
        with self.assertRaises(OSError):
            export._write_atomic(target, "overwrite attempt\n")
        self.assertTrue(os.path.islink(tmp))
        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)
        self.assertFalse(os.path.exists(target))

    def test_explicit_export_reports_error_preserves_tmp_and_ledger(self):
        self.new_job()
        self.intent()
        before = self._ledger_identity()
        tmp = self._plant()
        c, r = self.run_wf("export", "j")
        self.assertNotEqual(c, 0, r)
        self.assertEqual(r.get("code"), "export-error", r)
        self.assertIn("error", r["export"], r)
        self.assertIn("FileExistsError", r["export"]["error"], r)
        with open(tmp, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)
        self.assertEqual(self._ledger_identity(), before)

    def test_refresh_failure_after_commit_keeps_row_committed(self):
        self.new_job()
        before = self._ledger_identity()
        tmp = self._plant("head.json")
        c, r = self.intent(note="committed despite export failure")
        self.assertEqual(c, 0, r)
        self.assertEqual(r.get("outcome"), "COMMITTED", r)
        self.assertIn("error", r.get("export", {}), r)
        after = self._ledger_identity()
        self.assertEqual(after[0], before[0] + 1)
        self.assertEqual(after[1], r["self"])
        with open(tmp, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)
        # events.jsonl was written before head.json failed; the committed row is in the ledger regardless
        head = json.loads(json.dumps(r))  # result is JSON-serialisable (no exception object leaked)
        self.assertEqual(head["seq"], after[0])

    def test_normal_refresh_still_works(self):
        self.new_job()
        self.intent()
        note = export.refresh(self.db, "j")
        self.assertNotIn("error", note, note)
        self.assertEqual(export.check(self.db, "j"), [])
        out = paths.export_dir(self.db, "j")
        for name in export.FILES:
            self.assertFalse(os.path.exists(os.path.join(out, name + ".tmp")))


if __name__ == "__main__":
    unittest.main()
