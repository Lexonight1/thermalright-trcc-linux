#!/usr/bin/env python3
"""Triage a GitHub issue: fetch it, read its report, resolve its device.

    PYTHONPATH=src python3.12 dev/tools/triage.py --open         # the brief
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
# The C# oracle lives beside the audits, not in the shipping tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "decompiler"))

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


_RAW = re.compile(r'"?raw"?[:=]\s*"?([0-9a-fA-F]{16,})"?')

#: Whose reply counts as "answered".  Was a literal inside ``triage`` until the
#: sweep needed the same answer -- two copies of who the maintainer is would
#: disagree the first time one changed.
_MAINTAINER = "Lexonight1"


def scan_self_description(blob: bytes, resolution: tuple[int, int]) -> list[str]:
    """Where a panel's own dimensions appear in the bytes it sent us.

    The device replies with up to a kilobyte and we have only ever read six
    bytes of it -- the PM and SUB that index our hand-maintained geometry
    tables.  Whether the panel states its own size in there has never been
    checked, because every adapter truncated the reply to 64 bytes before
    anyone could look.

    It matters more than a curiosity.  Today a cooler we have no row for is
    unsupported until a reporter measures it -- one of them resorted to
    photographing test gratings and running an FFT.  If the dimensions are in
    the reply, an unknown panel could describe itself and the catalog problem
    largely dissolves.

    Reports both endiannesses at every offset, and stays quiet when it finds
    nothing: a false hit here would send someone building on sand.
    """
    hits: list[str] = []
    for label, value in (("width", resolution[0]), ("height", resolution[1])):
        for endian in ("little", "big"):
            needle = value.to_bytes(2, endian)
            start = 0
            while (i := blob.find(needle, start)) >= 0:
                hits.append(f"{label}={value} as uint16-{endian} at offset {i}")
                start = i + 1
    return hits


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


def verdict(issue: dict) -> str:
    """Who owes the next move on this issue.

    ``NEVER ANSWERED`` is not "zero comments" -- it is "the maintainer has
    never commented".  An issue with three replies from other users is still
    unanswered, and counting comments would score it as handled.  The single
    issue view read only the LAST comment, so a thread with no comments at all
    printed no flag whatsoever: the most neglected shape was the one silently
    unmarked.
    """
    comments = issue.get("comments") or []
    if not any(c.get("author", {}).get("login") == _MAINTAINER
               for c in comments):
        return "NEVER ANSWERED"
    if comments[-1].get("author", {}).get("login") != _MAINTAINER:
        return "AWAITING US"
    return "awaiting reporter"


def code_evidence() -> dict[int, tuple[int, int]]:
    """Issue number -> (files naming it, of which tests).  ONE grep, not 81.

    The question the brief could never answer: *have we already fixed this?*
    ``_KNOWN_FIXES`` answers it from a hand-written list of six commits, so it
    is silent about every fix nobody remembered to add -- and silent entirely
    when the reporter's version does not parse, which is most of them.

    This asks the tree instead.  A test naming ``#N`` is a regression lock
    somebody wrote FOR that report, which is the strongest cheap evidence that
    it was addressed.  It is evidence, not proof: the code may name an issue it
    only partially fixed (``#291`` was fixed for one skin of four and stayed
    open), so the column is a prompt to go and look, never a verdict.
    """
    out: dict[int, tuple[int, int]] = {}
    raw = _sh("grep", "-rnoE", "#[0-9]{2,4}", "--include=*.py",
              "src", "tests", "dev")
    seen: dict[int, set[str]] = {}
    for line in raw.splitlines():
        path, _, rest = line.partition(":")
        num = rest.rpartition("#")[2]
        if num.isdigit():
            seen.setdefault(int(num), set()).add(path)
    for num, files in seen.items():
        out[num] = (len(files),
                    sum(1 for f in files if f.startswith("tests/")))
    return out


def _age_days(stamp: str) -> int:
    from datetime import datetime, timezone
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return -1
    return (datetime.now(timezone.utc) - then).days


def sweep() -> int:
    """Every open issue, grouped by who owes the next move.

    ONE ``gh`` call, not one per issue: the per-issue triage downloads each
    attached report, which is right for one issue and minutes of network for
    eighty.  This is the standing brief -- "N open, M never answered" -- which
    was hand-counted every session because nothing computed it.
    """
    raw = _sh("gh", "issue", "list", "--state", "open", "--limit", "300",
              "--json", "number,title,author,comments,createdAt,labels")
    if not raw:
        print("could not list issues (is `gh` authenticated?)")
        return 1
    issues = json.loads(raw)
    evidence = code_evidence()
    groups: dict[str, list[dict]] = {}
    for issue in issues:
        groups.setdefault(verdict(issue), []).append(issue)

    counts = "  ·  ".join(
        f"{len(groups.get(v, []))} {v.lower()}"
        for v in ("NEVER ANSWERED", "AWAITING US", "awaiting reporter"))
    print(f"\n{len(issues)} open  ·  {counts}")
    unreplied = groups.get("NEVER ANSWERED", []) + groups.get("AWAITING US", [])
    with_test = [i for i in unreplied
                 if evidence.get(i["number"], (0, 0))[1]]
    print(f"{len(with_test)} of those {len(unreplied)} owe a reply AND already "
          f"have a TEST naming them — look before you ask for a log")

    for name in ("NEVER ANSWERED", "AWAITING US"):
        rows = sorted(groups.get(name, []),
                      key=lambda i: _age_days(i.get("createdAt", "")),
                      reverse=True)
        if not rows:
            continue
        print(f"\n{'=' * 78}\n{name}  ({len(rows)})")
        for issue in rows:
            comments = issue.get("comments") or []
            who = (comments[-1].get("author", {}).get("login", "?")
                   if comments else issue.get("author", {}).get("login", "?"))
            labels = ",".join(sorted(
                lbl["name"] for lbl in issue.get("labels", [])))[:22]
            files, tests = evidence.get(issue["number"], (0, 0))
            code = f"{files}f/{tests}t" if files else "  -  "
            print(f"  #{issue['number']:<4} {_age_days(issue.get('createdAt', '')):>4}d  "
                  f"{issue.get('title', '')[:38]:<38}  "
                  f"{len(comments)}c  {code:<7} last:{who[:15]:<15} {labels}")

    print(f"\n{'=' * 78}\n"
          f"Nf/Mt = files / TESTS naming the issue.  A test means somebody "
          f"already\nwrote a lock for this report -- check whether it shipped "
          f"before asking for a log.\n"
          f"One issue in full:  PYTHONPATH=src python3.12 "
          f"dev/tools/triage.py <number>")
    return 0


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
    state = verdict(issue)
    if last:
        who = last[-1].get("author", {}).get("login", "?")
        print(f"last word: {who} @ {last[-1].get('createdAt', '')[:10]}"
              f"{'   <-- ' + state if state != 'awaiting reporter' else ''}")
    else:
        print(f"no comments at all   <-- {state}")

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
        # Walk the SAME bytes through the C#.  Ours came from the shipping
        # functions above; this is what the vendor's app decides for the
        # identical fingerprint.  A divergence is a prompt to read
        # ``control-flow.json`` -- the transcription has itself been wrong,
        # inventing a 1280x480 mount and putting 1920x462 one SUB low, both
        # recorded as divergences against our CORRECT code.
        try:
            from audit_devices import summarise
            for line in summarise(pm_i, sub_i):
                print(f"  {line}")
        except Exception as e:
            print(f"  C# oracle: unavailable ({e})")
    if not seen:
        print("  handshake  (NONE — ask for `trcc report -o report.txt`, attached)")

    # The experiment nobody has run: does the panel state its own size?
    for hexblob in dict.fromkeys(_RAW.findall(text)):
        try:
            blob = bytes.fromhex(hexblob)
        except ValueError:
            continue
        print(f"\n  self-description: {len(blob)} bytes from the device")
        if len(blob) <= 64:
            print("    (truncated at 64 — this report predates v9.9.9; "
                  "a newer one carries the whole reply)")
        for pm, sub, w, h in _HANDSHAKE.findall(text):
            if not w:
                continue
            found = scan_self_description(blob, (int(w), int(h)))
            for hit in found:
                print(f"    *** {hit}")
            if not found:
                print(f"    no {w}x{h} anywhere in the reply "
                      "(so geometry stays a catalog fact for this panel)")
            break

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
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if "--open" in args:
        return sweep()
    for arg in args:
        triage(int(arg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
