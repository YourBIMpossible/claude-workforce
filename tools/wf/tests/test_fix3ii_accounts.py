"""FIX3-II lane F3-ACC (WF-196): `wfcore/accounts.py`, the account map and the caller check as pure functions
(interface contract r2 §4.1 `parse_accounts`, §4.3 `map_of`, `map_in_force`, `bound_sessions`, §4.4 `check_caller`;
ACCEPTANCE-ii MAP-3 and the §4.4 decision table, harness U). Every input is a literal row list; no ledger is opened.

Account ids are fakes in the host's canonical form (§2.2a): SID strings on Windows, decimal uids on Linux. FOREIGN is
the other platform's form, which the host refuses."""
from __future__ import annotations

import json
import os
import sys
import unittest
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
if WF_DIR not in sys.path:
    sys.path.insert(0, WF_DIR)

from wfcore import peer  # noqa: E402
from wfcore.db import WfRejected  # noqa: E402

try:
    from wfcore import accounts  # noqa: E402
    _IMPORT_ERROR = ""
except ImportError as _exc:  # before the lane's module exists every test fails, naming the import (the red)
    accounts = None  # type: ignore[assignment]
    _IMPORT_ERROR = f"wfcore.accounts cannot be imported: {_exc}"

PLAT = peer.host_platform()


def fake_id(n: int) -> str:
    """A fake account id in the host's canonical form."""
    return f"S-1-5-21-1000-2000-3000-{n}" if PLAT == "win32" else str(n)


OWN, B1, B2, R1, X = fake_id(500), fake_id(1001), fake_id(1002), fake_id(1003), fake_id(1009)
FOREIGN = "1001" if PLAT == "win32" else "S-1-5-21-1000-2000-3000-1001"
WFCORE = "0" * 64
MAP = {"builder": [B1, B2], "reviewer": [R1]}
ROLES = {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
         "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
         "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["tools/wf/**"]}}


def caller(account: str) -> peer.Caller:
    return peer.Caller(peer=peer.PeerIdentity(account=account, pid=None, platform=PLAT), broker_account=OWN,
                       broker_wfcore=WFCORE)


def row(seq: int, etype: str, content: Any, *, author: str = "coordinator", session: str = "C1",
        generation: int = 1) -> dict[str, Any]:
    return {"seq": seq, "type": etype, "author_role": author, "session_id": session, "generation": generation,
            "content": json.dumps(content)}


def grant_row(seq: int, state: str, account_map: Any = None) -> dict[str, Any]:
    ars: dict[str, Any] = {"roles": ROLES}
    if account_map is not None:
        ars["accounts"] = account_map
    return row(seq, "grant", {"grant_id": "G1", "state": state, "ars": ars})


def bind_row(seq: int, role: str, session: str, *, generation: Any = 1, account: str | None = None,
             author: str = "coordinator") -> dict[str, Any]:
    c: dict[str, Any] = {"role": role, "session": session, "generation": generation, "tools": [], "paths": [],
                         "grant_seq": 2, "replaces_seq": None}
    if account is not None:
        c["account"] = account
    return row(seq, "bind", c, author=author)


class _NoRead(list):
    """A row list that fails the test if anything iterates it: the own account's check reads nothing (§3.4)."""

    def __iter__(self):  # type: ignore[override]
        raise AssertionError("the rows were read")

    def __len__(self) -> int:
        raise AssertionError("the rows were read")


class _Accounts(unittest.TestCase):
    def setUp(self) -> None:
        if accounts is None:
            self.fail(_IMPORT_ERROR)

    def assertArs(self, fn: Any, *a: Any, **k: Any) -> WfRejected:
        with self.assertRaises(WfRejected) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.reason, "ars", str(cm.exception))
        return cm.exception

    def assertRefusal(self, reason: str, fn: Any, *a: Any, **k: Any) -> WfRejected:
        with self.assertRaises(WfRejected) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.reason, reason, str(cm.exception))
        self.assertTrue(str(cm.exception).startswith(f"rejected({reason})"), str(cm.exception))
        return cm.exception


# ===================================================================================================== §4.1

class ParseAccounts(_Accounts):
    """MAP-3 (U): every defect is rejected(ars), in §4.1's order; no map is None (the job runs as today)."""

    def parse(self, ars: Any) -> Any:
        return accounts.parse_accounts(ars, own_account=OWN, platform=PLAT)

    def test_rule1_no_ars_object_or_no_accounts_key_is_none(self):
        for ars in (None, "ars", [], 5, {}, {"roles": ROLES}, {"roles": ROLES, "replacement": {}}):
            with self.subTest(ars=ars):
                self.assertIsNone(self.parse(ars))

    def test_a_valid_map_is_returned_as_tuples(self):
        self.assertEqual(self.parse({"roles": ROLES, "accounts": MAP}), {"builder": (B1, B2), "reviewer": (R1,)})
        self.assertEqual(self.parse({"accounts": {"reviewer": [R1]}}), {"reviewer": (R1,)})

    def test_rule2_accounts_not_an_object_or_empty(self):
        for bad in ("B1", [B1], 7, None, {}):
            with self.subTest(accounts=bad):
                self.assertArs(self.parse, {"accounts": bad})

    def test_rule3_a_key_other_than_builder_or_reviewer(self):
        for key in ("coordinator", "owner", "Builder", ""):
            with self.subTest(key=key):
                self.assertArs(self.parse, {"accounts": {"builder": [B1], key: [B2]}})

    def test_rule4_a_value_that_is_not_a_non_empty_list_of_strings(self):
        for bad in (B1, [], [B1, None], [1001], (B1,), {"id": B1}):
            with self.subTest(value=bad):
                self.assertArs(self.parse, {"accounts": {"builder": bad}})

    def test_rule5_an_id_not_in_the_host_form(self):
        for bad in (FOREIGN, "", " " + B1, B1 + "\n", "S-1-5-21-01-2" if PLAT == "win32" else "01001"):
            with self.subTest(id=bad):
                self.assertArs(self.parse, {"accounts": {"builder": [B1, bad]}})

    def test_rule6_an_id_twice_anywhere(self):
        self.assertArs(self.parse, {"accounts": {"builder": [B1, B1]}})
        self.assertArs(self.parse, {"accounts": {"builder": [B1], "reviewer": [B1]}})

    def test_rule7_the_own_account_in_the_map(self):
        self.assertArs(self.parse, {"accounts": {"builder": [B1], "reviewer": [OWN]}})
        # map_of reads only rules 1-6, so a stored map holding the own account still reads (section 4.3)
        self.assertEqual(accounts.map_of({"ars": {"accounts": {"builder": [OWN]}}}), {"builder": (OWN,)})

    def test_the_rules_run_in_order(self):
        # a bad key (3) is named before a bad value (4); a bad value (4) before a bad id (5); a bad id (5) before
        # a repeat (6); a repeat (6) before the own account (7)
        e = self.assertArs(self.parse, {"accounts": {"owner": "x"}})
        self.assertIn("key", str(e))
        e = self.assertArs(self.parse, {"accounts": {"builder": "x", "reviewer": [FOREIGN]}})
        self.assertIn("list", str(e))
        e = self.assertArs(self.parse, {"accounts": {"builder": [FOREIGN, FOREIGN]}})
        self.assertIn("form", str(e))
        e = self.assertArs(self.parse, {"accounts": {"builder": [OWN, OWN]}})
        self.assertIn("twice", str(e))
        e = self.assertArs(self.parse, {"accounts": {"builder": [OWN]}})
        self.assertIn("own account", str(e))


# ===================================================================================================== §4.3

class MapOf(_Accounts):
    def test_no_grant_or_no_map_is_none(self):
        for g in (None, {}, {"ars": None}, {"ars": "x"}, {"ars": {"roles": ROLES}}):
            with self.subTest(grant=g):
                self.assertIsNone(accounts.map_of(g))

    def test_a_stored_map(self):
        self.assertEqual(accounts.map_of({"state": "activated", "ars": {"roles": ROLES, "accounts": MAP}}),
                         {"builder": (B1, B2), "reviewer": (R1,)})

    def test_a_defect_in_a_stored_map_is_rejected_ars(self):
        for bad in ({}, {"coordinator": [B1]}, {"builder": []}, {"builder": [FOREIGN]}, {"builder": [B1, B1]}):
            with self.subTest(accounts=bad):
                self.assertArs(accounts.map_of, {"ars": {"accounts": bad}})


class MapInForce(_Accounts):
    def test_no_rows_or_no_activated_grant_is_none(self):
        self.assertIsNone(accounts.map_in_force([]))
        self.assertIsNone(accounts.map_in_force([row(1, "contract", {}), grant_row(2, "proposed", MAP)]))

    def test_the_latest_activated_grant_decides(self):
        rows = [row(1, "contract", {}), grant_row(2, "proposed", MAP), grant_row(3, "activated", MAP)]
        self.assertEqual(accounts.map_in_force(rows), {"builder": (B1, B2), "reviewer": (R1,)})
        # a later activated grant without a map: no map in force
        self.assertIsNone(accounts.map_in_force(rows + [grant_row(4, "proposed"), grant_row(5, "activated")]))
        # and a later activated grant with another map replaces it
        other = {"builder": [B2]}
        later = [grant_row(4, "proposed", other), grant_row(5, "activated", other)]
        self.assertEqual(accounts.map_in_force(rows + later), {"builder": (B2,)})

    def test_it_stays_in_force_after_revoke_expire_or_a_new_proposal(self):
        rows = [row(1, "contract", {}), grant_row(2, "proposed", MAP), grant_row(3, "activated", MAP)]
        for later in ([row(4, "grant-revoked", {"grant_id": "G1"})], [row(4, "grant-expired", {"grant_id": "G1"})],
                      [grant_row(4, "proposed", {"builder": [X]})], [row(4, "job-cancelled", {})]):
            with self.subTest(later=later[0]["type"]):
                self.assertEqual(accounts.map_in_force(rows + later), {"builder": (B1, B2), "reviewer": (R1,)})


class BoundSessions(_Accounts):
    def test_every_bind_naming_the_account_at_any_generation(self):
        rows = [row(1, "contract", {}), grant_row(3, "activated", MAP), bind_row(4, "coordinator", "C1"),
                bind_row(5, "builder", "B1", account=B1), bind_row(9, "builder", "B2", generation=2, account=B2),
                bind_row(10, "reviewer", "R1", account=R1)]
        self.assertEqual(accounts.bound_sessions(rows, B1), [("builder", "B1", 1, 5)])
        self.assertEqual(accounts.bound_sessions(rows, B2), [("builder", "B2", 2, 9)])
        self.assertEqual(accounts.bound_sessions(rows, R1), [("reviewer", "R1", 1, 10)])
        self.assertEqual(accounts.bound_sessions(rows, X), [])
        self.assertEqual(accounts.bound_sessions(rows, OWN), [])

    def test_bind_py_filters(self):
        rows = [bind_row(5, "builder", "B1", account=B1, author="builder"),  # not authored by the coordinator
                bind_row(6, "owner", "B1", account=B1),                       # not an ARS role
                bind_row(7, "builder", "", account=B1),                       # empty session
                bind_row(8, "builder", "B1", generation="1", account=B1),     # not an int generation
                bind_row(9, "builder", "B1", generation=True, account=B1),    # bool is not an int generation
                row(10, "intent", {"account": B1, "role": "builder", "session": "B1", "generation": 1},
                    author="builder"),
                bind_row(11, "builder", "B1", account=B2)]                    # another account
        self.assertEqual(accounts.bound_sessions(rows, B1), [])


# ===================================================================================================== §4.4

class CheckCaller(_Accounts):
    """The §4.4 table, first matching row decides (SII:101-108)."""

    def setUp(self) -> None:
        super().setUp()
        self.rows = [row(1, "contract", {}), grant_row(2, "proposed", MAP), grant_row(3, "activated", MAP),
                     bind_row(4, "coordinator", "C1"), bind_row(5, "builder", "B1", account=B1),
                     bind_row(9, "reviewer", "R1", account=R1)]

    def check(self, account: str | None, role: str, session: str, rows: Any = None) -> None:
        accounts.check_caller(self.rows if rows is None else rows, None if account is None else caller(account),
                              role=role, session=session)

    def test_row1_no_caller_no_check(self):
        accounts.check_caller(_NoRead(), None, role="coordinator", session="C1")
        accounts.check_caller(_NoRead(), None, role="builder", session="anything")

    def test_row2_own_account_other_roles_pass_with_nothing_read(self):
        for role in ("coordinator", "owner", "no-such-role"):
            with self.subTest(role=role):
                accounts.check_caller(_NoRead(), caller(OWN), role=role, session="C1")

    def test_row3_own_account_claims_builder_or_reviewer(self):
        for role, session in (("builder", "B1"), ("reviewer", "R1")):
            with self.subTest(role=role):
                with self.assertRaises(WfRejected) as cm:
                    accounts.check_caller(_NoRead(), caller(OWN), role=role, session=session)
                self.assertEqual(cm.exception.reason, "caller-mismatch")
                self.assertEqual(cm.exception.info, {"account": OWN, "claimed_role": role, "claimed_session": session})

    def test_row4_unattributed_when_no_map_or_not_in_it(self):
        no_map = [row(1, "contract", {}), grant_row(2, "proposed"), grant_row(3, "activated")]
        for account, rows in ((B1, []), (B1, no_map), (B1, self.rows[:2]), (X, self.rows)):
            with self.subTest(account=account, rows=len(rows)):
                e = self.assertRefusal("caller-unattributed", self.check, account, "builder", "B1", rows)
                self.assertEqual(e.info, {"account": account})

    def test_row5_mapped_worker_claims_another_role(self):
        for role, session in (("coordinator", "C1"), ("owner", "O-x"), ("no-such-role", "B1")):
            with self.subTest(role=role):
                e = self.assertRefusal("caller-mismatch", self.check, B1, role, session)
                self.assertEqual(e.info, {"account": B1, "claimed_role": role, "claimed_session": session})

    def test_row6_mapped_worker_passes_only_for_its_bound_session_under_its_role(self):
        self.check(B1, "builder", "B1")
        self.check(R1, "reviewer", "R1")
        for account, role, session in ((B1, "builder", "B2"),    # another session
                                       (B1, "reviewer", "R1"),   # not in map[reviewer] (ID-4)
                                       (R1, "builder", "B1"),    # not in map[builder]
                                       (B2, "builder", "B2"),    # in the map, but no bind ties B2 to it
                                       (B2, "builder", "B1")):   # in the map, B1's session (ID-13 shape)
            with self.subTest(account=account, role=role, session=session):
                e = self.assertRefusal("caller-mismatch", self.check, account, role, session)
                self.assertEqual(e.info, {"account": account, "claimed_role": role, "claimed_session": session})

    def test_row6_a_bind_at_any_generation_ties_the_session(self):
        rows = self.rows + [row(10, "stop-confirmed", {"role": "builder", "session": "B1", "generation": 1}),
                            bind_row(13, "builder", "B2", generation=2, account=B2)]
        self.check(B2, "builder", "B2", rows)
        self.check(B1, "builder", "B1", rows)  # the generation and stop rules then decide (bind.check_actor)
        self.assertRefusal("caller-mismatch", self.check, B1, "builder", "B2", rows)
        self.assertRefusal("caller-mismatch", self.check, R1, "builder", "B2", rows)

    def test_a_map_after_revoke_still_decides(self):
        rows = self.rows + [row(10, "grant-revoked", {"grant_id": "G1"})]
        self.check(B1, "builder", "B1", rows)
        self.assertRefusal("caller-mismatch", self.check, B1, "coordinator", "C1", rows)


if __name__ == "__main__":
    unittest.main()
