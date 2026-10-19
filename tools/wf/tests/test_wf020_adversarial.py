"""WF-020 (Lazy) adversarial pass: hostile inputs and process-level failures against a scratch ledger (WF_TEST_TMP,
fixtures only). Written at 2dedc94, where findings F1, F2, F6 and F7 were expectedFailures; updated for
fix1/integration dfd6e85 (WF-020 review F1, WF-039 W39-M1/S1/S2): FIX1 E2 and I1 fixed them, so these are ordinary
tests of the FIX1 behaviour, and each fails on its own assertion (an exception escaping `wf.run` is reported through
`self.fail`) if the defect returns. Findings: from-lazy WF-020 FINDINGS.md.

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

    def test_contract_bom_rejected_crlf_accepted(self):
        """Renamed from `test_contract_file_with_utf8_bom_is_rejected_as_malformed_and_crlf_is_accepted`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
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

    def test_5000_digit_integer_is_a_usage_error(self):
        """Renamed from `test_5000_digit_integer_is_a_usage_error_not_a_crash`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        c, r = self.append_intent('{"path":"p","sha_before":"","note":' + "9" * 5000 + "}")
        self.assertEqual((c, r["code"]), (2, "usage"), r)

    def _clean_rejection(self, content: str, reason: str) -> None:
        before = self.head_seq()
        try:
            c, r = self.append_intent(content)
        except Exception as exc:  # noqa: BLE001 -- the WF-020 defect: an exception escaping wf.run is a FAIL here
            self.fail(f"{type(exc).__name__} escaped wf.run instead of rejected(malformed): {exc}")
        self.assertEqual((c, r.get("code")), (1, "rejected"), r)
        self.assertEqual(r["error"], f"rejected(malformed): {reason}")
        self.assertEqual(self.head_seq(), before, "nothing may be appended")

    def test_nan_in_content_is_rejected_not_a_crash(self):
        """F1, fixed by FIX1 E2: `NaN` in the content is `rejected(malformed)` at input, nothing appended. At 2dedc94
        json.loads accepted it and canonical() (allow_nan=False) raised ValueError out of `wf.run` (a traceback).
        Renamed from `test_nan_and_infinity_in_content_are_rejected_not_crashes`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        self._clean_rejection('{"path":"jobs/j/work/a","sha_before":"","note":NaN}', "non-finite number NaN is not JSON")

    def test_infinity_in_content_is_rejected_not_a_crash(self):
        """F1 (same cause as the NaN test), fixed by FIX1 E2: `Infinity` is `rejected(malformed)`."""
        self._clean_rejection('{"path":"jobs/j/work/a","sha_before":"","note":Infinity}',
                              "non-finite number Infinity is not JSON")

    def test_lone_surrogate_in_content_is_rejected(self):
        """F2, fixed by FIX1 E2: a lone surrogate (`"\\ud800"`) is `rejected(malformed)` naming the field. At 2dedc94
        hashing the content raised UnicodeEncodeError out of `wf.run`.
        Renamed from `test_lone_surrogate_in_content_is_rejected_not_a_crash`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        self._clean_rejection(json.dumps(dict(BASE, note="\ud800")),
                              "$.note: string contains a lone surrogate (not encodable as UTF-8)")


class IntentPathsAreUnconstrainedAtWriteTime(Adv):
    def test_escaping_intent_paths_commit_then_reconcile(self):
        """append accepts any string as an intent path; `wf reconcile` reports it as an exception (class
        matches-neither / attempt-without-completion). Designed record-and-verify behaviour: NOT A DEFECT, but the
        gate, not append, is what stops an escaping write. Renamed from
        `test_escaping_absolute_and_unc_intent_paths_are_committed_then_reported_by_reconcile` (84 characters): on
        Windows without long paths that name pushed the fixture marker path past 260 characters in a deep worktree."""
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
    def test_eight_concurrent_appends_distinct_seqs(self):
        """Renamed from `test_eight_concurrent_appends_all_commit_with_distinct_seqs_and_a_clean_chain`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
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

    def test_kill_before_commit_leaves_resolvable_marker(self):
        """Renamed from `test_process_terminated_between_insert_and_commit_leaves_a_resolvable_marker`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
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


class JobIdsAreDirectoryNames(Base):
    """A job id becomes `ledger/pending/<id>` and `jobs/<id>/export`. FIX1 I1 refuses, at `job new` and on every
    platform, an id that a filesystem could merge with another name or a device (validate.check_new_job_id). F6 and F7
    were expectedFailures on Windows at 2dedc94; the refusal is a string rule, so it is tested everywhere (W39-S1)."""

    def _second_job(self, a: str, b: str):
        self.new_job(a)
        c, r = self.run_wf("job", "new", b, "--contract", self.cpath, role="coordinator", session="C1")
        return c, r

    def _refused_before_writing(self, c: int, r: dict, job: str, reason: str) -> None:
        self.assertEqual((c, r.get("code")), (1, "rejected"), r)
        self.assertEqual(r["error"], f"rejected(malformed): job id {job!r} {reason}")
        c, r = self.run_wf("status", job, role="coordinator", session="C1")
        self.assertEqual((c, r.get("error")), (1, f"job {job} not found"), r)    # no job row was written for the id

    def test_job_ids_differing_only_in_case_are_refused(self):
        """F6, fixed by FIX1 I1: `job new J` after `job new j` is `rejected(malformed)`. At 2dedc94 it was accepted and,
        on NTFS, both jobs wrote `jobs/j/export` (see JobIdsOnNtfs)."""
        c, r = self._second_job("j", "J")
        self._refused_before_writing(c, r, "J", "equals existing job 'j' case-insensitively")

    def test_job_id_with_a_trailing_dot_is_refused(self):
        """F7, fixed by FIX1 I1: `job new j.` is `rejected(malformed)` (a trailing dot is refused on every platform; NTFS
        strips it, so `jobs/j./` would be `jobs/j/`)."""
        c, r = self._second_job("j", "j.")
        self._refused_before_writing(c, r, "j.", "must not end in a dot or a space")

    def test_device_name_and_double_dot_ids_are_refused_before_anything_is_written(self):
        """FIX1 I1 (W39-S2: at 2dedc94 these were `refused ... marker-unwritable` on Windows, after the attempt began):
        a device name, with or without an extension, and `..` are `rejected(malformed)` before the ledger exists."""
        for jid, reason in (("nul", "is a Windows device name"), ("nul.txt", "is a Windows device name"),
                            ("j..", "must not end in a dot or a space"), ("a..b", "must not contain '..'")):
            with self.subTest(job=jid):
                c, r = self.run_wf("job", "new", jid, "--contract", self.cpath, role="coordinator", session="C1")
                self.assertEqual((c, r.get("code")), (1, "rejected"), r)
                self.assertEqual(r["error"], f"rejected(malformed): job id {jid!r} {reason}")
        self.assertFalse(os.path.exists(self.db), "nothing was written, not even the ledger")


@unittest.skipUnless(sys.platform == "win32", "NTFS folds case and strips trailing dots (Windows only)")
class JobIdsOnNtfs(Base):
    """The NTFS half of F6/F7 (W39-S1): `jobs/J` and `jobs/j.` name the directory of job j, which is why I1 refuses
    those ids; with the refusals, job j's export is not overwritten and verify stays clean."""

    def test_ntfs_aliases_same_dir_and_j_stays_clean(self):
        """Renamed from `test_case_and_trailing_dot_variants_name_the_same_directory_and_job_j_stays_clean`
        (WF-020 review F1 follow-up 2: test-name length and Windows path length)."""
        self.new_job("j")
        jobs = os.path.join(self.dir, "jobs")
        for alias in ("J", "j."):
            with self.subTest(alias=alias):
                self.assertTrue(os.path.samefile(os.path.join(jobs, "j"), os.path.join(jobs, alias)))
                c, r = self.run_wf("job", "new", alias, "--contract", self.cpath, role="coordinator", session="C1")
                self.assertEqual((c, r.get("code")), (1, "rejected"), r)
        c, r = self.run_wf("verify", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)
        self.assertEqual(sorted(r["jobs"]), ["j"])
