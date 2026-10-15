"""T35 A3 (Zen): export-directory inventory in `export.check` (FIRST-RELEASE-SPEC.md section 4.4 P4 row, line 216:
"Added or deleted files in the ledger/runtime folders other than the listed ones -> violation"; T35 row line 310).

`export.check` lists `jobs/<job>/export/` with `os.scandir` (no descent, no link following). Legitimate inventory:
`manifest.txt`, every manifest entry, and staging names (`<file>.tmp`, `<file>.<pid>-<hex8>.tmp`), whose
orphan/active semantics stay exactly as delivered in PR #17. Every other entry is reported, never touched:
  unexpected export file: <name> · unexpected export directory: <name>/ · unexpected export link: <name>
  · unexpected export entry: <name> (anything else).

R3a (`test_R3a_added_stray_file_in_the_export_folder_is_reported_by_verify`) is carried here UNCHANGED from
tools/wf/tests/requirements_t35_unmet.py; White removes it there in the paired integration change (that file is
outside this lane's write scope), so until then it is deliberately present in both. No cleanup deletion: scratch
artifacts stay under WF_TEST_TMP for inspection.
"""
from __future__ import annotations

import json
import os
import unittest

from test_t35_remaining_requirements import _Flow  # noqa: E402
from test_wf import Base, CONTRACT, dbm, paths  # noqa: E402
from wfcore import commands, export  # noqa: E402

PLANTED = b"planted-by-test; must survive\n"


class ExportStrayFiles(Base):
    def _out(self) -> str:
        return paths.export_dir(self.db, "j")

    def _start(self) -> None:
        self.new_job(); self.intent()
        note = export.refresh(self.db, "j")
        self.assertNotIn("error", note, note)
        self.assertEqual(export.check(self.db, "j"), [])

    def _write(self, name: str, data: bytes = PLANTED) -> str:
        p = os.path.join(self._out(), name)
        with open(p, "xb") as fh:
            fh.write(data)
        return p

    def _verify(self) -> dict:
        return commands.verify(self.db, "j")

    # 1 + 2: an unexpected file is reported by check and by wf verify
    def test_unexpected_file_reported_by_check_and_verify(self):
        self._start()
        p = self._write("stray.txt")
        self.assertEqual(export.check(self.db, "j"), ["unexpected export file: stray.txt"])
        v = self._verify()
        self.assertFalse(v["clean"], v)
        self.assertIn("unexpected export file: stray.txt", v["jobs"]["j"]["export"])
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)  # report only

    # 3: an unexpected subdirectory is reported without descending into it
    def test_unexpected_directory_reported_without_descent(self):
        self._start()
        sub = os.path.join(self._out(), "sub")
        os.makedirs(os.path.join(sub, "deeper"))
        with open(os.path.join(sub, "inner-secret.txt"), "xb") as fh:
            fh.write(PLANTED)
        problems = export.check(self.db, "j")
        self.assertEqual(problems, ["unexpected export directory: sub/"])
        self.assertNotIn("inner-secret", json.dumps(problems))
        self.assertNotIn("deeper", json.dumps(problems))
        v = self._verify()
        self.assertFalse(v["clean"], v)
        self.assertIn("unexpected export directory: sub/", v["jobs"]["j"]["export"])
        self.assertTrue(os.path.isfile(os.path.join(sub, "inner-secret.txt")))

    # 4: an orphan temporary file and a stray coexist; both are reported
    def test_orphan_and_stray_both_reported(self):
        self._start()
        self._write("events.jsonl.tmp")
        self._write("stray.txt")
        problems = export.check(self.db, "j")
        self.assertIn(export.PRESERVED + "events.jsonl.tmp", problems)
        self.assertIn("unexpected export file: stray.txt", problems)
        self.assertEqual(len(problems), 2, problems)
        v = self._verify()
        self.assertFalse(v["clean"], v)

    # 5: a symlink entry is reported without following its target
    def test_symlink_entry_reported_without_following(self):
        self._start()
        victim = os.path.join(self.dir, "victim.txt")
        with open(victim, "xb") as fh:
            fh.write(PLANTED)
        link = os.path.join(self._out(), "link-to-victim.txt")
        try:
            os.symlink(victim, link)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlink creation not supported in this environment: {exc}")
        problems = export.check(self.db, "j")
        self.assertEqual(problems, ["unexpected export link: link-to-victim.txt"])
        self.assertNotIn("victim.txt", " ".join(problems).replace("link-to-victim.txt", ""))
        self.assertTrue(os.path.islink(link))
        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)

    # 6: wf export leaves the stray in place; check and verify keep reporting it
    def test_wf_export_leaves_stray_and_still_reported(self):
        self._start()
        p = self._write("stray.txt")
        c, r = self.run_wf("export", "j")
        self.assertNotEqual(c, 0, r)
        self.assertEqual(r.get("code"), "export-error", r)
        self.assertNotIn("error", r["export"], r)  # the refresh itself succeeded
        self.assertIn("unexpected export file: stray.txt", r["problems"], r)
        self.assertTrue(os.path.isfile(p))
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), PLANTED)
        self.assertEqual(export.check(self.db, "j"), ["unexpected export file: stray.txt"])
        self.assertFalse(self._verify()["clean"])

    # legitimate inventory only -> no stray problems; staging semantics unchanged
    def test_legitimate_inventory_is_clean_and_active_staging_not_stray(self):
        self._start()
        self.assertEqual(sorted(os.listdir(self._out())), ["contract.json", "events.jsonl", "head.json", "manifest.txt"])
        self.assertEqual(export.check(self.db, "j"), [])
        name = f"head.json.{os.getpid()}-deadbeef.tmp"
        tmp = self._write(name)
        with export._ACTIVE_LOCK:
            export._ACTIVE.add(tmp)
        try:
            self.assertEqual(export.check(self.db, "j"), [])  # active staging: neither orphan nor stray
        finally:
            with export._ACTIVE_LOCK:
                export._ACTIVE.discard(tmp)
        self.assertEqual(export.check(self.db, "j"), [export.PRESERVED + name])

    # the product's own retained checkpoint is legitimate; its abandoned staging file is an orphan, not a stray
    def test_checkpoint_file_is_legitimate_and_its_orphan_is_preserved(self):
        self._start()
        c, r = self.run_wf("checkpoint", "j")
        self.assertEqual(c, 0, r)
        self.assertTrue(os.path.isfile(os.path.join(self._out(), "checkpoint.json")))
        self.assertEqual(export.check(self.db, "j"), [])
        self.assertTrue(self._verify()["clean"])
        self._write("checkpoint.json.tmp")
        self.assertEqual(export.check(self.db, "j"), [export.PRESERVED + "checkpoint.json.tmp"])

    # the existing reporting paths keep their problems and add the stray
    def test_missing_manifest_still_reported_with_stray(self):
        self._start()
        os.remove(os.path.join(self._out(), "manifest.txt"))
        self._write("stray.txt")
        problems = export.check(self.db, "j")
        self.assertTrue(any(p.startswith("export manifest missing") for p in problems), problems)
        self.assertIn("unexpected export file: stray.txt", problems)

    def test_stale_events_and_stray_both_reported(self):
        self._start()
        with open(os.path.join(self._out(), "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("tampered\n")
        self._write("stray.txt")
        problems = export.check(self.db, "j")
        self.assertIn("export events.jsonl is stale relative to the ledger", problems)
        self.assertIn("unexpected export file: stray.txt", problems)


class R3UnexpectedFilesInLedgerAndRuntimeFolders(_Flow):
    """R3a carried unchanged from requirements_t35_unmet.py (same class name, same assertions); duplicated there
    until White's paired removal lands."""
    contract = dict(CONTRACT, watch=["runtime/gate/**"])

    def test_R3a_added_stray_file_in_the_export_folder_is_reported_by_verify(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 P4 row (line 216): "Added or deleted files in the
        ledger/runtime folders other than the listed ones -> violation"; T35 row (line 310) "unexpected files in
        ledger/runtime folders".
        Expected: after an unlisted file jobs/j/export/stray.txt is added, `wf verify` reports clean false and an
        export problem naming stray.txt.
        Mechanism: export.check lists the export directory (A3, this lane)."""
        self.start()
        stray = os.path.join(paths.export_dir(self.db, "j"), "stray.txt")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("unexpected\n")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertTrue(any("stray.txt" in p for p in v["jobs"]["j"]["export"]), v)


if __name__ == "__main__":
    unittest.main()
