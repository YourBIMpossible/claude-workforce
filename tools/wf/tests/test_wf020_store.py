"""WF-020 (Lazy): regression tests for export/manifest/marker/changeset paths that no earlier test exercised
(from-lazy WF-020 GAP-LIST.md). Written at 2dedc94, where findings F3, F4 and F5 were expectedFailures; updated for
fix1/integration dfd6e85 (WF-020 review F1, WF-039 W39-M1/M2/S5): FIX1 E3 and E4 fixed them, so these are ordinary tests
of the FIX1 behaviour, and each fails on its own assertion if the defect returns (an escaping exception is reported
through `self.fail`). Findings: from-lazy WF-020 FINDINGS.md.

Runs inside the test_wf.py harness (fresh scratch ledger per test under WF_TEST_TMP)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import test_wf
from test_wf import Base

from wfcore import export, manifest, markers, paths

SHA = "a" * 64


def make_dir_link(link: str, target: str) -> str | None:
    """Create a directory link `link` -> `target` without following it later: an NTFS junction on Windows (no privilege
    needed), a symlink elsewhere. Returns None when the link was made, else why not, so the caller can skip on a
    missing capability (W39-M2: no unguarded `cmd`)."""
    if sys.platform == "win32":
        try:
            made = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True,
                                  timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"junction not creatable: {type(exc).__name__}: {exc}"
        return None if made.returncode == 0 else f"junction not creatable: {(made.stdout + made.stderr).strip()}"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        return f"symlink not creatable: {type(exc).__name__}: {exc}"
    return None


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

    def test_unicode_digit_size_is_a_malformed_manifest_not_a_crash(self):
        """F5, fixed by FIX1 E4 (ASCII digits only): a size of '²' is ManifestMalformed with the field-2 reason. At
        2dedc94 str.isdigit() accepted it and int('²') raised a bare ValueError (ManifestMalformed is not a ValueError,
        manifest.py:14), which `export.check` let escape."""
        try:
            manifest.parse_line(1, f"{SHA}  ²  f")
        except manifest.ManifestMalformed as exc:
            self.assertEqual((exc.line_no, exc.reason), (1, "field 2 is not a non-negative integer"))
        except ValueError as exc:
            self.fail(f"bare ValueError escaped parse_line instead of ManifestMalformed: {exc}")
        else:
            self.fail("a Unicode-digit size was accepted")


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

    def test_stray_file_and_dir_reported_not_removed(self):
        """Renamed from `test_stray_file_and_directory_entries_are_reported_not_removed`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        with open(os.path.join(self.out, "stray.txt"), "w") as fh:
            fh.write("x")
        os.mkdir(os.path.join(self.out, "stray-dir"))
        problems = self._verify_problems()
        self.assertIn("unexpected export file: stray.txt", problems)
        self.assertIn("unexpected export directory: stray-dir/", problems)
        self.assertTrue(os.path.exists(os.path.join(self.out, "stray.txt")), "report only: nothing deleted")
        self.assertTrue(os.path.isdir(os.path.join(self.out, "stray-dir")), "report only: nothing deleted")

    def test_link_entry_reported_not_followed(self):
        """A junction (Windows) or a symlink (POSIX) in the export directory is `unexpected export link` (export.strays:
        `is_symlink() or is_junction()`); skipped where the platform cannot create one (W39-M2).
        Renamed from `test_a_link_entry_is_reported_as_a_link_not_followed_or_removed`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        target = os.path.join(self.dir, "link-target")
        os.mkdir(target)
        link = os.path.join(self.out, "stray-link")
        why = make_dir_link(link, target)
        if why is not None:
            self.skipTest(why)
        problems = self._verify_problems()
        self.assertIn("unexpected export link: stray-link", problems)
        self.assertNotIn("unexpected export directory: stray-link/", problems)
        self.assertTrue(os.path.lexists(link) and os.path.isdir(target), "report only: nothing deleted")

    def test_malformed_manifest_reported_with_line(self):
        """Renamed from `test_malformed_manifest_is_reported_with_its_line_number`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        with open(os.path.join(self.out, "manifest.txt"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("not a manifest line\n")
        problems = self._verify_problems()
        self.assertTrue(any(p.startswith("MALFORMED line 1: expected exactly 3 fields") for p in problems), problems)

    def test_unicode_digit_size_reported_by_verify(self):
        """F5 end to end (FIX1 E4): `wf verify` reports the '²' size as a MALFORMED manifest line instead of raising.
        Renamed from `test_unicode_digit_size_in_the_manifest_is_reported_by_verify`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        with open(os.path.join(self.out, "manifest.txt"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{SHA}  ²  events.jsonl\n")
        try:
            problems = self._verify_problems()
        except ValueError as exc:
            self.fail(f"bare ValueError escaped wf verify: {exc}")
        self.assertTrue(any(p.startswith("MALFORMED line 1: field 2 is not a non-negative integer") for p in problems),
                        problems)

    def test_missing_manifest_and_unreadable_export(self):
        """Renamed from `test_missing_manifest_and_unreadable_export_directory`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
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

    def test_export_refresh_reports_ledger_error(self):
        """Renamed from `test_export_refresh_reports_a_ledger_error_instead_of_raising`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        res = export.refresh(os.path.join(self.dir, "nope", "l.db"), "j")
        self.assertIn("error", res)
        self.assertTrue(res["error"].startswith(("WfRefused", "OperationalError", "OSError", "FileNotFoundError")), res)


class PendingMarkers(Base):
    """A damaged pending marker file. `append` refuses cleanly (uncertain-write-pending), and since FIX1 E3 the read-only
    commands that exist to diagnose that state report it as `marker-corrupt` (at 2dedc94 they raised)."""

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

    def test_append_refuses_corrupt_marker_state_named(self):
        """Renamed from `test_append_refuses_on_a_corrupt_marker_with_the_state_named`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        self._damage("{not json")
        c, r = self.intent()
        self.assertEqual((c, r["code"]), (1, "uncertain-write-pending"), r)
        self.assertIn("marker unreadable", r["error"])

    def test_append_refuses_marker_of_other_attempt(self):
        """Renamed from `test_append_refuses_on_a_marker_that_is_not_the_named_attempt`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        self._damage(json.dumps({"attempt_id": "cd" * 16, "seq": 7}))
        c, r = self.intent()
        self.assertEqual((c, r["code"]), (1, "uncertain-write-pending"), r)
        self.assertIn("marker malformed", r["error"])

    def _diagnose(self, cmd: str) -> dict:
        try:
            c, r = self.run_wf(cmd, "j", role="coordinator", session="C1")
        except Exception as exc:  # noqa: BLE001 -- the WF-020 defect: an exception escaping wf.run is a FAIL here
            self.fail(f"{type(exc).__name__} escaped wf {cmd} on a damaged marker: {exc}")
        self.assertEqual(c, 0, r)
        return r

    def _assert_corrupt_listed(self, entries: list, why: str) -> None:
        (m,) = entries
        self.assertEqual((m["seq"], m["attempt_id"], m["state"]), (7, self.aid, "marker-corrupt"), m)
        self.assertEqual(m["problem"], f"marker-corrupt: {self.marker}: {why}")

    def test_corrupt_marker_reported_not_a_crash(self):
        """F3, fixed by FIX1 E3: status, digest and verify report a corrupt marker file as `marker-corrupt` with a
        problem naming its path; verify is not clean and digest asks the owner to resolve it. At 2dedc94
        `markers.describe` let json.JSONDecodeError escape `wf.run` (traceback, no result).
        Renamed from `test_status_and_verify_report_a_corrupt_marker_instead_of_crashing`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        self._damage("{not json")
        why = "JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
        self._assert_corrupt_listed(self._diagnose("status")["pending_markers"], why)
        v = self._diagnose("verify")
        self.assertFalse(v["clean"], v)
        self._assert_corrupt_listed(v["jobs"]["j"]["pending_markers"], why)
        d = self._diagnose("digest")
        self.assertIn({"item": "pending write marker seq 7",
                       "reason": "marker state marker-corrupt: the write outcome is uncertain",
                       "recommendation": "run `wf resolve j 7` before any further write", "evidence": [self.marker]},
                      d["needs_owner"])

    def test_status_reports_list_marker_as_corrupt(self):
        """F4, fixed by FIX1 E3: a marker file holding `[1]` is listed as `marker-corrupt` ("not a JSON object (list)").
        At 2dedc94 `.get` on the list raised AttributeError out of `wf.run`.
        Renamed from `test_status_reports_a_marker_file_holding_a_json_list`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        self._damage("[1]")
        self._assert_corrupt_listed(self._diagnose("status")["pending_markers"], "not a JSON object (list)")
        self._assert_corrupt_listed(self._diagnose("verify")["jobs"]["j"]["pending_markers"], "not a JSON object (list)")

    def test_describe_vanished_marker_is_unreadable(self):
        """A marker listed by `list_markers` that is gone when describe() reads it (another process cleared it in
        between) is labelled `unreadable`, not an error (markers._corrupt_reason: a vanished file is (None, None)).
        Renamed from `test_describe_labels_a_marker_that_vanished_as_unreadable`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        ref = markers.MarkerRef(self.pend, 7, self.aid)
        self.assertFalse(os.path.exists(ref.path))
        self.assertEqual(markers.read(ref), None)
        with mock.patch.object(markers, "list_markers", return_value=[ref]):
            (entry,) = markers.describe(self.db, "j")
        self.assertEqual((entry["seq"], entry["attempt_id"], entry["state"]), (7, self.aid, "unreadable"))
        self.assertNotIn("problem", entry)

    def test_describe_empty_marker_is_unreadable(self):
        """A marker file holding `{}` (no state) is labelled `unreadable`, not `marker-corrupt` (it is a JSON object).
        W39-S5: this case was the body of the vanished-marker test before; it is its own test now.
        Renamed from `test_describe_labels_an_empty_marker_object_as_unreadable`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        with open(self.marker, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        (entry,) = markers.describe(self.db, "j")
        self.assertEqual((entry["seq"], entry["state"]), (7, "unreadable"))
        self.assertNotIn("problem", entry)

    def test_resolve_with_no_marker_is_already_cleared(self):
        c, r = self.run_wf("resolve", "j", "7", role="coordinator", session="C1")
        self.assertEqual((c, r["outcome"]), (0, "ALREADY-CLEARED"))
