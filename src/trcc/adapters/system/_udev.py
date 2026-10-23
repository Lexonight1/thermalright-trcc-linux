"""Generate udev rules + usb-storage quirks for TRCC devices on Linux.

Pure string generation + atomic file I/O.  Called from
`LinuxOS.setup()` so first-run users get non-root access to
devices registered in `core.registry.ALL_DEVICES`.

Who reaches the coolers: the user at the seat (``uaccess``) and members of
the ``trcc`` group -- the App running as a user service after a logout
(#311), a headless box.  Nobody else: it was every user (``MODE="0666"``)
until 2026-10-22, so any local account could read a hidraw node or send raw
SCSI to whatever device matched a cooler's vid:pid.

Per-wire subsystem mapping:

    Wire   | subsystems granted
    -------+-------------------------------
    SCSI   | scsi_generic, usb
    HID    | hidraw, usb
    LED    | hidraw, usb          (LED controllers are HID-based)
    BULK   | usb
    LY     | usb

Every device also gets `power/control="auto"` +
`power/autosuspend_delay_ms="10000"` so the kernel autosuspends the device
~10s after our frame stream stops; the firmware sleeps the panel in response
to the USB suspend (the mechanism the Windows app relies on — #143).  The
EXCEPTION is `_NO_AUTOSUSPEND` devices, whose firmware re-enumerates instead of
sleeping under suspend (a reconnect loop — #201); they pin `power/control="on"`.
The packages install the same files: ``packaging/`` is GENERATED from
:func:`host_files` by ``dev/gen_packaging_setup.py`` and gated equal by
``tests/test_udev.py``.  It used to be hand-kept "in step", and drifted: after
0416:5406 became HID the packaged rules still granted it scsi_generic only.

For SCSI devices we additionally write /etc/modprobe.d/trcc-lcd.conf
forcing `usb-storage` Bulk-Only (not UAS) so /dev/sgN is reliably
created.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ...core.models import MAINBOARD_DETECTOR, Wire
from ...core.registry import ALL_DEVICES
from ...core.rgb_catalog import MOTHERBOARD_CONTROLLERS
from ._elevate import reexec_as_root

log = logging.getLogger(__name__)


# Public: the health check reads these rather than restating them.  It
# restated the filename once, got it wrong ("99-trcc.rules"), and every
# correctly installed Linux box was told its udev rules were missing (#258).
# Numbered below 73 for its ``uaccess`` tag, as the RGB rules below are.
RULES_PATH = Path("/etc/udev/rules.d/70-trcc-lcd.rules")
# Its name until 2026-10-22, when it granted every user.  Setup removes TRCC's
# own copy: read after the new one, its MODE="0666" would win over it.  A NAME,
# placed beside RULES_PATH at call time, so a test that moves one moves both --
# a second full path here once let a test reach the real /etc.
LEGACY_RULES_NAME = "99-trcc-lcd.rules"
# The group that reaches the coolers without a seat.
DEVICE_GROUP = "trcc"
# The motherboard RGB TRCC drives, for the user at the seat.  Its own file,
# numbered below 73: ``uaccess`` is a tag that 73-seat-late.rules turns into
# an ACL, so a tag set in a 99- file is never applied.
RGB_RULES_PATH = Path("/etc/udev/rules.d/70-trcc-rgb.rules")
# Every directory udev reads rules from (udev(7), "RULES FILES"), highest
# priority first, plus /lib for distros without a merged /usr -- where the
# deb installs.  `trcc system setup` writes the first; the packages under
# packaging/ install the same file into one of the others.
RULES_DIRS = (
    RULES_PATH.parent,
    Path("/run/udev/rules.d"),
    Path("/usr/local/lib/udev/rules.d"),
    Path("/usr/lib/udev/rules.d"),
    Path("/lib/udev/rules.d"),
)
_MODPROBE_PATH = Path("/etc/modprobe.d/trcc-lcd.conf")
_MODULES_LOAD_PATH = Path("/etc/modules-load.d/trcc-sg.conf")
_MODULES_LOAD_RAPL_PATH = Path("/etc/modules-load.d/trcc-rapl.conf")
# SteamOS (3.6+) resets /etc on every OS update, except the paths a keep list
# names; a drop-in here is itself on SteamOS's own list
# (/usr/lib/rauc/atomic-update-keep.conf).  No other distro has the folder.
KEEP_LIST_DIR = Path("/etc/atomic-update.conf.d")

# `intel_rapl_msr` exposes the powercap RAPL energy counter on BOTH Intel and
# AMD (Zen) — the vendor-agnostic `/sys/class/powercap/intel-rapl:*` node trcc
# reads CPU package watts from (#194).
_RAPL_MODULE = "intel_rapl_msr"

# CPU package power read-grant.  `energy_uj` is root-only since CVE-2020-8694
# (PLATYPUS), so without this trcc can't report CPU watts on most distros.
# Scoped to ONLY the energy counter; reverted by deleting the rules file.
_POWERCAP_RULES = (
    "# CPU package power (RAPL) — make energy_uj readable without root so\n"
    "# trcc can report CPU watts (root-only since CVE-2020-8694).  Scoped to\n"
    "# the energy counter only; delete this file to revert.\n"
    'SUBSYSTEM=="powercap", KERNEL=="intel-rapl:*", '
    'RUN+="/bin/chmod 0444 /sys$devpath/energy_uj"\n'
)


# Wire → which kernel subsystems grant access for that protocol.
_WIRE_SUBSYSTEMS: dict[Wire, tuple[str, ...]] = {
    Wire.SCSI: ("scsi_generic", "usb"),
    Wire.HID:  ("hidraw", "usb"),
    Wire.LED:  ("hidraw", "usb"),
    Wire.BULK: ("usb",),
    Wire.LY:   ("usb",),
}


# Devices whose firmware can't survive USB autosuspend.  The kernel suspends the
# port ~10s after our frame stream stops; instead of sleeping the panel, these
# re-enumerate, the kernel re-suspends ~10s later, and you get a reconnect loop
# every ~10-15s (#201, em73es' ChiZhu GrandVision 360).  They pin
# power/control="on" (never autosuspend) instead of the "auto" the other panels
# use for idle-sleep (#143).  87ad:70db (USB/bulk) is reporter-confirmed;
# 87cd:70db (its SCSI sibling, same product family) is included by inference.
_NO_AUTOSUSPEND: frozenset[tuple[int, int]] = frozenset({
    (0x87ad, 0x70db),
    (0x87cd, 0x70db),
})


# ── Rule generation ──────────────────────────────────────────────────


def build_udev_rules() -> str:
    """Return the full udev rules file contents (no side effects)."""
    log.info("build_udev_rules: called")
    lines = [
        "# Thermalright TRCC devices — generated by `trcc system setup` (and packaged)",
        "# The user at this seat and the trcc group reach them; removing this file",
        "# reverts them to root-only",
        "",
    ]
    for (vid, pid), product in sorted(ALL_DEVICES.items()):
        subsystems = _WIRE_SUBSYSTEMS.get(product.wire)
        if subsystems is None:
            continue
        lines.append(f"# {product.vendor} {product.product}")
        for subsystem in subsystems:
            attr = "ATTRS" if subsystem in ("hidraw", "scsi_generic") else "ATTR"
            lines.append(
                f'SUBSYSTEM=="{subsystem}", '
                f'{attr}{{idVendor}}=="{vid:04x}", '
                f'{attr}{{idProduct}}=="{pid:04x}", '
                f'MODE="0660", GROUP="{DEVICE_GROUP}", TAG+="uaccess"'
            )
        control = "on" if (vid, pid) in _NO_AUTOSUSPEND else "auto"
        lines.append(
            f'ACTION=="add", SUBSYSTEM=="usb", '
            f'ATTR{{idVendor}}=="{vid:04x}", '
            f'ATTR{{idProduct}}=="{pid:04x}", '
            f'ATTR{{power/control}}="{control}", '
            f'ATTR{{power/autosuspend_delay_ms}}="10000"'
        )
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    return f"{body}\n{_POWERCAP_RULES}"


def build_rgb_udev_rules() -> str:
    """The motherboard RGB controllers TRCC drives itself (no side effects).

    Their HID node and their USB node: the user at the seat (``uaccess``, as
    OpenRGB's own 60-openrgb.rules grants them) and the ``trcc`` group, as the
    coolers -- so the App's user service keeps the board's lights after a
    logout.  OpenRGB's rule is not shipped by Debian's package or Ubuntu's LTS
    releases, so TRCC carries its own.  No power line: the controller is the
    board's, and how it sleeps is not TRCC's.
    """
    ids = sorted({(c.vid, c.pid) for c in MOTHERBOARD_CONTROLLERS
                  if c.detector == MAINBOARD_DETECTOR})
    log.info("build_rgb_udev_rules: %s", [f"{v:04x}:{p:04x}" for v, p in ids])
    lines = [
        "# Thermalright TRCC motherboard RGB — generated by `trcc system setup` (and packaged)",
        "# The user at this seat and the trcc group reach them; removing this file",
        "# takes these controllers back to root-only",
        "",
        "# ASUS Aura motherboard RGB",
    ]
    for vid, pid in ids:
        lines += [f'SUBSYSTEM=="{subsystem}", {attr}{{idVendor}}=="{vid:04x}", '
                  f'{attr}{{idProduct}}=="{pid:04x}", '
                  f'MODE="0660", GROUP="{DEVICE_GROUP}", TAG+="uaccess"'
                  for subsystem, attr in (("hidraw", "ATTRS"), ("usb", "ATTR"))]
    return "\n".join(lines) + "\n"


def rule_files() -> dict[Path, Callable[[], str]]:
    """Every udev rule file TRCC writes, and what it holds -- the ONE list
    that setup, its dry run, the packages and every "are the rules current?"
    read.  Built at call time, never at import: a table captured at import
    kept the real /etc paths after a test pointed ``RULES_PATH`` at a temp
    dir, and setup then reached for the real files."""
    files = {RULES_PATH: build_udev_rules, RGB_RULES_PATH: build_rgb_udev_rules}
    log.debug("rule_files: %s", [str(path) for path in files])
    return files


def build_modprobe_conf() -> str:
    """usb-storage quirks — force BOT on every SCSI device so /dev/sgN exists."""
    log.info("build_modprobe_conf: called")
    scsi_entries = [
        f"{vid:04x}:{pid:04x}:u"
        for (vid, pid), product in sorted(ALL_DEVICES.items())
        if product.wire is Wire.SCSI
    ]
    if not scsi_entries:
        return ""
    return (
        "# Thermalright TRCC SCSI LCDs — bypass UAS so /dev/sgN is created.\n"
        "# Generated by `trcc system setup` (and packaged).\n"
        f"options usb-storage quirks={','.join(scsi_entries)}\n"
    )


def _written_by_trcc(text: str | None) -> bool:
    """Whether a rule file is one TRCC wrote -- before setup removes one.

    Every version's first line starts ``# Thermalright`` and says "generated
    by" trcc: "auto-generated by trcc setup-udev" (legacy), "auto-generated by
    trcc.next setup", "generated by `trcc system setup`".  An admin's own
    file under our name is never touched.
    """
    first = (text or "").partition("\n")[0]
    ours = first.startswith("# Thermalright") and "generated by" in first and "trcc" in first
    log.debug("_written_by_trcc: %r -> %s", first[:60], ours)
    return ours


@dataclass(frozen=True, slots=True)
class RulesState:
    """The rule file udev reads, whether it is this version's, what it hides."""
    path: Path | None                 # None: no copy anywhere
    current: bool | None              # None: present but unreadable
    shadowed: tuple[Path, ...] = ()   # lower-priority copies udev skips


def rules_state(rules: Path | None = None) -> RulesState:
    """Which copy of the rule file *rules* (default: the devices' rules) is
    in force -- the ONE answer for every check.

    udev reads the highest-priority directory's file and skips the same name
    elsewhere ("Skipping overridden file", #201).  Checking ``/etc`` alone
    told every deb and Arch user their packaged rules were missing; checking
    existence alone hid a stale ``/etc`` copy shadowing a newer packaged one.
    """
    rules = rules or RULES_PATH       # at call time, as rule_files is
    present = [d / rules.name for d in RULES_DIRS
               if (d / rules.name).is_file()]
    if not present:
        log.info("rules_state: no %s in any rules directory", rules.name)
        return RulesState(path=None, current=None)
    path = present[0]
    try:
        current: bool | None = (path.read_text(encoding="utf-8")
                                == rule_files()[rules]())
    except OSError as e:
        log.debug("rules_state: %s unreadable (%s)", path, e)
        current = None
    state = RulesState(path=path, current=current, shadowed=tuple(present[1:]))
    log.info("rules_state: %s", state)
    return state


def host_files() -> dict[Path, str]:
    """Every host file setup writes, and its contents -- the ONE list.

    ``install`` writes it, ``--dry-run`` prints it, and the distro packages
    ship it (``dev/gen_packaging_setup.py``), so the three cannot disagree.
    """
    files = {path: build() for path, build in rule_files().items()}
    if modprobe := build_modprobe_conf():
        files[_MODPROBE_PATH] = modprobe
    files[_MODULES_LOAD_PATH] = "sg\n"
    files[_MODULES_LOAD_RAPL_PATH] = f"{_RAPL_MODULE}\n"
    log.debug("host_files: %s", [str(path) for path in files])
    return files


def sysusers_conf() -> str:
    """The distro packages' ``sysusers.d`` entry for the device group --
    created on install, before udev resolves the rules' GROUP.  Setup makes
    the group itself (``_join_device_group``)."""
    log.debug("sysusers_conf: %s", DEVICE_GROUP)
    return (f"# Thermalright TRCC -- members reach the coolers without a seat\n"
            f"g {DEVICE_GROUP} -\n")


def keep_list(paths: Iterable[Path]) -> dict[Path, str]:
    """On SteamOS, the drop-in that keeps *paths* through an OS update; else
    nothing.

    Without it the first update after setup threw the rules away, and the
    panel stopped answering until setup ran again.  One path per line is the
    format SteamOS reads.  Read at call time, as ``rule_files`` is, so a test
    can point ``KEEP_LIST_DIR`` elsewhere.
    """
    if not KEEP_LIST_DIR.is_dir():
        log.debug("keep_list: no %s -- not SteamOS", KEEP_LIST_DIR)
        return {}
    listed = [str(path) for path in paths]
    log.info("keep_list: SteamOS -- keeping %s through updates", listed)
    return {KEEP_LIST_DIR / "trcc-linux.conf": (
        "# Thermalright TRCC -- generated by `trcc system setup`: keep its files\n"
        "# through SteamOS updates, which reset /etc to the new image.\n"
        + "".join(f"{path}\n" for path in listed))}


# ── Installation ─────────────────────────────────────────────────────


def install(dry_run: bool = False) -> int:
    """Write the host config files and reload udev.

    Returns 0 on success, non-zero on failure.  Requires root; when
    called as non-root, re-execs via sudo.  `dry_run=True` prints what
    would be written and returns 0.
    """
    log.info("install: dry_run=%s", dry_run)
    files = host_files()
    files |= keep_list(files)

    if dry_run:
        for path, content in files.items():
            print(f"--- would write {path} ---")
            print(content)
        print(f"--- would modprobe {_RAPL_MODULE} (CPU power, now) ---")
        print(f"--- would create group {DEVICE_GROUP} and add "
              f"{_invoking_user() or 'no one (no sudo user)'} to it ---")
        legacy = RULES_PATH.with_name(LEGACY_RULES_NAME)
        if _written_by_trcc(_reads(legacy)):
            print(f"--- would remove {legacy} (every user's grant) ---")
        return 0

    if os.geteuid() != 0:
        return reexec_as_root(
            "from trcc.adapters.system._udev import install; sys.exit(install())"
        )

    _defer_to_packaged_rules(files)
    _retire_legacy_rules()
    _join_device_group(_invoking_user())

    try:
        for path, content in files.items():
            _write_atomic(path, content)
            log.info("Wrote %s", path)
    except OSError:
        log.exception("Could not write udev/modprobe config")
        return 1

    # Load the RAPL module now so CPU power works without a reboot — the
    # modules-load.d entry above only takes effect on next boot.  Best-effort:
    # a kernel without the module (or a VM) just leaves CPU power unavailable.
    try:
        subprocess.run(["modprobe", _RAPL_MODULE], check=False)
    except (OSError, subprocess.SubprocessError):
        log.warning("modprobe %s failed — CPU power may need a reboot",
                    _RAPL_MODULE)

    return _reload_udev()


def _defer_to_packaged_rules(files: dict[Path, str]) -> None:
    """Leave a current packaged rule in force instead of shadowing it (#201).

    An /etc copy of the same name hides the package's file for good, so every
    rule fix a package ships would stop at this machine.  When a current copy
    is packaged elsewhere, setup writes none, and removes an /etc copy only if
    TRCC wrote it -- never an admin's own.
    """
    for rules in rule_files():
        expected = files[rules]
        packaged = [d / rules.name for d in RULES_DIRS[1:]
                    if _reads(d / rules.name) == expected]
        if not packaged:
            log.debug("_defer_to_packaged_rules: no current packaged %s",
                      rules.name)
            continue
        del files[rules]
        log.info("_defer_to_packaged_rules: %s is current — not writing %s",
                 packaged[0], rules)
        if _written_by_trcc(_reads(rules)):
            rules.unlink()
            log.info("_defer_to_packaged_rules: removed our old %s, which "
                     "hid it", rules)


def _retire_legacy_rules() -> None:
    """Remove TRCC's rule file from before 2026-10-22 -- never an admin's own.

    It granted every user, and udev reads it after the new one, so its mode
    would win.  A package's copy goes with the package's upgrade.
    """
    legacy = RULES_PATH.with_name(LEGACY_RULES_NAME)
    text = _reads(legacy)
    if text is None:
        log.debug("_retire_legacy_rules: no %s", legacy)
        return
    if not _written_by_trcc(text):
        log.warning("_retire_legacy_rules: %s is not TRCC's -- left as it is; "
                    "if it sets MODE for these devices it decides who reaches "
                    "them", legacy)
        return
    legacy.unlink()
    log.info("_retire_legacy_rules: removed %s, which let every user reach "
             "the coolers", legacy)


def _invoking_user() -> str | None:
    """The account setup runs for: sudo's or pkexec's caller, else None."""
    if user := os.environ.get("SUDO_USER"):
        log.debug("_invoking_user: %s (sudo)", user)
        return user
    if uid := os.environ.get("PKEXEC_UID"):
        import pwd  # Unix only; this module is Linux's
        try:
            name = pwd.getpwuid(int(uid)).pw_name
        except (KeyError, ValueError):
            log.debug("_invoking_user: PKEXEC_UID %r names no account", uid)
            return None
        log.debug("_invoking_user: %s (pkexec)", name)
        return name
    log.debug("_invoking_user: none -- a root login")
    return None


def _join_device_group(user: str | None) -> None:
    """Create the ``trcc`` group if missing and add *user* to it.

    Best effort: setup never fails over it -- the user at the seat reaches
    the coolers through ``uaccess`` regardless.  The group is what keeps them
    reachable after a logout (the App's user service) and on a headless box;
    a new member has it from their next login.
    """
    import grp  # Unix only; this module is Linux's

    try:
        members = grp.getgrnam(DEVICE_GROUP).gr_mem
    except KeyError:
        members = []
        made = _run_first((["groupadd", "--system", DEVICE_GROUP],
                           ["addgroup", "-S", DEVICE_GROUP]))
        log.info("_join_device_group: group %s %s", DEVICE_GROUP,
                 "created" if made else "could NOT be created")
        if not made:
            return
    if user is None or user in members:
        log.info("_join_device_group: %s -- nothing to add",
                 "no user" if user is None else f"{user} is a member")
        return
    added = _run_first((["usermod", "-aG", DEVICE_GROUP, user],
                        ["addgroup", user, DEVICE_GROUP]))
    if added:
        log.warning("setup: %s joined the %s group -- it takes effect at the "
                    "next login (the panel works meanwhile while you are "
                    "logged in at this computer)", user, DEVICE_GROUP)
    else:
        log.warning("setup: could not add %s to the %s group -- run: "
                    "sudo usermod -aG %s %s", user, DEVICE_GROUP,
                    DEVICE_GROUP, user)


def _run_first(commands: Iterable[list[str]]) -> bool:
    """Run the first of *commands* whose program exists; whether it succeeded."""
    for cmd in commands:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            ok = subprocess.run(cmd, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            log.exception("_run_first: %s failed", cmd[0])
            return False
        log.info("_run_first: %s -> %s", " ".join(cmd), "ok" if ok else "failed")
        return ok
    log.warning("_run_first: none of %s is installed",
                [cmd[0] for cmd in commands])
    return False


def _reads(path: Path) -> str | None:
    """*path*'s text, or None when it is absent or unreadable."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        log.debug("_reads: %s not readable", path)
        return None


def _write_atomic(path: Path, content: str) -> None:
    """Write via a tmp file + rename so a crash can't leave half-written rules."""
    log.debug("_write_atomic: path=%s bytes=%d", path, len(content))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def _reload_udev() -> int:
    """udevadm control --reload-rules && udevadm trigger."""
    log.debug("_reload_udev: called")
    for cmd in (["udevadm", "control", "--reload-rules"],
                ["udevadm", "trigger", "--subsystem-match=usb"],
                ["udevadm", "trigger", "--subsystem-match=scsi_generic"],
                ["udevadm", "trigger", "--subsystem-match=hidraw"],
                # Re-fire the RAPL read-grant on the already-present
                # powercap nodes so CPU power works without a reboot (#194).
                ["udevadm", "trigger", "--subsystem-match=powercap"]):
        try:
            result = subprocess.run(cmd, check=False)
        except (OSError, subprocess.SubprocessError):
            log.exception("udevadm command failed: %s", cmd)
            return 1
        if result.returncode != 0:
            log.warning("udevadm exit %d: %s", result.returncode, " ".join(cmd))
    return 0


