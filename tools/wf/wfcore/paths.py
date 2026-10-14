"""Path resolution for the ledger, markers, exports and the fixture-only fault seam."""
from __future__ import annotations

import os

WF_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tools\wf
REPO_ROOT = os.path.dirname(os.path.dirname(WF_DIR))  # F:\Claude-Workforce
FIXTURES_TMP = os.path.join(REPO_ROOT, "fixtures", "tmp")
DEFAULT_DB = os.path.join(REPO_ROOT, "ledger", "ledger.db")


def norm(path: str) -> str:
    """Absolute, real (links resolved), case-folded Windows path for comparison."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_under(path: str, parent: str) -> bool:
    p = norm(path)
    q = norm(parent).rstrip("\\/") + os.sep
    return p.startswith(q)


def db_path(arg: str | None) -> str:
    return os.path.abspath(arg) if arg else DEFAULT_DB


def ledger_dir(db: str) -> str:
    return os.path.dirname(os.path.abspath(db))


def pending_dir(db: str, job: str) -> str:
    return os.path.join(ledger_dir(db), "pending", job)


def jobs_dir(db: str) -> str:
    d = ledger_dir(db)
    if os.path.basename(d).lower() == "ledger":
        return os.path.join(os.path.dirname(d), "jobs")
    return os.path.join(d, "jobs")


def export_dir(db: str, job: str) -> str:
    return os.path.join(jobs_dir(db), job, "export")


def faults_allowed(db: str) -> bool:
    return is_under(db, FIXTURES_TMP)
