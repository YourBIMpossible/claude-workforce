r"""PACKAGE FIX2 batch Z, lane F2ZP (WF-128, WF-128b; AMENDMENT FIX2-A17 Z6 as corrected by A18, A19 and A20;
spec :194).

Rule 1's gate-file identity walks a target on the boundary root's anchor link by link before resolving it: each
component is `lstat`ed, and a link's target is read with `readlink` without following it. A link whose target is
remote (A18: a UNC host that is not this machine by batch S's names, in any spelling: \\host\..., \\?\UNC\host\...,
\\.\UNC\host\..., \\?\GLOBALROOT\Device\Mup\...) ends the walk with no `stat`, `realpath` or `open` on anything under
it, and rule 1 fails closed (A20): deny `protected-path`, unless rule 0 refuses the target anyway because its first
link lies under the root (`unresolvable-path`, as at the base). That covers this machine under a name batch S does
not recognise (an IP or IPv6 literal, an FQDN: Lazy WF-132 M1), which the base resolved to the gate file. A link
whose target Windows cannot traverse (a doubled `\\?\` prefix, N1) fails closed as at the base. When the walk meets
no remote target, rule 1 resolves the target exactly as the base does. Followed as before: local device forms
(\\?\X:\..., \\?\Volume{GUID}\...), this machine's shares by batch S's names, and any drive letter, including one
mapped to a network share (A19, a known limit pinned below). No decision is weaker than the base; the stricter one is
a link outside the root to a remote target, now denied.

Windows only where a test needs symbolic links (Developer Mode or the symlink privilege) or junctions; skipped with
the reason elsewhere. Fixtures only: scratch ledgers under WF_TEST_TMP; `decide` is called in-process; no hook is
installed and nothing is written outside the scratch folder.
"""
from __future__ import annotations

import builtins
import ctypes
import os
import socket
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

    def admin_share(self, host: str, path: str | None = None) -> str:
        r"""`path` (default: the hooks dir) spelled through `host`'s admin share: \\host\X$\...."""
        path = path or self.hooks
        return BS * 2 + host + BS + path[0] + "$" + path[2:]

    def fails_closed(self, link: str, leaves: tuple[str, ...] = ("wf_gate_hook.py", "other.txt"),
                     host: str | None = None) -> None:
        """A20: a Write through `link` is deny protected-path for an unbound and a bound session, with no stat,
        realpath or open on the link or under it, no lstat under it, and (given `host`) no call naming the host."""
        for leaf in leaves:
            for session in ("U9", "B1"):
                with self.subTest(link=os.path.basename(link), leaf=leaf, session=session):
                    r, calls = self.watched(self.p(absolute=os.path.join(link, leaf), session=session))
                    self.assertEqual((r["decision"], r["reason"]), ("deny", "protected-path"), r)
                    self.assertEqual(self.under(calls, link, ("stat", "realpath", "open")), [])
                    self.assertEqual([c for c in self.under(calls, link, ("lstat",)) if os.path.normcase(c[1])
                                      != os.path.normcase(link)], [], "nothing under the link is lstat'ed")
                    if host is not None:
                        self.assertEqual([c for c in calls if host.casefold() in c[1].casefold()], [])


class Z6RemoteTargets(_Links):
    """A link on the root's anchor whose target is a remote UNC host: no I/O through it; outside the root rule 1
    fails closed (A20: the named stricter case, a genuine network share), inside it rule 0 refuses the link as before."""

    def remote_targets(self) -> list[str]:
        h = self.host
        return [BS * 2 + h + BS + "share", BS * 2 + "?" + BS + "UNC" + BS + h + BS + "share",
                BS * 2 + "?" + BS + "GLOBALROOT" + BS + "Device" + BS + "Mup" + BS + h + BS + "share"]

    def test_outside_the_root_no_io_through_the_link(self):
        """A20: deny protected-path (the base allowed U9 after the network wait: outside-boundary; B1 out-of-scope)."""
        for i, target in enumerate(self.remote_targets()):
            with self.subTest(target=target):
                self.fails_closed(self.symlink(target, f"remote-{i}"), host=self.host)

    def test_inside_the_root_rule_0_refuses_the_link(self):
        """Rule 0's link refusal decides, exactly as today (unresolvable-path), with no I/O through the link."""
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work, exist_ok=True)
        link = self.symlink(self.remote_targets()[0], "remote-in-root", base=work)
        r, calls = self.watched(self.p(absolute=os.path.join(link, "a.txt")))
        self.assertEqual((r["decision"], r["reason"]), ("deny", "unresolvable-path"), r)
        self.assertEqual(self.under(calls, link, ("stat", "realpath", "open")), [])

    def test_the_remote_target_is_classified_from_its_text(self):
        r"""A18's four spellings are remote, and so is this machine under a name batch S does not recognise (M1);
        this machine's admin share by its known names, local device forms and drive paths are followed. A doubled
        `\\?\` prefix (N1) is unresolvable: Windows cannot traverse it, so the walk raises and rule 1 fails closed.
        `\\.\UNC\host\...` cannot be stored as a symlink target, so it is checked on the classifier."""
        h = self.host
        for text in (*self.remote_targets(), BS * 2 + "." + BS + "UNC" + BS + h + BS + "share",
                     *(self.admin_share(n) for n in ("127.0.0.2", "0--1.ipv6-literal.net", "192.0.2.10",
                                                     "2001-db8--10.ipv6-literal.net", "wf-z6.example.invalid"))):
            with self.subTest(target=text):
                self.assertIsNone(gatecore._link_target(text, self.alias), "remote: the walk stops")
        for text in (BS * 2 + "?" + BS + BS * 2 + "?" + BS + "UNC" + BS + h + BS + "s",
                     BS * 2 + "?" + BS + BS * 2 + "?" + BS + self.hooks):
            with self.subTest(target=text):
                with self.assertRaises(OSError):
                    gatecore._link_target(text, self.alias)
        for text in (self.hooks, BS * 2 + "?" + BS + self.hooks, self.admin_share("localhost"),
                     self.admin_share(socket.gethostname()),
                     BS * 2 + "?" + BS + "Volume{00000000-0000-0000-0000-000000000000}" + BS + "x", "relative" + BS + "dir"):
            with self.subTest(target=text):
                self.assertIsNotNone(gatecore._link_target(text, self.alias), "followed as before")


class Z6SelfAliasFailsClosed(_Links):
    """A20 (Lazy WF-132 M1): a link outside the root to this machine under a name batch S does not recognise. The
    base followed it to the gate file over SMB loopback (deny protected-path); Z6 must never resolve it, so rule 1
    fails closed, for the gate file and for any other file through the link alike."""

    def test_this_machine_under_an_unrecognised_name_is_denied(self):
        """The IPv6 loopback literal and 127.0.0.2 name this machine on any host; the documentation addresses and
        the .invalid name stand in for a LAN or VPN IP, an IPv6 literal and an FQDN (none is contacted)."""
        for i, name in enumerate(("0--1.ipv6-literal.net", "127.0.0.2", "192.0.2.10", "2001-db8--10.ipv6-literal.net",
                                  "wf-z6.example.invalid")):
            with self.subTest(name=name):
                self.fails_closed(self.symlink(self.admin_share(name), f"self-alias-{i}"), host=name)

    def test_this_machine_by_its_known_names_is_followed(self):
        """Batch S's names (`localhost`, this host's name) map the admin share to its drive: followed as at the base,
        so the gate file is protected-path by identity and another file is decided by the later rules."""
        try:
            os.stat(self.admin_share("localhost", self.hooks[:3]))
        except OSError as exc:
            self.skipTest(f"this machine's admin share is not reachable here ({type(exc).__name__}: {exc})")
        for i, name in enumerate(("localhost", socket.gethostname())):
            link = self.symlink(self.admin_share(name), f"known-{i}")
            for session in ("U9", "B1"):
                with self.subTest(name=i, session=session):
                    r = self.d(self.p(absolute=os.path.join(link, "wf_gate_hook.py"), session=session))
                    self.assertEqual((r["decision"], r["reason"]), ("deny", "protected-path"), r)
            with self.subTest(name=i, leaf="other.txt"):  # followed, not failed closed
                r = self.d(self.p(absolute=os.path.join(link, "other.txt"), session="U9"))
                self.assertEqual((r["decision"], r["reason"]), ("allow", "outside-boundary"), r)


class Z6DoubledPrefix(_Links):
    r"""A20 (Lazy WF-132 N1): a junction stored with a `\\?\`-prefixed target reads back with the prefix doubled, and
    Windows cannot traverse it. The base failed closed on it (`os.stat`: WinError 123); so does the walk, unstripped."""

    def test_doubled_prefix_junction_fails_closed(self):
        import _winapi
        elsewhere = os.path.join(self.alias, "elsewhere")
        os.makedirs(elsewhere)
        link = os.path.join(self.alias, "doubled-junction")
        _winapi.CreateJunction(BS * 2 + "?" + BS + elsewhere, link)
        if not os.readlink(link).startswith(BS * 2 + "?" + BS + BS * 2 + "?" + BS):
            self.skipTest("this Windows does not read the junction back with a doubled prefix")
        self.fails_closed(link, leaves=("x.txt", "wf_gate_hook.py"))


class Z6DirectSpellings(_Links):
    """A20's direct spellings (no link): rule 1 and rule 0 decide them as at the base. This machine's admin share by
    batch S's names is the gate file (protected-path); any other UNC host has no local form and rule 0 refuses it
    (unresolvable-path). No I/O names an unrecognised host."""

    def test_direct_spellings_are_unchanged(self):
        rest = self.hook[0] + "$" + self.hook[2:]
        cases: list[tuple[str, str, str | None]] = [
            (BS * 2 + "localhost" + BS + rest, "protected-path", None),
            (BS * 2 + "?" + BS + "UNC" + BS + "localhost" + BS + rest, "protected-path", None),
            (BS * 2 + socket.gethostname() + BS + rest, "protected-path", None)]
        for name in ("127.0.0.2", "0--1.ipv6-literal.net", "192.0.2.10", self.host):
            cases += [(BS * 2 + name + BS + rest, "unresolvable-path", name),
                      (BS * 2 + "?" + BS + "UNC" + BS + name + BS + rest, "unresolvable-path", name)]
        for i, (spelled, reason, host) in enumerate(cases):
            for session in ("U9", "B1"):
                with self.subTest(case=i, session=session):
                    r, calls = self.watched(self.p(absolute=spelled, session=session))
                    self.assertEqual((r["decision"], r["reason"]), ("deny", reason), r)
                    if host is not None:
                        self.assertEqual([c for c in calls if host.casefold() in c[1].casefold()], [], "no I/O on it")


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


class Z6LinkHopLimit(_Links):
    """AMENDMENT FIX2-A21 (Lazy WF-135 S1): the walk fails closed exactly where Windows does. Windows traverses 63
    chained links in one path and fails the 64th (WinError 1921), where the base's `os.stat` fails closed."""

    def chain(self, n: int) -> str:
        """n junctions, link_1 -> link_2 -> ... -> link_n -> a real dir outside the root; returns link_1."""
        import _winapi
        end = os.path.join(self.alias, f"chain{n}-end")
        os.makedirs(end)
        nxt = end
        for k in range(n, 0, -1):
            link = os.path.join(self.alias, f"chain{n}-{k}")
            _winapi.CreateJunction(nxt, link)
            nxt = link
        return nxt

    def test_63_links_are_followed_as_at_the_base(self):
        first = self.chain(63)
        for session, decision in (("U9", ("allow", "outside-boundary")), ("B1", ("deny", "out-of-scope"))):
            with self.subTest(session=session):
                r = self.d(self.p(absolute=os.path.join(first, "x.txt"), session=session))
                self.assertEqual((r["decision"], r["reason"]), decision, r)

    def test_64_links_fail_closed_as_at_the_base(self):
        first = self.chain(64)
        for session in ("U9", "B1"):
            with self.subTest(session=session):
                r = self.d(self.p(absolute=os.path.join(first, "x.txt"), session=session))
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
