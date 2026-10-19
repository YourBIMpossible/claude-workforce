"""WF-020 (Lazy): remaining small gaps (from-lazy WF-020 GAP-LIST.md): `job abandon` blank reason, `verify --gate-file /
--gate-sha` pairing, `gate print-install` with a tree that has no hook, grant.authority's `malformed` states, and a
link under the baselined scope. Written at 2dedc94; updated for fix1/integration dfd6e85 (WF-020 review F1, WF-039
W39-S3: the link test asserts the per-link limitation and runs on POSIX with a symlink)."""
from __future__ import annotations

import json
import os

import test_wf
from test_wf import Base
from test_wf020_store import make_dir_link

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


class BaselineDoesNotFollowLinks(Base):
    """`job new` snapshots the read scope (tools/wf/** in the harness contract). A link inside it (an NTFS junction on
    Windows, a symlink on POSIX: changeset._is_link treats both alike) is skipped, not followed: the files behind it are
    not baselined, and a per-link limitation names it (changeset.py link handling)."""

    def test_link_under_the_scope_is_skipped_with_a_per_link_limitation(self):
        """W39-S3: asserts the per-link `skipped link or reparse point: tools/wf/jlink` text; the generic limitation
        ("links and junctions are skipped, not resolved") is in every baseline and proves nothing about this link."""
        scope = os.path.join(self.dir, "tools", "wf")
        os.makedirs(scope)
        with open(os.path.join(scope, "a.txt"), "w") as fh:
            fh.write("a")
        behind = os.path.join(self.dir, "behind")
        os.makedirs(behind)
        with open(os.path.join(behind, "secret.txt"), "w") as fh:
            fh.write("s")
        why = make_dir_link(os.path.join(scope, "jlink"), behind)
        if why is not None:
            self.skipTest(why)
        self.new_job()
        events = os.path.join(self.dir, "jobs", "j", "export", "events.jsonl")
        with open(events, encoding="utf-8") as fh:
            contract = json.loads(json.loads(fh.readline())["content"])
        baseline = contract["baseline_manifest"]
        self.assertEqual(sorted(baseline["entries"]), ["tools/wf/a.txt"])
        self.assertIn("skipped link or reparse point: tools/wf/jlink", baseline["limitations"])
        self.assertFalse(any("secret.txt" in x for x in baseline["limitations"] + list(baseline["entries"])))
