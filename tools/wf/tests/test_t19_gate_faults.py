"""T19 gate fault matrix — lane S4M (MyBuddy), WF-009. Contract: INTERFACE-CONTRACT-S4A.md sections 2 (rule 2 ledger
health, rule 4 recovery), 3 (write-failure rule), 4 (deployed hook), 7 (S4M row). Spec T19: "Gate or ledger database
corrupt, locked, unreadable, at an unexpected identity, or recovery open -> Gate and wf deny ... never allow. Gate
read has its own timeout and denies on expiry."

Two surfaces, same faults:
  core  `gatecore.decide(payload, root=, db_path=, now=, gate_files=, read_timeout_s=, expected_identity=)`
  hook  `python -I <copied wf_gate_hook.py>` in a fixture deployment directory under the test temp root (config
        `wf_gate.json` beside it; different cwd; scrubbed environment; payload on stdin; exit 0 allow / 2 deny).
Faults: unreadable (missing file; POSIX chmod 000), not-SQLite, locked past the timeout (a second connection holding
BEGIN EXCLUSIVE), identity change (file replaced between identity capture and decision; core only: the frozen hook
config carries no identity field), chain-tail tamper, open recovery, gate.db unwritable -> audit-unavailable (hook).

Platform reporting (contract section 7, owner item 5): every test whose outcome depends on file-locking, permission
or identity semantics exists once per platform (`*_linux`, `*_win32`) with an identical body; the other platform's
copy is skipped with the reason "platform-specific: <semantics> verified on <platform> only". MyBuddy reports the
Linux run; White runs the file on Windows. Nothing here claims equivalence between the two.

Hard limits honoured: no hook registration or invocation outside the fixture directory, no settings file written,
fixture ledgers only (test_wf.Base under WF_TEST_TMP). On a tree without gatecore / gatelog / the hook every test is
skipped with the reason "gate modules not present on this tree"; a skip is not completion.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

from test_wf import Base, GRANT, WF_DIR, log  # noqa: E402  (sets sys.path to the wf package; fresh WF_TEST_TMP)
from test_b2s1_wiring import GRANT_ARS  # noqa: E402  (ARS grant fixture; no test class imported)
from wfcore import canon, db as dbm  # noqa: E402

HOOK_SRC = os.path.join(WF_DIR, "gate", "wf_gate_hook.py")
GATE_PRESENT = (importlib.util.find_spec("wfcore.gatecore") is not None
                and importlib.util.find_spec("wfcore.gatelog") is not None and os.path.isfile(HOOK_SRC))
SKIP_GATE = "gate modules not present on this tree (gatecore / gatelog / gate/wf_gate_hook.py land on S4G and S4L)"
LINUX, WIN = sys.platform.startswith("linux"), sys.platform == "win32"
PLAT = "linux" if LINUX else ("win32" if WIN else sys.platform)
READ_TIMEOUT = 0.3            # bounded: every locked case returns within this plus process start-up
NOW = "2026-10-17T12:00:00Z"  # fixed instant inside the grant fixture's validity (expiry 2026-12-31)
WORK_REL = "jobs/j/work/a.txt"


def plat_skip(want: str):
    """Decorator: run only on `want`; elsewhere skip naming the semantics as platform-specific."""
    return unittest.skipUnless(PLAT == want, f"platform-specific: file-locking / permission / identity semantics "
                                             f"verified on {want} only (this host: {PLAT})")


def sha256_file(p: str) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@unittest.skipUnless(GATE_PRESENT, SKIP_GATE)
class T19GateFaults(Base):
    # ---------------------------------------------------------------- fixture
    def setUp(self) -> None:
        super().setUp()
        from wfcore import gatecore  # noqa: F401  (import guarded by the class skip)
        self.gatecore = importlib.import_module("wfcore.gatecore")
        self.gatelog = importlib.import_module("wfcore.gatelog")
        self.root = self.dir                      # boundary root = parent of the `ledger` directory
        self.gpath_ars = os.path.join(self.dir, "grant_ars.json")
        with open(self.gpath_ars, "w", encoding="utf-8") as fh:
            json.dump(GRANT_ARS, fh)
        self.work_abs = os.path.join(self.root, *WORK_REL.split("/"))
        os.makedirs(os.path.dirname(self.work_abs), exist_ok=True)
        self.evidence: dict = {"platform": PLAT, "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version}

    def tearDown(self) -> None:
        log(f"{self._testMethodName}-t19", self.evidence)

    def coord(self, *argv, **kw):
        return self.run_wf(*argv, role="coordinator", session="C1", **kw)

    def bound_job(self) -> None:
        """Job j with an in-force ARS grant, coordinator C1 and builder B1 explicitly bound: the gate's allow case."""
        self.new_job()
        c, r = self.coord("grant", "propose", "j", "--grant", self.gpath_ars); self.assertEqual(c, 0, r)
        h = r["block_hash"]
        c, r = self.coord("grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                          "--turn", "owner turn: activate on job j"); self.assertEqual(c, 0, r)
        for brole, bsess in (("coordinator", "C1"), ("builder", "B1")):
            c, r = self.coord("bind", "j", "--bind-role", brole, "--bind-session", bsess); self.assertEqual(c, 0, r)

    def payload(self, session: str = "B1", tool: str = "Write", path: str | None = None) -> dict:
        return {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": tool,
                "tool_input": {"file_path": path or self.work_abs, "content": "x"}, "cwd": self.root}

    # ---------------------------------------------------------------- core surface
    def decide(self, payload: dict | None = None, **kw) -> dict:
        args = dict(root=self.root, db_path=self.db, now=NOW, gate_files=[], read_timeout_s=READ_TIMEOUT)
        args.update(kw)
        d = self.gatecore.decide(payload or self.payload(), **args)
        self.evidence.setdefault("core", []).append({k: d.get(k) for k in ("decision", "reason", "channel")})
        return d

    def assert_core_denies(self, reason: str, payload: dict | None = None, **kw) -> dict:
        d = self.decide(payload, **kw)
        self.assertEqual((d["decision"], d["reason"]), ("deny", reason), d)
        self.assertEqual(d["channel"], "preventable", d)
        return d

    # ---------------------------------------------------------------- hook surface (fixture deployment)
    def deploy_hook(self, **config_override) -> str:
        """Copy the hook into a fixture hooks dir outside the checkout (under this test's dir), write wf_gate.json."""
        hooks = os.path.join(self.dir, "hooks-fixture"); os.makedirs(hooks, exist_ok=True)
        hook = os.path.join(hooks, "wf_gate_hook.py")
        shutil.copyfile(HOOK_SRC, hook)
        cfg = {"root": self.root, "db": self.db, "wf_dir": WF_DIR, "wfcore_sha256": self.wfcore_sha256(),
               "read_timeout_s": READ_TIMEOUT, "hook_sha256": sha256_file(hook)}
        cfg.update(config_override)
        with open(os.path.join(hooks, "wf_gate.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        self.hook = hook
        return hook

    def wfcore_sha256(self) -> str:
        """`wf_gate_hook.wfcore_sha256(wf_dir)` when the hook exposes it (S4A-A3 item 2 names it the reference), else
        the frozen A3 form computed here."""
        spec = importlib.util.spec_from_file_location("_wf_gate_hook_probe", HOOK_SRC)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                for name in dir(mod):
                    low = name.lower()
                    if "wfcore" in low and ("sha" in low or "hash" in low) and callable(getattr(mod, name)):
                        try:
                            v = getattr(mod, name)(WF_DIR)
                            if isinstance(v, str) and len(v) == 64:
                                self.evidence["wfcore_sha256_source"] = f"hook.{name}"
                                return v
                        except TypeError:
                            continue
            except Exception as exc:  # noqa: BLE001 — a hook that cannot be imported standalone is not our finding
                self.evidence["hook_probe_error"] = f"{type(exc).__name__}: {exc}"
        core = os.path.join(WF_DIR, "wfcore")  # AMENDMENT S4A-A3 item 2: sorted file names, "<sha256>  <name>\n" each
        text = "".join(f"{sha256_file(os.path.join(core, n))}  {n}\n" for n in sorted(os.listdir(core)) if n.endswith(".py"))
        self.evidence["wfcore_sha256_source"] = "S4A-A3 item 2 form (hook exposes no wfcore_sha256 callable)"
        return hashlib.sha256(text.encode()).hexdigest()

    def run_hook(self, payload: dict | None = None, timeout: float = 60.0) -> tuple[int, dict, str]:
        cwd = tempfile.mkdtemp(prefix="elsewhere-", dir=self.dir)     # a different cwd, inside the test root
        env = {"PATH": os.environ.get("PATH", "")}
        if WIN:
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        p = subprocess.run([sys.executable, "-I", self.hook], input=json.dumps(payload or self.payload()),
                           capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except ValueError:
            out = {"raw": p.stdout}
        self.evidence.setdefault("hook", []).append({"exit": p.returncode, "out": out, "stderr": p.stderr[-400:]})
        return p.returncode, out, p.stderr

    def assert_hook_denies(self, reason: str, payload: dict | None = None) -> tuple[dict, str]:
        code, out, err = self.run_hook(payload)
        self.assertEqual(code, 2, (out, err))
        self.assertEqual((out.get("decision"), out.get("reason")), ("deny", reason), (out, err))
        self.assertIn(reason, err, "the deny reason is also on stderr (PreToolUse block text)")
        return out, err

    def assert_hook_allows(self, payload: dict | None = None) -> dict:
        code, out, err = self.run_hook(payload)
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(out.get("decision"), "allow", (out, err))
        return out

    # ---------------------------------------------------------------- fault makers (fixture ledger only)
    def make_not_sqlite(self) -> None:
        with open(self.db, "wb") as fh:
            fh.write(b"this is not an SQLite database\n" * 64)

    def tamper_chain_tail(self) -> None:
        """Rewrite the head row's content without recomputing hashes (a level-1 chain failure within the tail)."""
        conn = sqlite3.connect(self.db, isolation_level=None)
        try:
            conn.execute("DROP TRIGGER events_no_update")
            conn.execute("UPDATE events SET content = content || ' ' WHERE job='j' AND seq = (SELECT MAX(seq) FROM events WHERE job='j')")
            dbm.ensure_schema(conn)
        finally:
            conn.close()

    def hold_exclusive(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db, isolation_level=None, timeout=5.0)
        c.execute("BEGIN EXCLUSIVE")
        self.addCleanup(c.close)
        return c

    def replace_file_identity(self) -> dict:
        """Replace the ledger file: copy to a sibling (a new file, hence a new identity while both exist), then
        os.replace it over the original. Same bytes, different st_ino / file id. A remove-then-create sequence is
        not used because POSIX filesystems may hand the new file the freed inode number."""
        before = self.gatecore.ledger_identity(self.db)
        tmp = self.db + ".replacement"
        shutil.copyfile(self.db, tmp)
        os.replace(tmp, self.db)
        after = self.gatecore.ledger_identity(self.db)
        self.evidence["identity"] = {"before": before, "after": after}
        return before

    def make_gate_db_unwritable(self) -> str:
        """`runtime/gate/gate.db` as a DIRECTORY: SQLite cannot open it as a database on any platform; the failures
        log beside it stays writable, so the failure line can be recorded."""
        d = os.path.join(self.root, "runtime", "gate", "gate.db"); os.makedirs(d, exist_ok=True)
        return d

    def rows_head(self) -> tuple[int, str]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j")
        finally:
            conn.close()

    # ================================================================ controls (the fixture is a genuine allow)
    def test_T19_control_core_and_hook_allow_on_healthy_fixture(self):
        self.bound_job()
        d = self.decide(); self.assertEqual((d["decision"], d["channel"]), ("allow", "preventable"), d)
        self.deploy_hook()
        out = self.assert_hook_allows()
        self.assertTrue(out.get("logged"), out)
        v = self.gatelog.verify(self.root)
        self.assertEqual((v["present"], v["rows"], v["chain_problems"], v["unlogged_attempts"]), (True, 1, [], 0), v)

    # ================================================================ unreadable
    def test_T19_unreadable_missing_ledger_core_and_hook(self):
        self.bound_job(); self.deploy_hook()
        head_before = self.rows_head()
        os.remove(self.db)
        for j in (self.db + "-journal",):
            if os.path.exists(j):
                os.remove(j)
        self.assert_core_denies("ledger-unreadable")
        self.assert_hook_denies("ledger-unreadable")
        self.assertFalse(os.path.exists(self.db), "the gate never (re)creates the ledger")
        self.evidence["head_before"] = head_before

    def _unreadable_permission(self):
        self.bound_job(); self.deploy_hook()
        os.chmod(self.db, 0)
        self.addCleanup(os.chmod, self.db, stat.S_IRUSR | stat.S_IWUSR)
        self.assert_core_denies("ledger-unreadable")
        self.assert_hook_denies("ledger-unreadable")

    @plat_skip("linux")
    def test_T19_unreadable_permission_denied_linux(self):
        """POSIX mode 000 on the ledger file (uid != 0)."""
        if os.geteuid() == 0:
            self.skipTest("running as root: mode 000 does not deny reads")
        self._unreadable_permission()

    @plat_skip("win32")
    def test_T19_unreadable_permission_denied_win32(self):
        """Windows: chmod(0) only clears the read-only attribute's complement; a real ACL deny is out of scope.
        Recorded as not-applicable by this fixture; the missing-file case covers `ledger-unreadable` on Windows."""
        self.skipTest("win32: POSIX mode bits do not deny reads; ACL changes are outside the fixture rules")

    # ================================================================ not SQLite
    def test_T19_not_sqlite_core_and_hook(self):
        self.bound_job(); self.deploy_hook()
        self.make_not_sqlite()
        self.assert_core_denies("ledger-unreadable")
        self.assert_hook_denies("ledger-unreadable")
        with open(self.db, "rb") as fh:
            self.assertTrue(fh.read().startswith(b"this is not"), "the gate never writes the ledger")

    # ================================================================ locked past the timeout
    def _locked(self):
        import time
        self.bound_job(); self.deploy_hook()
        self.hold_exclusive()                                           # second connection, BEGIN EXCLUSIVE, kept open
        t0 = time.monotonic()
        self.assert_core_denies("ledger-locked")
        core_s = time.monotonic() - t0
        t0 = time.monotonic()
        self.assert_hook_denies("ledger-locked")
        hook_s = time.monotonic() - t0
        self.evidence["locked_seconds"] = {"core": round(core_s, 3), "hook": round(hook_s, 3), "timeout": READ_TIMEOUT}
        self.assertGreaterEqual(core_s, READ_TIMEOUT * 0.5, "the deny came from the timeout, not an instant error")
        self.assertLess(core_s, READ_TIMEOUT + 5.0, "bounded wait")

    @plat_skip("linux")
    def test_T19_locked_past_timeout_linux(self):
        """SQLite rollback-journal locking via POSIX advisory locks (Linux)."""
        self._locked()

    @plat_skip("win32")
    def test_T19_locked_past_timeout_win32(self):
        """SQLite rollback-journal locking via Win32 byte-range locks."""
        self._locked()

    # ================================================================ identity change (core only; see docstring)
    def _identity(self):
        self.bound_job()
        expected = self.replace_file_identity()
        ident = self.evidence["identity"]
        self.assertNotEqual((ident["before"]["dev"], ident["before"]["ino"]), (ident["after"]["dev"], ident["after"]["ino"]),
                            f"fixture limitation on {PLAT}: the replacement kept the file identity {ident}")
        d = self.decide(expected_identity=expected)
        self.assertEqual((d["decision"], d["reason"]), ("deny", "ledger-identity"), d)
        # same bytes, matching identity supplied -> the healthy decision again (identity is the only difference)
        now_id = self.gatecore.ledger_identity(self.db)
        d2 = self.decide(expected_identity=now_id)
        self.assertEqual(d2["decision"], "allow", d2)

    @plat_skip("linux")
    def test_T19_identity_change_core_linux(self):
        """st_ino changes when a file is removed and re-created (ext4 and other POSIX filesystems)."""
        self._identity()

    @plat_skip("win32")
    def test_T19_identity_change_core_win32(self):
        """st_ino on Windows is the NTFS file reference number; replacement normally changes it."""
        self._identity()

    def test_T19_identity_hook_not_applicable_by_contract(self):
        """The frozen hook config (section 4) has no identity field, so the deployed hook cannot be given an
        `expected_identity`; the identity fault is a core-only case. Pinned so the gap is visible, not silent."""
        self.deploy_hook()
        with open(os.path.join(os.path.dirname(self.hook), "wf_gate.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(sorted(cfg), ["db", "hook_sha256", "read_timeout_s", "root", "wf_dir", "wfcore_sha256"])
        self.evidence["identity_hook"] = "not applicable: no identity field in the frozen hook config"

    # ================================================================ chain-tail tamper
    def test_T19_chain_tail_tamper_core_and_hook(self):
        self.bound_job(); self.deploy_hook()
        self.tamper_chain_tail()
        self.assert_core_denies("chain-tail")
        self.assert_hook_denies("chain-tail")

    # ================================================================ open recovery
    def test_T19_recovery_open_core_and_hook(self):
        self.bound_job(); self.deploy_hook()
        c, r = self.coord("recover", "begin", "j", "--reason", "T19 fixture", "--turn", "owner turn: recover job j")
        self.assertEqual(c, 0, r)
        self.assert_core_denies("recovery-open")
        self.assert_hook_denies("recovery-open")
        # shell channel for the bound session is denied too (rule 4 applies to both channels)
        d = self.decide(self.payload(tool="Bash") | {"tool_input": {"command": "echo hi"}})
        self.assertEqual((d["decision"], d["reason"], d["channel"]), ("deny", "recovery-open", "shell"), d)
        c, r = self.coord("recover", "close", "j", "--turn", "owner turn: recover close job j")
        self.assertEqual(c, 0, r)
        d = self.decide(); self.assertEqual(d["decision"], "allow", d)

    # ================================================================ gate.db unwritable -> audit-unavailable (hook)
    def test_T19_gate_db_unwritable_preventable_allow_becomes_audit_unavailable(self):
        self.bound_job(); self.deploy_hook()
        self.decide()                                                   # the core itself still says allow ...
        self.make_gate_db_unwritable()
        out, err = self.assert_hook_denies("audit-unavailable")        # ... but an unrecorded allow never proceeds
        self.assertIs(out.get("logged"), False, out)
        failures = os.path.join(self.root, "runtime", "gate", "gate-audit-failures.log")
        self.assertTrue(os.path.isfile(failures), "one failure line is appended")
        with open(failures, encoding="utf-8") as fh:
            lines = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(lines[0].get("tool_name"), "Write", lines[0])
        self.assertTrue(lines[0].get("error") or lines[0].get("error_class"), lines[0])

    def test_T19_gate_db_unwritable_deny_stays_deny(self):
        """Broken ledger + unwritable gate.db: the deny keeps its ledger reason (rule 2 runs before everything else,
        S4A-A3 item 5) and is never upgraded to another reason; one failures-log line is appended."""
        self.bound_job(); self.deploy_hook()
        self.make_gate_db_unwritable()
        self.make_not_sqlite()
        out, err = self.assert_hook_denies("ledger-unreadable")
        self.assertIs(out.get("logged"), False, out)
        failures = os.path.join(self.root, "runtime", "gate", "gate-audit-failures.log")
        with open(failures, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for x in fh if x.strip()), 1)

    def test_T19_gate_db_unwritable_shell_allow_stays_allow(self):
        """Healthy ledger, only gate.db unwritable: a shell attempt from an unbound session is not preventable, so
        the allow stands (`logged: false`), the recording failure is reported on stderr and one failures-log line
        is appended (section 3 write-failure rule)."""
        self.bound_job(); self.deploy_hook()
        self.make_gate_db_unwritable()
        code, out, err = self.run_hook(self.payload(session="S-unbound", tool="Bash") | {"tool_input": {"command": "echo"}})
        self.assertEqual((code, out.get("decision"), out.get("channel"), out.get("logged")),
                         (0, "allow", "shell", False), (out, err))
        self.assertTrue(err.strip(), "the recording failure is reported on stderr")
        failures = os.path.join(self.root, "runtime", "gate", "gate-audit-failures.log")
        with open(failures, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for x in fh if x.strip()), 1)

    # ================================================================ never allow: every fault, core + hook, in one sweep
    def test_T19_no_fault_ever_allows(self):
        """Independent of the specific reason strings: under each fault the decision is deny on both surfaces."""
        self.bound_job(); self.deploy_hook()
        faults = {"recovery-open": lambda: self.coord("recover", "begin", "j", "--reason", "sweep", "--turn", "owner turn: recover job j"),
                  "chain-tail": self.tamper_chain_tail, "not-sqlite": self.make_not_sqlite,
                  "missing": lambda: os.remove(self.db)}
        for name, make in faults.items():
            with self.subTest(fault=name):
                make()
                self.assertEqual(self.decide()["decision"], "deny", name)
                code, out, _ = self.run_hook()
                self.assertEqual((code, out.get("decision")), (2, "deny"), (name, out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
