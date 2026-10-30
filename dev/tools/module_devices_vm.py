#!/usr/bin/env python3
"""Drive ``trcc-module-devices`` in a throwaway Fedora VM: root, real udev,
none of your hardware.

The helper's grant and take-back are udev behaviour, and udev's behaviour
is what broke: tags are STICKY.  Delete the rule that tagged a node
``uaccess`` and the tag stays in the node's record, so
``73-seat-late.rules`` grants the seat's user again on the node's next
``change`` event -- from anything that triggers udev.  On a real desk a
take-back left the LCD's hub granted; in this VM a hub re-granted on 5 of 5
change events.  ``udevadm test`` cannot show it (it simulates from the
record), and no test can (it is udevd's), so this drives the real thing:

* QEMU (KVM, as you), two emulated hubs ``0409:55aa`` -- one with a USB
  keyboard ``0627:0001`` behind it, as the LCD sits behind its hub -- and a
  console auto-login, because uaccess grants only the seat's active user;
* the helper from this tree, copied in byte for byte;
* each round: ``enable``, ``disable``, then a ``change`` on every usb and
  hidraw device, three times -- and every node's group, mode, named-user
  ACL and sticky ``uaccess`` tag after each step.

Exit 1 if any round leaves a node granted after the take-back.

    python3.12 dev/tools/module_devices_vm.py up       # download once, verify, boot
    python3.12 dev/tools/module_devices_vm.py drive    # 3 rounds
    python3.12 dev/tools/module_devices_vm.py drive --helper old  # must FAIL
    python3.12 dev/tools/module_devices_vm.py down     # stop, drop its disk

The image (Fedora Cloud Base 44, ~560 MB) is kept in ``~/.cache/trcc-udev-vm``
and checked against Fedora's SHA256 list; the VM writes only to an overlay.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

RELEASE = "https://download.fedoraproject.org/pub/fedora/linux/releases/44/Cloud/x86_64/images"
IMAGE = "Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2"
CHECKSUM = "Fedora-Cloud-44-1.7-x86_64-CHECKSUM"
HOME = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "trcc-udev-vm"
HELPER = Path(__file__).resolve().parents[2] / "src/trcc/assets/trcc-module-devices"
PORT = 2299
#: What the VM's helper is asked to grant: both hubs, and the keyboard.
DEVICES = ("0409:55aa", "0627:0001")

USER_DATA = """\
#cloud-config
disable_root: false
users:
  - name: root
    ssh_authorized_keys: ["{key}"]
  - name: tester
    shell: /bin/bash
    lock_passwd: true
groups: [trcc]
packages: [acl]
write_files:
  - path: /etc/systemd/system/getty@tty1.service.d/autologin.conf
    content: |
      [Service]
      ExecStart=
      ExecStart=-/sbin/agetty --autologin tester --noclear %I $TERM
runcmd:
  - [systemctl, daemon-reload]
  - [systemctl, restart, getty@tty1.service]
"""

#: Runs inside the VM.  One line per state: ``<label>|node group mode acl tag|...``
ROUNDS = r"""
set -u
ids="$1"; rounds=$2
nodes=""
for d in /sys/bus/usb/devices/*; do
  [ -f "$d/idVendor" ] || continue
  case " $ids " in *" $(cat $d/idVendor):$(cat $d/idProduct) "*)
    nodes+=" $(printf /dev/bus/usb/%03d/%03d $(cat $d/busnum) $(cat $d/devnum))";; esac
done
for h in /sys/class/hidraw/hidraw*; do
  hid=$(basename "$(readlink -f $h/device)"); hid=${hid%.*}
  for id in $ids; do [ "$hid" = "0003:${id^^}" ] && nodes+=" /dev/$(basename $h)"; done
done
state() {
  line="$1"
  for n in $nodes; do
    t=$(stat -c %t:%T $n); rec=/run/udev/data/c$((16#${t%:*})):$((16#${t#*:}))
    tag=$(grep -c '^G:uaccess' $rec 2>/dev/null)
    line+="|$n $(stat -c '%G %a' $n) $(getfacl -cpn $n | grep -c '^user:[0-9]') ${tag:-0}"
  done
  echo "$line"
}
state start
for r in $(seq 1 $rounds); do
  python3 -I /root/trcc-module-devices enable vmdrive $ids; state "grant $r rc=$?"
  python3 -I /root/trcc-module-devices disable vmdrive; state "revoke $r rc=$?"
  for i in 1 2 3; do
    udevadm trigger --subsystem-match=usb --action=change
    udevadm trigger --subsystem-match=hidraw --action=change
    udevadm settle
  done
  state "3x change $r"
done
"""


def _ssh_base(port: int) -> list[str]:
    log.debug("_ssh_base: port %d", port)
    return ["-q", "-i", str(HOME / "key"), "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=3"]


def ssh(port: int, *command: str, stdin: str | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run *command* as root in the VM."""
    log.debug("ssh: %s", command)
    return subprocess.run(["ssh", *_ssh_base(port), "-p", str(port), "root@127.0.0.1",
                           *command], input=stdin, capture_output=True, text=True,
                          check=check)


def _download(name: str) -> Path:
    path = HOME / name
    if not path.exists():
        log.info("download: %s", name)
        tmp = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(f"{RELEASE}/{name}", tmp)
        tmp.replace(path)
    return path


def fetch_image() -> Path:
    """The cloud image, downloaded once and checked against Fedora's list."""
    log.info("fetch_image: %s", HOME)
    HOME.mkdir(parents=True, exist_ok=True)
    sums = _download(CHECKSUM).read_text(encoding="utf-8")
    want = re.search(rf"^SHA256 \({re.escape(IMAGE)}\) = ([0-9a-f]{{64}})$", sums, re.M)
    image = _download(IMAGE)
    got = hashlib.sha256(image.read_bytes()).hexdigest()
    if want is None or got != want.group(1):
        image.unlink()
        raise SystemExit(f"{IMAGE}: checksum mismatch -- deleted, run up again")
    log.info("fetch_image: %s verified", IMAGE)
    return image


def up(port: int) -> None:
    """Fresh overlay and seed, boot, wait for SSH."""
    log.info("up: port %d", port)
    if (HOME / "vm.pid").exists():
        raise SystemExit("a VM is already up -- run down first")
    image = fetch_image()
    key = HOME / "key"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       check=True)
    seed = HOME / "seed"
    seed.mkdir(exist_ok=True)
    pub = (HOME / "key.pub").read_text(encoding="utf-8").strip()
    (seed / "user-data").write_text(USER_DATA.format(key=pub), encoding="utf-8")
    (seed / "meta-data").write_text(f"instance-id: trcc-{time.time_ns()}\n"
                                    "local-hostname: udevvm\n", encoding="utf-8")
    subprocess.run(["genisoimage", "-quiet", "-output", str(HOME / "seed.iso"),
                    "-volid", "cidata", "-joliet", "-rock",
                    str(seed / "user-data"), str(seed / "meta-data")], check=True)
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", str(image),
                    "-F", "qcow2", str(HOME / "overlay.qcow2"), "10G"], check=True)
    subprocess.run([
        "qemu-system-x86_64", "-name", "trcc-udevvm", "-enable-kvm", "-cpu", "host",
        "-m", "2048", "-smp", "2",
        "-drive", f"file={HOME / 'overlay.qcow2'},if=virtio",
        "-drive", f"file={HOME / 'seed.iso'},media=cdrom",
        "-nic", f"user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22",
        "-device", "qemu-xhci,id=xhci",
        "-device", "usb-hub,bus=xhci.0,port=1", "-device", "usb-kbd,bus=xhci.0,port=1.1",
        "-device", "usb-hub,bus=xhci.0,port=2",
        "-display", "none", "-serial", f"file:{HOME / 'serial.log'}",
        "-daemonize", "-pidfile", str(HOME / "vm.pid")], check=True)
    for _ in range(60):
        # uaccess grants only the seat's ACTIVE user, and cloud-init restarts
        # the console login as its very last step: wait for that session.
        ready = ssh(port, "cloud-init status --wait >/dev/null 2>&1; "
                    "loginctl show-seat seat0 -p ActiveSession --value", check=False)
        if ready.returncode == 0 and (seat := ready.stdout.strip()):
            log.info("up: cloud-init done, seat0 active session %s", seat)
            print(f"up -- ssh -i {key} -p {port} root@127.0.0.1")
            return
        time.sleep(3)
    raise SystemExit("the VM did not answer on ssh in 3 minutes -- see serial.log")


def parse(lines: str) -> list[tuple[str, list[tuple[str, str, str, int, int]]]]:
    """``ROUNDS``' output: each state's label and its nodes (node, group,
    mode, named-user ACL entries, sticky uaccess tag)."""
    log.debug("parse: %d lines", len(lines.splitlines()))
    states = []
    for line in lines.splitlines():
        label, *nodes = line.split("|")
        rows = []
        for node in nodes:
            path, group, mode, acl, tag = node.split()
            rows.append((path, group, mode, int(acl), int(tag)))
        states.append((label, rows))
    return states


#: The nodes ``DEVICES`` give in the VM: two hubs, the keyboard, its hidraw.
NODES = 4


def incomplete(states: list[tuple[str, list[tuple[str, str, str, int, int]]]],
               rounds: int) -> str:
    """Why *states* is not a whole drive, or "" when it is."""
    log.debug("incomplete: %d states, %d rounds", len(states), rounds)
    if len(states) != 1 + 3 * rounds:
        return f"{len(states)} states, expected {1 + 3 * rounds}"
    if short := [label for label, rows in states if len(rows) != NODES]:
        return f"{short[0]!r} has not {NODES} nodes"
    # A take-back of a grant that never happened proves nothing.
    if ungranted := [f"{label}: {row[0]}" for label, rows in states
                     if label.startswith("grant") for row in rows
                     if not (row[3] and row[4] and row[1] == "trcc")]:
        return f"not granted first ({ungranted[0]}) -- is a user on the VM's seat?"
    return ""


def leaks(states: list[tuple[str, list[tuple[str, str, str, int, int]]]]) -> list[str]:
    """Every node still granted after a take-back: an ACL entry, our group,
    or the sticky tag that brings the ACL back."""
    found = [f"{label}: {path} group={group} acl={acl} tag={tag}"
             for label, rows in states if not label.startswith(("start", "grant"))
             for path, group, _mode, acl, tag in rows
             if acl or tag or group == "trcc"]
    log.info("leaks: %d", len(found))
    return found


def drive(port: int, rounds: int, helper: Path = HELPER) -> int:
    """Copy *helper* in -- this tree's by default -- run *rounds*, print
    every state."""
    log.info("drive: %d rounds of %s", rounds, helper)
    subprocess.run(["scp", *_ssh_base(port), "-P", str(port), str(helper),
                    "root@127.0.0.1:/root/trcc-module-devices"], check=True)
    ours = hashlib.sha256(helper.read_bytes()).hexdigest()
    theirs = ssh(port, "sha256sum", "/root/trcc-module-devices").stdout.split()[0]
    if theirs != ours:
        raise SystemExit(f"the helper in the VM is not {helper}")
    print(f"helper {ours[:16]} (systemd {ssh(port, 'systemctl', '--version').stdout.split()[1]})")
    # ssh joins its arguments into ONE remote command line: quote each.
    remote = shlex.join(["bash", "-s", "--", " ".join(DEVICES), str(rounds)])
    states = parse(ssh(port, remote, stdin=ROUNDS).stdout)
    if (missing := incomplete(states, rounds)):
        print(f"DID NOT RUN -- {missing}; a drive that did not run is not clean")
        return 2
    for label, rows in states:
        print(f"{label:<14} " + "  ".join(
            f"{path.removeprefix('/dev/')}[{group} {mode} acl={acl} tag={tag}]"
            for path, group, mode, acl, tag in rows))
    if found := leaks(states):
        print("\nLEAK -- granted after the take-back:\n  " + "\n  ".join(found))
        return 1
    print(f"\nclean: {rounds} round(s), nothing granted after any take-back")
    return 0


def down() -> None:
    """Stop the VM and drop its overlay; the verified image stays."""
    log.info("down")
    pid_file = HOME / "vm.pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
        except ProcessLookupError:
            log.info("down: the VM had already stopped")
        pid_file.unlink()
    for name in ("overlay.qcow2", "seed.iso", "serial.log"):
        (HOME / name).unlink(missing_ok=True)
    print("down")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("action", choices=("up", "drive", "down"))
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--helper", type=Path, default=HELPER,
                        help="drive another copy -- an old one, to see it fail")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    log.info("main: %s", args.action)
    match args.action:
        case "up":
            up(args.port)
        case "drive":
            return drive(args.port, args.rounds, args.helper)
        case "down":
            down()
    return 0


if __name__ == "__main__":
    sys.exit(main())
