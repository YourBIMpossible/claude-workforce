"""Grant-check corpus (B2 slice 3, lane S3G, Plexy): INTERFACE-CONTRACT-S3.md §4 and §8 S3G. The shared corpus
tools/wf/tests/data/grant_check_corpus.json (a list of {name, rows_fixture, now, role, action, path, expect_allow,
expect_reason}; >= 30 cases covering every reason, every authority state, renewal and invalidation) runs through
`grant.check` here; the S3W e2e runs `wf grant-check` over the same file, and slice 4's gate reuses it byte for byte.
Expectations in the corpus were written by hand from the contract, not computed by `grant.check`.

`rows_fixture` uses the step vocabulary of test_t34_grant_lifecycle.build_rows; the author binding passed to `check`
is the latest `bind` row for the checked role (what the wiring passes), else None. Also pinned: `grant.block` is
byte-identical to `commands._block` (the wiring switches to it).
"""
from __future__ import annotations

import json
import os
import unittest

from test_t34_grant_lifecycle import BASE_GRANT, JOB, binding_of, build_rows  # noqa: E402
from test_wf import CONTRACT  # noqa: E402
from wfcore import commands, grant  # noqa: E402

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grant_check_corpus.json")
KEYS = {"name", "rows_fixture", "now", "role", "action", "path", "expect_allow", "expect_reason"}
REASONS = {"allowed", "action-not-granted", "path-not-granted", "path-excluded", "role-not-in-ars", "unbound-role",
           "binding-outside-ars"} | (set(grant.AUTH_STATES) - {"in-force"})


def load() -> list[dict]:
    with open(CORPUS, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Corpus(unittest.TestCase):
    def test_corpus_shape_and_coverage(self):
        cases = load()
        self.assertIsInstance(cases, list)
        self.assertGreaterEqual(len(cases), 30)
        self.assertEqual(len({c["name"] for c in cases}), len(cases))
        for c in cases:
            with self.subTest(case=c.get("name")):
                self.assertEqual(set(c), KEYS)
                self.assertIs(type(c["expect_allow"]), bool)
                self.assertEqual(c["expect_allow"], c["expect_reason"] == "allowed")
                self.assertTrue(grant._instant(c["now"]))
        reasons = {c["expect_reason"] for c in cases}
        self.assertEqual(reasons, REASONS, "every reason and every non-in-force state is covered")
        states = {grant.authority(build_rows(c["rows_fixture"]), job=JOB, now=c["now"])["state"] for c in cases}
        self.assertEqual(states, set(grant.AUTH_STATES), "every authority state appears")
        names = " ".join(c["name"] for c in cases)
        self.assertIn("renewal", names); self.assertIn("binding_outside_new_ars", names)

    def test_every_case_through_grant_check(self):
        for c in load():
            with self.subTest(case=c["name"]):
                rows = build_rows(c["rows_fixture"])
                got = grant.check(rows, job=JOB, now=c["now"], role=c["role"], action=c["action"], path=c["path"],
                                  contract=CONTRACT, binding=binding_of(rows, c["role"]))
                self.assertEqual(set(got), {"allow", "reason", "authority", "grant_id", "block_hash"})
                self.assertEqual((got["allow"], got["reason"]), (c["expect_allow"], c["expect_reason"]), got)
                a = grant.authority(rows, job=JOB, now=c["now"])
                self.assertEqual((got["authority"], got["grant_id"], got["block_hash"]),
                                 (a["state"], a["grant_id"], a["block_hash"]))

    def test_guard_and_check_share_the_decision(self):
        """Owner item 2: for every corpus ledger, a W event is refused by the guard exactly when grant-check denies on
        authority (outside D-S3-1's none/proposed row), with the matching reason."""
        refusal = {"expired": "authority-expired", "revoked": "authority-revoked", "voided": "authority-voided",
                   "malformed": "authority-malformed", "cancelled": "cancelled"}
        from wfcore.db import WfRejected
        for c in load():
            rows = build_rows(c["rows_fixture"])
            state = grant.authority(rows, job=JOB, now=c["now"])["state"]
            with self.subTest(case=c["name"], state=state):
                if state in refusal:
                    with self.assertRaises(WfRejected) as cm:
                        grant.mutation_guard(rows, job=JOB, now=c["now"], etype="intent", binding=None)
                    self.assertEqual(cm.exception.reason, refusal[state])
                else:
                    grant.mutation_guard(rows, job=JOB, now=c["now"], etype="intent", binding=None)

    def test_block_equals_commands_block(self):
        """§2: grant.block is byte-identical to commands._block on every proposed revision in the corpus."""
        seen = 0
        for c in load():
            for r in build_rows(c["rows_fixture"]):
                if r["type"] == "grant" and json.loads(r["content"]).get("state") == "proposed":
                    g = json.loads(r["content"])
                    self.assertEqual(grant.block(JOB, g, r["seq"]), commands._block(JOB, g, r["seq"]))
                    seen += 1
        self.assertGreater(seen, 30)
        g = dict(BASE_GRANT, contract_sha256="a" * 64)
        del g["exclusions"]; del g["delegation"]                                 # defaults applied identically
        self.assertEqual(grant.block("other-job", g, 7), commands._block("other-job", g, 7))
        with self.assertRaises(KeyError):
            grant.block(JOB, {"grant_id": "G1"}, 1)


if __name__ == "__main__":
    unittest.main()
