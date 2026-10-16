"""Static validation of mapping.json against the test tree (no import, no execution).

A mapping is valid only when every referenced test id exists as a `test*` method on a module-level class in
`tools/wf/tests/<module>.py` (AST discovery, the same rule unittest uses for method names; class inheritance from
another test class in the same module is followed), no id is referenced twice within one requirement, every
`mapped` / `partial` requirement names at least one test, every `partial` / `missing` requirement names at least
one missing assertion with a proposed smallest test, and `out-of-layer` requirements name no test.
"""
from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field

STATUSES = ("mapped", "partial", "missing", "out-of-layer")


@dataclass(frozen=True)
class Validation:
    ok: bool
    errors: tuple[str, ...]
    selection: tuple[str, ...]                     # unique test ids, sorted
    by_requirement: dict[str, tuple[str, ...]] = field(default_factory=dict)


def discover_test_ids(tests_dir: str) -> set[str]:
    """`module.Class.method` for every test method in `tests_dir/*.py` (static)."""
    ids: set[str] = set()
    for name in sorted(os.listdir(tests_dir)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        path = os.path.join(tests_dir, name)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        module = name[:-3]
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

        def methods(cls: ast.ClassDef, seen: frozenset[str]) -> set[str]:
            own = {f.name for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and f.name.startswith("test")}
            for b in cls.bases:
                bname = b.id if isinstance(b, ast.Name) else None
                if bname and bname in classes and bname not in seen:
                    own |= methods(classes[bname], seen | {bname})
            return own

        for cname, cls in classes.items():
            for m in methods(cls, frozenset({cname})):
                ids.add(f"{module}.{cname}.{m}")
    return ids


def load_mapping(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate(mapping: dict, tests_dir: str) -> Validation:
    errors: list[str] = []
    reqs = mapping.get("requirements")
    if not isinstance(reqs, dict) or not reqs:
        return Validation(False, ("mapping has no 'requirements' object",), ())
    existing = discover_test_ids(tests_dir)
    selection: set[str] = set()
    by_req: dict[str, tuple[str, ...]] = {}
    for rid, row in reqs.items():
        if not isinstance(row, dict):
            errors.append(f"{rid}: row is not an object")
            continue
        status = row.get("status")
        if status not in STATUSES:
            errors.append(f"{rid}: status {status!r} not in {STATUSES}")
            continue
        tests = row.get("tests", [])
        if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
            errors.append(f"{rid}: tests is not a list of strings")
            continue
        if len(set(tests)) != len(tests):
            errors.append(f"{rid}: duplicate test id in the row")
        for t in tests:
            if t not in existing:
                errors.append(f"{rid}: test id does not exist in {os.path.basename(tests_dir)}: {t}")
        if status in ("mapped", "partial") and not tests:
            errors.append(f"{rid}: status {status} but no tests named")
        if status == "out-of-layer" and tests:
            errors.append(f"{rid}: out-of-layer rows name no tests")
        missing = row.get("missing", [])
        if status in ("partial", "missing"):
            if not isinstance(missing, list) or not missing:
                errors.append(f"{rid}: status {status} needs at least one missing-assertion entry")
            else:
                for i, m in enumerate(missing):
                    if not isinstance(m, dict) or not m.get("assertion") or not m.get("smallest_test"):
                        errors.append(f"{rid}: missing[{i}] needs 'assertion' and 'smallest_test'")
        if status == "mapped" and missing:
            errors.append(f"{rid}: status mapped but missing assertions listed (use partial)")
        if not row.get("sources"):
            errors.append(f"{rid}: no sources")
        by_req[rid] = tuple(tests)
        selection.update(tests)
    return Validation(not errors, tuple(errors), tuple(sorted(selection)), by_req)
