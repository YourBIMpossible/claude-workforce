"""T35 Windows path-resolution evidence (lane D, 2026-10-13). Spec FIRST-RELEASE-SPEC.md section 4.4 rule 2 says the
host filesystem's behaviour for links/junctions, 8.3 short names, trailing dots and alternate-stream syntax is
NOT-TESTED (G-13, G-29). This file records what NTFS on the test host actually does with real fixtures created under a
fresh scratch directory, and checks that pathpolicy classifies the OBSERVED forms consistently.

Rules of this file:
  - win32 only; every other platform is skipped with the reason recorded.
  - fixtures are created only under the test's own WF_TEST_TMP directory; no elevation, no service or policy change,
    no enabling of 8.3 name creation (an absent short name is recorded as a skip, never as a failure).
  - a fixture that cannot be created is a recorded SKIP naming the WinError; a skip is NOT evidence of NTFS behaviour.
  - asserts that depend on lane B's resolution (resolve-outside-boundary -> P5, alternate stream -> violation) run only
    when lane B is integrated (probe: `_lane_b_integrated()`); before that they are skipped with reason "lane B not
    integrated", so the file is green at dispatch-ready and gains teeth after integration.
  - a string check (what pathpolicy says about a path string) is never reported as filesystem behaviour; the two are
    kept in separate assertions and in separate lines of the evidence record (tests/logs/<test>.json via test_wf.log).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import unittest

from test_wf import Base, CONTRACT, log  # noqa: E402
from wfcore import pathpolicy  # noqa: E402

WIN = sys.platform == "win32"


def _lane_b_integrated() -> bool:
    """Probe: lane B's seam turns alternate-stream syntax into a violation; the seam stub does not."""
    classes = pathpolicy.classify(["jobs/j/work/a.txt:stream"], dict(CONTRACT))
    return bool(pathpolicy.violations(classes))


def _short_name(path: str) -> str | None:
    """GetShortPathNameW (read-only). None when the call fails or no distinct short name exists."""
    if not WIN:
        return None
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)  # type: ignore[attr-defined]
    if n == 0:
        return None
    return buf.value


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


@unittest.skipUnless(WIN, "Windows-only NTFS resolution evidence")
class T35WindowsResolution(Base):
    """Boundary = self.dir (fresh scratch). Contract write scope `work/**`, watch `cfg/**`, nothing else in scope."""

    def setUp(self) -> None:
        super().setUp()
        self.contract = dict(CONTRACT)
        self.contract.update({"write_scope": ["work/**"], "watch": ["cfg/**"], "presence_only": [], "exclusions": []})
        self.work = os.path.join(self.dir, "work")
        self.outside = os.path.join(self.dir, "..", os.path.basename(self.dir) + "-outside")
        os.makedirs(self.work)
        self.evidence: dict = {"host": sys.getwindowsversion().build if WIN else None, "lane_b": _lane_b_integrated()}

    def tearDown(self) -> None:
        log(f"{self._testMethodName}-ntfs", self.evidence)

    # ---- junction -------------------------------------------------------------------------------------------------
    def test_W1_junction_inside_scope_pointing_outside(self):
        os.makedirs(self.outside, exist_ok=True)
        _write(os.path.join(self.outside, "secret.txt"), "x")
        link = os.path.join(self.work, "jx")
        p = subprocess.run(["cmd", "/c", "mklink", "/J", link, self.outside], capture_output=True, text=True)
        self.evidence["mklink_rc"] = p.returncode
        if p.returncode != 0:
            self.evidence["skip"] = p.stderr.strip() or p.stdout.strip()
            self.skipTest(f"junction fixture not created (rc {p.returncode}): {self.evidence['skip']}")
        through = os.path.join(link, "secret.txt")
        self.assertTrue(os.path.exists(through), "NTFS: file is reachable through the junction")
        real = os.path.realpath(through)
        self.evidence["realpath"] = real
        # filesystem fact: realpath resolves the junction to the outside directory
        self.assertTrue(real.lower().startswith(os.path.realpath(self.outside).lower()), real)
        self.assertFalse(real.lower().startswith(os.path.realpath(self.dir).lower() + os.sep + "work"), real)
        # string fact: the unresolved relative form LOOKS like P1
        classes = pathpolicy.classify([_rel(self.dir, through)], self.contract)
        self.evidence["classify_unresolved"] = classes
        if not _lane_b_integrated():
            self.assertIn("work/jx/secret.txt", classes["P1"], "seam stub classifies by glob only (recorded)")
            self.skipTest("lane B not integrated: resolve-outside-boundary assertion deferred")
        # lane B: the resolved form must be reported outside the boundary, never in scope
        resolved_rel = _rel(self.dir, real)
        classes_r = pathpolicy.classify([resolved_rel], self.contract)
        self.evidence["classify_resolved"] = classes_r
        self.assertNotIn(resolved_rel, classes_r["P1"], classes_r)
        self.assertIn(resolved_rel, classes_r["P5"], classes_r)

    # ---- 8.3 short names ------------------------------------------------------------------------------------------
    def test_W2_short_name_maps_to_same_long_path(self):
        long_dir = os.path.join(self.work, "LongDirectoryNameForShortNameTest")
        os.makedirs(long_dir)
        target = os.path.join(long_dir, "document-with-long-name.txt")
        _write(target, "x")
        short = _short_name(target)
        self.evidence["short_name"] = short
        if short is None or short.lower() == target.lower():
            self.skipTest("no distinct 8.3 short name on this volume (8.3 creation disabled or not generated); "
                          "NOT enabled by this test")
        self.assertTrue(os.path.samefile(short, target), "NTFS: short and long names reach the same file")
        self.evidence["short_rel"] = _rel(self.dir, short)
        if not _lane_b_integrated():
            self.skipTest("lane B not integrated: short-name classification assertion deferred")
        a = pathpolicy.classify([_rel(self.dir, short)], self.contract)
        b = pathpolicy.classify([_rel(self.dir, target)], self.contract)
        # lane B may normalise the short name (then identical classes) or fail closed (violation); it must not
        # classify the short form as P1 while silently treating it as a different path from the long form
        if pathpolicy.violations(a):
            self.evidence["short_name_policy"] = "fail-closed"
        else:
            self.assertEqual({k: sorted(v) for k, v in a.items() if k in pathpolicy.CLASSES and k != "P1"},
                             {k: sorted(v) for k, v in b.items() if k in pathpolicy.CLASSES and k != "P1"})
            self.evidence["short_name_policy"] = "normalised"

    # ---- trailing dot ---------------------------------------------------------------------------------------------
    def test_W3_trailing_dot_name(self):
        plain = os.path.join(self.work, "note.txt")
        dotted = "\\\\?\\" + os.path.join(self.work, "note.txt.")
        try:
            _write(dotted, "dotted")
        except OSError as exc:
            self.evidence["skip"] = str(exc)
            self.skipTest(f"trailing-dot fixture not created: {exc}")
        self.evidence["plain_exists_after_dotted_create"] = os.path.exists(plain)
        self.evidence["dotted_listed_as"] = os.listdir(self.work)
        # filesystem fact recorded, not asserted: whether Win32 strips the dot when opened without the \\?\ prefix
        if not _lane_b_integrated():
            self.skipTest("lane B not integrated: trailing-dot classification assertion deferred")
        classes = pathpolicy.classify(["work/note.txt."], self.contract)
        self.evidence["classify_trailing_dot"] = classes
        # lane B either normalises (P1 after stripping) or fails closed; it must not leave the raw dotted string in P1
        self.assertFalse("work/note.txt." in classes["P1"] and not pathpolicy.violations(classes), classes)

    # ---- alternate data stream ------------------------------------------------------------------------------------
    def test_W4_alternate_data_stream_syntax(self):
        host = os.path.join(self.work, "host.txt")
        _write(host, "main")
        try:
            _write(host + ":side", "stream")
        except OSError as exc:
            self.evidence["skip"] = str(exc)
            self.skipTest(f"alternate stream fixture not created: {exc}")
        self.assertEqual(_read(host), "main", "NTFS: main stream unchanged by writing a named stream")
        self.assertEqual(_read(host + ":side"), "stream", "NTFS: named stream readable through file:stream")
        self.evidence["dir_listing_hides_stream"] = os.listdir(self.work) == ["host.txt"]
        classes = pathpolicy.classify(["work/host.txt:side"], self.contract)
        self.evidence["classify_stream"] = classes
        if not _lane_b_integrated():
            self.assertFalse(pathpolicy.violations(classes), "seam stub: stream syntax not yet a violation (recorded)")
            self.skipTest("lane B not integrated: alternate-stream violation assertion deferred")
        self.assertTrue(pathpolicy.violations(classes), ("spec 4.4 rule 2: stream syntax is a violation", classes))

    # ---- case and dot-dot (platform facts, always asserted) --------------------------------------------------------
    def test_W5_case_insensitive_and_dotdot(self):
        target = os.path.join(self.work, "Mixed.TXT")
        _write(target, "x")
        self.assertTrue(os.path.exists(os.path.join(self.work, "mixed.txt")), "NTFS: case-insensitive lookup")
        self.evidence["case_insensitive"] = True
        classes = pathpolicy.classify(["WORK/mixed.txt"], self.contract)
        self.assertIn("WORK/mixed.txt", classes["P1"], ("case-insensitive glob match held since pre-seam", classes))
        # real escape: the outside dir is a SIBLING of the boundary, so the relative form starts with ".."
        escape = _rel(self.dir, os.path.join(self.outside, "secret.txt"))
        self.assertTrue(escape.startswith("../"), escape)
        classes = pathpolicy.classify([escape], self.contract)
        self.evidence["classify_dotdot"] = classes
        if not _lane_b_integrated():
            self.skipTest("lane B not integrated: '..' resolves-outside-boundary assertion deferred")
        self.assertNotIn(escape, classes["P1"], classes)
        self.assertIn(escape, classes["P5"], classes)


if __name__ == "__main__":
    unittest.main()
