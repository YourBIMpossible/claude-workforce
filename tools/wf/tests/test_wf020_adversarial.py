"""WF-020 (Lazy) adversarial pass: hostile inputs and process-level failures against a scratch ledger (WF_TEST_TMP,
fixtures only). Each test records the CURRENT outcome at 2dedc94. Where the outcome is a crash (traceback instead of a
reported refusal) or contradicts the spec, the test asserts the spec outcome and is an expectedFailure whose docstring
names the observed behaviour: those are FINDINGS (from-lazy WF-020 FINDINGS.md), not fixes.

Spec anchors: FIRST-RELEASE-SPEC.md line 221 (Windows case-insensitive comparison, trailing dots/spaces, device names
fail closed); `rejected(malformed)` is the engine's answer to unusable input."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest

import test_wf
from test_wf import Base

from wfcore import paths

WF_PY = os.path.join(test_wf.WF_DIR, "wf.py")
BASE = {"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}


class Adv(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def sub(self, *argv: str, role="builder", session="B1", faults: str | None = None, timeout: float = 90):
        env = dict(os.environ)
        env.pop("WF_TEST_FAULTS", None)
        if faults:
            env["WF_TEST_FAULTS"] = faults
        p = subprocess.run([sys.executable, "-I", "-B", WF_PY, "--db", self.db, "--role", role, "--session", session,
                            "--json", *argv], capture_output=True, text=True, timeout=timeout, env=env)
        out = json.loads(p.stdout) if p.stdout.strip() else None
        return p.returncode, out, p.stderr

    def append_intent(self, content: str):
        return self.run_wf("append", "j", "--type", "intent", "--content", content)

    def head_seq(self) -> int:
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        return r["jobs"]["j"]["head"]["seq"]


class ContentEncodings(Adv):
    def test_accepted_unusual_but_valid_content(self):
        cases = {
            "nul": dict(BASE, note="a\x00b"),
            "emoji-rtl-crlf": dict(BASE, note="h\u00e9llo \U0001F600 \u202e rtl\r\nline2"),
            "nested-arrays": dict(BASE, deep=json.loads("[" * 200 + "]" * 200)),
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                before = self.head_seq()
                c, r = self.append_intent(json.dumps(body))
                self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
                self.assertEqual(self.head_seq(), before + 1)
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)

    def test_duplicate_keys_last_one_wins_silently(self):
        """json.loads keeps the last duplicate: `path` b is recorded, the first `path` a is dropped without notice.
        NOT A DEFECT for a record-and-verify ledger that stores what it parsed, recorded so a reader knows."""
        c, r = self.append_intent('{"path":"jobs/j/work/a","path":"jobs/j/work/b","sha_before":"","note":"n"}')
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        c, ev = self.run_wf("export", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, ev)
        with open(os.path.join(paths.export_dir(self.db, "j"), "events.jsonl"), encoding="utf-8") as fh:
            last = json.loads(fh.read().splitlines()[-1])
        self.assertEqual(json.loads(last["content"])["path"], "jobs/j/work/b")

    def test_large_event_body_from_a_file_is_committed(self):
        big = os.path.join(self.dir, "big.json")
        with open(big, "w", encoding="utf-8") as fh:
            json.dump(dict(BASE, note="x" * 2_000_000), fh)
        c, r = self.append_intent(big)
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)

    def test_contract_file_with_utf8_bom_is_rejected_as_malformed_and_crlf_is_accepted(self):
        bom = os.path.join(self.dir, "bom.json")
        with open(bom, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + json.dumps(test_wf.CONTRACT).encode())
        c, r = self.run_wf("job", "new", "jb", "--contract", bom, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertIn("Unexpected UTF-8 BOM", r["error"])
        crlf = os.path.join(self.dir, "crlf.json")
        with open(crlf, "wb") as fh:
            fh.write(json.dumps(test_wf.CONTRACT, indent=1).replace("\n", "\r\n").encode())
        c, r = self.run_wf("job", "new", "jc", "--contract", crlf, role="coordinator", session="C1")
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)

    def test_5000_digit_integer_is_a_usage_error_not_a_crash(self):
        c, r = self.append_intent('{"path":"p","sha_before":"","note":' + "9" * 5000 + "}")
        self.assertEqual((c, r["code"]), (2, "usage"), r)

    def _clean_rejection(self, content: str) -> None:
        before = self.head_seq()
        c, r = self.append_intent(content)           # an escaping ValueError/UnicodeError fails this test
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertEqual(self.head_seq(), before, "nothing may be appended")

    @unittest.expectedFailure
    def test_nan_and_infinity_in_content_are_rejected_not_crashes(self):
        """FINDING: json.loads accepts NaN/Infinity; canonical() (allow_nan=False) then raises ValueError('Out of range
        float values are not JSON compliant') which nothing catches, so `wf append` exits 1 with a traceback and no
        JSON result (verified with a real subprocess: stderr ends `ValueError ... nan`). Spec: unusable input is
        `rejected(malformed)`. Observed: ValueError escapes `wf.run`."""
        self._clean_rejection('{"path":"jobs/j/work/a","sha_before":"","note":NaN}')

    @unittest.expectedFailure
    def test_infinity_in_content_is_rejected_not_a_crash(self):
        """FINDING (same cause as the NaN test): `Infinity` -> ValueError from canonical()."""
        self._clean_rejection('{"path":"jobs/j/work/a","sha_before":"","note":Infinity}')

    @unittest.expectedFailure
    def test_lone_surrogate_in_content_is_rejected_not_a_crash(self):
        """FINDING: `"\\ud800"` parses (lone surrogate); hashing the content (`text.encode('utf-8')`) then raises
        UnicodeEncodeError ('surrogates not allowed'), uncaught: traceback, no JSON result. Observed: UnicodeEncodeError
        escapes `wf.run`."""
        self._clean_rejection(json.dumps(dict(BASE, note="\ud800")))


class IntentPathsAreUnconstrainedAtWriteTime(Adv):
    def test_escaping_absolute_and_unc_intent_paths_are_committed_then_reported_by_reconcile(self):
        """append accepts any string as an intent path; `wf reconcile` reports it as an exception (class
        matches-neither / attempt-without-completion). Designed record-and-verify behaviour: NOT A DEFECT, but the
        gate, not append, is what stops an escaping write."""
        paths_ = ["jobs/j/work/../../../../etc/x", "C:/Windows/x", "//server/share/x", "jobs/j/work/CON",
                  "jobs/j/work/a.", "jobs/j/work/" + "a" * 400]
        for p in paths_:
            with self.subTest(path=p[:40]):
                c, r = self.append_intent(json.dumps(dict(BASE, path=p)))
                self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        c, r = self.run_wf("reconcile", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        classes = {e["path"]: e["class"] for e in r["exceptions"]}
        self.assertEqual(classes["C:/Windows/x"], "matches-neither")
        self.assertEqual(classes["//server/share/x"], "matches-neither")
        self.assertEqual(classes["jobs/j/work/../../../../etc/x"], "matches-neither")
        self.assertEqual(set(classes), set(paths_), "every committed intent path is reported, none dropped")


class ProcessLevel(Adv):
    def test_eight_concurrent_appends_all_commit_with_distinct_seqs_and_a_clean_chain(self):
        before = self.head_seq()
        results: list = [None] * 8

        def worker(i: int) -> None:
            results[i] = self.sub("append", "j", "--type", "intent", "--content", json.dumps(dict(BASE, note=f"c{i}")))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        outcomes = [(rc, out and (out.get("outcome") or out.get("code"))) for rc, out, _ in results]
        self.assertEqual(outcomes, [(0, "COMMITTED")] * 8, results)
        seqs = sorted(out["seq"] for _, out, _ in results)
        self.assertEqual(seqs, list(range(before + 1, before + 9)))
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)
        self.assertEqual(r["jobs"]["j"]["pending_markers"], [])

    def test_process_terminated_between_insert_and_commit_leaves_a_resolvable_marker(self):
        env = dict(os.environ)
        env["WF_TEST_FAULTS"] = "delay-precommit:20000"
        p = subprocess.Popen([sys.executable, "-I", "-B", WF_PY, "--db", self.db, "--role", "builder", "--session", "B1",
                              "--json", "append", "j", "--type", "intent", "--content", json.dumps(BASE)], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not os.path.isdir(paths.pending_dir(self.db, "j")):
                time.sleep(0.05)
            time.sleep(0.5)
            self.assertIsNone(p.poll(), "the writer must still be inside its delay")
            p.terminate()
            p.wait(timeout=15)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
            p.stderr.close()
        before = self.head_seq()
        c, r = self.run_wf("status", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        (marker,) = r["pending_markers"]
        self.assertEqual((marker["state"], marker["seq"]), ("committing", before + 1))
        c, r = self.intent(note="blocked")
        self.assertEqual((c, r["code"]), (1, "uncertain-write-pending"), r)
        c, r = self.run_wf("resolve", "j", str(marker["seq"]), role="coordinator", session="C1")
        self.assertEqual((c, r["outcome"]), (0, "NOT-COMMITTED"), r)
        c, r = self.intent(note="after")
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)


@unittest.skipUnless(sys.platform == "win32", "job-id/path-component collisions are Windows (NTFS) behaviour")
class JobIdsAreDirectoryNames(Base):
    """A job id becomes `ledger/pending/<id>` and `jobs/<id>/export`. NTFS folds case and strips trailing dots, so ids that
    differ only by case or a trailing dot are the same directory."""

    def _two_jobs(self, a: str, b: str):
        self.new_job(a)
        c, r = self.run_wf("job", "new", b, "--contract", self.cpath, role="coordinator", session="C1")
        return c, r

    @unittest.expectedFailure
    def test_job_ids_differing_only_in_case_do_not_share_an_export_directory(self):
        """FINDING: `job new J` is accepted after `job new j`; both write `jobs/j/export` (same NTFS directory), so each
        refresh overwrites the other's events.jsonl and `wf verify` then reports 'export events.jsonl is stale relative
        to the ledger' for BOTH jobs (clean:false) with no hint of the collision. Spec line 221 requires case-insensitive
        comparison on Windows. Observed: second job accepted, verify not clean."""
        c, r = self._two_jobs("j", "J")
        self.assertEqual(c, 1, r)

    @unittest.expectedFailure
    def test_job_id_with_a_trailing_dot_is_not_accepted_as_a_second_name_for_the_same_directory(self):
        """FINDING: id `j.` matches the id regex ([A-Za-z0-9._-]) and is accepted; Windows strips the trailing dot, so
        `jobs/j./export` is `jobs/j/export` (and `ledger/pending/j.` is `pending/j`): two jobs, one directory.
        Observed: second job accepted."""
        c, r = self._two_jobs("j", "j.")
        self.assertEqual(c, 1, r)

    def test_device_name_and_double_dot_ids_are_refused_before_anything_is_written(self):
        for jid in ("nul", "j.."):
            with self.subTest(job=jid):
                c, r = self.run_wf("job", "new", jid, "--contract", self.cpath, role="coordinator", session="C1")
                self.assertEqual((c, r["code"]), (1, "refused"), r)
                self.assertIn("marker-unwritable", r["error"])
        c, r = self.run_wf("status", "nul", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "refused"), r)          # no job row was written for either id
