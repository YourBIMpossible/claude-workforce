"""T34 / T15 CLI authority matrix — lane S3M (MyBuddy), WF-006. Contract: INTERFACE-CONTRACT-S3.md sections 2-5, 8
(S3M row) and AMENDMENTS S3-A1 (CATEGORY keys, cancel = category O, `--check-role`, `now` format).

Every verb under test runs as a fresh subprocess `python -I tools/wf/wf.py --json ...` on a fixtures-tmp ledger
(`test_wf.Base`); the setup steps use the same CLI. Implicit mode (no `wf bind`): the matrix rows that need no
binding are the ones named by the task; explicit-binding rows (binding-outside-ars) are S3G's.

Subcases (test names):
  T34 revocation ................ test_T34_revocation_ends_authority_at_once
  T34 explicit expiry ........... test_T34_explicit_expiry
  T34 time expiry ............... test_T34_time_expiry_needs_no_row
  T34 amendment (new proposal) .. test_T34_amendment_by_new_proposal_voids_current_revision
  T34 renewal ................... test_T34_renewal_back_to_in_force_after_revocation
  T15 ledger half ............... test_T15_work_events_refused_under_revoked_grant
  cancellation .................. test_cancellation_W_and_L_refused_O_still_runs
  D-S3-1 none/proposed .......... test_D_S3_1_none_and_proposed_record_work_but_grant_check_denies
  grant-check vs guard .......... test_grant_check_agrees_with_mutation_guard_per_state
NOT RUN here (contract section 8): contract amendment and environment/stance change (no command in R1); gate halves
of T15/T34 (slice 4); explicit-binding rows after renewal (S3G).

Wiring: `wf grant revoke|expire`, `wf grant-check`, `wf cancel`, `wf log-intervention` and `status.authority` land on
White's `sprint/white-b2s3-wiring`; on a tree without them every test is skipped with the reason
"wiring not present on this tree". A skip is not completion; White re-runs on the combined tree.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest

from test_wf import Base, GRANT, WF_DIR  # noqa: E402  (sets sys.path to the wf package; fresh WF_TEST_TMP)
from wfcore import commands  # noqa: E402

WIRED = importlib.util.find_spec("wfcore.grant") is not None and all(
    hasattr(commands, f) for f in ("grant_revoke", "grant_expire", "grant_check_cmd", "cancel", "log_intervention"))
SKIP = "wiring not present on this tree (sprint/white-b2s3-wiring: grant revoke/expire, grant-check, cancel, log-intervention)"

INTENT = '{"path":"jobs/j/work/a.txt","sha_before":"","note":"%s"}'
DELIVERED = '{"claims":[{"claim":"c","evidence":["jobs/j/work/a.txt"]}]}'
CHECK = ("--check-role", "builder", "--action", "write", "--path", "jobs/j/work/a.txt")  # inside write_scope/resources
PAST_EXPIRY = "2020-01-01T00:00:00Z"
CANCEL_LIMITATION_KEY = "does not stop running processes"  # section 5 limitation text, distinctive fragment


def turn(job: str, what: str) -> str:
    return f"owner turn: {what} on job {job}"


@unittest.skipUnless(WIRED, SKIP)
class T34CliMatrix(Base):
    # ---------------------------------------------------------------- CLI driver
    def wf(self, *argv: str, role: str = "coordinator", session: str = "C1", timeout: float = 60.0) -> tuple[int, dict]:
        cmd = [sys.executable, "-I", "-B", os.path.join(WF_DIR, "wf.py"), "--json", "--db", self.db,
               "--role", role, "--session", session, *argv]
        env = dict(os.environ); env.pop("WF_TEST_FAULTS", None)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        try:
            res = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            res = {"raw": p.stdout}
        res.setdefault("_stderr", p.stderr[-1500:])
        return p.returncode, res

    def ok(self, code_res: tuple[int, dict]) -> dict:
        code, res = code_res
        self.assertEqual(code, 0, res); self.assertTrue(res.get("ok", True), res)
        return res

    def assert_rejected(self, code_res: tuple[int, dict], reason: str) -> dict:
        code, res = code_res
        self.assertEqual(code, 1, res)
        self.assertEqual(res.get("code"), "rejected", res)
        self.assertTrue(res.get("error", "").startswith(f"rejected({reason})"), (reason, res))
        return res

    # ---------------------------------------------------------------- fixture steps (all through the CLI)
    def grant_file(self, name: str, **override) -> str:
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({**GRANT, **override}, fh)
        return p

    def job(self, job: str = "j") -> None:
        self.ok(self.wf("job", "new", job, "--contract", self.cpath))

    def propose(self, job: str = "j", gpath: str | None = None) -> dict:
        return self.ok(self.wf("grant", "propose", job, "--grant", gpath or self.gpath))

    def activate(self, job: str = "j", gpath: str | None = None) -> str:
        r = self.propose(job, gpath); h = r["block_hash"]
        self.ok(self.wf("grant", "activate", job, "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                        "--turn", turn(job, "activate")))
        return h

    def authority(self, job: str = "j") -> dict:
        st = self.ok(self.wf("status", job))
        self.assertIn("authority", st, st)
        return st["authority"]

    def work(self, job: str = "j", note: str = "w") -> tuple[int, dict]:
        """A W-category event (intent) by the builder; implicit mode."""
        return self.wf("append", job, "--type", "intent", "--content", INTENT % note, role="builder", session="B1")

    def check(self, job: str = "j") -> tuple[int, dict]:
        return self.wf("grant-check", job, *CHECK, role="builder", session="B1")

    def revoke(self, job: str = "j", role: str = "coordinator") -> tuple[int, dict]:
        return self.wf("grant", "revoke", job, "--reason", "owner withdrew approval", "--turn", turn(job, "revoke"), role=role)

    def expire(self, job: str = "j") -> tuple[int, dict]:
        return self.wf("grant", "expire", job, "--reason", "owner-set early expiry", "--turn", turn(job, "expire"))

    def cancel(self, job: str = "j", role: str = "builder", session: str = "B1") -> tuple[int, dict]:
        return self.wf("cancel", job, "--reason", "owner cancelled the job", "--turn", turn(job, "cancel"),
                       role=role, session=session)

    def rows(self, job: str = "j") -> list[dict]:
        from wfcore import db as dbm
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(conn, job)
        finally:
            conn.close()

    def types(self, job: str = "j") -> list[str]:
        return [r["type"] for r in self.rows(job)]

    def assert_agree(self, job: str, *, state: str, check_allow: bool, check_reason: str | None,
                     work_ok: bool, work_reason: str | None) -> None:
        """grant-check and the mutation guard answer the same state on the same snapshot (section 4)."""
        a = self.authority(job)
        self.assertEqual(a["state"], state, a)
        code, r = self.check(job)
        self.assertEqual((code, r.get("allow")), (0 if check_allow else 1, check_allow), (state, r))
        self.assertEqual(r.get("authority"), state, r)
        if check_reason is not None:
            self.assertEqual(r.get("reason"), check_reason, r)
        n = len(self.rows(job))
        if work_ok:
            self.ok(self.work(job, note=f"under-{state}"))
            self.assertEqual(len(self.rows(job)), n + 1)
        else:
            self.assert_rejected(self.work(job, note=f"under-{state}"), work_reason or "")
            self.assertEqual(len(self.rows(job)), n, "a refused W event writes nothing")

    # ---------------------------------------------------------------- T34
    def test_T34_revocation_ends_authority_at_once(self):
        self.job(); h = self.activate()
        self.assert_agree("j", state="in-force", check_allow=True, check_reason=None, work_ok=True, work_reason=None)
        r = self.ok(self.revoke())
        self.assertEqual(self.types()[-1], "grant-revoked")
        row = json.loads(self.rows()[-1]["content"])
        self.assertEqual((row["block_hash"], row["grant_id"]), (h, GRANT["grant_id"]))
        self.assertIn("j", row["owner_turn"])
        a = self.authority(); self.assertEqual((a["state"], a["block_hash"]), ("revoked", h), a)
        self.assert_agree("j", state="revoked", check_allow=False, check_reason="revoked",
                          work_ok=False, work_reason="authority-revoked")
        self.assert_rejected(self.revoke(), "no-active-grant")          # nothing left to revoke (D-S3-4)
        self.assertEqual(self.types().count("grant-revoked"), 1)

    def test_T34_explicit_expiry(self):
        self.job(); h = self.activate()
        self.ok(self.expire())
        self.assertEqual(self.types()[-1], "grant-expired")
        a = self.authority()
        self.assertEqual((a["state"], a["reason"], a["block_hash"]), ("expired", "explicit", h), a)
        self.assert_agree("j", state="expired", check_allow=False, check_reason="expired",
                          work_ok=False, work_reason="authority-expired")
        self.assert_rejected(self.expire(), "no-active-grant")
        self.assertEqual(self.types().count("grant-expired"), 1)

    def test_T34_time_expiry_needs_no_row(self):
        self.job(); self.activate(gpath=self.grant_file("grant_past.json", expiry=PAST_EXPIRY))
        a = self.authority()
        self.assertEqual((a["state"], a["reason"], a["expiry"]), ("expired", "time", PAST_EXPIRY), a)
        self.assertNotIn("grant-expired", self.types())                  # time expiry writes nothing
        self.assert_agree("j", state="expired", check_allow=False, check_reason="expired",
                          work_ok=False, work_reason="authority-expired")
        self.assert_rejected(self.revoke(), "no-active-grant")          # nothing in force to end
        self.assertNotIn("grant-revoked", self.types())

    def test_T34_amendment_by_new_proposal_voids_current_revision(self):
        self.job(); h1 = self.activate()
        self.ok(self.work(note="before amendment"))
        amended = self.grant_file("grant_v2.json", actions=["write", "read"])  # amended actions; resources unchanged
        r = self.propose(gpath=amended)                                  # L allowed while in force (D-S3-3)
        self.assertNotEqual(r["block_hash"], h1)
        a = self.authority(); self.assertEqual(a["state"], "voided", a)
        self.assert_agree("j", state="voided", check_allow=False, check_reason="voided",
                          work_ok=False, work_reason="authority-voided")
        h2 = r["block_hash"]
        self.ok(self.wf("grant", "activate", "j", "--owner-hash", h2[:8], "--quote", f"approve G1 {h2[:8]}",
                        "--turn", turn("j", "activate amended")))
        a = self.authority(); self.assertEqual((a["state"], a["block_hash"]), ("in-force", h2), a)
        self.assert_agree("j", state="in-force", check_allow=True, check_reason=None, work_ok=True, work_reason=None)

    def test_T34_renewal_back_to_in_force_after_revocation(self):
        self.job(); h1 = self.activate()
        self.ok(self.revoke())
        self.assert_rejected(self.work(note="revoked"), "authority-revoked")
        h2 = self.activate()                                             # propose + activate = renewal
        self.assertNotEqual(h1, h2)
        a = self.authority(); self.assertEqual((a["state"], a["block_hash"]), ("in-force", h2), a)
        self.assert_agree("j", state="in-force", check_allow=True, check_reason=None, work_ok=True, work_reason=None)
        self.ok(self.revoke())                                           # the renewed revision can be ended too
        self.assertEqual(self.types().count("grant-revoked"), 2)
        self.assertEqual(self.authority()["state"], "revoked")

    # ---------------------------------------------------------------- T15 (ledger half)
    def test_T15_work_events_refused_under_revoked_grant(self):
        self.job(); self.activate(); self.ok(self.revoke())
        n = len(self.rows())
        self.assert_rejected(self.work(note="intent"), "authority-revoked")
        self.assert_rejected(self.wf("append", "j", "--type", "delivered", "--content", DELIVERED,
                                     role="builder", session="B1"), "authority-revoked")
        self.assertEqual(len(self.rows()), n, "no W row recorded under a revoked grant")
        code, r = self.check(); self.assertEqual((code, r["allow"], r["reason"]), (1, False, "revoked"), r)
        # O-category rows still pass the guard under revoked (blocker is O per S3-A1)
        self.ok(self.wf("append", "j", "--type", "blocker", "--content", '{"reason":"authority revoked"}',
                        role="builder", session="B1"))
        self.assertEqual(self.types()[-1], "blocker")

    # ---------------------------------------------------------------- cancellation
    def test_cancellation_W_and_L_refused_O_still_runs(self):
        self.job(); self.activate(); self.ok(self.work(note="before cancel"))
        r = self.ok(self.cancel())                                       # anyone may record it (builder here)
        self.assertEqual(self.types()[-1], "job-cancelled")
        self.assertIn(CANCEL_LIMITATION_KEY, json.dumps(r), "cancel output states the enforcement limitation")
        d = self.ok(self.wf("digest", "j"))
        self.assertIn(CANCEL_LIMITATION_KEY, json.dumps(d), "digest states the enforcement limitation")
        self.assert_agree("j", state="cancelled", check_allow=False, check_reason="cancelled",
                          work_ok=False, work_reason="cancelled")
        # L refused, no grant row added (the activated revision already holds proposed + activated rows)
        grants = self.types().count("grant")
        self.assert_rejected(self.wf("grant", "propose", "j", "--grant", self.gpath), "cancelled")
        self.assertEqual(self.types().count("grant"), grants)
        # second cancellation refused
        self.assert_rejected(self.cancel(role="coordinator", session="C1"), "cancelled")
        self.assertEqual(self.types().count("job-cancelled"), 1)
        # O still runs: revoke passes the guard (category O) and is then refused by validate_revoke because the
        # authority state is `cancelled`, not in-force (White, WF-006 review F1) — never a `cancelled` refusal.
        self.assert_rejected(self.revoke(), "no-active-grant")
        self.assertNotIn("grant-revoked", self.types())
        self.ok(self.wf("log-intervention", "j", "--label", "restart", "--necessity", "unknown",
                        "--minutes", "unknown", "--note", "owner restarted the worker"))
        self.assertEqual(self.types()[-1], "intervention")
        d = self.ok(self.wf("digest", "j"))
        self.assertEqual(d["active"]["interventions"]["unknown_count"], 1, d["active"].get("interventions"))
        self.ok(self.wf("recover", "begin", "j", "--reason", "cancelled job audit", "--turn", turn("j", "recover")))
        self.assertEqual(self.types()[-1], "recovery-opened")
        self.ok(self.wf("recover", "close", "j", "--turn", turn("j", "recover close")))
        self.assertEqual(self.types()[-1], "recovery-closed")
        self.ok(self.wf("job", "abandon", "j", "--reason", "cancelled"))
        self.ok(self.wf("close", "j", "--intervention", "none"))
        self.assertEqual(self.types()[-1], "close")
        self.assertEqual(self.authority()["state"], "cancelled")        # final (D-S3-2)
        # after abandon + close the engine's own refusal ("job j is abandoned", code refused) runs before the
        # authority guard (White, F2): assert only that work is refused and nothing is added
        n = len(self.rows())
        code, r = self.work(note="after close")
        self.assertEqual((code, r.get("code")), (1, "refused"), r)
        self.assertEqual(len(self.rows()), n)

    # ---------------------------------------------------------------- D-S3-1 and the agreement table
    def test_D_S3_1_none_and_proposed_record_work_but_grant_check_denies(self):
        self.job()
        self.assert_agree("j", state="none", check_allow=False, check_reason="none", work_ok=True, work_reason=None)
        self.propose()
        self.assert_agree("j", state="proposed", check_allow=False, check_reason="proposed", work_ok=True, work_reason=None)

    def test_grant_check_agrees_with_mutation_guard_per_state(self):
        """One job per state in one ledger; for each, `wf grant-check` and a W event are answered from the same
        state: allow <=> recorded, and the deny reason is the state the guard names (D-S3-1 rows excepted and pinned)."""
        table = {  # state -> (setup, check_allow, check_reason, work_ok, work_reason)
            "none": (lambda j: None, False, "none", True, None),
            "proposed": (lambda j: self.propose(j), False, "proposed", True, None),
            "in-force": (lambda j: self.activate(j), True, None, True, None),
            "revoked": (lambda j: (self.activate(j), self.ok(self.revoke(j))), False, "revoked", False, "authority-revoked"),
            "expired": (lambda j: (self.activate(j), self.ok(self.expire(j))), False, "expired", False, "authority-expired"),
            "voided": (lambda j: (self.activate(j), self.propose(j)), False, "voided", False, "authority-voided"),
            "cancelled": (lambda j: (self.activate(j), self.ok(self.cancel(j))), False, "cancelled", False, "cancelled"),
        }
        for state, (setup, allow, reason, work_ok, work_reason) in table.items():
            with self.subTest(state=state):
                job = "j" + state.replace("-", "")
                self.job(job); setup(job)
                self.assert_agree(job, state=state, check_allow=allow, check_reason=reason,
                                  work_ok=work_ok, work_reason=work_reason)
        # time expiry as a separate job: same answers as explicit expiry
        with self.subTest(state="expired-time"):
            self.job("jtime"); self.activate("jtime", gpath=self.grant_file("grant_past2.json", expiry=PAST_EXPIRY))
            self.assert_agree("jtime", state="expired", check_allow=False, check_reason="expired",
                              work_ok=False, work_reason="authority-expired")


if __name__ == "__main__":
    unittest.main(verbosity=2)
