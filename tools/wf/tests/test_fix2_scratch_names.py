"""FIX2 #40 (package FIX2, batch T, lane F2TL): short per-test scratch folders.

`test_wf.Base.setUp` names each test's scratch folder `t-<8 hex>`, from the sha256 of the full test id, instead of
the method name. So the deepest scratch path (a pending marker under the folder's ledger) no longer grows with the
test name: on Windows hosts without long paths the suite failed with `marker-unwritable` once root length + test-name
length passed 118 (WF-062). A collision fails loudly, and each run maps folder -> test id in `scratch-map.tsv`
under its scratch root. Log names longer than `LOG_NAME_MAX` are shortened, so test-name-derived log paths cannot
reach MAX_PATH either."""
from __future__ import annotations

import os
import re
import unittest

import test_wf
from test_wf import LOGDIR, LOG_NAME_MAX, SCRATCH_MAP, TMP, Base, log, scratch_name


def _ids(suite) -> list[str]:
    out: list[str] = []
    for t in suite:
        out += _ids(t) if isinstance(t, unittest.TestSuite) else [t.id()]
    return out


class ScratchFolder(Base):
    def test_folder_is_the_short_id_of_the_test_id(self):
        name = os.path.basename(self.dir)
        self.assertEqual(name, scratch_name(self.id()))
        self.assertRegex(name, r"^t-[0-9a-f]{8}$")
        self.assertEqual(os.path.dirname(self.dir), TMP)

    def test_mapping_is_logged_once_for_this_test(self):
        with open(SCRATCH_MAP, encoding="utf-8") as fh:
            rows = [line.rstrip("\n").split("\t") for line in fh]
        mine = [r for r in rows if r[0] == os.path.basename(self.dir)]
        self.assertEqual(mine, [[os.path.basename(self.dir), self.id()]])

    def test_harness_log_names_use_the_short_id(self):
        before = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
        self.run_wf("status", "j", role="coordinator", session="C1")
        new = sorted(set(os.listdir(LOGDIR)) - before)
        self.assertEqual(len(new), 1, new)
        self.assertRegex(new[0], rf"^{re.escape(self.scratch)}-\d+\.json$")


class Collision(unittest.TestCase):
    def test_a_collision_fails_loudly_and_creates_nothing(self):
        class Probe(Base):
            def runTest(self) -> None:
                pass

        probe = Probe()
        name = scratch_name(probe.id())
        self.assertNotIn(name, test_wf._SCRATCH_IDS)
        test_wf._SCRATCH_IDS[name] = "another.Test.id"
        try:
            with self.assertRaises(SystemExit) as cm:
                probe.setUp()
            self.assertIn("collides", str(cm.exception))
            self.assertIn("another.Test.id", str(cm.exception))
            self.assertFalse(os.path.exists(os.path.join(TMP, name)))
        finally:
            del test_wf._SCRATCH_IDS[name]


class AllLoadedTests(unittest.TestCase):
    def test_short_ids_are_unique_and_bounded_across_all_loaded_tests(self):
        ids = sorted(set(_ids(unittest.TestLoader().discover(test_wf.HERE, pattern="test_*.py", top_level_dir=test_wf.HERE))))
        self.assertGreater(len(ids), 500, "the whole suite should be loaded")
        names: dict[str, list[str]] = {}
        for i in ids:
            names.setdefault(scratch_name(i), []).append(i)
        self.assertEqual({k: v for k, v in names.items() if len(v) > 1}, {})
        self.assertEqual({len(n) for n in names}, {10})


class LogNames(unittest.TestCase):
    def test_a_long_log_name_is_shortened_and_kept_distinct(self):
        a, b = "x" * 150 + "-a", "x" * 150 + "-b"
        before = set(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else set()
        log(a, {"n": 1})
        log(b, {"n": 2})
        new = sorted(set(os.listdir(LOGDIR)) - before)
        self.assertEqual(len(new), 2, new)
        for f in new:
            self.assertLessEqual(len(f), LOG_NAME_MAX + len(".json"))

    def test_a_short_log_name_is_unchanged(self):
        log("fix2-40-short", {"n": 1})
        self.assertTrue(os.path.isfile(os.path.join(LOGDIR, "fix2-40-short.json")))


if __name__ == "__main__":
    unittest.main()
