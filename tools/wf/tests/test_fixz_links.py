r"""PACKAGE FIX2 batch Z, lane F2ZP (WF-128; AMENDMENT FIX2-A17 Z6 as corrected by A18 and A19; spec :194).

Rule 1's gate-file identity resolves a target on the boundary root's anchor with a link-by-link walk: each component
is `lstat`ed, a link's target is read with `readlink` without following it, and a link whose target is remote ends
the walk with no `stat`, `realpath` or `open` on anything under it. Rule 1 then compares by spelling only, and the
later rules decide as before. Remote (A18) = a UNC host that is not this machine, in any spelling: \\host\...,
\\?\UNC\host\..., \\.\UNC\host\..., \\?\GLOBALROOT\Device\Mup\.... Followed as today: local device forms (\\?\X:\...,
\\?\Volume{GUID}\...), this machine's admin shares, and any drive letter, including one mapped to a network share
(A19, a known limit pinned below). Every other decision is unchanged.

Windows only where a test needs symbolic links (Developer Mode or the symlink privilege) or junctions; skipped with
the reason elsewhere. Fixtures only: scratch ledgers under WF_TEST_TMP; `decide` is called in-process; no hook is
installed and nothing is written outside the scratch folder.
"""
from __future__ import annotations

import builtins
import ctypes
import os
import unittest
import uuid
from typing import Any
from unittest import mock

from test_gate_decide import _Gate
from wfcore import gatecore

WINDOWS = os.name == "nt"
BS = "\\"


class _Links(_Gate):
    def setUp(self) -> None:
        super().setUp()
        if not WINDOWS:
            self.skipTest("UNC link targets, junctions and volume paths exist on Windows only")
        self.explicit()
        self.hooks = os.path.dirname(self.hook)
        os.makedirs(self.hooks)
        with open(self.hook, "w", encoding="utf-8") as fh:
            fh.write("# fixture gate hook\n")
        self.alias = os.path.join(os.path.dirname(self.dir), f"{self.scratch}-alias")   # root's anchor, outside root
        os.makedirs(self.alias)
        self.host = f"wf-no-such-host-{uuid.uuid4().hex[:8]}"

    def symlink(self, target: str, name: str, base: str | None = None) -> str:
        link = os.path.join(base or self.alias, name)
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink not creatable here ({type(exc).__name__}: {exc})")
        return link

    def watched(self, payload: dict) -> tuple[dict, list[tuple[str, str]]]:
        """decide() with os.stat, os.lstat, os.path.realpath, os.readlink and open recorded: (result, calls)."""
        calls: list[tuple[str, str]] = []

        def spy(name: str, real: Any) -> Any:
            def call(path: Any, *a: Any, **k: Any) -> Any:
                calls.append((name, str(path)))
                return real(path, *a, **k)
            return call
        with mock.patch.object(os, "stat", spy("stat", os.stat)), mock.patch.object(os, "lstat", spy("lstat", os.lstat)), \
                mock.patch.object(os.path, "realpath", spy("realpath", os.path.realpath)), \
                mock.patch.object(os, "readlink", spy("readlink", os.readlink)), \
                mock.patch.object(builtins, "open", spy("open", builtins.open)):
            r = self.d(payload)
        return r, calls

    def under(self, calls: list[tuple[str, str]], link: str, kinds: tuple[str, ...]) -> list[tuple[str, str]]:
        """Calls of the given kinds on the link itself or on any path through it."""
        key = os.path.normcase(link)
        return [(k, p) for k, p in calls if k in kinds
                and (os.path.normcase(p) == key or os.path.normcase(p).startswith(key + BS))]


class Z6RemoteTargets(_Links):
    """A link on the root's anchor whose target is a remote UNC host: no I/O through it, the same decision."""

    def remote_targets(self) -> list[str]:
        h = self.host
        return [BS * 2 + h + BS + "share", BS * 2 + "?" + BS + "UNC" + BS + h + BS + "share",
                BS * 2 + "?" + BS + "GLOBALROOT" + BS + "Device" + BS + "Mup" + BS + h + BS + "share"]

    def test_outside_the_root_no_io_through_the_link(self):
        for i, target in enumerate(self.remote_targets()):
            link = self.symlink(target, f"remote-{i}")
            for session, reason in (("U9", "outside-boundary"), ("B1", "out-of-scope")):
                with self.subTest(target=target, session=session):
                    r, calls = self.watched(self.p(absolute=os.path.join(link, "wf_gate_hook.py"), session=session))
                    self.assertEqual(r["reason"], reason, r)
                    self.assertEqual(self.under(calls, link, ("stat", "realpath", "open")), [])
                    self.assertEqual([c for c in self.under(calls, link, ("lstat",)) if os.path.normcase(c[1])
                                      != os.path.normcase(link)], [], "nothing under the link is lstat'ed")
                    self.assertEqual([c for c in calls if self.host.casefold() in c[1].casefold()], [])

    def test_inside_the_root_rule_0_refuses_the_link(self):
        """Rule 0's link refusal decides, exactly as today (unresolvable-path), with no I/O through the link."""
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work, exist_ok=True)
        link = self.symlink(self.remote_targets()[0], "remote-in-root", base=work)
        r, calls = self.watched(self.p(absolute=os.path.join(link, "a.txt")))
        self.assertEqual((r["decision"], r["reason"]), ("deny", "unresolvable-path"), r)
        self.assertEqual(self.under(calls, link, ("stat", "realpath", "open")), [])

    def test_the_remote_target_is_classified_from_its_text(self):
        r"""A18's four spellings are remote; this machine's admin share, local device forms and drive paths are not.
        `\\.\UNC\host\...` cannot be stored as a symlink target, so it is checked on the classifier."""
        h = self.host
        for text in (*self.remote_targets(), BS * 2 + "." + BS + "UNC" + BS + h + BS + "share",
                     BS * 2 + "?" + BS + BS * 2 + "?" + BS + "UNC" + BS + h + BS + "s"):
            with self.subTest(target=text):
                self.assertIsNone(gatecore._link_target(text, self.alias), "remote: the walk stops")
        drive = self.hook[0]
        for text in (self.hooks, BS * 2 + "?" + BS + self.hooks, BS * 2 + "localhost" + BS + drive + "$" + self.hooks[2:],
                     BS * 2 + "?" + BS + "Volume{00000000-0000-0000-0000-000000000000}" + BS + "x", "relative" + BS + "dir"):
            with self.subTest(target=text):
                self.assertIsNotNone(gatecore._link_target(text, self.alias), "followed as today")


class Z6LocalTargetsUnchanged(_Links):
    """Plexy's measured cases (A18): a local link alias of a gate file is still protected-path."""

    def denied(self, link: str) -> None:
        for session in ("U9", "B1"):
            with self.subTest(link=link, session=session):
                r = self.d(self.p(absolute=os.path.join(link, "wf_gate_hook.py"), session=session))
                self.assertEqual((r["decision"], r["reason"]), ("deny", "protected-path"), r)

    def volume_hooks(self) -> str:
        buf = ctypes.create_unicode_buffer(64)
        if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(self.hooks[:3], buf, len(buf)):
            self.skipTest("no volume GUID path for this drive")
        return buf.value + self.hooks[3:]

    def test_symlink_to_the_volume_guid_dir(self):
        self.denied(self.symlink(self.volume_hooks(), "volume-symlink"))

    def test_junction_to_the_volume_guid_dir(self):
        import _winapi
        link = os.path.join(self.alias, "volume-junction")
        try:
            _winapi.CreateJunction(self.volume_hooks(), link)
        except OSError as exc:
            self.skipTest(f"junction to a volume path not creatable here ({exc})")
        self.denied(link)

    def test_symlink_to_the_drive_form(self):
        self.denied(self.symlink(self.hooks, "drive-symlink"))

    def test_symlink_to_the_extended_length_form(self):
        self.denied(self.symlink(BS * 2 + "?" + BS + self.hooks, "extended-symlink"))

    def test_relative_symlink(self):
        self.denied(self.symlink(os.path.relpath(self.hooks, self.alias), "relative-symlink"))

    def test_chain_of_local_links(self):
        first = self.symlink(self.hooks, "chain-1")
        self.denied(self.symlink(first, "chain-2"))

    def test_link_loop_fails_closed(self):
        """A loop cannot be resolved: rule 1 treats it as the gate file (fail closed), as today."""
        a, b = os.path.join(self.alias, "loop-a"), os.path.join(self.alias, "loop-b")
        self.symlink(b, "loop-a")
        self.symlink(a, "loop-b")
        r = self.d(self.p(absolute=os.path.join(a, "x.txt"), session="U9"))
        self.assertEqual((r["decision"], r["reason"]), ("deny", "protected-path"), r)


class Z6KnownLimitDriveLetters(_Links):
    """A19: a link to a drive letter is followed as today, even if that letter were a mapped network drive (telling
    one from a local drive needs a drive-type query, which Z6 does not add). Pinned with local stand-ins."""

    def test_limit_link_to_a_drive_letter_is_followed(self):
        root_drive = self.hook[0].upper()
        others = sorted({d[0].upper() for d in os.listdrives()} - {root_drive}) if hasattr(os, "listdrives") else []
        targets = [BS * 2 + "?" + BS + root_drive + ":" + BS]                    # \\?\X:\ of the root's own drive
        targets += [d + ":" + BS for d in others[:1]]                          # another local drive, if there is one
        for i, target in enumerate(targets):
            link = self.symlink(target, f"drive-{i}")
            with self.subTest(target=target):
                r, calls = self.watched(self.p(absolute=os.path.join(link, "wf-z6-probe.txt"), session="U9"))
                self.assertEqual(r["reason"], "outside-boundary", r)
                plain = target[4:] if target.startswith(BS * 2 + "?" + BS) else target       # X:\ either way
                reached = [c for c in calls if c[0] in ("stat", "realpath")
                           and (self.under([c], link, (c[0],)) or os.path.normcase(c[1].replace(BS * 2 + "?" + BS, "", 1))
                                .startswith(os.path.normcase(plain)))]
                self.assertTrue(reached, f"rule 1 resolved through the drive-letter target: {calls}")


if __name__ == "__main__":
    unittest.main()
