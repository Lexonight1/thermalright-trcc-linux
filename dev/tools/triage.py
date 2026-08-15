#!/usr/bin/env python3
"""Triage a GitHub issue: fetch it, read its report, resolve its device.

    PYTHONPATH=src python3.12 dev/tools/triage.py 262
    PYTHONPATH=src python3.12 dev/tools/triage.py 262 244 267    # several

Every issue triaged by hand repeats the same five steps: pull the thread,
download the attached ``trcc report`` (the useful ones are ATTACHED, not
pasted), scrape the handshake line, work out what that fingerprint means, and
check whether the reporter's version predates the fix.  Done manually that is
ten minutes an issue and the version check is the one people skip -- which is
how a reporter gets told to test a fix that was not in their build.

The device resolution goes through the SHIPPING functions (``bulk_profile``,
``get_profile``, ``is_portrait_mounted``).  A hand-copy of those rules in an
auditor has drifted before: it dropped a guard, invented an FBL, and reported
a reporter-confirmed device as a bug.  An oracle that re-implements the thing
it audits proves nothing about the code that ships.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.registry import find_product

_ATTACHMENT = re.compile(r"https://github\.com/user-attachments/files/\d+/\S+?(?=[\s)\]]|$)")
# Two shapes carry the same bytes: the log line
# ``handshake OK: PM=N SUB=M … resolution=(w, h)`` and the report/hid-debug
# line ``Handshake: PM=N SUB=M fbl=X resolution=WxH``.  Reporters paste
# whichever they have, so accept both or miss half of them.
_HANDSHAKE = re.compile(
    r"[Hh]andshake(?: OK)?:\s*PM=(\d+)\s+SUB=(\d+)"
    r"(?:[^\n]*?resolution=\(?(\d+)[,x]\s*(\d+)\)?)?")
_VERSION = re.compile(
    r"trcc-linux:?\s+(\d+\.\d+\.\d+)|^\s*version\s+(\d+\.\d+\.\d+)"
    r"|TRCC(?: Linux)?:?\s+(\d+\.\d+\.\d+)", re.M)
_DISTRO = re.compile(
    r"distro_name → (.+)|^\s*distro\s+(.+)$|Distro:\s*(.+)$|^-?\s*OS:\s*(.+)$", re.M)
_INSTALLER = re.compile(
    r"installed_by\s+(\w+)|Installed(?:ation method)?:\s*(\w+)")
_USB_ID = re.compile(r"\b([0-9a-f]{4}):([0-9a-f]{4})\b")

# Fixes worth checking a reporter's version against.  Each is a commit that
# shipped; the release is resolved from git so this cannot go stale the way a
# hand-written version number does.
_KNOWN_FIXES = {
    "d40f17b9": "a reply identifies a panel (PM=0 → wrong geometry)",
    "157d85e8": "the display angle turns the wire, not the preview",
    "f4eee481": "firmware quirks resolve on a direct connect (CLI)",
    "cf17f609": "saved themes survive a symlinked /home (atomic distros)",
    "da4be2e9": "video themes stop freezing the UI and eating GBs",
    "010d001f": "CLI sensor readings stop being frozen at launch",
}


def _sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()


def _release_of(commit: str) -> str:
    tags = _sh("git", "tag", "--contains", commit).splitlines()
    return sorted(tags)[0] if tags else "UNRELEASED"


def _issue(number: int) -> dict:
    raw = _sh("gh", "issue", "view", str(number), "--json",
              "title,body,comments,author,state")
    return json.loads(raw) if raw else {}


def _fetch(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"(could not download: {e})"


def _first(match: re.Match | None) -> str:
    return next((g for g in match.groups() if g), "?") if match else "?"


def triage(number: int) -> None:
    issue = _issue(number)
    if not issue:
        print(f"#{number}: could not read the issue (is `gh` authenticated?)")
        return
    text = issue.get("body", "") + "\n" + "\n".join(
        c.get("body", "") for c in issue.get("comments", []))

    print(f"\n{'=' * 78}\n#{number}  {issue.get('title', '')[:66]}")
    print(f"{issue.get('state', '?')}  ·  opened by {issue.get('author', {}).get('login', '?')}"
          f"  ·  {len(issue.get('comments', []))} comment(s)")
    last = issue.get("comments") or []
    if last:
        who = last[-1].get("author", {}).get("login", "?")
        print(f"last word: {who} @ {last[-1].get('createdAt', '')[:10]}"
              f"{'   <-- AWAITING US' if who != 'Lexonight1' else ''}")

    for url in dict.fromkeys(_ATTACHMENT.findall(text)):
        print(f"\nattached report: {url.rsplit('/', 1)[-1]}")
        text += "\n" + _fetch(url)

    print(f"\n  version    {_first(_VERSION.search(text))}")
    print(f"  distro     {_first(_DISTRO.search(text))}")
    print(f"  installer  {_first(_INSTALLER.search(text))}")

    ids = {f"{v}:{p}" for v, p in _USB_ID.findall(text)
           if find_product(int(v, 16), int(p, 16)) is not None}
    print(f"  device(s)  {', '.join(sorted(ids)) or '(none recognised)'}")

    seen = set()
    for pm, sub, w, h in _HANDSHAKE.findall(text):
        if (pm, sub) in seen:
            continue
        seen.add((pm, sub))
        pm_i, sub_i = int(pm), int(sub)
        note = ""
        if "87ad:70db" in ids or "87cd:70db" in ids or "0402:3922" in ids:
            _, prof = bulk_profile(pm_i, sub_i)
            note = (f"{prof.width}x{prof.height} "
                    f"{'JPEG' if prof.jpeg else 'RGB565'}"
                    f"{' PORTRAIT-MOUNTED' if prof.portrait_mounted else ''}")
        elif w:
            note = f"{w}x{h}"
        flag = "   <-- PM=0: identified nothing" if pm_i == 0 else ""
        print(f"  handshake  PM={pm} SUB={sub}  {note}{flag}")
    if not seen:
        print("  handshake  (NONE — ask for `trcc report -o report.txt`, attached)")

    version = _first(_VERSION.search(text))
    if version != "?":
        print("\n  fixes since their version:")
        for commit, what in _KNOWN_FIXES.items():
            rel = _release_of(commit)
            if rel == "UNRELEASED":
                print(f"    · {what}  —  NOT RELEASED YET")
            elif tuple(map(int, rel.lstrip('v').split('.'))) > tuple(map(int, version.split('.'))):
                print(f"    · {rel}  {what}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        triage(int(arg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
