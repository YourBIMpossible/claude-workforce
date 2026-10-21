"""PACKAGE-FIX3-II, lane F3-E2E (WF-205; contract §8 O1): the four lanes joined. INTERFACE-CONTRACT-FIX3-II r2 §6.3 and
§7's E2E column, with AMENDMENTS A1-A3, plus WF-199 N4 (role-bearing writes through the broker, compared with direct).

The full stack, within one OS account:
- `wfbroker.main` serves the ledger with the production run (`wf.run_brokered`) and F3-ACC's account check;
- its listener is the real one (`peer.listen`: peer_win's named pipe, or peer_linux's socket), wrapped by fix3ii_fakes'
  ScriptedPeerListener, so the broker reads a scripted fake account for each connection, in connection order;
- the clients are `wf --broker` in-process (`wf.run`, the real `peer.connect`), or subprocesses for ID-2's shell forms,
  ID-4's child, ID-5's detached client and BRK-12's concurrent clients.

Real worker accounts, ACLs and their refusals are the parked RA lane (§7, D23): no test here needs a second account,
an ACL change, admin or a service. W's own setup is its direct (`--db`) calls, as ACCEPTANCE-ii's fixtures say.
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
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

import fix3ii_fakes as fakes
import wf
import wfbroker
from wfcore import broker, client, peer, wire
from wfcore import db as dbm

SCRUB = ("WF_DB", "WF_BROKER", "WF_ROLE", "WF_SESSION", "WF_TEST_FAULTS")
PLATFORM = peer.host_platform()
FAKE = fakes.FAKE
B1, B2, R1, UNLISTED = FAKE["B1"], FAKE["B2"], FAKE["R1"], FAKE["unlisted"]
PY = sys.executable
WF_PY = os.path.join(test_wf.WF_DIR, "wf.py")
WFCORE = wire.wfcore_sha256(test_wf.WF_DIR)
INTENT = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"})
ROLES = {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["tools/wf/**"]},
         "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
         "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["tools/wf/**"]}}
REPLACEMENT = {"allowed": True, "max": 1, "expiry": "2999-12-31T23:59:59Z"}
GRANT_MAP = dict(test_wf.GRANT, ars={"roles": ROLES, "replacement": REPLACEMENT,
                                     "accounts": {"builder": [B1, B2], "reviewer": [R1]}})
GRANT_NOMAP = dict(test_wf.GRANT, ars={"roles": ROLES, "replacement": REPLACEMENT})
SPOOF = ["log-intervention", "j", "--label", "spoof", "--necessity", "unknown", "--minutes", "1"]  # coordinator-writable
_COUNTER = iter(range(1, 1_000_000))


def stop_content(session: str, generation: int) -> str:
    return json.dumps({"role": "builder", "session": session, "generation": generation,
                       "how": "process exit observed", "when": "2026-10-21T04:00:00Z",
                       "owner_check": "no child process, no outstanding operation",
                       "owner_turn": "owner turn: builder session stopped in job j"})


class _Env(unittest.TestCase):
    """Every new test scrubs the route and fault variables (contract §6.3) and records WF_BROKER unset (O5)."""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        super().setUp()
        self.assertNotIn("WF_BROKER", os.environ)


class _E2E(_Env, test_wf.Base):
    """W's ledger, the broker over the real transport, and clients."""

    def setUp(self) -> None:
        super().setUp()
        self.own = peer.own_account()  # the real platform module (F3-OSI-W / F3-OSI-L)
        self.ids = {"own": self.own, "B1": B1, "B2": B2, "R1": R1, "unlisted": UNLISTED}
        self.assertNotIn(self.own, FAKE.values())
        self.address = self._new_address()
        self.log_path = os.path.join(self.dir, "runtime", "broker", "broker-log.jsonl")
        self.gmap = os.path.join(self.dir, "grant-map.json")
        self.gnomap = os.path.join(self.dir, "grant-nomap.json")
        for path, g in ((self.gmap, GRANT_MAP), (self.gnomap, GRANT_NOMAP)):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(g, fh)
        self.emitted: list[dict] = []
        self.listeners: list[Any] = []
        self.serving: threading.Thread | None = None
        self.exits: list[int] = []

    def _new_address(self) -> str:
        if PLATFORM == "win32":
            return f"\\\\.\\pipe\\wf-e2e-{os.getpid()}-{next(_COUNTER)}"
        d = tempfile.mkdtemp(prefix="wfe2e-")  # a short path: sun_path holds 107 bytes
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.join(d, "b.sock")

    # ---- W's direct calls (setup) ---------------------------------------------------------------------------------
    def w(self, *argv: str, db: str | None = None, ok: bool = True) -> tuple[int, dict]:
        res = wf.run(["--db", db or self.db, "--role", "coordinator", "--session", "C1", *argv])
        if ok:
            self.assertEqual(res[0], 0, res)
        return res

    def direct(self, role: str, session: str, *argv: str, generation: int = 1, db: str | None = None):
        return wf.run(["--db", db or self.db, "--role", role, "--session", session, "--generation", str(generation),
                       *argv])

    def activate_grant(self, job: str, gpath: str, db: str | None = None) -> None:
        h = self.w("grant", "propose", job, "--grant", gpath, db=db)[1]["block_hash"][:8]
        self.w("grant", "activate", job, "--owner-hash", h, "--quote", f"approve G1 {h}", "--turn", "owner turn",
               db=db)

    def explicit(self, job: str = "j", db: str | None = None) -> None:
        """contract, grant with the map (proposed, activated), C1 self-bind, builder B1 bound to B1's account."""
        self.w("job", "new", job, "--contract", self.cpath, db=db)
        self.activate_grant(job, self.gmap, db=db)
        self.w("bind", job, "--bind-role", "coordinator", "--bind-session", "C1", db=db)
        self.w("bind", job, "--bind-role", "builder", "--bind-session", "B1", "--bind-account", B1, db=db)

    def replaced(self) -> None:
        """explicit(); B1 intent; stop B1; reconcile; resume; B2 bound at generation 2 to B2's account."""
        self.explicit()
        self.assertEqual(self.direct("builder", "B1", "append", "j", "--type", "intent", "--content", INTENT)[0], 0)
        self.w("append", "j", "--type", "stop-confirmed", "--content", stop_content("B1", 1))
        self.w("reconcile", "j")
        self.w("recover", "decide", "j", "--decision", "resume", "--turn", "owner turn: resume job j with a new builder")
        self.w("bind", "j", "--bind-role", "builder", "--bind-session", "B2", "--bind-generation", "2",
               "--bind-account", B2)

    # ---- the broker -----------------------------------------------------------------------------------------------
    def start(self, *who: Any, db: str | None = None, clients: tuple[str, ...] = (B1, B2, R1)) -> None:
        """wfbroker.main serving on a thread. The n-th connection is read as `who[n]` (a name of self.ids, or an
        OSError); three more as the own account follow, for the stop."""
        script = [w if isinstance(w, BaseException) else (self.ids[w], 4242) for w in who]
        script += [(self.own, os.getpid())] * 3
        real_listen = peer.listen

        def listen(address: str, client_accounts):
            sl = fakes.ScriptedPeerListener(real_listen(address, client_accounts), script)
            self.listeners.append(sl)
            return sl

        self.enterContext(mock.patch.object(peer, "listen", listen))
        self.enterContext(mock.patch.object(wfbroker, "_emit", self.emitted.append))
        argv = ["--db", db or self.db, "--address", self.address]
        for c in clients:
            argv += ["--client-account", c]
        self.serving = threading.Thread(target=lambda: self.exits.append(wfbroker.main(argv)), daemon=True)
        self.serving.start()
        self.wait_for(lambda: any(e.get("code") in ("broker-ready", "broker-config") for e in self.emitted),
                      "the ready line")
        self.assertEqual(self.emitted[0]["code"], "broker-ready", self.emitted)
        self.addCleanup(self.stop)

    def stop(self) -> None:
        if self.serving is None or not self.serving.is_alive():
            return
        for _ in range(3):
            wfbroker.main(["--stop", "--address", self.address])
            self.serving.join(15)
            if not self.serving.is_alive():
                return
        for sl in self.listeners:  # last resort: a closed listener ends serve() after ACCEPT_ERRORS_MAX failures
            sl.close()
        self.serving.join(30)

    def wait_for(self, predicate, what: str, timeout: float = 30.0) -> None:
        end = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > end:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.02)

    def log(self) -> list[dict]:
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="ascii") as fh:
            return [json.loads(line) for line in fh]

    def logged(self, n: int) -> list[dict]:
        """The log once it holds `n` lines: the broker writes a connection's line after it answers and closes, so a
        client can return first."""
        self.wait_for(lambda: len(self.log()) >= n, f"{n} log line(s)")
        return self.log()

    # ---- clients --------------------------------------------------------------------------------------------------
    def wf_b(self, *argv: str) -> tuple[int, dict]:
        """`wf --broker <address> …` in-process, over the real transport."""
        return wf.run(["--broker", self.address, *argv])

    def wf_proc(self, argv: list[str], env: dict | None = None, **kw: Any) -> tuple[int, dict]:
        """`python -I wf.py --json --broker <address> …` as a child process."""
        r = subprocess.run([PY, "-I", WF_PY, "--json", "--broker", self.address, *argv], env=env or dict(os.environ),
                           capture_output=True, text=True, timeout=120, **kw)
        return r.returncode, json.loads(r.stdout)

    def state(self, job: str = "j", db: str | None = None) -> tuple[int, tuple[int, str]]:
        return fakes.ledger_state(db or self.db, job)

    def rows(self, job: str = "j", db: str | None = None) -> list[dict]:
        conn = dbm.connect(db or self.db, create=False)
        try:
            with dbm.read_snapshot(conn):
                return dbm.rows(conn, job)
        finally:
            conn.close()

    def assertRefused(self, res: tuple[int, dict], reason: str, exit_code: int = 1) -> None:
        code, body = res
        self.assertEqual((code, body.get("code")), (exit_code, "usage" if reason == "usage" else "rejected"), body)
        self.assertTrue(body["error"].startswith(f"rejected({reason})"), body)


# ===================================================================================================== the ID rows

class Identity(_E2E):
    """ID-1, ID-3, ID-4, ID-7, ID-9 (b)(c), ID-13: the account decides, through the whole stack."""

    def test_id1_the_C_spoof_literal(self):
        self.explicit()
        before = self.state()
        self.start("B1", "B1")
        self.assertRefused(self.wf_b("--role", "coordinator", "--session", "C1", *SPOOF), "caller-mismatch")
        # the literal form of ACCEPTANCE-ii's cell, an intent claimed as the coordinator: the account check is first
        self.assertRefused(self.wf_b("--role", "coordinator", "--session", "C1", "append", "j", "--type", "intent",
                                     "--content", INTENT), "caller-mismatch")
        self.assertEqual(self.state(), before)
        self.assertEqual([x["reason"] for x in self.logged(2)], ["caller-mismatch", "caller-mismatch"])

    def test_id3_the_owner_spoof(self):
        self.explicit()
        before = self.state()
        self.start("B1")
        self.assertRefused(self.wf_b("--role", "owner", "--session", "O-x", *SPOOF), "caller-mismatch")
        self.assertEqual(self.state(), before)

    def test_id4_a_nested_session(self):
        """A child process of B1's claims reviewer R1, with R1 bound to R1's account: the account is still B1's."""
        self.explicit()
        self.assertEqual(self.direct("builder", "B1", "append", "j", "--type", "delivered", "--content",
                                     '{"claims":[{"claim":"c","evidence":["e"]}]}')[0], 0)
        self.w("bind", "j", "--bind-role", "reviewer", "--bind-session", "R1", "--bind-account", R1)
        before = self.state()
        ack = json.dumps({"role": "reviewer", "session": "R1", "bind_seq": before[1][0]})  # a reviewer-writable claim
        self.start("B1")
        self.assertRefused(self.wf_proc(["--role", "reviewer", "--session", "R1", "append", "j", "--type",
                                         "ack-assignment", "--content", ack]), "caller-mismatch")
        self.assertEqual(self.state(), before)

    def test_id7_the_green_paths(self):
        """W direct claims C; B2's account claims B2 at generation 2: first its acknowledgement of the reconcile report
        (the existing replacement rule), then its own intent."""
        self.replaced()
        self.assertEqual(self.w(*SPOOF)[0], 0)  # W, direct, as C1
        report = [r for r in self.rows() if r["type"] == "reconcile-report"][-1]
        ids = [e["id"] for e in json.loads(report["content"])["exceptions"]]
        self.start("B2", "B2")
        b2 = ["--role", "builder", "--session", "B2", "--generation", "2"]
        code, res = self.wf_b(*b2, "ack-reconcile", "j", "--report", str(report["seq"]), "--exceptions",
                              json.dumps(ids))
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)
        code, res = self.wf_b(*b2, "append", "j", "--type", "intent", "--content", INTENT)
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)

    def test_id7_b1_and_r1_claim_their_own_sessions(self):
        self.explicit()
        self.start("B1", "B1", "R1")
        code, res = self.wf_b("--role", "builder", "--session", "B1", "append", "j", "--type", "intent", "--content",
                              INTENT)
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)
        code, res = self.wf_b("--role", "builder", "--session", "B1", "append", "j", "--type", "delivered",
                              "--content", '{"claims":[{"claim":"c","evidence":["e"]}]}')
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)
        self.w("bind", "j", "--bind-role", "reviewer", "--bind-session", "R1", "--bind-account", R1)
        code, res = self.wf_b("--role", "reviewer", "--session", "R1", "append", "j", "--type", "ack-assignment",
                              "--content", json.dumps({"role": "reviewer", "session": "R1",
                                                       "bind_seq": self.rows()[-1]["seq"]}))
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)

    def test_id9_b_a_job_without_a_map_and_c_a_session_no_bind_ties(self):
        self.w("job", "new", "j", "--contract", self.cpath)
        self.activate_grant("j", self.gnomap)  # (b): the latest activated grant has no ars.accounts
        self.w("job", "new", "m", "--contract", self.cpath)
        self.activate_grant("m", self.gmap)  # (c): a map, and no bind at all (implicit mode)
        before = (self.state("j"), self.state("m"))
        self.start("B1", "B1")
        self.assertRefused(self.wf_b("--role", "builder", "--session", "B1", "append", "j", "--type", "intent",
                                     "--content", INTENT), "caller-unattributed")
        self.assertRefused(self.wf_b("--role", "builder", "--session", "B1", "append", "m", "--type", "intent",
                                     "--content", INTENT), "caller-mismatch")
        self.assertEqual((self.state("j"), self.state("m")), before)

    def test_id13_a_builder_claims_the_other_builders_session(self):
        self.replaced()
        before = self.state()
        self.start("B1", "R1")
        for who in ("B1", "R1"):
            with self.subTest(who=who):
                self.assertRefused(self.wf_b("--role", "builder", "--session", "B2", "--generation", "2", "append",
                                             "j", "--type", "intent", "--content", INTENT), "caller-mismatch")
        self.assertEqual(self.state(), before)
        self.assertFalse([r for r in self.rows() if r["type"] == "claimant-conflict"])


class ShellForms(_E2E):
    """ID-2: the same spoof, indirect. Each shell form is a subprocess client; the broker sees one request and the
    account, not the form. ID-5: a client started out of the session tree, detached."""

    def spoof_cmd(self, role: str = "coordinator", session: str = "C1") -> list[str]:
        return [PY, "-I", WF_PY, "--json", "--role", role, "--session", session, "--broker", self.address, *SPOOF]

    def shell_forms(self) -> dict[str, list[str]]:
        literal = self.spoof_cmd()
        d = os.path.join(self.dir, "forms")
        os.makedirs(d)
        if PLATFORM == "win32":
            sh = shutil.which("pwsh") or shutil.which("powershell")
            self.assertIsNotNone(sh, "PowerShell is needed for the Windows shell forms")
            q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
            call = "& " + " ".join(q(a) for a in literal[:4])  # python -I wf.py --json
            tail = " ".join(q(a) for a in literal[8:])
            parts = f"$r = 'coord' + 'inator'; $s = 'C' + '1'; {call} --role $r --session $s {tail}; exit $LASTEXITCODE"
            plain = " ".join(["&"] + [q(a) for a in literal]) + "; exit $LASTEXITCODE"
            script = os.path.join(d, "spoof.ps1")
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
        script = os.path.join(d, "spoof.sh")
        with open(script, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(plain + "\n")
        enc = base64.b64encode(plain.encode("utf-8")).decode("ascii")
        return {"string parts": ["bash", "-c", parts],
                "encoded command": ["bash", "-c", f"eval \"$(printf %s {enc} | base64 -d)\""],
                "script file": ["bash", script]}

    def run_form(self, argv: list[str], env: dict | None = None) -> tuple[int, dict]:
        r = subprocess.run(argv, env=env or dict(os.environ), capture_output=True, text=True, timeout=180)
        out = r.stdout.strip()
        self.assertTrue(out, f"no output: rc {r.returncode}, stderr {r.stderr[-400:]}")
        return r.returncode, json.loads(out[out.index("{"):])

    def test_id2_each_form_reaches_the_broker_as_the_same_request(self):
        self.explicit()
        before = self.state()
        forms = self.shell_forms()
        self.start(*["B1"] * (len(forms) + 2))
        env = dict(os.environ, WF_ROLE="coordinator", WF_SESSION="C1")
        results = {"WF_ROLE and WF_SESSION": self.run_form([PY, "-I", WF_PY, "--json", "--broker", self.address,
                                                            *SPOOF], env),
                   "explicit": self.run_form(self.spoof_cmd())}
        for name, argv in forms.items():
            results[name] = self.run_form(argv)
        for name, res in results.items():
            with self.subTest(form=name):
                self.assertRefused(res, "caller-mismatch")
        self.assertEqual(self.state(), before)
        lines = self.logged(len(results))
        self.assertEqual([(x["cmd"], x["reason"]) for x in lines], [("log-intervention", "caller-mismatch")] * len(results))

    def test_id5_out_of_the_tree(self):
        """A detached client: on Windows a DETACHED_PROCESS in a new process group; on Linux `setsid` and a double fork."""
        self.explicit()
        before = self.state()
        self.start("B1")
        out = os.path.join(self.dir, "detached-out.json")
        cmd = self.spoof_cmd()
        if PLATFORM == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            with open(out, "w", encoding="utf-8") as fh:
                p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.DEVNULL,
                                     creationflags=flags)
                p.wait(120)
        else:
            q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
            subprocess.run(["setsid", "sh", "-c", f"( {' '.join(q(a) for a in cmd)} > {q(out)} 2>/dev/null & )"],
                           check=True, timeout=60)

        def done() -> bool:
            try:
                with open(out, encoding="utf-8") as fh:
                    json.loads(fh.read())
                return True
            except (OSError, ValueError):
                return False

        self.wait_for(done, "the detached client's answer", timeout=120)
        with open(out, encoding="utf-8") as fh:
            res = json.loads(fh.read())
        self.assertEqual((res["code"], res["error"].split(":", 1)[0]), ("rejected", "rejected(caller-mismatch)"))
        self.assertEqual(self.state(), before)


class ReadsWhoamiMap(_E2E):
    """ID-8 (reads, the real transport), ID-10 and MAP-1 (`whoami <J>`), W-3 (W's start check)."""

    def test_id8_reads_from_workers_answer_as_directly(self):
        self.explicit()
        lines = (["status", "j"], ["digest", "j"], ["verify", "j"], ["verify"],
                 ["grant-check", "j", "--check-role", "builder", "--action", "write", "--path", "jobs/j/work/a.txt"])
        self.start(*["B1", "R1"] * len(lines))
        for line in lines:
            for who in ("B1", "R1"):
                with self.subTest(line=line, who=who):
                    self.assertEqual(self.wf_b("--role", "builder", "--session", "B1", *line),
                                     self.direct("builder", "B1", *line))

    def test_id10_and_map1_whoami_with_the_job(self):
        self.explicit()
        before = self.state()
        bind_seq = self.rows()[-1]["seq"]
        self.start("B1", "R1", "own")
        code, res = self.wf_b("whoami", "j")
        self.assertEqual((code, {k: res[k] for k in ("mode", "account", "own", "map_in_force", "roles", "sessions")}),
                         (0, {"mode": "broker", "account": B1, "own": False, "map_in_force": True, "roles": ["builder"],
                              "sessions": [{"role": "builder", "session": "B1", "generation": 1, "bind_seq": bind_seq}]}))
        self.assertEqual(res["broker"], {"account": self.own, "wfcore": WFCORE})
        code, res = self.wf_b("whoami", "j")
        self.assertEqual((res["account"], res["roles"], res["sessions"]), (R1, ["reviewer"], []))
        code, res = self.wf_b("whoami", "j")
        self.assertEqual((res["own"], res["map_in_force"], res["roles"]), (True, True, []))
        self.assertEqual(self.state(), before)

    def test_w3_the_owners_start_check(self):
        self.explicit()
        before = self.state()
        self.start("own")
        code, res = self.wf_b("whoami")
        self.assertEqual((code, res["mode"], res["own"], res["account"], res["broker"]),
                         (0, "broker", True, self.own, {"account": self.own, "wfcore": WFCORE}))
        self.assertEqual(self.state(), before)


class Broker(_E2E):
    """BRK-1, -2, -4, -5, -8, -9, -10 and -12 over the real transport, with the account check."""

    def intent_argv(self, msg_id: str, session: str = "B1") -> list[str]:
        return ["--role", "builder", "--session", session, "--msg-id", msg_id, "append", "j", "--type", "intent",
                "--content", json.dumps({"path": f"jobs/j/work/{msg_id}.txt", "sha_before": "", "note": msg_id})]

    def request_for(self, *argv: str) -> dict:
        sent = []
        with mock.patch.object(client, "call", lambda a, r, connect=None: (sent.append(r), (0, {}))[1]):
            wf.run(["--broker", self.address, *argv])
        return sent[0]

    def raw(self, data: bytes) -> None:
        ch = peer.connect(self.address, time.monotonic() + 30)
        try:
            ch.send_all(data, time.monotonic() + 30)
        finally:
            ch.close()

    def msg_rows(self, msg_id: str) -> list[dict]:
        return [r for r in self.rows() if r["msg_id"] == msg_id]

    def test_brk1_no_broker_on_the_real_transport(self):
        self.explicit()
        before = self.state()
        os.environ["WF_DB"] = self.db
        with mock.patch.object(client, "CONNECT_BOUND_S", 0.5):
            code, res = self.wf_b(*self.intent_argv("m-brk1"))
        self.assertEqual((code, res["code"], res["reason"], res["address"]),
                         (1, "broker-unavailable", "no-endpoint", self.address))
        self.assertEqual(self.state(), before)

    def test_brk2_a_lost_response_then_a_resend(self):
        self.explicit()
        self.start("B1", "B1", "B1", "B1")
        self.raw(wire.frame(wire.encode_request(self.request_for(*self.intent_argv("m-lost")))))
        self.wait_for(lambda: len(self.log()) == 1, "the first log line")
        self.assertEqual(len(self.msg_rows("m-lost")), 1)
        code, res = self.wf_b(*self.intent_argv("m-lost"))
        self.assertEqual((code, res.get("outcome")), (0, "DUPLICATE-IGNORED"), res)
        data = wire.frame(wire.encode_request(self.request_for(*self.intent_argv("m-part"))))
        self.raw(data[: len(data) // 2])
        self.wait_for(lambda: len(self.log()) == 3, "the partial frame's log line")
        self.assertEqual(self.msg_rows("m-part"), [])
        code, res = self.wf_b(*self.intent_argv("m-part"))
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)
        self.assertEqual((len(self.msg_rows("m-lost")), len(self.msg_rows("m-part"))), (1, 1))

    def test_brk4_a_file_name_of_the_brokers_is_never_read(self):
        """A raw client sends the name of a JSON file that exists in the broker's account as inline input: the broker
        parses JSON text only (`inline_only`), so it is `usage`, and nothing is read."""
        self.explicit()
        before = self.state()
        self.start("B1")
        req = self.request_for(*self.intent_argv("m-file"))
        req["inputs"] = {"--content": self.cpath}  # a file in the broker's account; its JSON would parse
        code, res = client.call(self.address, req)
        self.assertEqual((code, res["code"]), (2, "usage"), res)
        self.assertIn("not a file and not JSON", res["error"])
        self.assertEqual(self.state(), before)

    def test_brk5_role_bearing_writes_reach_the_check(self):
        self.explicit()
        before = self.state()
        self.start("B1", "B1", "B1")
        self.assertRefused(self.wf_b("--role", "coordinator", "--session", "C1", "bind", "j", "--bind-role", "builder",
                                     "--bind-session", "B9", "--bind-account", B2), "caller-mismatch")
        self.assertRefused(self.wf_b("--role", "coordinator", "--session", "C1", "cancel", "j", "--reason", "r",
                                     "--turn", "owner turn j"), "caller-mismatch")
        self.assertRefused(self.wf_b("--role", "builder", "--session", "B1", "resolve", "j", "1"), "not-brokered")
        self.assertEqual(self.state(), before)

    def test_brk8_the_peer_cannot_be_read(self):
        self.explicit()
        before = self.state()
        self.start(OSError("scripted: the peer cannot be read"))
        self.assertRefused(self.wf_b(*self.intent_argv("m-brk8")), "caller-unattributed")
        self.assertEqual(self.state(), before)
        line = self.logged(1)[0]
        self.assertEqual((line["peer"], line["reason"]), (None, "caller-unattributed"))

    def test_brk9_one_broker_per_address_then_a_restart(self):
        self.explicit()
        before = self.state()
        self.start("B1")
        second = []
        with mock.patch.object(wfbroker, "_emit", second.append):
            self.assertEqual(wfbroker.main(["--db", self.db, "--address", self.address]), 1)
        self.assertEqual((second[0]["code"], second[0]["check"]), ("broker-config", "address-in-use"))
        self.assertEqual(self.wf_b("whoami")[1]["mode"], "broker")  # the first keeps serving
        self.stop()
        self.assertEqual(self.exits, [0])
        self.emitted.clear()
        self.start("B1")
        self.assertEqual(self.wf_b("whoami")[0], 0)
        self.assertEqual(self.state(), before)

    def test_brk10_the_uncertain_write_path_with_the_check(self):
        self.explicit()
        self.start("B1", "B1", "B1", "B1")
        os.environ["WF_TEST_FAULTS"] = "commit-error:IOERR"  # the broker's own environment (it runs in-process)
        code, res = self.wf_b(*self.intent_argv("m-u1"))
        del os.environ["WF_TEST_FAULTS"]
        self.assertEqual((res["code"], res["auto_resolve"]["outcome"]), ("commit-uncertain", "SKIPPED"), res)
        self.assertTrue(res["action"].startswith("wf resolve j "), res)
        code, nxt = self.wf_b(*self.intent_argv("m-u2"))
        self.assertEqual(nxt["code"], "uncertain-write-pending", nxt)
        self.assertRefused(self.wf_b("--role", "builder", "--session", "B1", "resolve", "j", str(res["seq"]),
                                     "--attempt", res["attempt_id"]), "not-brokered")
        code, decided = wf.run(["--db", self.db, "resolve", "j", str(res["seq"]), "--attempt", res["attempt_id"]])
        self.assertEqual((code, decided["outcome"]), (0, "NOT-COMMITTED"), decided)
        code, ok = self.wf_b(*self.intent_argv("m-u3"))
        self.assertEqual((code, ok.get("outcome")), (0, "COMMITTED"), ok)

    def test_brk12_serial_dispatch(self):
        """Two client processes write at once; the one pipe instance (or the socket) takes them one at a time."""
        self.explicit()
        self.start("B1", "B1")
        procs = [subprocess.Popen([PY, "-I", WF_PY, "--json", "--broker", self.address, *self.intent_argv(m)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for m in ("m-s1", "m-s2")]
        outs = [json.loads(p.communicate(timeout=180)[0]) for p in procs]
        self.assertEqual([(o.get("outcome"), o.get("ok")) for o in outs], [("COMMITTED", True)] * 2, outs)
        self.assertEqual(sorted(len(self.msg_rows(m)) for m in ("m-s1", "m-s2")), [1, 1])
        lines = self.logged(2)
        self.assertEqual(sorted(x["msg_id"] for x in lines), ["m-s1", "m-s2"])
        seqs = {r["msg_id"]: r["seq"] for r in self.rows() if r["msg_id"] in ("m-s1", "m-s2")}
        self.assertEqual([x["msg_id"] for x in lines], sorted(seqs, key=seqs.get))  # rows in the order taken


class MapAndW(_E2E):
    """MAP-4 through the CLI; W-2, the owner account through the broker."""

    def test_map4_the_bind_account_rules(self):
        self.explicit()
        before = self.state()
        cases = (("ars", ["--bind-role", "builder", "--bind-session", "B2", "--bind-account", R1]),     # (a)
                 ("ars", ["--bind-role", "builder", "--bind-session", "B9", "--bind-account", B1]),     # (b)
                 ("ars", ["--bind-role", "reviewer", "--bind-session", "R1"]),                          # (c)
                 ("malformed", ["--bind-role", "coordinator", "--bind-session", "C2", "--bind-account", B1]))  # (d)
        self.start(*["own"] * len(cases))
        for reason, extra in cases:
            with self.subTest(reason=reason, extra=extra):
                self.assertRefused(self.w("bind", "j", *extra, ok=False), reason)  # direct, W
                self.assertRefused(self.wf_b("--role", "coordinator", "--session", "C1", "bind", "j", *extra), reason)
        self.assertEqual(self.state(), before)

    def test_w2_the_owner_account_through_the_broker(self):
        self.explicit()
        self.start("own", "own", "own", "own")
        code, res = self.wf_b("--role", "coordinator", "--session", "C1", *SPOOF)
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)
        code, res = self.wf_b("--role", "owner", "--session", "O-1", *SPOOF)
        self.assertNotIn("caller-", json.dumps(res))  # accepted by the account check; the existing checks decide
        before = self.state()
        self.assertRefused(self.wf_b("--role", "builder", "--session", "B1", "append", "j", "--type", "intent",
                                     "--content", INTENT), "caller-mismatch")
        self.assertRefused(self.wf_b("--role", "reviewer", "--session", "R1", "append", "j", "--type", "intent",
                                     "--content", INTENT), "caller-mismatch")
        self.assertEqual(self.state(), before)


class DirectComparison(_E2E):
    """WF-199 N4: role-bearing writes through the broker compared with direct mode. Twin ledgers take the same
    commands with the same msg_ids; W's coordinator writes go through the broker as the own account and B1's builder
    writes as B1's account; each answer and each recorded row (all but `ts` and the hash chain) are equal."""

    def test_n4_role_bearing_writes_through_the_broker_equal_direct(self):
        twin = os.path.join(self.dir, "twin", "ledger", "l.db")
        for db in (self.db, twin):  # each ledger exists before its broker starts (wfbroker's ledger check)
            self.w("job", "new", "j0", "--contract", self.cpath, db=db)
        h = None
        steps: list[tuple[str, str, list[str]]] = [
            ("own", "C1", ["job", "new", "j", "--contract", self.cpath]),
            ("own", "C1", ["grant", "propose", "j", "--grant", self.gmap]),
            ("own", "C1", ["grant", "activate", "j", "--owner-hash", "@h", "--quote", "approve G1 @h", "--turn",
                           "owner turn"]),
            ("own", "C1", ["bind", "j", "--bind-role", "coordinator", "--bind-session", "C1"]),
            ("own", "C1", ["bind", "j", "--bind-role", "builder", "--bind-session", "B1", "--bind-account", B1]),
            ("B1", "B1", ["append", "j", "--type", "intent", "--content", INTENT]),
            ("B1", "B1", ["append", "j", "--type", "delivered", "--content",
                          '{"claims":[{"claim":"c","evidence":["e"]}]}']),
            ("own", "C1", SPOOF),
            ("own", "C1", ["reconcile", "j"]),
            ("own", "C1", ["grant", "revoke", "j", "--reason", "n4", "--turn", "owner turn naming job j"]),
            ("own", "C1", ["cancel", "j", "--reason", "n4", "--turn", "owner turn naming job j"]),
        ]
        self.start(*[who for who, _, _ in steps], db=twin)
        for n, (who, session, argv) in enumerate(steps, 1):
            role = "builder" if who == "B1" else "coordinator"
            argv = [a.replace("@h", h or "") for a in argv]
            g = ["--role", role, "--session", session, "--msg-id", f"n4-{n}"]
            d = wf.run(["--db", self.db, *g, *argv])
            b = self.wf_b(*g, *argv)
            with self.subTest(step=n, argv=argv[:2]):
                self.assertEqual((b[0], b[1].get("outcome"), b[1].get("code"), b[1].get("seq")),
                                 (d[0], d[1].get("outcome"), d[1].get("code"), d[1].get("seq")), (d, b))
            if argv[:2] == ["grant", "propose"]:
                h = d[1]["block_hash"][:8]
                self.assertEqual(b[1]["block_hash"], d[1]["block_hash"])
        keep = ("seq", "type", "msg_id", "author_role", "session_id", "generation", "reply_to", "grant_ref", "content")
        direct_rows = [{k: r[k] for k in keep} for r in self.rows()]
        broker_rows = [{k: r[k] for k in keep} for r in self.rows(db=twin)]
        self.assertEqual(len(direct_rows), len(steps))
        self.assertEqual(broker_rows, direct_rows)


class RealAccountRows(unittest.TestCase):
    """§7's RA column: a declared skip in this build, never a pass."""

    REASON = "real accounts: RA lane parked (PROTOCOL NOTE 2026-10-21T01:48Z)"

    def test_ra_the_identity_rows_as_real_accounts(self):
        self.skipTest(self.REASON)

    def test_ra_acl_rows(self):
        self.skipTest(self.REASON)


if __name__ == "__main__":
    unittest.main()
