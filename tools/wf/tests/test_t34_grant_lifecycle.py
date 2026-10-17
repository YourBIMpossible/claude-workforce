"""T34 grant lifecycle and the command-authority matrix (B2 slice 3, lane S3G, Plexy): INTERFACE-CONTRACT-S3.md §1
schemas, §2 `authority()` states and reasons, §3 matrix through `mutation_guard`, §5 cancel / intervention, §6, §7
dispatch, §8 S3G; AMENDMENT S3-A1 (CATEGORY keys = event types, cancel is O, `now` format). Contract copy: WorkForce
collab folder `contracts/b2s3` (SHA256SUMS verified). Spec: FIRST-RELEASE-SPEC.md §2B l.48, §4.1, §6.3, T29, T34.

Fixtures are row lists built by `build_rows(steps)` in exactly the shape `validate.build_row` writes (canonical
content with the injected `contract_sha256`, activated rows copying the proposed fields as `commands.grant_activate`
does). `RealLedgerShape` pins that equivalence: a ledger written through the real `wf` CLI gives the same block hash
and the same `authority()` result as the hand-built rows for the same grant. Hand-built rows are required for the
revision-mismatch cases (tampered activated field, tampered contract pin), which no command can produce.

Step vocabulary (shared with tests/data/grant_check_corpus.json, `rows_fixture`): `contract`; `propose` (+ `grant`
overrides); `activate`; `revoke`; `expire`; `cancel`; `bind` (role, session, tools, paths); `intervention` (label,
necessity, minutes, note); `revoke_named` (grant_id, block_hash); `tamper_activated` (field, value); `tamper_pin`.
The `tamper_*` steps have no CLI equivalent (a CLI replay skips those cases); every other step maps to one `wf` verb.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from typing import Any

from test_wf import Base, CONTRACT  # noqa: E402
from wfcore import commands, db as dbm, grant, txn, validate  # noqa: E402
from wfcore.canon import canonical, sha256_obj  # noqa: E402
from wfcore.db import OpLog, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

JOB = "j"
NOW = "2026-10-17T12:00:00Z"
EXPIRY = "2026-12-31T00:00:00Z"
CSHA = sha256_obj(CONTRACT)
ROLES = {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
         "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
         "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["jobs/j/work/**"]}}
BASE_GRANT = {"grant_id": "G1", "actions": ["write", "review"], "resources": ["jobs/j/work/**"],
              "exclusions": ["jobs/j/work/secret/**"], "ars": {"roles": ROLES}, "delegation": {},
              "expiry": EXPIRY, "revocation_rule": "owner"}
TURN = "owner turn: decision on job j"


def build_rows(steps: list[dict], job: str = JOB) -> list[dict]:
    """Rows as `validate.build_row` would write them (see the module docstring for the step vocabulary)."""
    rows: list[dict] = []

    def add(etype: str, content: dict, role: str = "coordinator", session: str = "C1", **kw: Any) -> None:
        c = dict(content)
        c["contract_sha256"] = kw.get("pin", CSHA)
        rows.append({"job": job, "seq": len(rows) + 1, "type": etype, "msg_id": f"m{len(rows) + 1}",
                     "author_role": role, "session_id": session, "generation": 1, "reply_to": None,
                     "grant_ref": None, "ts": "2026-10-17T00:00:00.000Z", "content": canonical(c)})

    def latest(state: str) -> tuple[dict, dict] | None:
        for r in reversed(rows):
            if r["type"] == "grant" and json.loads(r["content"]).get("state") == state:
                return r, json.loads(r["content"])
        return None

    for st in steps:
        op = st["op"]
        if op == "contract":
            add("contract", {"contract": CONTRACT})
        elif op == "propose":
            g = copy.deepcopy(BASE_GRANT)
            g.update(copy.deepcopy(st.get("grant", {})))
            g["state"] = "proposed"
            add("grant", g)
        elif op == "activate":
            prow, pc = latest("proposed")  # type: ignore[misc]
            content = {k: v for k, v in pc.items() if k != "contract_sha256"}
            content.update(state="activated", block_hash=grant.block(job, pc, prow["seq"])[1],
                           source_turn="owner turn", quote="approve G1", proposed_seq=prow["seq"])
            add("grant", content)
        elif op in ("revoke", "expire"):
            _, ac = latest("activated")  # type: ignore[misc]
            add("grant-revoked" if op == "revoke" else "grant-expired",
                {"grant_id": ac["grant_id"], "block_hash": ac["block_hash"], "reason": st.get("reason", "owner"),
                 "owner_turn": TURN}, role="owner", session="O1")
        elif op == "revoke_named":
            add("grant-revoked", {"grant_id": st["grant_id"], "block_hash": st["block_hash"], "reason": "owner",
                                  "owner_turn": TURN}, role="owner", session="O1")
        elif op == "cancel":
            add("job-cancelled", {"reason": st.get("reason", "owner stop"), "owner_turn": TURN},
                role="owner", session="O1")
        elif op == "bind":
            add("bind", {"role": st["role"], "session": st["session"], "generation": 1, "tools": st["tools"],
                         "paths": st["paths"], "grant_seq": len(rows), "replaces_seq": None})
        elif op == "intervention":
            add("intervention", {k: st[k] for k in ("label", "necessity", "minutes", "note")})
        elif op == "tamper_activated":
            r, c = latest("activated")  # type: ignore[misc]
            c[st["field"]] = st["value"]
            r["content"] = canonical(c)
        elif op == "tamper_pin":
            r, c = latest("activated")  # type: ignore[misc]
            c["contract_sha256"] = "0" * 64
            r["content"] = canonical(c)
        else:
            raise ValueError(f"unknown fixture step {op!r}")
    return rows


def binding_of(rows: list[dict], role: str) -> dict | None:
    """The author's explicit binding as the wiring passes it ({role, tools, paths}), latest per role, or None."""
    found = None
    for r in rows:
        if r["type"] == "bind":
            c = json.loads(r["content"])
            if c.get("role") == role:
                found = {"role": role, "tools": c["tools"], "paths": c["paths"]}
    return found


S = {  # named state fixtures
    "none": [{"op": "contract"}],
    "proposed": [{"op": "contract"}, {"op": "propose"}],
    "in-force": [{"op": "contract"}, {"op": "propose"}, {"op": "activate"}],
    "expired": [{"op": "contract"}, {"op": "propose"}, {"op": "activate"}, {"op": "expire"}],
    "revoked": [{"op": "contract"}, {"op": "propose"}, {"op": "activate"}, {"op": "revoke"}],
    "voided": [{"op": "contract"}, {"op": "propose"}, {"op": "activate"}, {"op": "propose", "grant": {"grant_id": "G2"}}],
    "malformed": [{"op": "contract"}, {"op": "propose"}, {"op": "activate"},
                  {"op": "tamper_activated", "field": "actions", "value": ["write", "review", "deploy"]}],
    "cancelled": [{"op": "contract"}, {"op": "propose"}, {"op": "activate"}, {"op": "cancel"}],
}


def auth(steps: list[dict], now: str = NOW) -> dict:
    return grant.authority(build_rows(steps), job=JOB, now=now)


# ===================================================================================================== §2 states

class AuthorityStates(unittest.TestCase):
    def test_every_named_state(self):
        for state, steps in S.items():
            with self.subTest(state=state):
                a = auth(steps)
                self.assertEqual(a["state"], state, a)
                self.assertEqual(set(a), {"state", "reason", "grant_id", "block_hash", "proposed_seq",
                                          "activation_seq", "expiry", "grant"})
        self.assertEqual(set(grant.AUTH_STATES), set(S))

    def test_in_force_fields(self):
        rows = build_rows(S["in-force"])
        a = grant.authority(rows, job=JOB, now=NOW)
        bh = json.loads(rows[2]["content"])["block_hash"]
        self.assertEqual((a["reason"], a["grant_id"], a["block_hash"], a["proposed_seq"], a["activation_seq"],
                          a["expiry"]), (None, "G1", bh, 2, 3, EXPIRY))
        self.assertEqual(a["grant"]["actions"], ["write", "review"])

    def test_reasons(self):
        cases = {
            "none": ("none", "no-grant"), "proposed": ("proposed", "not-activated"),
            "voided": ("voided", "new-proposal"), "revoked": ("revoked", "revoked"),
            "expired": ("expired", "explicit"), "cancelled": ("cancelled", "job-cancelled"),
            "malformed": ("malformed", "revision-mismatch")}
        for name, want in cases.items():
            with self.subTest(fixture=name):
                a = auth(S[name])
                self.assertEqual((a["state"], a["reason"]), want)

    def test_time_expiry_boundary(self):
        self.assertEqual(auth(S["in-force"], now="2026-12-30T23:59:59Z")["state"], "in-force")
        a = auth(S["in-force"], now=EXPIRY)                                  # now >= expiry
        self.assertEqual((a["state"], a["reason"]), ("expired", "time"))
        self.assertEqual(auth(S["in-force"], now="2027-01-01T00:00:00Z")["reason"], "time")

    def test_revision_mismatch_variants(self):
        """Owner item 2: revision-mismatched authority is denial (malformed / revision-mismatch)."""
        base = S["in-force"]
        for name, extra in (("actions widened", {"op": "tamper_activated", "field": "actions", "value": ["deploy"]}),
                            ("resources widened", {"op": "tamper_activated", "field": "resources", "value": ["**"]}),
                            ("ars changed", {"op": "tamper_activated", "field": "ars", "value": {}}),
                            ("expiry extended", {"op": "tamper_activated", "field": "expiry", "value": "2999-01-01T00:00:00Z"}),
                            ("block hash", {"op": "tamper_activated", "field": "block_hash", "value": "f" * 64}),
                            ("proposed_seq", {"op": "tamper_activated", "field": "proposed_seq", "value": 1}),
                            ("contract pin", {"op": "tamper_pin"})):
            with self.subTest(case=name):
                a = auth(base + [extra])
                self.assertEqual((a["state"], a["reason"]), ("malformed", "revision-mismatch"), a)

    def test_expiry_malformed_and_other_fields(self):
        for bad in ("2026-12-31", "2026-12-31T00:00:00.000Z", "2026-02-30T00:00:00Z", 20261231, None):
            with self.subTest(expiry=bad):
                a = auth([{"op": "contract"}, {"op": "propose", "grant": {"expiry": bad}}, {"op": "activate"}])
                self.assertEqual((a["state"], a["reason"]), ("malformed", "expiry-malformed"))
        a = auth([{"op": "contract"}, {"op": "propose", "grant": {"actions": "write"}}, {"op": "activate"}])
        self.assertEqual((a["state"], a["reason"]), ("malformed", "field:actions"))
        a = auth([{"op": "contract"}, {"op": "propose", "grant": {"ars": "owner"}}, {"op": "activate"}])
        self.assertEqual((a["state"], a["reason"]), ("malformed", "field:ars"))
        self.assertEqual(auth(S["in-force"], now="2026-10-17T12:00:00.123Z")["reason"], "now-malformed")

    def test_revoke_and_expire_must_name_the_current_revision(self):
        rows = build_rows(S["in-force"])
        a = grant.authority(rows, job=JOB, now=NOW)
        other = build_rows(S["in-force"] + [{"op": "revoke_named", "grant_id": "G9", "block_hash": a["block_hash"]}])
        self.assertEqual(grant.authority(other, job=JOB, now=NOW)["state"], "in-force")
        other = build_rows(S["in-force"] + [{"op": "revoke_named", "grant_id": "G1", "block_hash": "e" * 64}])
        self.assertEqual(grant.authority(other, job=JOB, now=NOW)["state"], "in-force")

    def test_revoke_before_activation_does_not_end_a_later_revision(self):
        """Revocation ends the revision it names, after its activation; a renewal is a new revision."""
        steps = S["revoked"] + [{"op": "propose", "grant": {"grant_id": "G2"}}, {"op": "activate"}]
        a = auth(steps)
        self.assertEqual((a["state"], a["grant_id"]), ("in-force", "G2"))

    def test_renewal_paths(self):
        """revoke / explicit expiry / time expiry / void / malformed -> propose + activate -> in-force."""
        renew = [{"op": "propose", "grant": {"grant_id": "G2", "expiry": "2027-06-30T00:00:00Z"}}, {"op": "activate"}]
        for name in ("revoked", "expired", "voided", "malformed"):
            with self.subTest(after=name):
                a = auth(S[name] + renew)
                self.assertEqual((a["state"], a["grant_id"]), ("in-force", "G2"))
        a = auth(S["in-force"] + renew, now="2027-01-15T00:00:00Z")          # G1 time-expired, G2 renews
        self.assertEqual((a["state"], a["grant_id"]), ("in-force", "G2"))
        self.assertEqual(auth(S["cancelled"] + renew)["state"], "cancelled")  # D-S3-2: cancellation is final

    def test_fresh_derivation_every_call(self):
        rows = build_rows(S["in-force"])
        self.assertEqual(grant.authority(rows, job=JOB, now=NOW)["state"], "in-force")
        rows += build_rows(S["revoked"])[3:]                                   # append the revoke row (seq 4)
        self.assertEqual(grant.authority(rows, job=JOB, now=NOW)["state"], "revoked")
        self.assertEqual(grant.authority(build_rows(S["in-force"]), job=JOB, now=EXPIRY)["state"], "expired")


# ===================================================================================================== §3 matrix

#: expected outcome per category x state: None = allowed, else the rejection reason (§3 table, S3-A1)
EXPECT = {
    "O": {s: None for s in S},
    "L": {**{s: None for s in S}, "cancelled": "cancelled"},
    "W": {"none": None, "proposed": None, "in-force": None, "expired": "authority-expired",
          "revoked": "authority-revoked", "voided": "authority-voided", "malformed": "authority-malformed",
          "cancelled": "cancelled"},
}


class MutationGuardMatrix(unittest.TestCase):
    def test_category_keys_exactly_as_amended(self):
        """S3-A1 item 1: event types only, exact sets."""
        by = {c: sorted(k for k, v in grant.CATEGORY.items() if v == c) for c in "ROLW"}
        self.assertEqual(by["O"], sorted(["grant-revoked", "grant-expired", "job-cancelled", "intervention", "close",
                                          "recovery-opened", "exception-acknowledged", "recovery-closed",
                                          "recovery-decision", "stop-confirmed", "ack-reconcile", "blocker",
                                          "reconcile-report"]))
        self.assertEqual(by["L"], ["grant"])
        self.assertEqual(by["W"], sorted(["intent", "receipt", "handoff", "delivered", "ack-assignment", "bind",
                                          "accept"]))
        self.assertEqual(by["R"], [])
        self.assertNotIn("contract", grant.CATEGORY)

    def test_every_event_type_in_every_state(self):
        """The full matrix: every CATEGORY event type x every authority state through mutation_guard."""
        n = 0
        for state, steps in S.items():
            rows = build_rows(steps)
            self.assertEqual(grant.authority(rows, job=JOB, now=NOW)["state"], state)
            for etype, cat in grant.CATEGORY.items():
                want = EXPECT[cat][state]
                with self.subTest(state=state, etype=etype):
                    if want is None:
                        grant.mutation_guard(rows, job=JOB, now=NOW, etype=etype, binding=None)
                    else:
                        with self.assertRaises(WfRejected) as cm:
                            grant.mutation_guard(rows, job=JOB, now=NOW, etype=etype, binding=None)
                        self.assertEqual(cm.exception.reason, want)
                n += 1
        self.assertEqual(n, len(S) * len(grant.CATEGORY))

    def test_cancellation_is_O_and_final_for_L_and_W(self):
        """S3-A1 item 2: a first job-cancelled passes in every state; after it L and W are refused, O still runs."""
        for state, steps in S.items():
            with self.subTest(state=state):
                grant.mutation_guard(build_rows(steps), job=JOB, now=NOW, etype="job-cancelled", binding=None)
        rows = build_rows(S["revoked"] + [{"op": "cancel"}])
        for etype in ("grant", "intent", "bind", "accept"):
            with self.assertRaises(WfRejected) as cm:
                grant.mutation_guard(rows, job=JOB, now=NOW, etype=etype, binding=None)
            self.assertEqual(cm.exception.reason, "cancelled")
        for etype in ("grant-revoked", "close", "recovery-opened", "recovery-closed", "intervention", "stop-confirmed"):
            grant.mutation_guard(rows, job=JOB, now=NOW, etype=etype, binding=None)

    def test_unknown_event_type_is_malformed(self):
        for etype in ("contract", "status", "grant-check", "observe", "nope"):
            with self.subTest(etype=etype):
                with self.assertRaises(WfRejected) as cm:
                    grant.mutation_guard(build_rows(S["in-force"]), job=JOB, now=NOW, etype=etype, binding=None)
                self.assertEqual(cm.exception.reason, "malformed")

    def test_time_expiry_refuses_W_without_any_row(self):
        rows = build_rows(S["in-force"])
        grant.mutation_guard(rows, job=JOB, now=NOW, etype="intent", binding=None)
        with self.assertRaises(WfRejected) as cm:
            grant.mutation_guard(rows, job=JOB, now=EXPIRY, etype="intent", binding=None)
        self.assertEqual(cm.exception.reason, "authority-expired")
        grant.mutation_guard(rows, job=JOB, now=EXPIRY, etype="grant", binding=None)       # renewal possible

    def test_bindings_inside_and_outside_the_new_ars_after_renewal(self):
        """§3 / §2B l.48: after renewal, a binding inside the new ARS keeps authority; outside -> binding-outside-ars
        for its W events until a new bind; O and L unaffected."""
        bound = [{"op": "contract"}, {"op": "propose"}, {"op": "activate"},
                 {"op": "bind", "role": "builder", "session": "B1", "tools": ["Write"], "paths": ["jobs/j/work/**"]},
                 {"op": "revoke"}]
        narrower = copy.deepcopy(ROLES)
        narrower["builder"] = {"tools": ["Write"], "actions": ["write"], "paths": ["jobs/j/work/sub/**"]}
        same = [{"op": "propose", "grant": {"grant_id": "G2"}}, {"op": "activate"}]
        narrow = [{"op": "propose", "grant": {"grant_id": "G3", "ars": {"roles": narrower}}}, {"op": "activate"}]
        rows_in = build_rows(bound + same)
        b = binding_of(rows_in, "builder")
        self.assertEqual(grant.authority(rows_in, job=JOB, now=NOW)["state"], "in-force")
        grant.mutation_guard(rows_in, job=JOB, now=NOW, etype="intent", binding=b)
        rows_out = build_rows(bound + narrow)
        b = binding_of(rows_out, "builder")
        for etype in ("intent", "receipt", "delivered", "ack-assignment"):
            with self.assertRaises(WfRejected) as cm:
                grant.mutation_guard(rows_out, job=JOB, now=NOW, etype=etype, binding=b)
            self.assertEqual(cm.exception.reason, "binding-outside-ars")
        grant.mutation_guard(rows_out, job=JOB, now=NOW, etype="stop-confirmed", binding=b)   # O
        grant.mutation_guard(rows_out, job=JOB, now=NOW, etype="grant", binding=b)            # L
        no_role = copy.deepcopy(ROLES); del no_role["builder"]
        rows_gone = build_rows(bound + [{"op": "propose", "grant": {"grant_id": "G4", "ars": {"roles": no_role}}},
                                        {"op": "activate"}])
        with self.assertRaises(WfRejected) as cm:
            grant.mutation_guard(rows_gone, job=JOB, now=NOW, etype="intent", binding=binding_of(rows_gone, "builder"))
        self.assertEqual(cm.exception.reason, "binding-outside-ars")

    def test_free_form_ars_has_no_binding_check(self):
        rows = build_rows([{"op": "contract"}, {"op": "propose", "grant": {"ars": {"a": "owner"}}}, {"op": "activate"}])
        grant.mutation_guard(rows, job=JOB, now=NOW, etype="intent",
                             binding={"role": "builder", "tools": ["Bash"], "paths": ["**"]})


# ===================================================================================================== validators

class Validators(unittest.TestCase):
    def _rev(self, rows: list[dict], **over: Any) -> dict:
        a = grant.authority(rows, job=JOB, now=NOW)
        return dict({"grant_id": a["grant_id"], "block_hash": a["block_hash"], "reason": "owner",
                     "owner_turn": TURN}, **over)

    def test_validate_revoke(self):
        rows = build_rows(S["in-force"])
        grant.validate_revoke(rows, job=JOB, now=NOW, content=self._rev(rows))
        for over, reason in (({"owner_turn": "turn without the id"}, "no-provenance"), ({"owner_turn": ""}, "malformed"),
                             ({"grant_id": "G9"}, "malformed"), ({"block_hash": "a" * 64}, "malformed"),
                             ({"block_hash": "XYZ"}, "malformed"), ({"reason": " "}, "malformed")):
            with self.subTest(over=over):
                with self.assertRaises(WfRejected) as cm:
                    grant.validate_revoke(rows, job=JOB, now=NOW, content=self._rev(rows, **over))
                self.assertEqual(cm.exception.reason, reason)
        for state in ("none", "proposed", "revoked", "expired", "voided", "malformed", "cancelled"):   # D-S3-4
            with self.subTest(state=state):
                rows_s = build_rows(S[state])
                content = {"grant_id": "G1", "block_hash": "a" * 64, "reason": "owner", "owner_turn": TURN}
                with self.assertRaises(WfRejected) as cm:
                    grant.validate_revoke(rows_s, job=JOB, now=NOW, content=content)
                self.assertEqual(cm.exception.reason, "no-active-grant")
        with self.assertRaises(WfRejected) as cm:                                  # time-expired: nothing in force
            grant.validate_revoke(rows, job=JOB, now=EXPIRY, content=self._rev(rows))
        self.assertEqual(cm.exception.reason, "no-active-grant")

    def test_validate_cancel(self):
        good = {"reason": "owner stop", "owner_turn": TURN}
        for state, steps in S.items():
            if state == "cancelled":
                continue
            with self.subTest(state=state):
                grant.validate_cancel(build_rows(steps), job=JOB, content=good)
        with self.assertRaises(WfRejected) as cm:
            grant.validate_cancel(build_rows(S["cancelled"]), job=JOB, content=good)
        self.assertEqual(cm.exception.reason, "cancelled")
        for bad, reason in (({"reason": "x", "owner_turn": "no id here"}, "no-provenance"),
                            ({"reason": "", "owner_turn": TURN}, "malformed"),
                            ({"reason": "x"}, "malformed"), (dict(good, extra=1), "malformed")):
            with self.subTest(content=bad):
                with self.assertRaises(WfRejected) as cm:
                    grant.validate_cancel(build_rows(S["in-force"]), job=JOB, content=bad)
                self.assertEqual(cm.exception.reason, reason)

    def test_schemas(self):
        ok = {"grant-revoked": {"grant_id": "G1", "block_hash": "a" * 64, "reason": "r", "owner_turn": TURN},
              "grant-expired": {"grant_id": "G1", "block_hash": "a" * 64, "reason": "r", "owner_turn": TURN},
              "job-cancelled": {"reason": "r", "owner_turn": TURN},
              "intervention": {"label": "restart", "necessity": "necessary", "minutes": 5, "note": ""}}
        for etype, c in ok.items():
            with self.subTest(etype=etype):
                grant.validate_content(etype, dict(c, contract_sha256=CSHA), CONTRACT, None)
        grant.validate_content("intervention", dict(ok["intervention"], minutes="unknown"), CONTRACT, None)
        grant.validate_content("intervention", dict(ok["intervention"], minutes=0, necessity="unknown"), CONTRACT, None)
        bad = [("grant-revoked", {"block_hash": "A" * 64}), ("grant-revoked", {"grant_id": ""}),
               ("grant-expired", {"owner_turn": ""}), ("job-cancelled", {"reason": None}),
               ("intervention", {"necessity": "maybe"}), ("intervention", {"minutes": -1}),
               ("intervention", {"minutes": 2.5}), ("intervention", {"minutes": True}),
               ("intervention", {"minutes": "about 5"}), ("intervention", {"label": " "}),
               ("intervention", {"note": None})]
        for etype, patch in bad:
            with self.subTest(etype=etype, patch=patch):
                with self.assertRaises(WfRejected) as cm:
                    grant.validate_content(etype, dict(ok[etype], **patch), CONTRACT, None)
                self.assertEqual(cm.exception.reason, "malformed")
        for etype, c in ok.items():
            missing = dict(c); missing.popitem()
            for content in (missing, dict(c, extra=1), "x"):
                with self.subTest(etype=etype, content=content):
                    with self.assertRaises(WfRejected):
                        grant.validate_content(etype, content, CONTRACT, None)
        with self.assertRaises(WfRejected):
            grant.validate_content("grant", {}, CONTRACT, None)

    def test_intervention_summary_never_imputes(self):
        rows = build_rows([{"op": "contract"},
                           {"op": "intervention", "label": "restart", "necessity": "necessary", "minutes": 5, "note": "a"},
                           {"op": "intervention", "label": "nudge", "necessity": "avoidable", "minutes": "unknown",
                            "note": "b"},
                           {"op": "intervention", "label": "fix", "necessity": "unknown", "minutes": 12, "note": ""}])
        s = grant.intervention_summary(rows)
        self.assertEqual((s["count"], s["minutes_known"], s["unknown_count"]), (3, 17, 1))
        self.assertEqual([e["label"] for e in s["entries"]], ["restart", "nudge", "fix"])
        self.assertEqual(s["entries"][1]["minutes"], "unknown")
        self.assertEqual(grant.intervention_summary(build_rows(S["in-force"])),
                         {"count": 0, "minutes_known": 0, "unknown_count": 0, "entries": []})

    def test_cancel_limitation_text(self):
        self.assertEqual(grant.CANCEL_LIMITATION,
                         "cancellation denies later work recorded through wf; it does not stop running processes or "
                         "prevent effects outside this CLI; no enforcement on real workers is claimed")
        self.assertEqual(grant.CONTENT_TYPES, ("grant-revoked", "grant-expired", "job-cancelled", "intervention"))


# ===================================================================================================== real ledger

class RealLedgerShape(Base):
    """The fixture rows have the shape of real ledger rows; §7 dispatch accepts the four types through build_row."""

    def _grant_file(self, g: dict) -> None:
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(g, fh)

    def _rows(self) -> list[dict]:
        c = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(c, JOB)
        finally:
            c.close()

    def test_cli_written_grant_matches_fixture_rows(self):
        self._grant_file(BASE_GRANT)
        self.new_job()
        code, res = self.run_wf("grant", "propose", JOB, "--grant", self.gpath, role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        real = self._rows()
        self.assertEqual(res["block_hash"], grant.block(JOB, json.loads(real[-1]["content"]), real[-1]["seq"])[1])
        fixture = build_rows([{"op": "contract"}, {"op": "propose"}])
        self.assertEqual(json.loads(real[-1]["content"]), json.loads(fixture[-1]["content"]))
        self.assertEqual(grant.authority(real, job=JOB, now=NOW)["state"], "proposed")
        code, res = self.run_wf("grant", "activate", JOB, "--owner-hash", res["block_hash"][:8], "--quote",
                                "approve G1", "--turn", "owner turn", role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        real = self._rows()
        a = grant.authority(real, job=JOB, now=NOW)
        self.assertEqual((a["state"], a["block_hash"]), ("in-force", res["block_hash"]))
        fx = grant.authority(build_rows(S["in-force"]), job=JOB, now=NOW)
        self.assertEqual((fx["state"], fx["block_hash"]), ("in-force", res["block_hash"]))

    def test_section7_dispatch_accepts_lifecycle_rows(self):
        """§7: the four types pass validate.build_row (role table + schema dispatch) and land on the chain."""
        self._grant_file(BASE_GRANT)
        self.new_job(); self.activate()
        rows = self._rows()
        a = grant.authority(rows, job=JOB, now=NOW)
        writes = [("intervention", "owner", {"label": "restart", "necessity": "necessary", "minutes": "unknown",
                                             "note": "n"}),
                  ("grant-revoked", "reviewer", {"grant_id": a["grant_id"], "block_hash": a["block_hash"],
                                                 "reason": "r", "owner_turn": TURN}),
                  ("job-cancelled", "builder", {"reason": "stop", "owner_turn": TURN})]
        for etype, role, content in writes:
            def build(conn: Any, hs: int, hself: str, etype=etype, role=role, content=content) -> dict:
                c = dbm.contract(conn, JOB)
                return validate.build_row(conn, JOB, hs, hself, etype=etype, msg_id=f"x-{etype}", author_role=role,
                                          session_id=f"{role[0].upper()}9", generation=1, reply_to=None,
                                          grant_ref=None, content=content, contract_sha=c["sha256"],
                                          contract_content=json.loads(c["content"]))
            txn.write_event(self.db, JOB, Faults(), OpLog(), build=build, session="x")
        rows = self._rows()
        self.assertEqual([r["type"] for r in rows[-3:]], ["intervention", "grant-revoked", "job-cancelled"])
        self.assertEqual(grant.authority(rows, job=JOB, now=NOW)["state"], "cancelled")
        c = dbm.connect(self.db, create=False)
        try:
            self.assertEqual(dbm.verify_chain(c, JOB), [])
        finally:
            c.close()
        for etype, role in (("grant-expired", "builder"), ("intervention", "reviewer")):
            with self.subTest(etype=etype, role=role):
                self.assertNotIn(role, validate.ROLE_FOR_TYPE[etype])
        self.assertNotIn("job-cancelled", validate.APPEND_TYPES)


if __name__ == "__main__":
    unittest.main()
