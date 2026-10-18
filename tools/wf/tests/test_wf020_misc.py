"""WF-020 (Lazy): remaining small gaps (from-lazy WF-020 GAP-LIST.md): `job abandon` blank reason, `verify --gate-file /
--gate-sha` pairing, `gate print-install` with a tree that has no hook, and grant.authority's `malformed` states.
Current behaviour at 2dedc94 is asserted."""
from __future__ import annotations

import json
import os

import test_wf
from test_wf import Base

from wfcore import grant


class AbandonAndVerifyFlags(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()

    def test_abandon_with_a_blank_reason_is_a_usage_error_and_changes_nothing(self):
        c, r = self.run_wf("job", "abandon", "j", "--reason", " ", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"), r)
        self.assertIn("--reason is required", r["error"])
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)
        self.assertEqual(r["jobs"]["j"]["head"]["seq"], 1)

    def test_gate_file_and_gate_sha_must_be_given_together(self):
        gate_file = os.path.join(self.dir, "hook.py")
        with open(gate_file, "w") as fh:
            fh.write("x")
        for argv in (("--gate-file", gate_file), ("--gate-sha", "a" * 64)):
            with self.subTest(argv=argv):
                c, r = self.run_wf("verify", "j", *argv, role="coordinator", session="C1")
                self.assertEqual((c, r["code"]), (2, "usage"), r)
                self.assertIn("--gate-file and --gate-sha are used together", r["error"])


class GatePrintInstall(Base):
    def test_a_tree_without_the_hook_source_is_refused(self):
        empty = os.path.join(self.dir, "tree")
        os.makedirs(empty)
        c, r = self.run_wf("gate", "print-install", "--hooks-dir", os.path.join(self.dir, "hooks"), "--root", self.dir,
                           "--wf-dir", empty, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("hook source not found", r["error"])
        self.assertEqual(sorted(os.listdir(empty)), [], "print-install writes nothing")


def grant_row(seq: int, content: dict) -> dict:
    return {"seq": seq, "type": "grant", "content": json.dumps(content)}


class AuthorityMalformedStates(Base):
    NOW = "2026-10-18T00:00:00Z"

    def test_latest_grant_row_in_a_state_other_than_proposed_or_activated(self):
        res = grant.authority([grant_row(1, {"state": "revoked"})], job="j", now=self.NOW)
        self.assertEqual((res["state"], res["reason"]), ("malformed", "grant-state"))

    def test_activated_grant_that_cites_no_proposal_is_a_revision_mismatch(self):
        res = grant.authority([grant_row(1, {"state": "activated"})], job="j", now=self.NOW)
        self.assertEqual((res["state"], res["reason"]), ("malformed", "revision-mismatch"))

    def test_malformed_clock_instant_is_denial(self):
        res = grant.authority([grant_row(1, {"state": "activated"})], job="j", now="yesterday")
        self.assertEqual((res["state"], res["reason"]), ("malformed", "now-malformed"))

    def test_no_grant_and_cancelled_job(self):
        self.assertEqual(grant.authority([], job="j", now=self.NOW)["state"], "none")
        res = grant.authority([{"seq": 1, "type": "job-cancelled", "content": "{}"}], job="j", now=self.NOW)
        self.assertEqual((res["state"], res["reason"]), ("cancelled", "job-cancelled"))


class BaselineDoesNotFollowJunctions(Base):
    """`job new` snapshots the read scope (tools/wf/** in the harness contract). A junction inside it is skipped, not
    followed: the files behind it are not baselined and the limitation says so (changeset.py link handling)."""

    def test_junction_under_the_scope_is_skipped_with_a_limitation(self):
        import subprocess
        import sys
        if sys.platform != "win32":
            self.skipTest("uses an NTFS junction")
        scope = os.path.join(self.dir, "tools", "wf")
        os.makedirs(scope)
        with open(os.path.join(scope, "a.txt"), "w") as fh:
            fh.write("a")
        behind = os.path.join(self.dir, "behind")
        os.makedirs(behind)
        with open(os.path.join(behind, "secret.txt"), "w") as fh:
            fh.write("s")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(scope, "jlink"), behind], capture_output=True,
                              text=True)
        if made.returncode != 0:
            self.skipTest(f"junction not creatable: {made.stdout.strip()}")
        self.new_job()
        events = os.path.join(self.dir, "jobs", "j", "export", "events.jsonl")
        with open(events, encoding="utf-8") as fh:
            contract = json.loads(json.loads(fh.readline())["content"])
        baseline = contract["baseline_manifest"]
        self.assertEqual(sorted(baseline["entries"]), ["tools/wf/a.txt"])
        self.assertTrue(any("links and junctions are skipped, not resolved" in x for x in baseline["limitations"]),
                        baseline["limitations"])
