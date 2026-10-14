"""Fixture-only fault seam (FB1.4 D8). Honoured only for ledgers under fixtures\\tmp\\."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from . import paths

ENV = "WF_TEST_FAULTS"
KNOWN = {
    "delay-precommit", "delay-postcommit", "delay-aborting",
    "commit-error", "guard-bypass", "kill-before-commit", "marker-unwritable",
}


class FaultsNotAllowed(Exception):
    pass


@dataclass
class Faults:
    items: dict[str, str] = field(default_factory=dict)
    used: list[str] = field(default_factory=list)

    def has(self, name: str) -> bool:
        return name in self.items

    def arg(self, name: str) -> str:
        return self.items.get(name, "")

    def sleep_ms(self, name: str) -> None:
        if name in self.items:
            self.used.append(f"{name}:{self.items[name]}")
            time.sleep(int(self.items[name]) / 1000.0)

    def mark(self, name: str) -> None:
        self.used.append(f"{name}:{self.items.get(name, '')}")


def load(db: str) -> Faults:
    """Parse WF_TEST_FAULTS; refuse any value for a product ledger path."""
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return Faults()
    if not paths.faults_allowed(db):
        raise FaultsNotAllowed(f"faults-not-allowed: {ENV} is set but ledger {db} is not under {paths.FIXTURES_TMP}")
    items: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, arg = part.partition(":")
        if name not in KNOWN:
            raise FaultsNotAllowed(f"faults-not-allowed: unknown fault {name!r}")
        items[name] = arg
    return Faults(items=items)
