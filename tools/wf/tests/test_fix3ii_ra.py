"""PACKAGE-FIX3-II, lane F3-RA (WF-280; AMENDMENT FIX3-II-A5, route (a)): ACCEPTANCE-ii r2's RA rows on a prepared
host, each run inside the account's own session.

Route (a) (A5 §1): a person opens a session as each test account (a `runas` window on Windows, a login on Linux) and
starts that account's side of these tests in it. The runner's side (the test root, the ledger, the broker) runs in the
runner's account. Nothing here starts a process as another account, registers a task, changes an ACL or needs
admin: there is no cross-account entry point (A5 §2 drops `tools/wf/ra/**`).

Prepared hosts (ACCEPTANCE-ii, "Prepared hosts: how a test knows"):
- `WF_TEST_OS_ACCOUNTS` unset: every test here skips with the reason `host not prepared (WF_TEST_OS_ACCOUNTS unset)`.
- Set: it names the host-local manifest (`os`; `accounts` B1, B2, R1 and `unlisted`, each an `id` and a `runas`
  handle; `root`, a test-only root). A failed precondition fails the test; it never skips it.
- `WF_TEST_RA_SIDE` names the session's side: `runner`, `B1`, `B2`, `R1` or `unlisted`. On a prepared host only that
  side's tests are loaded (`load_tests`), so no test skips there. Under route (a), AII's "each `runas` handle runs a
  process that the OS reports as the listed id" is checked from inside the session: the account the OS reports for
  this process must be the manifest's id for the side.

One gate-1 run on a prepared host, in this order:
1. The runner's session: `WF_TEST_RA_SIDE=runner`. It writes this run's jobs in `<root>/ledger/wf.db` with W's direct
   calls, starts the broker at `wfbroker.address_for_root(<root>)` for B1, B2 and R1, checks W-3 and ID-7 (W), then
   serves until W stops the broker (`python -I tools/wf/wfbroker.py --stop --address <address>`, from another window
   of the runner's account).
2. While it serves, the `unlisted`, `B1`, `B2` and `R1` sides, each in that account's own session, from a checkout at
   the same head (the broker refuses another wfcore: BRK-6).
3. W stops the broker. The runner's last test then checks the broker log for this run: a request from each of B1, B2
   and R1, none from the unlisted account (ACL-3), and ID-13's job unchanged.
Each side's transcript is part of the evidence (A5 §2, PACK §3).

ID-5's Windows form is A5 §3's decision (i): a detached process started inside `<B1 account>`'s own session, with no
console parent; no scheduled task. ACL-2's exact refusal text is printed for the record (AII: "recorded at the lane's
first run").
"""
from __future__ import annotations

import base64
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest import mock

import test_wf  # noqa: F401  (puts tools/wf on sys.path)

import wf
import wfbroker
from wfcore import accounts, client, peer, wire
from wfcore import db as dbm

MANIFEST_VAR = "WF_TEST_OS_ACCOUNTS"
SIDE_VAR = "WF_TEST_RA_SIDE"
SKIP_REASON = "host not prepared (WF_TEST_OS_ACCOUNTS unset)"  # ACCEPTANCE-ii, "Prepared hosts", exactly
WORKERS = ("B1", "B2", "R1")
LISTED = (*WORKERS, "unlisted")
SIDES = ("runner", *LISTED)
SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
PY = sys.executable
WF_PY = os.path.join(test_wf.WF_DIR, "wf.py")
WFBROKER_PY = os.path.join(test_wf.WF_DIR, "wfbroker.py")
WFCORE = wire.wfcore_sha256(test_wf.WF_DIR)
READY_LIMIT_S = 60.0
SERVE_LIMIT_S = 4 * 3600.0  # the runner serves until W's --stop, at most this long
RUN_RE = re.compile(r"^(ra-\d{8}T\d{6}Z-[0-9a-f]{4})-w\Z")
REPLACEMENT = {"allowed": True, "max": 1, "expiry": "2999-12-31T23:59:59Z"}
DELIVERED = '{"claims":[{"claim":"c","evidence":["e"]}]}'


class PreconditionError(Exception):
    """A prepared host whose setup is not in place: the test fails, never skips."""


@dataclasses.dataclass(frozen=True)
class Manifest:
    """The host-local manifest (ACCEPTANCE-ii, "Prepared hosts"). It never enters the repo or a document."""

    os: str
    ids: dict[str, str]
    runas: dict[str, str]
    root: str

    @property
    def ledger(self) -> str:
        return os.path.join(self.root, "ledger", "wf.db")

    @property
    def address(self) -> str:
        return wfbroker.address_for_root(self.root, self.os)


def load_manifest(path: str) -> Manifest:
    """Read and check the manifest. Any defect raises PreconditionError."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PreconditionError(f"the manifest {MANIFEST_VAR} names does not read as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PreconditionError("the manifest is not a JSON object")
    host = peer.host_platform()
    if data.get("os") != host:
        raise PreconditionError(f"the manifest's os is {data.get('os')!r}, and this host is {host!r}")
    entries = data.get("accounts")
    if not isinstance(entries, dict) or sorted(entries) != sorted(LISTED):
        raise PreconditionError(f"the manifest's accounts must be exactly {', '.join(LISTED)}")
    ids: dict[str, str] = {}
    runas: dict[str, str] = {}
    for name in LISTED:
        entry = entries[name]
        if not isinstance(entry, dict):
            raise PreconditionError(f"accounts.{name} is not an object")
        aid, handle = entry.get("id"), entry.get("runas")
        if not peer.is_account_id(aid, host):
            raise PreconditionError(f"accounts.{name}.id is not a {host} account id")
        if not isinstance(handle, str) or not handle.strip():
            raise PreconditionError(f"accounts.{name}.runas is not a non-empty string")
        ids[name], runas[name] = aid, handle
    if len(set(ids.values())) != len(ids):
        raise PreconditionError("two accounts in the manifest share an id")
    root = data.get("root")
    if not isinstance(root, str) or not os.path.isabs(root) or not os.path.isdir(root):
        raise PreconditionError("the manifest's root is not an absolute path to a folder")
    return Manifest(os=host, ids=ids, runas=runas, root=os.path.normpath(root))


def job_names(run: str) -> dict[str, str]:
    """This run's jobs, one per use, so that no side's writes reach another side's before-and-after reads."""
    return {k: f"{run}-{k}" for k in ("w", "b1", "b1g", "r1", "b2", "13")}


def intent(job: str) -> str:
    return json.dumps({"path": f"jobs/{job}/work/a.txt", "sha_before": "", "note": "n"})


def spoof(job: str) -> list[str]:
    return ["log-intervention", job, "--label", "spoof", "--necessity", "unknown", "--minutes", "1"]  # coordinator


def contract_for(job: str) -> str:
    return json.dumps(dict(test_wf.CONTRACT, write_scope=[f"jobs/{job}/work/**"]))


def grant_for(job: str, ids: dict[str, str]) -> str:
    roles = {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
             "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": [f"jobs/{job}/work/**"]},
             "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["tools/wf/**"]}}
    return json.dumps(dict(test_wf.GRANT, resources=[f"jobs/{job}/work/**"], expiry="2999-12-31T00:00:00Z",
                           ars={"roles": roles, "replacement": REPLACEMENT,
                                "accounts": {"builder": [ids["B1"], ids["B2"]], "reviewer": [ids["R1"]]}}))


def ledger_state(db: str, job: str) -> tuple[int, tuple[int, str]]:
    """(row count, (head seq, head self)), read-only: a refusal leaves both unchanged (ACCEPTANCE-ii, ACC:29)."""
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            return len(dbm.rows(conn, job)), dbm.head(conn, job)
    finally:
        conn.close()


def ledger_rows(db: str, job: str) -> list[dict]:
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            return dbm.rows(conn, job)
    finally:
        conn.close()


def scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in SCRUB}


class _RA(unittest.TestCase):
    """The skip rule and the preconditions every side shares."""

    side = ""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        path = os.environ.get(MANIFEST_VAR, "")
        if not path:
            self.skipTest(SKIP_REASON)
        for k in SCRUB:
            os.environ.pop(k, None)
        side = os.environ.get(SIDE_VAR, "")
        if side not in SIDES:
            self.fail(f"precondition: {SIDE_VAR} is {side!r}; on a prepared host it names this session's side, "
                      f"one of {', '.join(SIDES)}")
        if side != self.side:
            self.fail(f"precondition: {SIDE_VAR} is {side!r}, and this test belongs to the {self.side} side")
        try:
            self.m = load_manifest(path)
        except PreconditionError as exc:
            self.fail(f"precondition: {exc}")
        self.own = peer.own_account()

    def redact(self, obj: Any) -> str:
        """`obj` as text, each manifest id and this process's own replaced by a name: account ids stay out of the
        transcripts that become evidence (ACCEPTANCE-ii, "Prepared hosts"). Assertions compare ids, never print them."""
        text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
        for name, aid in self.m.ids.items():
            text = text.replace(aid, f"<{name} account>")
        return text.replace(self.own, "<this account>")

    def assertRefused(self, res: tuple[int, dict], reason: str) -> None:
        code, body = res
        self.assertEqual((code, body.get("code")), (1, "rejected"), self.redact(body))
        self.assertTrue(str(body.get("error", "")).startswith(f"rejected({reason})"), self.redact(body))


# ===================================================================================================== the runner
class RunnerSide(_RA):
    """The runner's account: W's direct setup, the broker, W-3, ID-7 (W), and the log check after W's stop."""

    side = "runner"
    m: Manifest | None = None
    run_id = ""
    jobs: dict[str, str] = {}
    proc: subprocess.Popen | None = None
    lines: list[dict] = []
    started_ts = ""
    setup_error = ""
    snapshot_13: tuple[int, tuple[int, str]] | None = None

    @classmethod
    def setUpClass(cls) -> None:
        """Set up only on the runner's side of a prepared host. A failure is kept and fails each test (setUp)."""
        super().setUpClass()
        path = os.environ.get(MANIFEST_VAR, "")
        if not path or os.environ.get(SIDE_VAR) != cls.side:
            return
        try:
            cls.m = load_manifest(path)
            own = peer.own_account()
            if own in cls.m.ids.values():
                raise PreconditionError("the runner's account is one of the listed accounts")
            if not os.path.isdir(os.path.dirname(cls.m.ledger)):
                raise PreconditionError("<root>/ledger is not a folder: the host's setup (SPEC-ii §9 S8) makes it")
            cls.run_id = "ra-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(2).hex()
            cls.jobs = job_names(cls.run_id)
            with mock.patch.dict(os.environ):
                for k in SCRUB:
                    os.environ.pop(k, None)
                cls._set_up_jobs()
            os.makedirs(os.path.join(os.path.dirname(cls.m.ledger), "pending"), exist_ok=True)
            cls.snapshot_13 = ledger_state(cls.m.ledger, cls.jobs["13"])
            cls._start_broker()
        except Exception as exc:  # noqa: BLE001  (kept, then reported as each test's failure)
            cls.setup_error = f"{type(exc).__name__}: {exc}"

    @classmethod
    def tearDownClass(cls) -> None:
        proc = cls.proc
        if proc is not None and proc.poll() is None:
            subprocess.run([PY, "-I", WFBROKER_PY, "--stop", "--address", cls.m.address], env=scrubbed_env(),
                           capture_output=True, text=True, timeout=60)
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(30)
        super().tearDownClass()

    # ---- W's direct calls (setup) ---------------------------------------------------------------------------------
    @classmethod
    def _w(cls, *argv: str) -> dict:
        code, res = wf.run(["--db", cls.m.ledger, "--role", "coordinator", "--session", "C1", *argv])
        if code != 0:
            raise AssertionError(f"W's setup call {' '.join(argv[:3])} failed: {res}")
        return res

    @classmethod
    def _direct(cls, role: str, session: str, *argv: str, generation: int = 1) -> None:
        code, res = wf.run(["--db", cls.m.ledger, "--role", role, "--session", session, "--generation",
                            str(generation), *argv])
        if code != 0:
            raise AssertionError(f"the direct setup call {role} {session} {' '.join(argv[:3])} failed: {res}")

    @classmethod
    def _explicit(cls, job: str) -> None:
        """The contract, the grant with the map (proposed, then activated), C1's self-bind, B1 bound to B1's account."""
        ids = cls.m.ids
        cls._w("job", "new", job, "--contract", contract_for(job))
        h = cls._w("grant", "propose", job, "--grant", grant_for(job, ids))["block_hash"][:8]
        cls._w("grant", "activate", job, "--owner-hash", h, "--quote", f"approve G1 {h}", "--turn", "owner turn")
        cls._w("bind", job, "--bind-role", "coordinator", "--bind-session", "C1")
        cls._w("bind", job, "--bind-role", "builder", "--bind-session", "B1", "--bind-account", ids["B1"])

    @classmethod
    def _replaced(cls, job: str) -> None:
        """_explicit; B1's intent; B1 stopped; reconcile; resume; B2 bound at generation 2 to B2's account."""
        cls._explicit(job)
        cls._direct("builder", "B1", "append", job, "--type", "intent", "--content", intent(job))
        stop = json.dumps({"role": "builder", "session": "B1", "generation": 1, "how": "process exit observed",
                           "when": "2026-10-21T04:00:00Z", "owner_check": "no child process, no outstanding operation",
                           "owner_turn": f"owner turn: builder session stopped in job {job}"})
        cls._w("append", job, "--type", "stop-confirmed", "--content", stop)
        cls._w("reconcile", job)
        cls._w("recover", "decide", job, "--decision", "resume", "--turn",
               f"owner turn: resume job {job} with a new builder")
        cls._w("bind", job, "--bind-role", "builder", "--bind-session", "B2", "--bind-generation", "2",
               "--bind-account", cls.m.ids["B2"])

    @classmethod
    def _set_up_jobs(cls) -> None:
        j, r1 = cls.jobs, cls.m.ids["R1"]
        cls._explicit(j["w"])
        cls._explicit(j["b1"])  # B1's refusals; R1 bound so that ID-4's child can cite R1's binding
        cls._direct("builder", "B1", "append", j["b1"], "--type", "delivered", "--content", DELIVERED)
        cls._w("bind", j["b1"], "--bind-role", "reviewer", "--bind-session", "R1", "--bind-account", r1)
        cls._explicit(j["b1g"])  # B1's own claims: ACL-2, then ID-7
        cls._explicit(j["r1"])  # R1's own claims
        cls._direct("builder", "B1", "append", j["r1"], "--type", "intent", "--content", intent(j["r1"]))
        cls._direct("builder", "B1", "append", j["r1"], "--type", "delivered", "--content", DELIVERED)
        cls._w("bind", j["r1"], "--bind-role", "reviewer", "--bind-session", "R1", "--bind-account", r1)
        cls._replaced(j["b2"])  # B2's own claims at generation 2
        cls._replaced(j["13"])  # ID-13, from B1's and R1's sessions

    # ---- the broker -----------------------------------------------------------------------------------------------
    @classmethod
    def _start_broker(cls) -> None:
        argv = [PY, "-I", WFBROKER_PY, "--db", cls.m.ledger, "--address", cls.m.address]
        for name in WORKERS:
            argv += ["--client-account", cls.m.ids[name]]
        cls.started_ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        cls.lines = []
        cls.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=scrubbed_env())
        threading.Thread(target=cls._read_lines, args=(cls.proc,), daemon=True).start()
        end = time.monotonic() + READY_LIMIT_S
        while not cls.lines:
            if cls.proc.poll() is not None or time.monotonic() > end:
                raise AssertionError(f"the broker gave no ready line (exit {cls.proc.poll()})")
            time.sleep(0.05)
        if cls.lines[0].get("code") != "broker-ready":
            raise AssertionError(f"the broker did not start: {cls.lines[0]}")
        sys.stderr.write(f"\n[RA runner] run {cls.run_id}; broker ready at {cls.m.address}. Run the unlisted, B1, B2 "
                         f"and R1 sides now, each in its own session; then stop the broker from another window of this "
                         f"account: python -I tools/wf/wfbroker.py --stop --address {cls.m.address}\n")

    @classmethod
    def _read_lines(cls, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                cls.lines.append(obj)

    def log_since_start(self) -> list[dict]:
        """This run's broker log lines (the log and its one rotated file, `ts` at or after the start)."""
        base = os.path.join(self.m.root, *wfbroker.LOG_REL)
        out: list[dict] = []
        for path in (base[: -len(".jsonl")] + ".1.jsonl", base):
            if not os.path.exists(path):
                continue
            with open(path, encoding="ascii") as fh:
                for line in fh:
                    entry = json.loads(line)
                    if str(entry.get("ts") or "") >= self.started_ts:
                        out.append(entry)
        return out

    def setUp(self) -> None:
        super().setUp()
        if self.setup_error:
            self.fail(f"precondition: the runner's setup failed: {self.setup_error}")
        if self.own in self.m.ids.values():
            self.fail("precondition: the runner's account is one of the listed accounts")

    # ---- the rows -------------------------------------------------------------------------------------------------
    def test_id7_w_direct_claims_C(self):
        """ID-7, W's part: as `<owner account>`, direct `--db`, W claims `<C>`: accepted."""
        job = self.jobs["w"]
        before = ledger_state(self.m.ledger, job)
        code, res = wf.run(["--db", self.m.ledger, "--role", "coordinator", "--session", "C1", *spoof(job)])
        self.assertEqual(code, 0, res)
        self.assertEqual(ledger_state(self.m.ledger, job)[0], before[0] + 1)

    def test_w3_the_start_check_as_the_owner_account(self):
        """W-3: after the start, `wf --broker <address> whoami` as the owner account prints the broker's account, equal
        to the caller's, and the wfcore sha. Nothing is written."""
        before = ledger_state(self.m.ledger, self.jobs["w"])
        code, res = wf.run(["--broker", self.m.address, "whoami"])
        broker_part = res.get("broker") or {}
        self.assertEqual((code, res.get("mode"), res.get("own"), res.get("account") == self.own,
                          broker_part.get("account") == self.own, broker_part.get("wfcore")),
                         (0, "broker", True, True, True, WFCORE), self.redact(res))
        self.assertEqual(ledger_state(self.m.ledger, self.jobs["w"]), before)

    def test_zz_serve_then_check_the_log(self):
        """Serve until W stops the broker. Then: a request from each worker account reached it (each side ran); none
        from the unlisted account (ACL-3: refused at connect, nothing dispatched); ID-13's job is unchanged."""
        assert self.proc is not None
        try:
            self.proc.wait(SERVE_LIMIT_S)
        except subprocess.TimeoutExpired:
            self.fail(f"W did not stop the broker within {SERVE_LIMIT_S:.0f} s")
        self.assertEqual(self.proc.returncode, 0, self.redact(self.lines[-3:]))
        peers = {e.get("peer") for e in self.log_since_start()}
        missing = [name for name in WORKERS if self.m.ids[name] not in peers]
        self.assertEqual(missing, [], "no request reached the broker from these accounts: run each side in its own "
                                      "session before W stops the broker")
        self.assertFalse(self.m.ids["unlisted"] in peers, "a request from the unlisted account was dispatched (ACL-3)")
        self.assertEqual(ledger_state(self.m.ledger, self.jobs["13"]), self.snapshot_13)


# ===================================================================================================== the accounts
class _WorkerSide(_RA):
    """A worker account's own session: B1, B2 or R1. The ledger is read directly, under the Read ACL (ACL-1)."""

    role = "builder"
    session = ""
    generation = 1
    own_job = ""  # the key in job_names() of the job whose reads, whoami and ACL checks this side uses

    def setUp(self) -> None:
        super().setUp()
        if self.own != self.m.ids[self.side]:
            self.fail(f"precondition: this session's account is not the manifest's {self.side} account "
                      f"(runas handle {self.m.runas[self.side]!r})")
        self.db = self.m.ledger
        if not os.path.isfile(self.db):
            self.fail("precondition: <root>/ledger/wf.db is missing: start the runner's side first")
        conn = dbm.connect(self.db, create=False)
        try:
            with dbm.read_snapshot(conn):
                runs = sorted(m.group(1) for m in (RUN_RE.match(j) for j in dbm.jobs(conn)) if m)
        finally:
            conn.close()
        if not runs:
            self.fail("precondition: the ledger holds no RA run: start the runner's side first")
        self.jobs = job_names(runs[-1])
        self.job = self.jobs[self.own_job]
        self.address = self.m.address

    def state(self, job: str | None = None) -> tuple[int, tuple[int, str]]:
        return ledger_state(self.db, job or self.job)

    def rows(self, job: str | None = None) -> list[dict]:
        return ledger_rows(self.db, job or self.job)

    def wf_b(self, *argv: str) -> tuple[int, dict]:
        """`wf --broker <address> …` in-process, from this session's account."""
        return wf.run(["--broker", self.address, *argv])

    def bind_seq(self, job: str, name: str) -> int:
        sessions = accounts.bound_sessions(self.rows(job), self.m.ids[name])
        self.assertEqual(len(sessions), 1, sessions)
        return sessions[0][3]

    def own_claim(self) -> tuple[str, list[str]]:
        """(job, argv): this account's first valid write, as its own session (ACL-2's claim, then ID-7's)."""
        raise NotImplementedError

    # ---- rows every worker account runs ---------------------------------------------------------------------------
    def test_acl1_the_ledger_acl_is_in_place(self):
        """ACL-1: the OS denies opening `wf.db` for writing and creating a file in `ledger` and in `ledger/pending`;
        opening `wf.db` for reading succeeds. The ledger is unchanged."""
        before = self.state()
        ledger_dir = os.path.dirname(self.db)
        pending = os.path.join(ledger_dir, "pending")
        self.assertTrue(os.path.isdir(pending), "ledger/pending is missing: the runner's setup makes it")
        with self.assertRaises(PermissionError, msg="wf.db opened for writing"):
            with open(self.db, "r+b"):
                pass
        for folder in (ledger_dir, pending):
            probe = os.path.join(folder, f"ra-acl1-{self.side}-{os.urandom(4).hex()}")
            with self.subTest(folder=folder):
                with self.assertRaises(PermissionError, msg=f"a file was created in {folder}: {probe} (left in place)"):
                    with open(probe, "x", encoding="utf-8"):
                        pass
        with open(self.db, "rb") as fh:
            self.assertEqual(len(fh.read(16)), 16)
        self.assertEqual(self.state(), before)

    def test_acl2_direct_wf_is_not_committed(self):
        """ACL-2: this account's own claim, direct (`wf --db <ledger> …`), not through the broker: not committed; a
        non-zero exit and the ledger unchanged. The claim is one the broker commits (ID-7), so the refusal is the ACL's,
        not a wf refusal; its text is printed for the record."""
        job, claim = self.own_claim()
        before = self.state(job)
        r = subprocess.run([PY, "-I", WF_PY, "--json", "--db", self.db, *claim], env=scrubbed_env(),
                           capture_output=True, text=True, timeout=120)
        out, err = self.redact(r.stdout.strip()[:600]), self.redact(r.stderr.strip()[-600:])
        sys.stderr.write(f"\n[RA {self.side}] ACL-2 exit {r.returncode}; stdout: {out!r}; stderr: {err!r}\n")
        self.assertNotEqual(r.returncode, 0, out)
        try:
            body = json.loads(r.stdout)
        except ValueError:
            body = None
        if isinstance(body, dict):
            self.assertFalse(str(body.get("error", "")).startswith("rejected("), f"a wf refusal, not the ACL's: {out}")
        self.assertEqual(self.state(job), before)

    def test_id7_claims_its_own_session(self):
        """ID-7: this account claims its own session through the broker: accepted, COMMITTED."""
        _, claim = self.own_claim()
        code, res = self.wf_b(*claim)
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), self.redact(res))

    def test_id8_direct_reads_under_the_read_acl(self):
        """ID-8: status, digest and verify, direct under the Read ACL: allowed, with no account check."""
        for line in (["status", self.job], ["digest", self.job], ["verify", self.job], ["verify"]):
            with self.subTest(line=line):
                code, res = wf.run(["--db", self.db, "--role", self.role, "--session", self.session, *line])
                self.assertEqual(code, 0, self.redact(res))

    def test_id10_whoami_as_this_account(self):
        """ID-10: `whoami <J>` through the broker prints the account it read, the broker's own account and wfcore sha,
        and the roles and bound sessions the map in force gives this account. Nothing is written."""
        before = self.state()
        own_id = self.m.ids[self.side]
        expected_roles = ["reviewer"] if self.side == "R1" else ["builder"]
        expected_sessions = [{"role": r, "session": s, "generation": g, "bind_seq": q}
                             for r, s, g, q in accounts.bound_sessions(self.rows(), own_id)]
        self.assertTrue(expected_sessions, "the runner's setup binds a session of this account in this job")
        code, res = self.wf_b("whoami", self.job)
        broker_part = res.get("broker") or {}
        self.assertEqual((code, res.get("mode"), res.get("account") == own_id, res.get("own"), res.get("map_in_force"),
                          res.get("roles"), res.get("sessions"), broker_part.get("wfcore")),
                         (0, "broker", True, False, True, expected_roles, expected_sessions, WFCORE), self.redact(res))
        self.assertFalse(broker_part.get("account") in self.m.ids.values(), "the broker runs as a listed account")
        self.assertEqual(self.state(), before)


class B1Side(_WorkerSide):
    """`<B1 account>`'s own session: ID-1 to ID-5, ID-7, ID-8, ID-10, ID-13, ACL-1 and ACL-2."""

    side, session, own_job = "B1", "B1", "b1"

    def own_claim(self) -> tuple[str, list[str]]:
        job = self.jobs["b1g"]
        return job, ["--role", "builder", "--session", "B1", "--generation", "1", "append", job, "--type", "intent",
                     "--content", intent(job)]

    def spoof_cmd(self) -> list[str]:
        return [PY, "-I", WF_PY, "--json", "--role", "coordinator", "--session", "C1", "--broker", self.address,
                *spoof(self.job)]

    def run_form(self, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, dict]:
        r = subprocess.run(argv, env=env or scrubbed_env(), capture_output=True, text=True, timeout=180)
        out = r.stdout.strip()
        self.assertTrue(out, f"no output: rc {r.returncode}, stderr {r.stderr[-400:]}")
        return r.returncode, json.loads(out[out.index("{"):])

    def shell_forms(self, folder: str) -> dict[str, list[str]]:
        """ID-2's shell forms of the literal spoof: string parts, an encoded command, a script file written then run."""
        literal = self.spoof_cmd()
        if self.m.os == "win32":
            sh = shutil.which("pwsh") or shutil.which("powershell")
            self.assertIsNotNone(sh, "PowerShell is needed for the Windows shell forms")
            q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
            call = "& " + " ".join(q(a) for a in literal[:4])  # python -I wf.py --json
            tail = " ".join(q(a) for a in literal[8:])
            parts = f"$r = 'coord' + 'inator'; $s = 'C' + '1'; {call} --role $r --session $s {tail}; exit $LASTEXITCODE"
            plain = " ".join(["&"] + [q(a) for a in literal]) + "; exit $LASTEXITCODE"
            script = os.path.join(folder, "spoof.ps1")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(plain + "\n")
            enc = base64.b64encode(plain.encode("utf-16-le")).decode("ascii")
            return {"string parts": [sh, "-NoProfile", "-NonInteractive", "-Command", parts],
                    "encoded command": [sh, "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                    "script file": [sh, "-NoProfile", "-NonInteractive", "-File", script]}
        q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
        call = " ".join(q(a) for a in literal[:4])
        tail = " ".join(q(a) for a in literal[8:])
        parts = f"r=coord'inator'; s=C'1'; {call} --role \"$r\" --session \"$s\" {tail}"
        plain = " ".join(q(a) for a in literal)
        script = os.path.join(folder, "spoof.sh")
        with open(script, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(plain + "\n")
        enc = base64.b64encode(plain.encode("utf-8")).decode("ascii")
        return {"string parts": ["bash", "-c", parts],
                "encoded command": ["bash", "-c", f"eval \"$(printf %s {enc} | base64 -d)\""],
                "script file": ["bash", script]}

    def temp_folder(self) -> str:
        """A folder in this account's own temp space, for its script and output files."""
        d = tempfile.mkdtemp(prefix="wf-ra-")
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_id1_the_C_spoof_literal(self):
        """ID-1: as `<B1 account>`, with B1 bound to it, a claim of `<C>`: caller-mismatch; ledger unchanged."""
        before = self.state()
        self.assertRefused(self.wf_b("--role", "coordinator", "--session", "C1", *spoof(self.job)), "caller-mismatch")
        self.assertRefused(self.wf_b("--role", "coordinator", "--session", "C1", "append", self.job, "--type",
                                     "intent", "--content", intent(self.job)), "caller-mismatch")
        self.assertEqual(self.state(), before)

    def test_id2_each_shell_form_run_as_B1(self):
        """ID-2: the same spoof through WF_ROLE/WF_SESSION, string parts, an encoded command and a script file, each
        run as `<B1 account>`: caller-mismatch for each; ledger unchanged."""
        before = self.state()
        env = dict(scrubbed_env(), WF_ROLE="coordinator", WF_SESSION="C1")
        results = {"WF_ROLE and WF_SESSION": self.run_form([PY, "-I", WF_PY, "--json", "--broker", self.address,
                                                            *spoof(self.job)], env),
                   "explicit": self.run_form(self.spoof_cmd())}
        for name, argv in self.shell_forms(self.temp_folder()).items():
            results[name] = self.run_form(argv)
        for name, res in results.items():
            with self.subTest(form=name):
                self.assertRefused(res, "caller-mismatch")
        self.assertEqual(self.state(), before)

    def test_id3_the_owner_spoof(self):
        """ID-3: as `<B1 account>`, `--role owner --session O-x log-intervention <J> …`: caller-mismatch."""
        before = self.state()
        self.assertRefused(self.wf_b("--role", "owner", "--session", "O-x", *spoof(self.job)), "caller-mismatch")
        self.assertEqual(self.state(), before)

    def test_id4_a_nested_session(self):
        """ID-4: a child process of this session claims reviewer R1, with R1 bound to `<R1 account>`: the account is
        still B1's, so caller-mismatch; ledger unchanged."""
        before = self.state()
        ack = json.dumps({"role": "reviewer", "session": "R1", "bind_seq": self.bind_seq(self.job, "R1")})
        r = subprocess.run([PY, "-I", WF_PY, "--json", "--broker", self.address, "--role", "reviewer", "--session",
                            "R1", "append", self.job, "--type", "ack-assignment", "--content", ack],
                           env=scrubbed_env(), capture_output=True, text=True, timeout=120)
        self.assertRefused((r.returncode, json.loads(r.stdout)), "caller-mismatch")
        self.assertEqual(self.state(), before)

    def test_id5_out_of_the_tree(self):
        """ID-5: a claim of `<C>` started outside any session tree but still as `<B1 account>`. Windows (A5 §3 (i)): a
        detached process in a new process group, with no console parent; Linux: `setsid` and a double fork."""
        before = self.state()
        out = os.path.join(self.temp_folder(), "detached-out.json")
        cmd = self.spoof_cmd()
        if self.m.os == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            with open(out, "w", encoding="utf-8") as fh:
                p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.DEVNULL,
                                     env=scrubbed_env(), creationflags=flags)
                p.wait(120)
        else:
            q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
            subprocess.run(["setsid", "sh", "-c", f"( {' '.join(q(a) for a in cmd)} > {q(out)} 2>/dev/null & )"],
                           env=scrubbed_env(), check=True, timeout=60)
        res = self.wait_for_json(out)
        self.assertEqual((res.get("code"), str(res.get("error", "")).split(":", 1)[0]),
                         ("rejected", "rejected(caller-mismatch)"), self.redact(res))
        self.assertEqual(self.state(), before)

    def wait_for_json(self, path: str, timeout: float = 120.0) -> dict:
        end = time.monotonic() + timeout
        while True:
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.loads(fh.read())
            except (OSError, ValueError):
                if time.monotonic() > end:
                    self.fail("timed out waiting for the detached client's answer")
                time.sleep(0.05)

    def test_id13_claims_the_other_builders_session(self):
        """ID-13: B2 bound at generation 2 to `<B2 account>`; as `<B1 account>`, a claim of B2's session at generation
        2, and of session B3 at generation 2: caller-mismatch for both; ledger unchanged; no claimant-conflict row."""
        _id13(self)


class B2Side(_WorkerSide):
    """`<B2 account>`'s own session: ID-7 (B2 at generation 2), ID-8, ID-10, ACL-1 and ACL-2."""

    side, session, generation, own_job = "B2", "B2", 2, "b2"

    def own_claim(self) -> tuple[str, list[str]]:
        """B2's first valid write: its acknowledgement of the reconcile report (the existing replacement rule)."""
        report = [r for r in self.rows() if r["type"] == "reconcile-report"][-1]
        ids = [e["id"] for e in json.loads(report["content"])["exceptions"]]
        return self.job, ["--role", "builder", "--session", "B2", "--generation", "2", "ack-reconcile", self.job,
                          "--report", str(report["seq"]), "--exceptions", json.dumps(ids)]

    def test_id7_claims_its_own_session(self):
        """ID-7: as `<B2 account>`, B2 claims B2 at generation 2: its acknowledgement, then its own intent."""
        super().test_id7_claims_its_own_session()
        code, res = self.wf_b("--role", "builder", "--session", "B2", "--generation", "2", "append", self.job, "--type",
                              "intent", "--content", intent(self.job))
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), self.redact(res))


class R1Side(_WorkerSide):
    """`<R1 account>`'s own session: ID-7, ID-8, ID-10, ID-13 (as R1), ACL-1 and ACL-2."""

    side, role, session, own_job = "R1", "reviewer", "R1", "r1"

    def own_claim(self) -> tuple[str, list[str]]:
        ack = json.dumps({"role": "reviewer", "session": "R1", "bind_seq": self.bind_seq(self.job, "R1")})
        return self.job, ["--role", "reviewer", "--session", "R1", "append", self.job, "--type", "ack-assignment",
                          "--content", ack]

    def test_id13_claims_the_other_builders_session(self):
        """ID-13, "also the same claim as `<R1 account>`": caller-mismatch for both; ledger unchanged."""
        _id13(self)


def _id13(t: _WorkerSide) -> None:
    job = t.jobs["13"]
    before = t.state(job)
    for session in ("B2", "B3"):
        with t.subTest(session=session):
            t.assertRefused(t.wf_b("--role", "builder", "--session", session, "--generation", "2", "append", job,
                                   "--type", "intent", "--content", intent(job)), "caller-mismatch")
    t.assertEqual(t.state(job), before)
    t.assertFalse([r for r in t.rows(job) if r["type"] == "blocker"])


class UnlistedSide(_RA):
    """The manifest's `unlisted` account, in no map and not in the broker's `--client-account` list: ACL-3."""

    side = "unlisted"

    def setUp(self) -> None:
        super().setUp()
        if self.own != self.m.ids["unlisted"]:
            self.fail(f"precondition: this session's account is not the manifest's unlisted account "
                      f"(runas handle {self.m.runas['unlisted']!r})")

    def test_acl3_the_access_list_refuses_the_connection(self):
        """ACL-3: the connection is denied: `broker-unavailable` with reason `access-denied`; nothing is dispatched (the
        runner's log check shows no line from this account)."""
        code, res = wf.run(["--broker", self.m.address, "whoami"])
        self.assertEqual((code, res.get("code"), res.get("reason")),
                         (client.EXIT_ERR, "broker-unavailable", "access-denied"), self.redact(res))


_SIDE_CLASSES: dict[str, type[_RA]] = {"runner": RunnerSide, "B1": B1Side, "B2": B2Side, "R1": R1Side,
                                       "unlisted": UnlistedSide}


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite,
               pattern: str | None) -> unittest.TestSuite:
    """The five sides' tests only (the shared bases hold no rows of their own). Unprepared host: every side, each test
    a declared skip. Prepared host: only this session's side, so none skips; an unknown or missing side loads every
    side, and each test fails its side precondition."""
    cls = _SIDE_CLASSES.get(os.environ.get(SIDE_VAR, "")) if os.environ.get(MANIFEST_VAR, "") else None
    chosen = [cls] if cls is not None else list(_SIDE_CLASSES.values())
    return unittest.TestSuite(loader.loadTestsFromTestCase(c) for c in chosen)


if __name__ == "__main__":
    unittest.main()
