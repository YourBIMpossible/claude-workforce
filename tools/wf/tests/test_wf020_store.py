"""WF-020 (Lazy): regression tests for export/manifest/marker/changeset paths that no earlier test exercised
(from-lazy WF-020 GAP-LIST.md). Current behaviour at 2dedc94 is asserted. Where the CURRENT behaviour contradicts the
spec or the code's own fallback, the test asserts the spec and is marked expectedFailure with the observed behaviour in
its docstring: that is a FINDING (see from-lazy WF-020 FINDINGS.md), not a fix.

Runs inside the test_wf.py harness (fresh scratch ledger per test under WF_TEST_TMP)."""
from __future__ import annotations

import json
import os
import subprocess
import unittest

import test_wf
from test_wf import Base

from wfcore import export, manifest, markers, paths

SHA = "a" * 64


class ManifestUnit(unittest.TestCase):
    def test_format_line_validates_every_field(self):
        for e, text in ((manifest.Entry("A" * 64, 1, "f"), "sha256 must be 64 lowercase hex"),
                        (manifest.Entry("a" * 63, 1, "f"), "sha256 must be 64 lowercase hex"),
                        (manifest.Entry(SHA, -1, "f"), "size must be non-negative"),
                        (manifest.Entry(SHA, 1, ""), "relpath must be non-empty"),
                        (manifest.Entry(SHA, 1, "a  b"), "relpath must be non-empty"),
                        (manifest.Entry(SHA, 1, "a\nb"), "relpath must be non-empty")):
            with self.subTest(entry=e), self.assertRaises(ValueError) as cm:
                manifest.format_line(e)
            self.assertIn(text, str(cm.exception))
        self.assertEqual(manifest.format_line(manifest.Entry(SHA, 3, "events.jsonl")), f"{SHA}  3  events.jsonl")

    def test_parse_line_rejects_malformed_lines_with_a_line_number(self):
        for line, reason in ((f"{SHA}  1", "expected exactly 3 fields, found 2"),
                             (f"{SHA}  1  a  b", "expected exactly 3 fields, found 4"),
                             (f"{'g' * 64}  1  f", "field 1 is not a 64-hex sha256"),
                             (f"{SHA}  x  f", "field 2 is not a non-negative integer"),
                             (f"{SHA}  -1  f", "field 2 is not a non-negative integer"),
                             (f"{SHA}  1   ", "field 3 is empty or has surrounding whitespace"),
                             (f"{SHA}  1   f", "field 3 is empty or has surrounding whitespace")):
            with self.subTest(line=line), self.assertRaises(manifest.ManifestMalformed) as cm:
                manifest.parse_line(7, line)
            self.assertEqual((cm.exception.line_no, cm.exception.reason), (7, reason))

    def test_read_rejects_whitespace_only_line_and_skips_empty_ones(self):
        p = os.path.join(self.id().replace(".", "_") + ".txt")
        d = os.path.join(test_wf.TMP, "manifest-unit")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, p)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{SHA}  1  a\n\n{SHA}  2  b\n")
        self.assertEqual([e.relpath for e in manifest.read(path)], ["a", "b"])
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{SHA}  1  a\n   \n")
        with self.assertRaises(manifest.ManifestMalformed) as cm:
            manifest.read(path)
        self.assertEqual(cm.exception.reason, "whitespace-only line")

    @unittest.expectedFailure
    def test_unicode_digit_size_is_a_malformed_manifest_not_a_crash(self):
        """FINDING: parse_line uses str.isdigit(), which is True for '²'; int('²') then raises a bare ValueError
        that `export.check` (it catches only ManifestMalformed) lets escape, so `wf verify` would traceback. Spec: a
        malformed manifest line is reported as MALFORMED. Observed: ValueError."""
        with self.assertRaises(manifest.ManifestMalformed):
            manifest.parse_line(1, f"{SHA}  ²  f")


class ExportChecks(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.out = paths.export_dir(self.db, "j")

    def _verify_problems(self) -> list[str]:
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        return r["jobs"]["j"]["export"]

    def test_clean_export_has_no_problems(self):
        self.assertEqual(self._verify_problems(), [])

    def test_stray_file_directory_and_junction_entries_are_reported_not_removed(self):
        with open(os.path.join(self.out, "stray.txt"), "w") as fh:
            fh.write("x")
        os.mkdir(os.path.join(self.out, "stray-dir"))
        target = os.path.join(self.dir, "junction-target")
        os.mkdir(target)
        link = os.path.join(self.out, "stray-link")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
        problems = self._verify_problems()
        self.assertIn("unexpected export file: stray.txt", problems)
        self.assertIn("unexpected export directory: stray-dir/", problems)
        if made.returncode == 0:
            self.assertIn("unexpected export link: stray-link", problems)
        self.assertTrue(os.path.exists(os.path.join(self.out, "stray.txt")), "report only: nothing deleted")

    def test_malformed_manifest_is_reported_with_its_line_number(self):
        with open(os.path.join(self.out, "manifest.txt"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("not a manifest line\n")
        problems = self._verify_problems()
        self.assertTrue(any(p.startswith("MALFORMED line 1: expected exactly 3 fields") for p in problems), problems)

    def test_missing_manifest_and_unreadable_export_directory(self):
        os.remove(os.path.join(self.out, "manifest.txt"))
        problems = self._verify_problems()
        self.assertTrue(any(p.startswith("export manifest missing") for p in problems), problems)
        # replace the export directory with a plain file: scandir fails, the problem names the exception type
        for name in os.listdir(self.out):
            os.remove(os.path.join(self.out, name))
        os.rmdir(self.out)
        with open(self.out, "w") as fh:
            fh.write("not a directory")
        problems = self._verify_problems()
        self.assertTrue(any(p.startswith("export directory unreadable:") for p in problems), problems)

    def test_export_file_missing_or_changed_is_reported(self):
        os.remove(os.path.join(self.out, "head.json"))
        self.assertIn("export file missing: head.json", self._verify_problems())

    def test_export_refresh_reports_a_ledger_error_instead_of_raising(self):
        res = export.refresh(os.path.join(self.dir, "nope", "l.db"), "j")
        self.assertIn("error", res)
        self.assertTrue(res["error"].startswith(("WfRefused", "OperationalError", "OSError", "FileNotFoundError")), res)


class PendingMarkers(Base):
    """A damaged pending marker file. `append` refuses cleanly (uncertain-write-pending); the read-only commands
    that exist to diagnose that state do not (see the expectedFailure tests)."""

    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()
        self.aid = "ab" * 16
        self.pend = paths.pending_dir(self.db, "j")
        os.makedirs(self.pend, exist_ok=True)
        self.marker = os.path.join(self.pend, f"7-{self.aid}.json")

    def _damage(self, text: str) -> None:
        with open(self.marker, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def test_append_refuses_on_a_corrupt_marker_with_the_state_named(self):
        self._damage("{not json")
        c, r = self.intent()
        self.assertEqual((c, r["code"]), (1, "uncertain-write-pending"), r)
        self.assertIn("marker unreadable", r["error"])

    def test_append_refuses_on_a_marker_that_is_not_the_named_attempt(self):
        self._damage(json.dumps({"attempt_id": "cd" * 16, "seq": 7}))
        c, r = self.intent()
        self.assertEqual((c, r["code"]), (1, "uncertain-write-pending"), r)
        self.assertIn("marker malformed", r["error"])

    @unittest.expectedFailure
    def test_status_and_verify_report_a_corrupt_marker_instead_of_crashing(self):
        """FINDING: `markers.describe` calls `read(ref)` with no ValueError handler, so a corrupt marker file makes
        status/digest/verify/close (and `resolve`) raise json.JSONDecodeError (traceback, no result) although
        describe() has its own fallback state label 'unreadable'. Spec 3A: the diagnosing commands must report. Observed:
        JSONDecodeError escapes `wf.run`."""
        self._damage("{not json")
        for cmd in (("status", "j"), ("digest", "j"), ("verify", "j")):
            with self.subTest(cmd=cmd):
                c, r = self.run_wf(*cmd, role="coordinator", session="C1")
                self.assertEqual(c, 0, r)

    @unittest.expectedFailure
    def test_status_reports_a_marker_file_holding_a_json_list(self):
        """FINDING: a marker file containing `[1]` parses, `read(ref) or {}` keeps the list, and `.get` then raises
        AttributeError in describe(): status/verify traceback. Observed: AttributeError escapes `wf.run`."""
        self._damage("[1]")
        c, r = self.run_wf("status", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)

    def test_describe_labels_a_marker_that_vanished_as_unreadable(self):
        ref = markers.MarkerRef(self.pend, 7, self.aid)
        self.assertEqual(markers.read(ref), None)
        with open(self.marker, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        (entry,) = markers.describe(self.db, "j")
        self.assertEqual((entry["seq"], entry["state"]), (7, "unreadable"))

    def test_resolve_with_no_marker_is_already_cleared(self):
        c, r = self.run_wf("resolve", "j", "7", role="coordinator", session="C1")
        self.assertEqual((c, r["outcome"]), (0, "ALREADY-CLEARED"))
