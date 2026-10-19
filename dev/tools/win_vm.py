#!/usr/bin/env python3
"""Drive the win11 VM from the host: screenshots, keystrokes, files, recipes.

The VM (libvirt session domain ``win11``, GNOME Boxes) passes the maintainer's
SCSI panel through, so Windows behaviour can be measured on real glass.  It has
no ssh and user-mode networking only; the guest reaches the host's loopback at
10.0.2.2.  This tool is the whole loop:

    python3 dev/tools/win_vm.py serve &          # loopback server, port 8765
    python3 dev/tools/win_vm.py pack             # working tree -> payload/src.zip
    python3 dev/tools/win_vm.py shot             # screenshot -> cache/shot.png
    python3 dev/tools/win_vm.py switch           # Alt+Tab between the two shells
    python3 dev/tools/win_vm.py type 'whoami'    # probe focus: no Enter
    python3 dev/tools/win_vm.py run report       # type the one-liner for a recipe
    python3 dev/tools/win_vm.py exec cmd.exe /c whoami   # guest agent (see below)
    python3 dev/tools/win_vm.py panel            # hand the panel back after a restart

Recipes are ``dev/tools/win_vm/<name>.ps1``; each says on its first line whether
it needs the ADMIN or the NON-ADMIN shell.  They upload their results to the
server, which saves them in ``~/.cache/trcc-win-vm/inbox``.

Lessons, each paid for on 2026-10-16/18 (memory ``reference_read_the_vm_disk``):

1.  **Prove focus before typing.**  ``switch``, then ``shot`` (the active title
    bar is dark), then ``type`` a harmless probe WITHOUT Enter.  Keystrokes land
    wherever focus is: a run once went to the admin shell, and typing while the
    user used the VM opened eight PowerShells.  Recipes print the integrity level
    (Medium = normal, High = admin) so a wrong shell shows in the output.
2.  **Type as little as possible.**  ``run`` types only
    ``iex (iwr -useb http://10.0.2.2:8765/<name>.ps1).Content``; the server sends
    ``.ps1`` as text/plain, or ``iex`` gets bytes.
3.  **In a recipe, ``| Out-String``, never ``| Out-Host``** -- Out-Host escapes a
    ``*> file`` redirect and the output is lost.
4.  **Never pipe ``virsh`` through ``tail -1``**: it hid that a detach-device had
    not happened.  Confirm a USB detach with ``journalctl -k`` on the host.
5.  **The guest agent runs as LocalSystem.**  ``exec`` is for admin work only; a
    non-admin test still needs the normal shell.  The agent that shipped in the VM
    (0.12.1) has no ``guest-exec``; ``win_vm/install_qga.ps1`` installs a current
    one (Red Hat's qemu-ga MSI -- put it in ``payload/`` first).
6.  **A VM restart drops the panel.**  The start resets it, it re-enumerates
    under a new device number, the host's usb-storage claims it, and the VM
    keeps a passthrough to the old number: Windows sees no panel.  ``panel``
    re-attaches it by vendor/product (2026-10-18).
7.  **Scratch tools die with ``/tmp``.**  This file exists because the session
    scratchpad copies were wiped by a host reboot.
"""
from __future__ import annotations

import argparse
import base64
import http.server
import json
import string
import subprocess
import sys
import time
import zipfile
from pathlib import Path

DOMAIN = "win11"
VIRSH = ["virsh", "-c", "qemu:///session"]
PORT = 8765
GUEST_URL = f"http://10.0.2.2:{PORT}"
REPO = Path(__file__).resolve().parents[2]
RECIPES = Path(__file__).resolve().parent / "win_vm"
CACHE = Path.home() / ".cache" / "trcc-win-vm"
PAYLOAD, INBOX = CACHE / "payload", CACHE / "inbox"

_PLAIN = {c: f"KEY_{c.upper()}" for c in string.ascii_lowercase}
_PLAIN |= {str(d): f"KEY_{d}" for d in range(10)}
_PLAIN |= {" ": "KEY_SPACE", "-": "KEY_MINUS", "=": "KEY_EQUAL",
           "[": "KEY_LEFTBRACE", "]": "KEY_RIGHTBRACE", "\\": "KEY_BACKSLASH",
           ";": "KEY_SEMICOLON", "'": "KEY_APOSTROPHE", "`": "KEY_GRAVE",
           ",": "KEY_COMMA", ".": "KEY_DOT", "/": "KEY_SLASH", "\t": "KEY_TAB",
           "\n": "KEY_ENTER"}
_SHIFTED = {c.upper(): f"KEY_{c.upper()}" for c in string.ascii_lowercase}
_SHIFTED |= dict(zip("!@#$%^&*()", [f"KEY_{d}" for d in "1234567890"],
                      strict=True))
_SHIFTED |= {"_": "KEY_MINUS", "+": "KEY_EQUAL", "{": "KEY_LEFTBRACE",
             "}": "KEY_RIGHTBRACE", "|": "KEY_BACKSLASH", ":": "KEY_SEMICOLON",
             '"': "KEY_APOSTROPHE", "~": "KEY_GRAVE", "<": "KEY_COMMA",
             ">": "KEY_DOT", "?": "KEY_SLASH"}


def send_keys(*keys: str) -> None:
    """One chord: every key down, then up (``KEY_LEFTALT KEY_TAB``)."""
    subprocess.run([*VIRSH, "send-key", DOMAIN, "--codeset", "linux", *keys],
                   check=True, stdout=subprocess.DEVNULL)


def type_text(text: str) -> None:
    """Type *text* one key at a time (US layout)."""
    for ch in text:
        if ch in _PLAIN:
            send_keys(_PLAIN[ch])
        elif ch in _SHIFTED:
            send_keys("KEY_LEFTSHIFT", _SHIFTED[ch])
        else:
            raise SystemExit(f"no key for {ch!r}")


def shot(out: Path) -> Path:
    """Screenshot the VM to *out* (.png).  The console renders queued keys
    slowly: wait a few seconds after typing or the line looks truncated."""
    ppm = out.with_suffix(".ppm")
    subprocess.run([*VIRSH, "screenshot", DOMAIN, str(ppm)], check=True,
                   stdout=subprocess.DEVNULL)
    try:
        from PIL import Image
        Image.open(ppm).save(out)
    except ImportError:                     # python3.12 here has PySide6, not PIL
        from PySide6.QtGui import QImage
        QImage(str(ppm)).save(str(out))
    ppm.unlink()
    return out


class _Handler(http.server.SimpleHTTPRequestHandler):
    """GET: a recipe, else a payload file.  PUT: save into the inbox."""

    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".ps1": "text/plain", ".py": "text/plain"}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(PAYLOAD), **kwargs)

    def translate_path(self, path: str) -> str:
        name = Path(path.split("?", 1)[0]).name
        recipe = RECIPES / name
        return str(recipe if recipe.is_file() else PAYLOAD / name)

    def do_PUT(self) -> None:
        name = Path(self.path).name
        size = int(self.headers.get("Content-Length", 0))
        (INBOX / name).write_bytes(self.rfile.read(size))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"UPLOADED\n")


def serve() -> None:
    """Loopback only: the guest reaches it as 10.0.2.2, nothing else can."""
    PAYLOAD.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    print(f"serving recipes {RECIPES} + payload {PAYLOAD}; inbox {INBOX}")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), _Handler).serve_forever()


def pack() -> Path:
    """The working tree (tracked + untracked, not ignored) as payload/src.zip,
    without the theme archives -- 783 MB with them, ~27 MB without.  The local
    Windows build script rides along, for ``build_installer``."""
    PAYLOAD.mkdir(parents=True, exist_ok=True)
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "src", "installer", "pyproject.toml", "README.md",
         "dev/tools/build_windows.ps1"],
        cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    out = PAYLOAD / "src.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files:
            path = REPO / name
            if path.is_file() and path.suffix != ".7z":
                z.write(path, name)
    return out


_PANEL = """<hostdev mode='subsystem' type='usb' managed='yes'>
  <source><vendor id='0x0402'/><product id='0x3922'/></source>
  <address type='usb' bus='0' port='7'/>
</hostdev>"""


def _panel_line() -> str:
    """The panel's row in QEMU's ``info usb``, or ``""``."""
    out = subprocess.run([*VIRSH, "qemu-monitor-command", DOMAIN, "--hmp", "info usb"],
                         capture_output=True, text=True, check=True).stdout
    return next((line.strip() for line in out.splitlines() if "hostdev0" in line), "")


def _panel_connected() -> bool:
    """QEMU shows address 0 until it has opened the host device."""
    line = _panel_line()
    return bool(line) and not line.startswith("Device 0.0,")


def _wait(done, what: str) -> None:
    """Poll *done* for up to 15 s -- both virsh calls return before QEMU acts."""
    for _ in range(30):
        if done():
            return
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {what}: {_panel_line() or 'no panel row'}")


def reattach_panel() -> None:
    """Drop the VM's passthrough of the panel and attach it again by
    vendor/product, so it follows the panel's current device number."""
    subprocess.run([*VIRSH, "detach-device-alias", DOMAIN, "hostdev0", "--live"],
                   check=False)
    _wait(lambda: not _panel_line(), "the old passthrough to go")
    xml = CACHE / "panel.xml"
    xml.write_text(_PANEL)
    subprocess.run([*VIRSH, "attach-device", DOMAIN, str(xml), "--live"], check=True)
    _wait(_panel_connected, "the panel to connect")
    print(_panel_line())


def agent(command: dict) -> dict:
    """One guest-agent command; its ``return`` value."""
    out = subprocess.run([*VIRSH, "qemu-agent-command", DOMAIN,
                          json.dumps(command)], capture_output=True, text=True,
                         timeout=60)
    if out.returncode:
        raise SystemExit(out.stderr.strip() or f"agent failed: {command}")
    return json.loads(out.stdout)["return"]


def guest_exec(argv: list[str]) -> int:
    """Run *argv* in the guest as LocalSystem, print its output, return its code."""
    pid = agent({"execute": "guest-exec", "arguments": {
        "path": argv[0], "arg": argv[1:], "capture-output": True}})["pid"]
    status: dict = {}
    for _ in range(1200):
        status = agent({"execute": "guest-exec-status", "arguments": {"pid": pid}})
        if status.get("exited"):
            break
        time.sleep(0.5)
    for key in ("out-data", "err-data"):
        if key in status:
            sys.stdout.write(base64.b64decode(status[key]).decode("utf-8", "replace"))
    return int(status.get("exitcode", 1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shot")
    s.add_argument("out", nargs="?", type=Path, default=CACHE / "shot.png")
    t = sub.add_parser("type")
    t.add_argument("text")
    t.add_argument("--enter", action="store_true")
    sub.add_parser("switch")
    r = sub.add_parser("run")
    r.add_argument("recipe")
    sub.add_parser("serve")
    sub.add_parser("pack")
    sub.add_parser("panel")
    e = sub.add_parser("exec")
    e.add_argument("argv", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    match a.cmd:
        case "shot":
            print(shot(a.out))
        case "type":
            type_text(a.text)
            if a.enter:
                send_keys("KEY_ENTER")
        case "switch":
            send_keys("KEY_LEFTALT", "KEY_TAB")
        case "run":
            if not (RECIPES / f"{a.recipe}.ps1").is_file():
                raise SystemExit(f"no recipe {a.recipe}: {sorted(p.stem for p in RECIPES.glob('*.ps1'))}")
            send_keys("KEY_ESC")
            type_text(f"cls; iex (iwr -useb {GUEST_URL}/{a.recipe}.ps1).Content")
            send_keys("KEY_ENTER")
        case "serve":
            serve()
        case "pack":
            print(pack())
        case "panel":
            reattach_panel()
        case "exec":
            return guest_exec(a.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
