"""WF-019 (Plexy, read-only audit): demonstrated gaps between FIRST-RELEASE-SPEC Rev 5.3 §8 and tools/wf at 2dedc94.

Each test asserts the SPEC's expected outcome and is marked `unittest.expectedFailure`, because the product at 2dedc94
behaves otherwise; the docstring states the exact observed behaviour. These tests are evidence for the R1 decision, not
a fix and not a claim that the gap must be closed (see WF-019 TRACEABILITY-T01-T37.md and PROPOSED-TESTS.md).
"""
from __future__ import annotations

import unittest

from test_wf import Base  # noqa: E402


class T29QuoteContent(Base):
    @unittest.expectedFailure
    def test_T29_activate_with_quote_lacking_grant_id_and_block_hash_stays_unbound(self):
        """Spec §3 step 4 (FIRST-RELEASE-SPEC.md:84) and §5 rule 7 (:235), case T29 (:304): `wf grant activate`
        succeeds only if the recorded quote text or the owner's referenced block contains the grant id and the block
        hash; otherwise `rejected(unbound-approval)`.

        Observed at 2dedc94: with the correct `--owner-hash` prefix, a quote of "q" (no grant id, no block hash) is
        COMMITTED and authority becomes in-force. `commands.grant_activate` only requires a non-empty quote
        (commands.py:264-265). Known and retained by owner decision D-022 ("T29 stays PARTIAL; the quote-content and
        provenance gaps are retained; no waiver", 04-DECISIONS.md:40-41)."""
        self.new_job()
        code, res = self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        h = res["block_hash"]
        code, res = self.run_wf("grant", "activate", "j", "--owner-hash", h[:8], "--quote", "q", "--turn", "owner turn",
                                role="coordinator", session="C1")
        self.assertIn("rejected(unbound-approval)", res.get("error", ""), res)
        code, res = self.run_wf("status", "j")
        self.assertNotEqual(res["axes"]["authority"], "in-force", res)


if __name__ == "__main__":
    unittest.main()
