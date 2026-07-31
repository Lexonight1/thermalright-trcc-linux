# Changelog

## v9.9.5

**Applying a video theme is instant.** It took over half a minute — 33 seconds
on a 228-frame theme, measured on real hardware. The decoder was launching a
separate `ffmpeg` process for every single frame, the file was being decoded
three times over on each apply, and two of those decodes raced each other on
different threads. The whole archive is now decoded in one pass, and rendering
never decodes at all. Same theme, same hardware: **33.56s → 0.21s**.

**Video themes play in the native skin.** In `trcc qtgui` a video theme
rendered frame 0 over and over and never advanced — it looked like a still
image. The frame advance had been written separately in the GUI, the CLI and
the REST API, and the native skin simply had no copy of it. All four now share
one command, so playback behaves identically everywhere and works in daemon
mode too. (#249)

**The native skin actually exits, and your panel goes dark on shutdown.**
Quitting `trcc qtgui` left the process running indefinitely — one report had it
still polling sensors and holding the display 1h41m after quitting. It also
ignored `SIGTERM`, which is what your system sends at shutdown, so the panel
was left lit showing its last frame instead of blanking. Closing, `Ctrl-C` and
PC shutdown all now release the device cleanly.

**`trcc.latest.log` covers the latest run.** It was never truncated, so it
accumulated days of launches — meaning a log that looked like it described your
current run could actually be showing you a previous one. If you have sent us a
log before, this is why some diagnoses missed. Frame, interval and pause state
are now recorded on the animation tick too, so a stuck or wrong-speed video can
be diagnosed from the log alone.

## v9.9.4

**Frozen Warframe SE and other HID panels crash on connect — fixed.** Every pip,
Fedora and Arch install crashed with `AttributeError: 'hid.device' object has no
attribute 'nonblocking'` the moment one of these panels connected; only Debian
escaped it. Two different PyPI packages both import as `hid` with incompatible
APIs, and TRCC drove one against the other. It now detects which one is
installed and speaks its API. (#244, #253)

**Unplug and replug your panel — it comes back.** Before, the device never
recovered: TRCC logged a write failure every ~150 ms (one report counted 1800+
in two minutes) and only restarting the app fixed it. Nothing was listening for
the unplug, so the dead connection was never released. (#254, #246)

**`display load-image` shows your image instead of a black screen.** Unless your
file happened to be named `00.png`, the panel went black while the command
reported success. (#245)

**Daemon mode stops crashing on common commands.** With `TRCC_DAEMON=1`, video
playback, slideshow, LED play and two API endpoints raised an error instead of
working. (#249)

**Large backgrounds display on Trofeo Vision panels.** Above roughly 512 KB the
firmware silently ignored the image and the glass kept showing the previous
frame. Big images are now compressed to fit rather than vanishing. (#251)

**LED commands aimed at a non-LED device now refuse.** `trcc led color
<lcd-key> ffffff` used to answer "LED color set" and exit 0, quietly saving LED
settings under an LCD's key. All 21 LED commands now answer in one voice. (#252)

**`trcc --version` works again** — plus `-V` and `trcc version`. The rebuild
dropped it, which made it needlessly hard to say which version you are on when
filing an issue. (#247)

**The CLI documentation matches the CLI.** The reference page is now generated
from the commands themselves so it cannot drift again, and 40 stale commands
across the guides were corrected — a test checks every documented invocation
against the real CLI. (#247)

## v9.9.3

**Your custom themes finally stick.** If you saved a theme with your own video
background or a positioned layer mask, then restarted TRCC and got a cloud
background (or the mask jumped to the middle) — that's fixed. Custom videos and
masks are now copied into your own library and referenced by the saved theme, so
they survive a restart exactly as you set them. (#242, #243)

**Portrait themes on the Mjolnir Vision 360 (640×480) aren't squeezed anymore.**
At display angle 90°/270° a portrait theme was being crushed to fit; it now
rotates properly, matching the official app. (#234)

**Frozen Warframe SE now displays.** One firmware revision of this panel
(0416:5302, bcdDevice 4.07) ignored the frames TRCC sent and stayed blank. TRCC
now speaks the HID-report protocol that firmware needs — huge thanks to the
reporters who reverse-engineered it. (#228)

**Video plays from the terminal + API.** `trcc display play` (and the API tick)
now actually animate a video background instead of showing a single frozen
frame, and the tick line tells you which frame it's on. (#239)

**Intel Arc GPU temperature works.** On the newer `xe` driver (Arc Pro cards),
GPU temperature read empty because it looked for a sensor channel that driver
doesn't expose. It now finds the right one. (#236)

**Set the media player from any interface.** A new media-player command works the
same from the GUI, CLI, and API — and the source is saved with your theme.

**Under the hood.** `hidapi` is now a proper Linux dependency (so the panels that
need it work out of the box), plus a batch of older reported crashes and quirks
confirmed already fixed and shipping here.

## v9.9.2

**Fan RPM shows up again.** Fan speeds were reading a flat 0 RPM on every board —
the metrics snapshot was quietly building itself from every source *except* the
fans. They're back. (#145, #207)

**Rotated panels now match the official app.** On a panel you've rotated to
portrait, a landscape background at 90°/270° draws a clean black backdrop behind
your metrics — exactly like the Windows app does — instead of stretched
letterbox bars.

**A display fix for some panels.** Certain panels weren't receiving frames
cleanly; TRCC now sends the image in the 512-byte chunks their firmware expects.
(#150)

**Read your panel's fingerprint at a glance.** A selectable line under the
rotation control shows the device's name, USB id, and protocol codes — handy
when you're filing an issue.

**Under the hood.** The render pipeline is being consolidated onto one shared
compose→encode path (no change to what ships to your panel — verified
byte-for-byte), and a "less noise" fix stops the log repeating a GPU-absent
warning every tick.

## v9.9.1

**If you're on AMD and v9.9.0 tried to install 938 MB of NVIDIA drivers — that
was my mistake, and it's fixed.** The Arch package listed the NVIDIA sensor
reader as a required dependency, so pacman pulled in `nvidia-utils` and the
whole EGL stack on machines with no NVIDIA card in them. It's back to being
optional, where it belongs.

If you skipped v9.9.0 because of this, v9.9.1 is safe to install.

**NVIDIA owners: TRCC now tells you the right command for your distro.** The
reader stays optional — nobody should be handed a driver stack they don't need —
but if you *do* have an NVIDIA card and the reader is missing, TRCC now names
the exact package for your system instead of guessing:

```bash
sudo pacman -S python-nvidia-ml-py     # Arch / CachyOS
sudo apt install python3-pynvml        # Ubuntu / Debian
```

Previously it suggested Fedora's package name on every distro, so the command it
gave Arch users didn't exist.

**Your panel now says when it's asleep.** If a panel goes idle it sleeps, and
reconnecting used to fail with a bare `[Errno 110] Operation timed out` — which
looks exactly like broken hardware. TRCC now says it's asleep, and how to keep
it awake.

**Videos: `display play` explains itself.** Loading a video and then running
`trcc display play` redrew a single frame every couple of seconds and looked
frozen. `play` is the metrics loop; `display play-video` is the video player.
TRCC now says so instead of leaving you guessing.

Thanks to **@em73es** and **@SunnyZorya23** for catching the AMD regression
within hours, and **@developer-yl** and **@armangido** for the reports behind
the rest.

(#216, #207, #150)

## v9.9.0

**NVIDIA GPU sensors now work on Arch and CachyOS.** If you installed the Arch
package and your NVIDIA card showed no temperature, load or power — this is why.
The NVIDIA reader was listed as an *optional* dependency, so pacman never
installed it, and TRCC silently fell back to no GPU data. Nothing told you. It's
now a required dependency like every other, so a fresh install just works.

Already installed? `sudo pacman -Syu trcc-linux` will pull it in — or
`sudo pacman -S python-nvidia-ml-py` if you'd rather not upgrade yet.

**The Trofeo Vision 9.16 can be rotated again.** On the 1920×462 ultrawide
panel, setting the display to 90° or 270° silently did nothing and the screen
stayed landscape. The rotation itself had been correct for two releases — the
panel was simply listed as landscape-only, so the app refused the setting before
it ever reached the screen. All four angles now work, matching the official app
exactly.

**Installing on Ubuntu/Debian actually works now.** Our own one-liner was
broken: `dpkg` reports an error when dependencies are missing, which caused the
very next command — the one that installs those dependencies — to be skipped. If
you got a wall of "depends on … however: Package is not installed", that was us,
not you. The instructions now use `apt install ./trcc-linux-latest_all.deb`,
which installs the package and its dependencies together.

**No more unplugging your cooler.** Every install guide used to end with "unplug
and replug the USB cable" — on a panel that usually lives inside your case,
behind tempered glass. It was never needed: the installer already applies the
USB permissions, and TRCC finds your cooler on its own when it starts. Just run
`trcc gui`. (If it isn't detected, restarting still sorts it.)

(#207, #221, #150)

## v9.8.9

**TRCC no longer tells everyone they need to update.** The update check was
comparing the latest release against `0.0.0` instead of your actual version, so
it announced "update available" to every user on every release — including
people already on the newest build. It now reports the version you're really
running, and says "up to date" when you are.

That one bug did real damage beyond the annoyance: because the nag never went
away, people upgraded again and again and reasonably believed they were current
when they weren't. Two of the fixes below exist to make sure that can't hide
again.

**Uninstalling no longer deletes the themes you made.** `install.sh --uninstall`
removed `~/.trcc-user` — where your own saved themes and backgrounds live —
without asking, while its `--help` only said "Remove TRCC Linux". It now asks
first and keeps your work unless you explicitly say otherwise; answering "no"
(the default) leaves your themes untouched. Downloaded/online themes are
unaffected either way, since those come back on their own.

**`trcc report` now says which TRCC you're actually running.** Reports carried no
version at all, so "which version are you on?" couldn't be answered from a
report — and the answer is usually the whole story. Reports now open with an
Install section listing your version, where TRCC is installed, which Python it
runs under, and how it was installed. If you're hitting a bug, this is the part
that gets it fixed fastest.

**`trcc doctor` catches installs that can't update.** Two things silently make an
upgrade do nothing, and both are now checked. If more than one `trcc` is on your
PATH, an upgrade can land on one while a different one keeps running — doctor
now warns and tells you which one actually runs. And if Python is serving cached
bytecode that no longer matches the installed code, doctor fails loudly with the
fix, instead of you running a version you no longer have on disk.

**"How was this installed?" is now answered honestly.** TRCC reported the install
method by guessing, and the guess was wrong nearly everywhere: on macOS, BSD and
Windows a pip install reported "source"; on Linux anything on your PATH reported
"pip"; and the About panel reported whichever package manager your distro
happens to ship. It now reads the real installer.

(#175, #207, #220, #150)

## v9.8.8

**Widescreen panels now actually rotate and fill the screen at 90° and 270°.**
v9.8.7 corrected the widescreen rotation math, but the live render path was
quietly bypassing it — so on an 854×480 / 1600×720 / 1920×462 / 1280×480 panel
the image was still sent the wrong way up, and the theme sat small and
letterboxed instead of filling the display at portrait angles. Two fixes here:
the render now applies the rotation it's supposed to (the frame is sent in the
panel's real landscape shape at every angle), and rotating the display reloads
the theme's portrait artwork so the background fills the screen. Verified against
the official app's own rotation logic. (#169, #203)

## v9.8.7

**Widescreen panels display the right way up at 90° and 270°.** On widescreen
LCDs — the Assassin 120 / Wonder Vision and similar 854×480, 1600×720, 1920×462
and 1280×480 panels — rotating the display to 90° or 270° showed the image
turned 180° from where it should be. The wire rotation now matches the official
app exactly at every angle: the base rotation is fixed per panel size (it never
depended on the handshake sub-byte, which is what had thrown it off for some
panels). (#203, #169)

**Elite Vision 360 ARGB now works.** This panel speaks a different variant of the
bulk protocol than the other USB LCDs, so TRCC couldn't finish its handshake and
the screen stayed blank. It's now recognised and driven with its own protocol
(fixed 320×320). (#212)

**Showing a theme from the terminal works on its own now.** Driving a panel
purely from the command line, `trcc display play` and `trcc display keepalive`
failed with "No active theme" / "No cached frame", and `trcc theme cloud-load`
failed with "Not attached" — because each command is a fresh process that didn't
reload what you'd already set. Those commands now restore the device's theme and
background automatically before they start streaming, the same way the GUI does
on connect. (#150)

**Clearer message when a panel needs setup.** When a SCSI LCD can only be opened
as root, TRCC now points you at `trcc system setup` (then reboot) instead of a
bare permission error. (#217)

## v9.8.6

**Grand Vision 360 (and its square-panel siblings) show at the right size.**
The Grand Vision 360 and similar 480×480 bulk panels were being detected as
320×240, so the picture was the wrong size and looked rotated or garbled at
every display angle. The panel now resolves to its true 480×480. The cause was
a device-ID table that had drifted from the original app's rules; it's now an
exact match, so the panel gets exactly the resolution the official software
would give it. (#176, #186)

**`trcc report` now captures your panel's handshake.** The debug report includes
each connected device's exact resolution and identity bytes (PM/SUB/raw),
instead of only whatever happened to still be in the log tail. This is the piece
that lets a new or mis-detected panel be mapped correctly — previously that
information had usually scrolled out of the log by the time the report ran.

## v9.8.5

**CPU and case fans report again.** On boards whose fan headers aren't labelled
(the common super-I/O chips — and the water-cooler pumps every Thermalright
cooler ships), the fan panel bound its CPU/GPU rows to whichever header came
first — often a disconnected one reading 0 RPM — while your real, spinning fans
fell off the end. The panel now puts the **spinning** fans in the visible slots,
so your pump and radiator fans show up instead of a row of zeros.

**Your panel goes dark on shutdown.** LCD panels used to hold their last image
lit after you powered the PC off. They now blank when the app closes on
shutdown (`trcc display sleep <device>` does it on demand too).

**`trcc display test` works on its own.** Running a wire command in a fresh
terminal — `display test`, `display color`, and friends — now auto-connects the
device instead of failing with "Not attached".

**Elite Vision 360 ARGB White connects.** This cooler was talking the wrong USB
protocol in our registry and wouldn't come up; it's now correctly a Bulk device.

**Arch on AMD no longer pulls the NVIDIA stack.** The Arch package listed the
NVIDIA sensor library as a hard dependency, dragging in `nvidia-utils` + the EGL
stack (~885 MiB) on AMD-only systems. It's an optional dependency now.

**Metric units follow the °C/°F toggle on more masks.** On the flag-template
(0xDC) cloud masks, values now draw their unit and it switches with your
Celsius/Fahrenheit choice. Plus a fix for the overlay "flash" highlight and a
quieter log (a per-tick sensor line was flooding it).

## v9.8.4

**LED digital coolers that came up a dim, colourless "888" now display
properly.** Single-display LED digital coolers (AX120, frozen-horizon/magic,
LF15, LF13, CZ1 and similar) could show dim grey with no colour even though the
numbers were correct. The cause was a leftover "test mode" flag that got saved
into your config in an earlier session and was silently restored on every
launch. Test mode is a momentary diagnostic now — it never persists across
restarts — so your colours come back. Its reference colours are also full-bright
now, and it applies to the multi-zone coolers too.

**Metric text renders at the right size, and fan speeds show again.** Large
authored metric sizes (like a big hero temperature) were being shrunk to a small
default; they now render at their intended size. Fan-RPM tiles that previously
drew nothing now show the speed.

**Show or hide the unit on each metric.** Every metric element can draw its value
with the unit (45°C, 60%, 1200 RPM) or as a bare number when the unit is already
part of the theme art — a per-element toggle in the editor, the CLI
(`--show-unit` / `--hide-unit`) and the API. Celsius/Fahrenheit stays where it
has always been, on the About panel.

## v9.8.3

**NVIDIA GPU metrics now work out of the box.** If you installed via `pip` (or
`pipx`) and your NVIDIA card showed no temperature/usage/clock, that's fixed:
the NVML reader (`nvidia-ml-py`) is now a core dependency, so a plain
`pip install trcc-linux` includes it — no separate install step, no in-app
prompt to chase. It's pure-Python and does nothing on machines without an
NVIDIA card, so there's no cost if you're on AMD/Intel.

**Some SCSI panels that wouldn't connect now do.** On certain coolers (Winbond
and ChiZhu SCSI displays), a boot-time helper was accidentally overwriting the
USB-storage quirk list down to a single device — which stripped the setting that
lets the panel expose the device node the app needs, so it never connected.
It now applies the full list. If your SCSI panel was failing with a "no
/dev/sg* node" error, update and reboot (or unplug/replug the panel).

**Docs:** the diagnostic commands live under `trcc system` now (e.g.
`trcc system hid-debug`) — corrected the guides that still showed the old
`trcc hid-debug` form.

## v9.8.2

**Widescreen panels now rotate at 270° too.** v9.8.1 fixed 270° on the smaller
rotating panels; this extends the exact same fix to the widescreen panels
(854×480 / 1280×480 / 1600×720 / 1920×462), which were still showing 270° the
same as 90°. Now the content flips to follow the physical rotation on those
panels as well — so all rotating panels behave the same way.

If you're on a widescreen panel and rotate to 270°, please let me know how it
looks — a screenshot at each angle helps me confirm the orientation is exactly
right on the glass.

## v9.8.1

**Rotating to 270° now shows your content the right way up.**  On rotating
panels, setting the display to 270° used to look identical to 90° — the
background, mask, and metrics were all a half-turn off.  Now the whole composite
flips to follow the physical rotation, so 90° and 270° are proper opposites the
way they should be.  This covers the smaller rotating panels (320×240 / 640×480);
the widescreen panels handle rotation on a separate path that's still being
worked on, so they're unchanged in this release.

Also for maintainers: a diagnostic replay tool now boots and replays a user's
`trcc report` with no hardware (`dev/mock_gui.py --issue N --replay --check`),
which makes reproducing device-specific bug reports much faster.

## v9.8.0

Rotating a display to portrait now switches to your portrait artwork instead of
turning the landscape version on its side.

**Portrait masks display upright at 90° / 270°.**  On panels that can rotate,
picking a portrait mask and rotating the display used to spin the whole
landscape layout sideways.  Now the app loads the portrait version of your
content and composes it upright — the way the original Windows app does — so
masks, backgrounds, and overlay text stay aligned and nothing gets clipped.

**Saving a theme while rotated saves it in the right orientation.**  A theme you
save at 90° / 270° is now stored as portrait (and a landscape one as landscape),
so it comes back exactly as you saved it the next time you load it — no more
themes reloading in the wrong orientation.

Under the hood this restores the original per-orientation content model the app
was built on, with the whole change covered by tests and verified on a real
rotating panel.  Note: Thermalright ships far fewer portrait masks (5) than
landscape ones (110) — that's the vendor's catalog, not a bug in this release.

## v9.7.10

Saved themes now remember everything you set — and they come back exactly that
way after a restart.

**Your saved theme's background is remembered again — including video
backgrounds.**  A saved theme stores each asset (background image or video, and
mask) as a symlink into your data folder rather than a copy, so nothing is
duplicated and a video background now plays back when you reload the theme
instead of showing nothing.  The only real file a saved theme keeps is its own
layout (the overlay you edited in settings), which is rewritten each time you
save.

**Restarting the app brings back the last theme you were using — with your
changes.**  On launch the app now restores the exact theme you last had on each
device, along with its background, mask, and overlay edits, instead of falling
back to the shipped default.  Your own saved theme and a shipped theme that
happen to share a name now coexist — saving never overwrites the original, and
shipped themes simply list first.

**Rotating the display no longer wipes your edits.**  Changing orientation now
keeps your overlay layout, background, and mask, matching the original Windows
app's behaviour (it rotates what's on screen rather than reloading from scratch).

## v9.7.9

NVIDIA GPU sensors on the Debian/Ubuntu package, and a clearer setup.

**NVIDIA GPU readings now work on the packaged (`.deb`) install.**  On the
bundled package, `trcc system setup` installed the GPU sensor reader with
`pip install --user`, which pip refuses *inside* the package's virtual
environment — so the reader never installed (no GPU temperature / usage / power),
and setup reported failure even though device access had already succeeded.  The
reader now installs correctly into the virtual environment, and a GPU-reader
hiccup no longer makes the whole setup look broken — granting device access is
the part that matters, and it stands on its own.  (Thanks to the reporter who
patiently chased this one down across two versions.)

## v9.7.8

LED digit colouring, CPU power, autostart and glitch fixes.

**Effect colours render one colour per digit, not a rainbow per segment.**  On
the digital segment coolers (PA120 PRO, AX120, AK120, LC1, LF8, CZ1, LC2, LF11,
LF10, LF12), the Rainbow / Colour-cycle effects spread the full spectrum across
every individual segment, so a metric number looked like confetti instead of a
solid colour.  Each digit now shows one cohesive colour with only a gentle
gradient between digits — matching the original software — while decoration
strips keep their flowing rainbow.

**CPU power now works on AMD as well as Intel.**  CPU package power is read from
the kernel's RAPL energy counter — the same vendor-agnostic node on AMD (Zen)
and Intel — but it's root-only on most distros, so the reading came back blank.
`trcc setup` now grants read access to it (and loads the RAPL module), so the
CPU watts appear after running setup.  `trcc report` also gained a RAPL section
so a blank reading is diagnosable at a glance.

**Autostart can start hidden in the tray again.**  `trcc gui --resume` (used by
the autostart entry) was dropped in an earlier rewrite, so logging in always
popped a visible window.  The flag is restored — autostart starts hidden in the
system tray and existing autostart entries pick it up automatically.

**The segment numbers no longer hiccup when they update.**  Every sensor refresh
(~2 s) re-rendered an already-animating LED device an extra time, nudging the
effect one step forward in lock-step with the numbers updating — a brief visible
glitch.  Animating devices are now driven only by the animation loop.  Dragging
the brightness slider is also smoothed so it no longer floods the device.

**Clean shutdown.**  The GUI now handles the shutdown signal the session manager
sends, so devices are released cleanly when the PC powers off instead of being
killed mid-frame.

## v9.7.7

Rotation, LED-effect and resume fixes.

**Rotate-panel themes no longer clip at Display Angle 90°/270°.**  On a
non-square rotate panel (e.g. the Frozen Warframe 320×240), a local theme saved
in one orientation had no portrait variant on disk, so at 90°/270° its text
overflowed the narrower canvas and clipped (e.g. `796 MHz` cut off).  The whole
theme is now composed on its native landscape canvas — where nothing clips — and
rotated as one unit into the portrait buffer, so background and text stay
aligned and full-screen, matching how the original software handles it.  Cloud
themes, JPEG widescreen panels, square panels and the 0°/180° angles are
unchanged.

**LED effect modes animate on multi-zone coolers.**  On a multi-zone cooler
(PA120 / LF10), setting Breathing / Colour-cycle / Rainbow on individual zones
left the effect frozen on a solid colour — the animation tick only looked at the
device-wide mode, not the per-zone modes.  The animator now sees the zone modes,
so the effects run.

**The display comes back after sleep/wake.**  After the machine suspended and
resumed, the USB link a device opened beforehand was stale, so the panel stayed
blank until you restarted the app.  TRCC now reconnects each device on resume
(re-handshake + fresh send worker) so the LED segment displays and LCD panels
repaint on their own.

## v9.7.6

LED-panel polish and sensor fixes.

**LED panel labels are now translatable.**  The memory (LC1), disk (LF11) and
clock (LC2) panels rendered their field labels from baked artwork — some of it
Chinese (`(位DDR5)`), some missing entirely (the disk panel showed values with
no "Drive Temp"/"Read Rate"/… names) — and the metric-selection buttons baked
their labels into the button images.  Every LED-panel label is now an i18n
`tr()` overlay, drawn at the original coordinates and font, so changing the app
language changes the panel.  English text is sourced from the official panels;
the clock's "first day of the week" also fixes an upstream typo (it's
Sunday/Monday, not "Tuesday").

**LED segment temperature shows in your unit.**  The 7-segment display lit the
°F label but kept printing the raw °C number (e.g. 26 °C shown as "26°F").  It
now converts to the device's selected °C/°F.

**LED brightness dims the preview, not just the device.**  The brightness slider
only affected the physical LEDs; the on-screen preview stayed full-bright.  Both
now follow one signal, so the slider previews correctly.

**LED rotation-interval control restored.**  The "Circulate" rotation-speed field
was never shown, so metric-page styles (AX120, AK120, LC1, LF8, LF12, CZ1, LF11,
LF15) couldn't set how fast the display cycles.  Carousel/interval/selector
visibility now matches the device: page styles get the interval field, the
"select all" styles (PA120/LF10) correctly don't, and the clock/solid panels
show neither.

**SSD/disk temperature.**  Disk temperature is wired on Linux (kernel
`nvme`/`drivetemp`) and Windows (LibreHardwareMonitor); HDD metrics are on by
default, and the System-Info hard-disk temperature row is bound correctly.

**Preview stays upright on rotated panels.**  Non-square panels mounted at 90/180°
no longer show a sideways/upside-down preview (the wire output is unchanged).

**The running window raises on relaunch without hanging (#196).**

## v9.7.5

Follow-up fixes from reporters testing v9.7.4.

**PA120 / LF10 mode buttons now work (#192).**  v9.7.4 fixed zone selection and
per-zone colour, but the mode/effect buttons (solid / breathe / colour-cycle /
rainbow) did nothing — `SetLedMode` wrote one global mode while the render reads
each zone's own.  It's now per-zone like the colour setter.

**Ubuntu 26.04 / Python 3.14 install fixed (#157).**  The `.deb` installed the
package to a Python-version-specific path (`python3.12/dist-packages`), so on a
system running a different Python (26.04 ships 3.14) `trcc` died with
`ModuleNotFoundError`.  The relocate-to-version-agnostic step now covers the
`/usr/local/lib` install scheme Debian/Ubuntu actually uses.  Reproduced and
verified in an Ubuntu 26.04 container.

**NVIDIA reader installs correctly in a virtualenv (#161).**  The "install GPU
sensor support?" prompt ran the OS package manager, which targets system Python
— invisible to a pip/venv install.  It now installs `nvidia-ml-py` into the
running interpreter via pip when in a venv.

**Diagnostic reports keep the device handshake line.**  Per-frame log spam was
scrolling the once-per-connect `handshake OK: PM=… SUB=… resolution=…` line out
of the report tail — the single most useful line for device-geometry triage.
Per-frame logs are now DEBUG, and the HID handshake logs its PM/SUB too.

## v9.7.4

Device-recovery and LED fixes, plus the GUI/cross-platform work that accumulated
since v9.7.3.

**Display no longer goes blank after sleep/resume (#189).**  On resume the kernel
re-enumerates the USB device, leaving the app's open handle stale — every write
failed with `EIO` and never recovered, so an LED cooler's screen stayed dark
until a reboot.  `EIO` is now treated as a disconnect, and every wire
(SCSI/HID/Bulk/LY/LED) heals a stale handle in place with one
close→open→re-handshake retry before escalating.

**PA120 / LF10 metric-zone selection + colouring restored (#192).**  Those zones
are an independent multi-select — the picked colour applies to every selected
zone, and "circulate" applies it to all — matching the original app.  A rework
had turned them into a single-select "radio" and left the per-zone colour list
unpopulated, so the buttons had no visible effect.  Both are fixed.

**LED device picker fixed in the native skin (#176)** — it passed a label where a
device id was expected, so display commands failed.

**No more duplicate LibreHardwareMonitor helpers (#191)** — a slow sensor
namespace could make the app spawn extra helper processes; it now waits for the
one it started.

**Empty-window state.**  With no device connected the GUI now shows the system
dashboard plus a per-OS "connect a device" hint, instead of a blank panel.

**Clearer failures.**  Device-connect failures now surface on the event bus with
OS-correct hints (run `trcc system setup`, install the WinUSB driver, "run as
administrator", etc.) across every UI.

**Cross-platform restoration (Windows/BSD).**  Windows memory type/form-factor +
disk type/SMART, a WMI `Win32_VideoController` GPU fallback, and a COM-safe WMI
seam are back; BSD regained XDG autostart.

## v9.7.3

Windows GUI launch fixes — two crashes that v9.7.2 exposed by letting the app
boot past the earlier `typer.Exit` / `fcntl` errors.

**`usb.core.NoBackendError` on launch — fixed.**  Windows has no system libusb,
and the packaged build's bare `usb.core.find` couldn't locate the bundled DLL,
so device discovery crashed.  USB scanning now routes through `libusb_package`'s
bundled backend (the canonical pyocd fix, #131 — dropped in the cutover).
Verified on a Windows 11 VM: the device was detected.

**`AF_UNIX not available` crash on launch — fixed.**  The GUI hosts an optional
IPC socket; on Windows (no `AF_UNIX`) it now skips it and runs in-process,
instead of raising.  This restores the pre-cutover behavior.

Together with v9.7.2's `typer.Exit` + `fcntl` fixes, the Windows GUI launch path
is clear again.

## v9.7.2

Bug-fix release: a Windows GUI launch crash, the GPU selector, and automatic
SELinux setup.

**Windows: GUI no longer crashes on launch (#187).**  `trcc-gui.exe` died with
*"Failed to Execute script '__main__' due to unhandled exception"* on every
v9.7.1 launch.  The packaged app calls the GUI launcher directly (outside the
command-line runner), but it raised `typer.Exit` — an internal control-flow
signal that only works inside that runner — so it escaped as an unhandled
exception.  The launcher now exits cleanly via `SystemExit` in every context
(packaged exe, `trcc-gui`/`trcc-lcd` scripts, and `python -m trcc gui`).

**GPU selector now works.**  On multi-GPU machines the control-center GPU
picker listed the GPUs and saved your choice, but the displayed metric always
came from the first discrete GPU — the selection never reached the sensor
layer.  Picking a GPU now re-routes the metric, through the universal command
path so the GUI, qtgui, CLI (`trcc config gpu`), and API all honour it, and the
choice persists across restarts.

**SELinux USB policy installs automatically.**  On enforcing systems (Bazzite,
Silverblue, Fedora Atomic) the `trcc_usb` policy that allows USB device access
used to need a manual command.  The RPM now builds and loads it in `%post`, and
source / pip installs load it via `trcc system setup`.

**Source / pip setup fixes.**  `trcc system setup` failed with
`ModuleNotFoundError: No module named 'trcc'` after `pip install --user` — the
sudo re-exec couldn't see the user's site-packages; it now injects the right
import paths.  Setup also reported "udev rules not installed" right next to
"completed" on a first run; it now re-checks permissions after installing.

## v9.7.1

Display-correctness release for LED and LCD panels, on top of a per-device
panel model sourced from the Windows app's behaviour.

**LCD — portrait orientation now works end-to-end.**  Non-square panels
(e.g. 854×480) mounted rotated showed squished or mis-rotated content.  The
render pipeline is now orientation-driven from a single source
(`oriented_resolution`): at 90°/270° the canvas composes portrait and the
content is NOT re-rotated — it's authored portrait, matching the Windows
`themeDirection` model (no runtime rotation).  Cloud backgrounds download per
resolution AND orientation (`web/480854` vs `web/854480`), re-apply on
rotation, and decode video at the oriented canvas — so a portrait cloud
background fills the screen instead of being squished.  Every cloud-asset
command (themes, masks, backgrounds, save / export / import / upload) resolves
the oriented catalog, so the CLI and API behave exactly like the GUI.

**LED — control panel fixes.**  "Circulate" now rotates through exactly the
metric pages you toggle (the per-page selection was never persisted, so it
stuck on page 0); selecting a metric page reaches the device; the effect modes
(breathing / colour-cycle / rainbow / carousel) animate at full speed again
(a ~150 ms tick was dropped in the cutover); zone buttons render their images;
and the selector buttons are labelled with the metric they show.

**LED metrics no longer flicker.**  The displayed value was re-sampled from raw
instantaneous sensors ~7×/second; it now updates once per refresh interval
(steady), matching the gauges.

**Per-device panel model.**  A toolkit-free model — sourced from the Windows
app and keyed on the handshake — drives which sections each LED / LCD panel
shows, shared by both GUI front-ends.

Plus tooling: `audit_csharp` extracts device / panel / resolution facts from the
decompile per release, and button images for the 2.1.6 devices (LC10 / LC13 /
LC15 / LF014 / LD11 / RX1) are bundled.

## v9.7.0

Feature + performance release.  Brings the animated-theme GUI to CPU
parity with the legacy app, restores the LED multi-zone and DDR-multiplier
features the cutover had dropped, adds Linux CPU package-power readings,
and broadens the REST/CLI surface — followed by an architecture-audit pass
that fixed two real bugs and cleaned up the new code.

**Performance — animated themes (24% → ~7% CPU).**  A genuinely-animated
theme used to peg a core at ~24%.  Two changes bring it to legacy's ~6%:
a `VideoFrameCache` pre-composites every video frame's background+mask once
(reusing `_build_bg_mask` per cursor, byte-identical to the live path) so a
tick after the first loop is a list lookup instead of a fresh
decode+fit+composite; and the GUI preview now **observes the sent frame**
instead of re-rendering the whole pipeline a second time per tick — the
rendered surface rides the `FrameSent` event to `handler.handle_frame`, the
legacy single-source shape.  Side benefit: the preview now shows the exact
frame the device received (no more raw-vs-personalized sensor drift).

**LED — multi-zone rendering + DDR multiplier.**  Multi-zone devices
(PA120, LF10) rendered every zone in one global colour because the zone
setter Commands persisted state the render path ignored.  `RenderLed` now
fills each zone's mapped LEDs from its own mode/colour/brightness
(`tick_multi_zone`) and advances the zone-sync carousel through the enabled
zones.  Separately, `memory_ratio` — the DDR memory multiplier (1/2/4) the
LC1 gauge scales its reading by — was mis-retyped to a bool during the
cutover and frozen at the default; it's now an int again, consistent across
the model, Commands, settings, CLI (`led memory-ratio <1|2|4>`), API, and
both GUIs.  (LED hardware verification is reporter-pending.)

**Sensors — Linux CPU package power.**  `cpu:power` was blank on Linux;
it now reads the powercap RAPL `energy_uj` counter and reports watts as
Δenergy/Δt (summed across package domains, wraparound dropped,
root-only counters degrade to None).  Thread-safe across the poll +
render threads.

**API — surface expansion.**  New routes: `GET /devices/{key}` (single-
device detail), `GET /system/metrics` (flat `{id: value}` map),
`POST /devices/{key}/display/reset` (stop video + red frame), and the
cloud-theme browsing trio — `GET /theme/web` (preview gallery),
`POST /theme/init` (per-resolution prefetch), and a `/static/web` mount
serving the preview images.  The gallery enumeration runs on the bus
(`ListWebThemes` Command), so it works through the daemon proxy.

**CLI.**  `--json` on the `display` / `led` / `system snapshot` commands
for scripting; `led list-styles` shows per-style `segments=N zones=N`
capability columns again; `trcc shell` prints a clear "install
prompt_toolkit" message instead of a fatal traceback when the optional
dependency is absent.

**Fixes.**  `POST /devices/{key}/display/theme` 500'd on every call
(`platform.user_content_dir()` AttributeError — it's on `Paths`); fixed
with a regression test.  The RAPL reader's cross-thread `_last` race
(garbage watts) is now lock-guarded.  A stale GUI hint pointed at a
removed `trcc download` command.

**Internal.**  Architecture-audit pass: stripped the `VideoFrameCache` to
its used surface (the overlay/brightness layers were speculative dead
code), shared `build_frame`/`build_preview_surface`'s layer-resolve so the
preview uses the video cache too, moved filesystem logic out of the
`/theme/web` route into a Command, deduplicated `parse_resolution` to
`core.models`, replaced GUI lambdas with named slots, and added a public
`DisplayService.encode_png/jpeg` seam so routes stop reaching the private
renderer.

## v9.6.5

Issue-driven patch release.  Closes #149 (Levita right-side notch),
#150 (HID-debug partial-init import cycle), #151 (Help link 404).
Folds in the v9.6.4 disconnect-recovery fix that pip-installed users
haven't picked up yet, and adds the testability + diagnostics
infrastructure needed to debug the next class of regressions on
real reporter hardware.

**Issue #149 — Thermalright Levita right-side camera notch.**  Reporter
oranura's Levita (87AD:70DB Bulk, PM=64 SUB=3) shares the chipset
with the older GrandVision 360 / WonderVision lineup but the
smartphone-derived AMOLED panel carries its notch on the right edge
instead of the left.  Previously the same split-overlay PNG covered
the left side regardless, so Levita owners saw a black bar masking
the wrong half of the screen.

The fix lives in three layers:

- ``_LCD_BUTTON_IMAGE`` and ``_LED_BUTTON_IMAGE`` were flat ``dict[PM,
  dict[SUB|None, …]]`` lookups.  The C# decompile's
  ``ADDUserButton(ID, pm, sub)`` dispatcher actually keys on
  ``(deviceClass, pm, sub)`` where ``deviceClass`` comes from the USB
  enumerator (case 1 LED / case 2 HID T2 / case 3-4 HID T3 / case
  257 Bulk).  PM=64 in Bulk ≠ PM=64 in HID T2.  Without VID/PID
  scoping the lookup cross-contaminates and that's how Levita was
  masquerading as LM30.  Collapsed both flat tables into one
  ``_VARIANT_REGISTRY: dict[(VID, PID), dict[PM, dict[SUB|None,
  VariantOverride]]]`` sourced row-by-row from the C# case map.
  SCSI siblings alias to the Bulk dict (87CD:70DB, 0402:3922,
  0416:5406 share ``_BULK_VARIANTS``) because the C# router sends
  them through case 257 too.
- ``DeviceInfo`` gained a ``panel_cutout: PanelCutout | None`` field
  (hardware truth — populated from the registry, not user config).
  ``enrich_from_handshake`` resolves the variant override on
  ``(self.vid, self.pid, self.effective_pm, self.sub_byte)`` and
  copies both ``button_image`` and ``panel_cutout`` onto the DTO.
  Logs at INFO on hit / DEBUG on miss with the full key so reporter
  diagnostics show exactly which row resolved.
- ``Renderer.flip_horizontal`` added to the port + QImage backend so
  ``apply_split_overlay`` can mirror the cached PNG when the active
  device's ``panel_cutout`` sits past the canvas midline.  Cache key
  extended from ``(style, rotation)`` to ``(style, rotation,
  mirrored)``.  Existing left-cutout PNGs stay untouched for
  GrandVision and WonderVision; Levita gets the mirrored variant.

**Issue #150 — ``hid-debug`` partial-init crash.**
``adapters/infra/diagnostics`` does ``from
trcc.adapters.device.hid_protocol import HidProtocol`` to drive an
interactive handshake.  Because ``hid_protocol`` does ``from .factory
import ...`` and ``factory`` had module-bottom ``from .hid_protocol
import HidProtocol``, importing the diagnostic module first
triggered the cycle: ``factory`` re-entered ``hid_protocol`` while
its class body was still executing and got an ``AttributeError``.
Fix: moved the five protocol imports out of ``factory.py``'s module
bottom and into each ``ProtocolFactory.make()`` method.  ``factory``
no longer touches a protocol module at import time, the cycle is
broken in both directions, and reporters who ran the hid-debug
command on Debian / Proxmox stop hitting the crash.

**Issue #151 — Help button 404.**  The GUI Help button opened
``doc/TROUBLESHOOTING.md`` but the file was renamed to
``GUIDE_TROUBLESHOOTING.md`` in the doc reshuffle.  One-line URL fix
in ``_on_help_clicked``.

**Issue #148 — ``trccd`` daemon blank display.**  Already addressed
on ``main``: ``_boot.trcc(discover_now=True)`` flows through
``Trcc.discover()`` which calls ``self._metrics_loop.start()``.  This
release publishes the fix to PyPI and the distro packages so the
CachyOS reporter doesn't have to install from source.

**Persistent log file (debugging infrastructure).**  The file handler
was ``logging.FileHandler(path, mode='w')`` — every restart wiped
the previous session's log, which made the next-class of "freezes
after N minutes" bugs un-diagnosable: by the time the user could
report, the evidence was gone.  Switched to
``logging.handlers.RotatingFileHandler(path, mode='a', maxBytes=10
MiB, backupCount=2)`` so a crash captured in flight survives the
restart cycle that follows.

**TRACE log level for per-frame chatter.**  ``send_frame``, ``tick``,
``overlay.render`` and friends emit ~15-60 log records per second
per device.  Even at ``DEBUG`` they swamped the file and buried the
diagnostic signal.  Added ``TRACE_LEVEL = 5`` (below ``DEBUG``) in
``core/models/constants`` and moved the per-frame log lines to
``log.log(TRACE_LEVEL, …)``.  Default file handler stays at ``DEBUG``
— frame spam is filtered out.  Pass ``-vv`` (verbosity ≥ 2) to open
the TRACE gate when you actively need frame-rate diagnostics.

**Disconnect recovery (folded forward from main).**  The v9.6.4
release predated the ``ENODEV/EBADF/ENXIO`` recovery commit
``4cd9c99c``.  This release ships it: ``_guarded_send`` now
classifies disconnect-class errnos, closes the stale transport
after the failure threshold, fires ``on_state_changed("connected",
False)``, and the lazy-open machinery re-opens the handle on the
next send.  Users whose USB device transiently disconnects
(suspend/resume without ``PrepareForSleep``, kernel hub blip)
recover transparently on the next frame instead of getting stuck
on the firmware's "USB waiting" splash.

**Test suite repaired (27 failures → 0).**  Pytest had accumulated 27
failures during a long stretch of "ignore-tests-during-refactor"
phases (Phase 9, 10A, 11, metrics unification, daemon redesign,
sensor-source rebase, Trcc-singleton, TrccProxy).  Five root causes,
all fixed at the test boundary:

- API / CLI tests patched the legacy ``_system_svc.all_metrics``;
  production reads ``trcc().os.metrics`` now (``Platform.metrics``).
  Migrated 5 test files + hoisted the ``trcc`` import in
  ``ui/api/system`` and ``ui/cli/_system`` so the patch target is
  module-level.
- LED service tests for temp-linked / load-linked modes used
  ``HardwareMetrics(cpu_temp=…)`` without setting ``_populated``;
  the production tick path now gates on ``_populated`` to hold the
  prior color when a sensor is absent.  Tests updated to pass the
  ``_populated`` set; the "missing metric defaults to zero" test
  is now "missing metric holds prior color".
- Device-open tests patched ``usb.core.find``; production calls
  ``trcc.adapters.device._pyusb_find.find`` (the libusb-package
  shim that fixes Windows).  Migrated bulk / BSD / macOS / detector
  / integration tests to patch the new seam.
- ``mock_io`` fixture patched ``_base.pynvml`` and
  ``NVML_AVAILABLE`` but not ``_pn`` — the module-level alias
  the production code reads.  NVIDIA-fallback tests now exercise
  the real ``_discover_nvidia`` path.
- ``get_button_image`` / ``get_variant_override`` gained
  ``vid, pid`` parameters; ~30 test cases migrated to pass them.

Plus targeted fixes for ``test_builder`` (asserts ``platform.sensors``
not the removed ``create_sensor_enumerator``) and ``TestUCSystemInfo``
(panel is now a Topic.METRICS observer, no local timer).

**Variant resolution smoke harness.** ``dev/smoke_variant_resolution.py``
walks every registered ``(VID, PID, PM, SUB)`` tuple — 347 lookups +
225 handshake-driven enrichments — and asserts the resolved button
image + panel_cutout match the registry, every referenced ``A1*.png``
asset exists, the cutout-side decision (mirror vs no-mirror) is
unambiguous, the wire-dict roundtrip preserves ``PanelCutout``, and
no-variant-table families (HID T3 + LY) stay button-stable.  Plus
the existing ``smoke_platforms`` got an unrelated fix for an
``object()`` stub that didn't survive the disconnect-recovery
``setattr`` in ``LCDDevice._wire_protocol_observers``.

## v9.6.4

Quiet-log patch + a handful more `src/` lambdas converted to named callables.
No behaviour changes for users on a healthy install — strictly a "what
trcc report shows on a working box" hygiene release.

**Log hygiene (issue #144):** two false-positive lines in `trcc report`
on Fedora 44 / Python 3.14 dropped to DEBUG with clearer wording.

- `sysfs VID/PID walk failed for /sys/class/scsi_generic/sg0/device —
  skipping device` (WARNING) → now reads `sysfs ... has no USB parent
  — skipping (non-USB SCSI device)` (DEBUG).  The line fires for every
  non-USB SCSI generic device on the box (CD-ROMs, virtual disks,
  AHCI hosts) and isn't a defect.
- `SCSI transport factory not injected` (ERROR) → now reads `SCSI
  transport factory not yet wired — skipping open` (DEBUG).  The line
  fires when a `ScsiProtocol` instance is created before the
  composition root finishes wiring (lazy-init race that the call site
  bails on gracefully) — not an actual failure.

**Continued no-lambdas sweep:** 7 more `src/` lambdas converted to
named callables (`data_repository.py` fetch_fn, `dc_writer.py`
`_metric_to_hardware_ids`, Windows `SensorSource` sort key,
`CarouselConfig.theme_indices` default_factory, `ThemeService` sort
key, GUI SIGINT handler, sensor-picker checkbox slot).  Continues the
Smalltalk-style "every callable has a real qualname" discipline from
v9.6.3.

## v9.6.3

Daemon-mode regression fix + cross-platform metric-source hygiene.
No user-visible feature changes; this is an internal correctness
release that closes a latent crash for `TRCC_DAEMON=1` users and
silences the metrics-fallback log spam on dev / minimal-install boxes.

**Daemon-mode metrics crash (latent regression in v9.6.2).** The
2026-05-14 metrics chain migration moved CLI / API call sites to
`_trcc().os.metrics`, but `TrccProxy` had no `os` facade.  Anyone with
`TRCC_DAEMON=1` would crash on every metrics-touching command
(`trcc info`, `trcc led test`, theme overlay loops, API
`/system/metrics`).  Fix: added `OSFacadeProxy` exposing `.metrics`
via a new `_meta.metrics` IPC route, plus `HardwareMetrics` wire
serialization helpers.  Verified end-to-end by
`dev/smoke_daemon_metrics.py`.

**Capability probing moved to one-shot at construction.**
`LinuxPlatform.__init__` now resolves subprocess tool paths via
`shutil.which()` once at startup — `_tools: dict[str, str | None]` is
frozen for the rest of the app's lifetime.  Read paths consult the
cached map and skip absent tools cleanly.  No more `try: subprocess;
except FileNotFoundError: log.debug("X not installed")` chain firing
on every metrics tick — startup logs ONE `INFO` line listing what's
available and what's absent, then deterministic reads thereafter.

**NVML probe-at-discovery (sysctl-style).** Each NVIDIA card now has
its supported metric set resolved at `_discover_nvidia` time by
calling each `_read_nvml_<metric>` once.  Cards register `SensorInfo`
entries only for metrics they actually answer; `_poll_nvidia` is a
flat loop over the per-card supported set with no per-tick try/except
for "NVML_ERROR_NOT_SUPPORTED" cases.  Per-card fan / power /
mem-info absence is now silent (the card genuinely doesn't support it)
instead of logging on every poll.

**Cross-OS log hygiene.** Same "professional code never says 'failed'
for normal absence" pass across the Windows, macOS, and BSD platform
adapters.  18 noisy log lines neutralized — `"X returned <ExceptionName>"`
instead of `"X failed"` / `"X not installed"`.

**Smoke harnesses added under `dev/`:**
- `smoke_metrics_chain.py` — 9 checks pinning every former
  `svc.all_metrics` / `get_all_metrics()` call site.
- `smoke_l1_unification.py` — verifies GUI panel + LCD overlay + LED
  engine all observe the same `HardwareMetrics` record.  Run this on
  the Windows VM to confirm the original L1 broadcast fix.
- `smoke_daemon_metrics.py` — boots a daemon, drives
  `TrccProxy.os.metrics` over IPC, asserts populated record.

**Lambda audit.** 69 lambdas removed from `src/trcc` (119 → 50).
Every callable in the runtime is now a named class method, function,
or `operator.itemgetter`-style accessor — Smalltalk-style discipline
applied to Qt signal slots (variable-cardinality button bindings now
use `setProperty` + `self.sender().property(key)`), benchmark
scaffolding, sort keys, and dataclass `default_factory`.  Remaining
50 are concentrated in GUI panels not yet swept; tracked for a
follow-up session.

## v9.6.2

User-visible fixes for the open issue backlog. Two parallel packaging
bugs and the daemon's missing metrics loop. Architecture work
landed in v9.6.1 stays the same — only the packaging seams and the
daemon's lifecycle call change.

**Daemon metrics loop wired (#148).** ``trccd.service`` users on
v9.5.7 / v9.6.0 / v9.6.1 saw the device discovered + IPC socket up
but the panel stayed blank — the daemon never started the per-process
metrics tick that drives ``device.tick()``.  Phase 9 moved
``start_metrics_loop`` into ``Trcc`` but the daemon's call site was
never updated. The lifecycle is now wired through a ``MetricsLoop``
port produced by ``Platform.build_metrics_loop()`` — fourth factory
in the OS → Protocol → Device → MetricsLoop chain. ``Trcc.discover()``
starts the loop after enumeration; ``Trcc.cleanup()`` stops it.
GUI/API/daemon all converge on the same composition.

**Cross-distro NVIDIA install hint (#134 + #145).** The doctor used
to hardcode ``pip install nvidia-ml-py`` which fails under PEP 668 on
Ubuntu 24.04 / Linux Mint 22. ``_INSTALL_MAP`` now has an entry for
``nvidia-ml-py`` covering apt / dnf / pacman / zypper / rpm-ostree;
``check_gpu()`` takes a ``pm`` parameter so the hint resolves to the
right native package name per distro. Arch packaging side: the
``PKGBUILD`` optdepend + ``release.yml`` Arch optdepend were renamed
``python-pynvml`` → ``python-nvidia-ml-py`` to match the upstream
PyPI name we pin (``nvidia-ml-py``).

**Windows libusb backend (#131).** Reporter on Windows 11 Python
3.12 pip-installed trcc-linux and hit
``usb.core.NoBackendError: No backend available`` — pyusb needs
``libusb-1.0.dll`` on PATH and pip-installed users don't get one.
The PyInstaller installer already bundles it via libusb-package;
pip-installed users now get the same wheel as a transitive dep:

  - ``pyproject.toml``: ``libusb-package>=1.0.26.3; sys_platform ==
    'win32' and python_version < '3.14'``. Version-gated because
    upstream wheels only go to cp313 today; the cap lifts when cp314
    ships.
  - New ``trcc/adapters/device/_pyusb_find.py`` is the single seam:
    uses ``libusb_package.find`` on Windows (drop-in wrapper around
    ``usb.core.find`` per pyocd's canonical pattern), falls back to
    ``usb.core.find`` everywhere else. 7 call sites switched.

Verified on Linux: ruff + pyright 0/0/0; ``smoke_sleep_cycle``
13/13; ``smoke_platforms`` 42/42; real GUI on SCSI 0402:3922 frames
flowing. Verified on Windows VM (Python 3.12): ``libusb-package``
installs as transitive dep, ``_pyusb_find`` logs
``pyusb backend: libusb-package (bundled libusb-1.0)``,
``python -m trcc detect`` succeeds without ``NoBackendError``.

## v9.6.1

Architecture refactor + Windows sensor strategy chain + per-OS smoke
harnesses. No user-facing behavior change for shipping users on
Linux / Windows / macOS / BSD — every gate green: ruff, pyright
0/0/0, smoke 42/42, pytest 5663 passed.

**Three-factory chain (foundation).** ``PlatformFactory`` /
``ProtocolFactory`` / ``DeviceFactory`` now share one idiom: ABC +
``@register`` self-registering subclasses + classmethod chokepoint.
``Device`` ABC (``core/device/base.py``) replaces the
``LCDDevice|LEDDevice`` union alias; ``protocol`` is DI'd at Device
construction via ``ProtocolFactory.for_info(info)`` lookup-by-name.
``addr → usb_address`` rename + ``DeviceInfo.from_detected`` DTO
chokepoint structurally close the #130/#131 dual-VID/PID bug class.
Uniform ``Platform.detect_devices()`` across all 4 OSes; smoke
``dev/smoke_platforms.py`` verifies 42 contract assertions.

**Windows sensor strategy chain.** ``WindowsSensorSource`` ABC with
``@register('key')`` self-registering subclasses, mirroring the
factory pattern. Three sources today, each with a DI'd I/O port:

  - ``HWiNFOSource`` (priority 10) — reads
    ``Global\HWiNFO_SENS_SM2`` MMF directly when HWiNFO64 is
    installed. ``_MappingPort`` ABC + two adapters (ctypes prod +
    in-memory for tests). Parser verifiable on Linux without a live
    MMF via fixture-based tests.
  - ``LHMSource`` (priority 20) — bundled LHM subprocess +
    ``root\LibreHardwareMonitor`` WMI namespace.
  - ``MSAcpiSource`` (priority 30) — last-resort native ACPI
    thermal zones via ``root\wmi``.

OS-isolated: concrete sources only import on Windows. Fixture-based
parser tests under ``tests/fixtures/hwinfo/``;
``dev/dump_hwinfo_shm.py`` lets contributors submit MMF dumps.

**Per-OS runtime smoke harnesses.** Four new scripts under
``dev/``: ``smoke_linux.py`` / ``smoke_windows.py`` /
``smoke_macos.py`` / ``smoke_bsd.py``. Each runs on its target OS,
exercises the real ``PlatformFactory.current()`` →
``detect_devices()`` → enumerator → ``read_all()`` chain, prints a
paste-ready ``[OK]/[WARN]/[SKIP]/[FAIL]`` report. Hardware-optional
(probes that need the LCD report SKIP). Self-locating: wrong-OS
exits cleanly.

**Documentation.** ``claude21.md`` codifies the OS smoke-testing
law (harness map, verification gates per change shape).
``doc/SENSOR_RESEARCH.md`` is the standing reference for what each
supported OS exposes, by which API, with what permissions, with
≥2 citations per fact and a verdict matrix per OS × metric.

**Windows log-file robustness.** ``RotatingFileHandler`` opens log
file with ``encoding='utf-8'``, ``errors='replace'`` — closes the
residual cp1252 file-write crash window the console-side
reconfigure didn't cover.

**Style.** New sensor source code uses explicit ternary + explicit
comparisons (``X if cond else Y`` over ``X or Y``; ``== 0`` over
bare truthy on ints; ``!= ''`` over bare truthy on strings).

## v9.6.0

Windows runtime: bundle LibreHardwareMonitor + silence log-rotation noise.

The Windows installer was shipping without the LibreHardwareMonitor
wrapper (``HardwareMonitor`` on PyPI), so anything not covered by
``psutil`` / ``wmi`` / ``pynvml`` came back blank — CPU temperature,
GPU temperature on AMD/Intel, fan RPM, motherboard sensors.  The GUI
sidebar still showed CPU% (psutil), but theme overlays on the LCD
device that referenced any LHM-only sensor rendered as empty space.
Result: device displayed the theme image with no metric text drawn
on it.

Fix is upstream of the runtime: ``pyproject.toml`` ``[windows]`` extra
now declares ``HardwareMonitor>=1.1.0``, ``pythonnet>=3.0.3``, and
``pywin32>=306``; ``windows.yml`` adds ``--hidden-import`` /
``--collect-all`` for ``HardwareMonitor`` and ``clr_loader`` to both
the CLI and GUI PyInstaller invocations; the bundle verification step
now fails the build if ``LibreHardwareMonitorLib.dll`` isn't present
in the dist tree.  Linux / macOS / BSD packaging is unchanged — those
distros use ``hwmon`` / ``IOKit`` / ``sysctl`` natively.

Second fix bundled here: ``trcc report`` (or any CLI invocation) run
while the GUI is open was printing ``--- Logging error ---`` walls
from ``RotatingFileHandler.doRollover`` — Python's stdlib handler
can't rename a file that's open in another process on Windows
(``WinError 32``), and the default error path prints the entire
traceback to stderr *and* drops the log record.  ``__main__.py`` now
swaps in a ``_SafeRotatingFileHandler`` subclass on win32 only that
swallows ``PermissionError`` / ``OSError`` from rotation; the record
still lands in the (un-rotated) file, no traceback escapes.  Linux /
macOS still use the vanilla stdlib handler — they don't have this
problem.

Closes the regression behind the Win11-VM "GUI is dark, device shows
no metrics" report.

## v9.5.12

Windows installer asset fix.

The PyInstaller spec in ``windows.yml`` only bundled
``src/trcc/assets`` (icons / fonts / .desktop / polkit policy) — the
~600 GUI PNGs at ``src/trcc/ui/gui/assets/`` (sidebar, tabs, theme
panels, preview frames, control buttons) were never copied into the
Windows ``.exe``.  ``_copy_assets_to_user_dir`` silently no-op'd
because the bundled package directory didn't exist, ``Assets.get()``
returned ``None`` for every key, and the GUI rendered with empty
stylesheets — a dark window with no graphics.

The macOS workflow already had the second ``--add-data`` line for the
GUI assets tree.  Windows just never got it.  Added it to both the CLI
and GUI ``pyinstaller`` invocations; mirrors macOS exactly, no app code
changes.

Linux is unaffected — distribution packages (RPM / DEB / Arch) install
the assets directly from the wheel layout.

## v9.5.11

Fast follow-up on v9.5.10.

v9.5.10 stopped the crash on every Linux launch (the suspend hook was
calling ``QDBusConnection.connect`` with a Python callable, which
PySide6 rejects with a TypeError) — but my defensive bytes-form slot
``b'1handle(bool)'`` was *also* rejected at runtime ("wrong argument
values"), so the hook silently failed.  Tee86's #144 fix was effectively
inert on v9.5.10 even though the launcher worked.

Empirical bisect on PySide6 6.10.3: the binding's declared
``slot: bytes | bytearray | memoryview`` is wrong — only the ``str``
returned by ``PySide6.QtCore.SLOT('handle(bool)')`` is accepted at
runtime.  Switched to that and verified end-to-end:
``subscribe_power: PrepareForSleep listener active`` now logs against
the live system bus.

This is the version that actually ships the #144 sleep/resume fix
working as designed.

## v9.5.10

### User-facing fixes

- **trcc gui crashes on launch on every Linux v9.5.9 install.** v9.5.9's
  `subscribe_power` (the #144 Tee86 post-suspend hook) called PySide6's
  `QDBusConnection.connect()` with a Python callable, but PySide6 only
  binds D-Bus signals to a `QObject` receiver paired with a SLOT-bytes
  string. Every `trcc` invocation hit
  `TypeError: 'PySide6.QtDBus.QDBusConnection.connect' called with wrong
  argument types: (str, str, str, str, str, function)` at
  `Trcc.__init__` and exited 1 — meaning the desktop entry / `trcc gui`
  / `trcc anything` all silently failed.  Fix introduces a tiny
  `_PrepareForSleepBridge(QObject)` with a `@Slot(bool)` handler held by
  reference on `LinuxPlatform`, plus a defensive try/except around
  `bus.connect` so any future PySide6 API tweak degrades to "suspend
  hook inactive" instead of crashing the launcher.
- **#136 questist — `trcc video` → `range() arg 3 must not be zero`.** When
  the device handshake didn't extract PM/SUB the resolution collapsed to
  a zero dimension and reached `VideoDecoder` unchecked, producing the
  cryptic range() error from the decode loop. `_decode` now raises a
  clear `ValueError` at entry naming the actual problem (handshake
  didn't determine the resolution) and points at `trcc report`.
- **#131 lallemandgianni — `'DeviceInfo' has no attribute 'addr'` →
  `wmi.x_wmi_uninitialised_thread`.** The addr-threading half was fixed
  in v9.5.2; v9.5.10 closes the second half. Worker-thread WMI calls
  (USB enumeration, sensor discovery) crashed because
  `pythoncom.CoInitialize()` was never called. New
  `_windows_wmi.wmi_handle()` helper centralizes the discipline; all 5
  WMI call sites in `windows_platform.py` + `windows/detector.py` route
  through it.

### Tooling for contributors

- **`dev/smoke_anything.py` — parameterized OS / device / from-report
  harness.** Drop a `trcc report` dump from a GitHub issue into the
  `--from-report` flag and the harness runs every probe through the
  fully DI'd stack at the reporter's exact OS + VID:PID. Each probe is
  a real-bug class we've already paid for; if any goes `BAD`, that's
  the bug. Probes today cover video target dims, addr-threading, RAPL
  permission, geometry stability, handshake idempotency, sleep/resume
  cycle, WMI CoInitialize.

### Internal

- `slots=True` on `DeviceInfo` and `LEDState` (Kite shape — hot
  containers, no `__dict__` needed).
- Documentation refresh: src/ tree under README/CLAUDE shows current
  ui/{gui,cli,api} layout; CLI/API counts updated everywhere
  (49→78 endpoints, 56/58/60→95 commands); 65 untagged code blocks
  across `doc/` now carry language hints; `README` Documentation table
  fixes a dead link to a doc/audit/ folder that never existed.

## v9.5.9

Fast follow-up — v9.5.8 packages didn't build because release CI runs
`ruff check .` (full repo), not `ruff check src/ tests/`.  The new
banned-api lint rule (TID251) and the new sleep cycle smoke (`dev/smoke_sleep_cycle.py`)
both tripped on the dev/ side (legitimate adapter access for fixtures
+ standard sys.path.insert pattern).  Added a `dev/**/*.py`
per-file-ignore mirroring the tests/ shape so CI lints clean.

Same content as v9.5.8 — fixes #144 Tee86 post-suspend, sealed UI
seam, Pythonic Platform/Trcc surface, resolution alias merge,
ControllerBuilder cleanup.  Just with packages that actually build
this time.

## v9.5.8

### User-facing fixes
- **#144 Tee86 — LCD stays black after Linux suspend/resume.** The OS suspend cycle dropped USB power on the panel, but the app held a cached SCSI fd whose underlying USB device had silently re-enumerated; subsequent writes went into a closed handle and the firmware never saw a fresh handshake. v9.5.8 hooks systemd-logind's `PrepareForSleep` D-Bus signal at the Platform layer (was previously GUI-only), tears down every device's transport cleanly before the suspend, and re-runs full discovery on resume. Suspend, wake, the LCD comes back without a reboot. CLI long-running commands (`trcc serve`) and the API daemon now also survive suspend/resume — was previously GUI-only.

### Architecture
- **Sealed UI ↔ adapters seam** — `src/trcc/ui/` can no longer import from `trcc.adapters.device.*` (enforced by `flake8-tidy-imports` banned-api lint). Every device action travels through `trcc.lcd` / `trcc.led` / `trcc.control_center` / `trcc.probe` / `trcc.handshake` / `trcc.cleanup`. Future regressions become a CI failure, not a code-review oversight. Six pre-existing UI bypass sites deleted (cli probe, cli system, cli perf, api perf, gui handshake worker, gui close-event).
- **Pythonic `Platform` and `Trcc` surface.** `Platform.for_current_os()` classmethod replaces module-level `make_platform()`. `for d in platform` enumerates USB. `platform.sensors` is a `@cached_property` instead of `create_sensor_enumerator()`. `(0x0402, 0x3922) in trcc` and `trcc[(vid, pid)]` work as you'd expect. Composition reads like English.
- **Resolution alias merge — 5 names → 1.** `output_resolution` / `canvas_resolution` / `effective_resolution` / `_rotated_resolution` all collapsed into `canvas_size` on `DisplayService`; `LCDDevice` lost the three corresponding delegates. Native dims stay on `lcd_size`. The pattern that produced #137 (FW360 Ultra upside-down) was alias drift — fixed structurally so the bug class can't return.
- **`SYSTEM_SUSPENDED` / `SYSTEM_RESUMED` events** on the EventBus — UIs subscribe to flush their own resources on suspend (GUI screencast stops here). Verified end-to-end by `dev/smoke_sleep_cycle.py` (13 assertions on suspend → cleanup → discover → resume order).
- **`ControllerBuilder` cleanup** — deleted 5 thin one-line wrappers (`build_ensure_data_fn`, `build_download_fns`, `build_detect_fn`, `build_device_svc`, `build_hardware_fns`); callers go straight to source. The two real composers (`build_device`, `build_system`) stay.

## v9.5.7

### User-facing fixes
- **GUI no longer crashes on launch** (regression from v9.5.4). The `aboutToQuit.connect(t.suspend_all_devices)` line introduced for #143 tried to weakref a bound method of `Trcc`, but `Trcc` has `__slots__` without `__weakref__`, so Qt failed with `TypeError: cannot create weak reference to 'Trcc' object`. Every user on v9.5.4 / 5 / 6 had a broken GUI. v9.5.7 deletes the entire mechanism (see below) and the broken connect line goes with it.
- **#143 LCD-sleep-on-shutdown re-fixed properly.** v9.5.4 wrote sysfs power attributes from userspace, which silently failed for every unprivileged user (the actual install path for everyone) — the panel kept showing "USB communication lost" indefinitely until VBUS dropped, exactly the bug we claimed to fix. v9.5.7 reverts the userspace mechanism and replaces it with a one-line udev rule change: `power/autosuspend_delay_ms=10000` instead of `power/autosuspend=-1`. The kernel now autosuspends the device ~10s after our process exits — same end state as Windows selective suspend, fully kernel-native, no shutdown hook needed. Re-run `trcc setup-udev` (or fresh-install the .deb / .rpm / .pkg.tar.zst) to apply.
- **Brightness now consistently dims overlay text on animated themes** (parity with static themes). The video-cache path composited the live text overlay on top of an already-dimmed bg+mask surface, leaving the metrics text at full brightness. Static themes baked the overlay first then dimmed everything together, so they always behaved correctly. v9.5.7 dims the cached text overlay alongside the L3 surface, so bg + mask + text all share the same brightness on every render path.
- **`trcc report` now shows real SCSI handshake data.** The report path skipped `ControllerBuilder` (where `set_scsi_transport` is normally wired), so SCSI users got `SCSI transport factory not injected` instead of FBL / resolution / raw response bytes. v9.5.7 wires the transport in `report()` before `DebugReport.collect()` runs.
- **Theme cloud downloads no longer race on the temp file.** Concurrent downloads of the same theme used to share a single `<theme>.tmp` path — first thread to rename won, second thread errored with `[Errno 2] No such file or directory`. v9.5.7 uses `mkstemp` so each download gets a unique temp; `os.replace` atomically commits the result. No more spurious download errors when multi-mirror retry kicks in.
- **GUI no longer crashes mid-render on theme reload.** `update_video_cache_text` and four other display sites had a TOCTOU race on `self._cache`: line N checked `if self._cache and self._cache.active`, line N+1 called `self._cache.method(...)`, but a concurrent thread could null `self._cache` between the check and the call (Python attribute reads aren't atomic across two ops). v9.5.7 snapshots the cache reference into a local before each block. Affected the metrics tick, brightness change, rotation change, and video-tick render paths.

### Architecture
- **v9.5.4 #143 mechanism deleted.** `Platform.suspend_usb_device` ABC method, `LinuxPlatform.suspend_usb_device` (sysfs walker), `WindowsPlatform.suspend_usb_device`, `Trcc.suspend_all_devices`, the `trcc sleep` CLI command, the `POST /trcc/sleep` API endpoint, the `qapp.aboutToQuit.connect(...)` GUI hook, the `ExecStop=` in `trccd.service`, and the 5+2 associated tests — all gone. -122 net lines. The kernel handles panel-sleep via the udev rule change above; userspace doesn't need to know about USB suspend.
- **Quieter Qt log output.** Extended `QT_LOGGING_RULES` to silence `qt.qpa.theme.gnome` warnings about `xdg-desktop-portal-gnome` failing to start — these were noise on every non-GNOME / portal-less session and weren't actionable for users. `setdefault` so anyone debugging can still override.
- **Honest `Co-Authored-By` for JoshWrites's atomic-write pattern.** The v9.5.6 trailer was malformed (`Co-Authored-By: JoshWrites` without an email), so GitHub didn't link the contribution to his profile. Empty follow-up commit `e2d49b8c` re-emitted the trailer with the correct noreply email so the credit lands.

## v9.5.6

### User-facing fixes
- **`trcc setup-udev`, `trcc setup-selinux`, `trcc setup-polkit`, `trcc setup-winusb` now exist** (#139 Zombie-hive, Pop!_OS pipx). The doctor + factory + diagnostics messages told users to run these commands in 22 places combined, but only the internal sudo-reexec dispatch table knew about them — none were registered as Typer commands, so `trcc setup-udev` returned `Error: No such command 'setup-udev'`. Now wired:
  - `setup-udev` — installs udev rules + usb-storage quirks (Linux); on Windows aliases to the WinUSB Zadig guide.
  - `setup-winusb` — same call as `setup-udev`, OS-natural name for Windows.
  - `setup-selinux` — installs the SELinux policy module (Fedora/RHEL/CentOS).
  - `setup-polkit` — installs the polkit policy for password-prompted privileged ops.

### Architecture
- **Regression test for suggested-command drift** — `TestCLISuggestedCommandsExist` AST-walks `src/trcc/`, regex-extracts every `'trcc <hyphenated-cmd>'` from string literals, and asserts each is in `app.registered_commands`. Closes the gap that let `setup-udev` and 3 siblings live in user-facing messages without a Typer entry. Catches the same drift class for any future hyphenated command.
- **Two stale-string drive-bys** spotted during the audit: a docstring in `core/trcc.py` referenced `trcc lcd test` (the actual command is `trcc test`); a print in `ui/cli/_display.py` told users to "use 'trcc led' commands" when the family is `led-*` (led-brightness, led-color, led-mode, …).

## v9.5.5

PyPI + distro re-release of v9.5.4 — original v9.5.4 hit PyPI's per-project
10 GB storage cap mid-publish, which cascaded and skipped the RPM / DEB /
Arch build jobs. Freed space on PyPI side and re-tagged. **Same content
as v9.5.4** — no source changes. Use this version for every install path.

## v9.5.4

### User-facing fixes
- **LCD panel sleeps cleanly on shutdown / app close** (#143 k1w3l, Elite Vision 360 SCSI). After the app exited, the panel stayed lit showing "USB communication lost" until VBUS dropped — Windows TRCC sleeps the panel, Linux didn't. Audit of the C# v2.1.4 source confirmed Windows sends nothing at shutdown; its USB stack just selective-suspends the device. Linux can't do the same because our udev rule pins `power/autosuspend=-1` to keep frames flowing. The fix: a new `trcc sleep` chokepoint that re-enables autosuspend (writes `auto` → `power/control`) and unconfigures each matching USB device (writes `0` → `bConfigurationValue`) so the firmware sees a clean detach and powers down the panel. Wired through the GUI's `aboutToQuit` (catches close-button), the systemd service's `ExecStop=` (catches reboot/shutdown), and exposed as `trcc sleep` / `POST /trcc/sleep` for any remaining flows.

### Architecture
- **`Platform.suspend_usb_device(vid, pid) -> bool`** — new capability port. Linux walks `/sys/bus/usb/devices`, matches on VID:PID (covers SCSI which has no `UsbAddress` and the multi-LCD case where two physical devices share VID:PID), writes the sysfs nodes, returns `True` on at least one success. Windows returns `True` no-op (USB stack handles it). macOS / BSD default to `False` until a reporter surfaces the need.
- **`Trcc.suspend_all_devices() -> OpResult`** — single chokepoint for app shutdown. Collects unique `(vid, pid)` across LCD+LED registries, runs `cleanup()` and `DeviceProtocolFactory.close_all()` to release transports BEFORE writing `bConfigurationValue=0` (avoids EBUSY), then asks the platform to suspend each pair. Idempotent.

## v9.5.3

### User-facing fixes
- **Square-device rotation works live, no restart** (#137 satoru8 FW360 Ultra; closes the same bug class for every square panel — Frozen Warframe Pro 320×320, GrandVision 360 480×480, Mjolnir 480×480, FW360 Ultra 480×480). Three independent issues collided to mask each other: (1) `VideoFrameCache.encoding_params` cached `encode_angle` at build time so rotation changes never reached the device until restart; (2) `_apply_adjustments` and `encode_for_device` BOTH applied the user's rotation, so they cancelled — square devices silently ignored every rotation setting; (3) FW360 Ultra has a hardware-mounted 180° baseline that the FBL=72 profile didn't encode. Cache no longer stores encode_angle (read fresh per tick), encode is the sole rotator (matches C# `ImageToJpg`), and `DeviceProfile.encode_pm_bases` adds PM-keyed baselines (FBL=72 PM=6 → 180°). Now a rotation knob actually rotates.
- **GUI no longer crashes on RAPL permission denied** (#139 Zombie-hive). `_discover_rapl` did `Path.exists()` on `/sys/class/powercap/intel-rapl:*/energy_uj`; on pipx installs without `trcc setup-rapl` run, the file isn't world-readable and `Path.exists()` raises `PermissionError`, aborting the whole GUI launch. Wrapped both the directory glob and per-domain `exists()` check in OSError guards. RAPL discovery silently skips when perms aren't there; `trcc setup-rapl` still fixes the perms permanently if you want the readings.
- **Localized weekday abbreviations** (#141 ronny79privat). The LCD overlay's weekday label was hardcoded English; only Chinese had a parallel list. Added per-language tables for German (`MO`/`DI`/`MI`/`DO`/`FR`/`SA`/`SO`), French, Spanish, Portuguese, Russian, Japanese, and Korean — keyed by ISO 639-1 code, follows the existing TRCC language setting. Live update on language change, no restart.
- **Non-square rotated devices write correct dirs to config** (audit). `LCDDevice._persist_dirs` rebuilt `theme_dir`/`web_dir`/`masks_dir` paths from `Orientation.native` ignoring rotation, while the running app served portrait-variant dirs through `Orientation.theme_dir`/`web_dir`/`masks_dir`. Restart-from-config loaded the wrong content for non-square users started rotated. Now reads from `Orientation` directly — single source of truth.
- **PM=0 devices keep their handshake byte** (audit). `_discover_resolution` had `if pm: dev.pm_byte = pm` — silently dropped PM=0 (a legitimate value in the button-image table) into the default code path. Now copies unconditionally.

### Architecture
- **Encode cache key includes rotation angle** — `DeviceService._last_encode_id` (just `id(image)`) renamed `_last_encode_key` and gains the angle: `(id(image), encode_angle)`. Same source surface sent at a new angle now triggers a re-encode instead of returning stale-rotation bytes. Same shape as the cache-stale fix above; this closes the equivalent path in `send_frame` / `send_frame_async` for any caller that bypasses the device chokepoint.
- **One source of truth for "is rotated"** — `Orientation.image_rotation_for(overlay_w, overlay_h)` is now the single predicate; `DisplayService._image_rotation` delegates to it instead of re-implementing with its own portrait-overlay check. Closes the drift surface that produced the rotation bug class.
- **Topic.FRAME on every state change** — LCDDevice publishes `Topic.FRAME(device_path, image)` after `set_brightness`/`set_rotation`/`set_split_mode`, not just on the metrics tick. UIs (GUI preview, API WebSocket, IPC daemon clients) observe what the device sees without polling.

### Maintainer-facing
- **Test suite green again**: 17 stale fixture failures from the Phase 9 + 10A migration cleaned up (adapter exception types, MockProtocol method names, GUI handler observer-only contract, BSD/macOS platform skip markers, 0xDD parser positive-test rewrite). 5,646 passing, 0 failing on `main`.
- Phase 11.6 broad-except narrowing previously landed (~150 sites narrowed from `except Exception` to documented per-API exception tuples). With the Phase 9+10A+11.6 stack now stable on `main`, the v10.0.0 major bump (Phase 9 deleted public symbols `TrccApp`, `find_active`, `InstanceKind`, `DeviceProxy` and friends) is the next milestone — gated on hardware donor confirmation across HID/Bulk/LY/LED protocols.

## v9.5.2

PyPI re-release of v9.5.1 — original v9.5.1 hit PyPI's per-project 10 GB
storage cap on upload (older release artifacts accumulated). Freed space
on PyPI side and re-tagged. **Same content as v9.5.1** — no source
changes. Use this version for `pip install --upgrade trcc-linux`; v9.5.1
GitHub-only artifacts still work for distro/manual installs.

## v9.5.1

### User-facing fixes
- **Bulk / HID / LY / LED handshake on every non-SCSI device fixed** (#131, #132, #133): the v9.5.0 multi-device refactor threaded `addr: UsbAddress | None` through every USB transport but missed adding the field to `DeviceInfo` itself. The factory's bulk/hid/ly/led lambdas read `di.addr` and raised `AttributeError`, caught silently in `services/device.py:_discover_resolution` and surfaced as "Resolution discovery failed" + LCD-stays-black on Windows. Every Bulk (`87AD:70DB`), HID Type 2 (`0416:5302`), HID Type 3 (`0418:5303/5304`), LY (`0416:5408/5409`), and LED (`0416:8001`) device on v9.5.0 hit this. The fix is one field on `DeviceInfo` plus a copy in `from_detected`. Regression coverage: parametrized over every entry in `ALL_DEVICES` (10 devices × 2 invariants), proves the addr survives DTO copy and the factory dispatch.
- **AMD GPU detection on Windows** (#131): users running AMD GPUs on Windows without LibreHardwareMonitor saw "No GPUs detected" — `pynvml` is NVIDIA-only, and we had no AMD path. v9.5.1 adds a `Win32_VideoController` WMI fallback covering every video controller Windows knows about. Sensor data (temp/load/power) still requires LHM or future ADLX integration; this layer adds detection so the card appears in `trcc gpus` for selection. 10 unit tests with mocked `Win32_VideoController` cover the conversion (reporter's exact card, multi-GPU ordering, ghost-controller skip, package-missing, COM-init failure, fallback ordering).
- **API `/devices/{id}/select` 404 fixed** (regression-as-side-effect): the endpoint queried `_device_svc.devices` which was never populated in the API flow — silently broken since the Trcc facade landed. `api/devices.py` rewritten to read the device list from Trcc directly, which fixes the lookup and removes a redundant module-level service.

### Architecture (paving for v9.6)
- **`make_platform()`** in `adapters/system/__init__.py` is the single OS-detection chokepoint. Honors `TRCC_MOCK=1` and `TRCC_MOCK=path.json`. Both `Trcc.for_current_os()` and `ControllerBuilder.for_current_os()` route through it — TRCC_MOCK propagates everywhere automatically without per-site env checks.
- **`for_current_os()` runtime calls collapsed from 22+ to 1**: only the legitimate composition entry in `cli/__init__.py:main()` remains. Adapter-infra layer (diagnostics × 6, data_repository, install/gui), CLI command stragglers, and the public `detect_devices()` library API all route through `make_platform()`.
- **TrccApp composes Trcc internally**: `TrccApp.__init__` builds `self._trcc = Trcc(builder.os)`; `scan()` populates BOTH the legacy `_devices: dict[str, Device]` AND the inner `_trcc._lcd_devices`/`_led_devices` lists. `_boot.trcc()` returns `TrccApp._instance._trcc` when TrccApp is initialised — single source of truth for connected devices, no more parallel registries.
- **Pythonic Trcc**: `__slots__` (no per-instance `__dict__`, ~40% smaller, faster attribute access), container protocol (`__iter__`/`__len__`/`__bool__`), context manager (`__enter__`/`__exit__` → `cleanup()`), and read-only `os`/`lcd_device`/`led_device`/`lcd_devices`/`led_devices`/`has_lcd`/`has_led` properties (tuple returns for immutability).
- **CLI consumer cleanup**: 12+ sites moved off `TrccApp.get()` to `_boot.trcc()` — `i18n`, `_system.setup`, all of `_theme` (load/save/import), `_display` (test/screencast/render_overlay/play_video), and `__init__.py` (gpu-list/set, test_led/lcd, show_info, resume, render). 8 sites moved off `ControllerBuilder.for_current_os()` to `_boot.trcc().os` or `make_platform()`. `_ensure_system()` self-resolves when called argless.
- **Boot fns DI**: `_boot.trcc(platform=None)` and `_boot.get_trcc(platform=None)` accept an optional `Platform` for explicit injection — clean DI for tests and the dev/mock_* scripts.
- **dev/mock_*.py drop the `ControllerBuilder.for_current_os = classmethod(...)` monkey-patch entirely**: pure DI via `_boot.trcc(platform)` / `_boot.get_trcc(platform)` after the bootstrap returns MockPlatform. `_mock_bootstrap.py` adds the repo root to `sys.path` so `tests.mock_platform` resolves consistently across pytest/dev/prod contexts (single MockPlatform module identity for `isinstance` checks).

### Maintainer-facing
- **Eager module-level `_device_svc = DeviceService(...)`** deleted from `api/__init__.py`. API endpoint state lives where FastAPI expects it now.

### Issues this likely closes after reporter retest
- #131 — addr error fixed; AMD GPU now detected via WMI (sensor data still needs LHM, tracked separately for v9.6.x); Bug B (`ValueError: I/O operation on closed file`) and Bug D (LCD black on Windows) are cascade-of-A — should resolve with the addr fix.
- #132 — same addr cascade for HID 0418:5303 (Trofeo Vision).
- #133 — same addr fix; the explicit AttributeError reporter saw is gone.

What's pending in v9.5.x: GUI off `TrccApp.init()`, full `TrccApp` deletion, composition root in `__main__.py`, test fixture rewrite. These are the remaining ~5% of the architectural cleanup, scheduled for a focused session.

## v9.5.0

Milestone release rolling up the v9.4.8 → v9.4.15 patch series — every fix from those releases is included here, distilled into one tag for the people whose package manager wakes up once a week.

### User-facing fixes (every install)
- **Cloud theme downloads work again**: deduped the `czhorde.bj` slug from the GitHub URL (was producing 404s on every cloud theme/mask download), tries every configured mirror in order with first-win short-circuit, and the click-to-download path no longer gets gated on a stale in-flight flag.
- **Multi-device support actually drives both coolers**: two cases. First, the detector now uses `usb.core.find(find_all=True)` and emits a `DetectedDevice` per physical USB so two same-VID/PID coolers (e.g. Frozen Warframe 240 + Trofeo Vision 6.86", both `0416:5302`) are both discovered. Second — and this is the one that actually mattered — every USB transport (`PyUsbTransport`, `HidApiTransport`, the macOS/BSD `UsbBotScsiTransport`, plus `open_usb_device` for Bulk/LY) now binds to the specific `(bus, address)` the detector observed via pyusb's `custom_match`. Without that second half, both protocol instances opened the same physical USB and frames sent to "device 2" landed on device 1.
- **Headless `led-mode temp_linked` / `load_linked` no longer reads zeros**: on a headless box with no GUI, the CLI never started a metrics loop, so sensor-linked LED modes computed colors against a freshly-zeroed `HardwareMetrics`. The CLI composition root now bootstraps `SystemService` once and seeds every connected device with current metrics before the command runs.
- **Legacy GUI taskbar icon path corrected** so the icon shows on first launch.
- **LCD config tolerant of `next/`-shape `config.json`** when users opt back into the legacy build after trying `TRCC_NEXT=1`.

### Maintainer-facing
- **Stale-workflow only fires on `awaiting-reporter`-labeled issues** (14-day stale, 7-day grace). Previously closed any inactive issue at 5 days regardless of who owed the response — issue #128 hit that the same day we shipped its fix.

### Architecture (refactors that paved the way)
- **`Trcc` is a container** — `__iter__` yields every connected LCD then LED, `__len__` returns total. Replaces `_lcd_devices` / `_led_devices` poking at call sites.
- **`UsbAddress` model** in `core/models/device.py` — frozen-slots dataclass that parses `usb:bus:address` strings, matches a pyusb device via `(bus, address)`, stringifies cleanly. `DetectedDevice.addr` exposes it as a property derived from `usb_path`. Threaded through `UsbProtocol`, `_BulkLikeProtocol`, `BulkFrameDevice`, and the SCSI transport factory so every USB-class adapter binds by physical address rather than VID:PID alone.

### Experimental `TRCC_NEXT=1` build (opt-in clean rebuild)
v9.5.0 is the first release where `next/` has feature parity for the things real users notice on a fresh install:
- 11 LED segment-display renderers (AX120, PA120, AK120, LC1, LF8, LF12, LF10, CZ1, LC2, LF11, LF15) ported verbatim — `LegacyMetricsView.__getattr__` translates next/'s `dict[str, SensorReading]` into the legacy attribute shape so the segment math copies across without edits.
- Cloud-theme `0xDD` DC parser — verified against 5 real `config1.dc` files; HARDWARE / CUSTOM elements decode cleanly, TIME / WEEKDAY / DATE emit placeholder text for a follow-up dynamic renderer.
- Theme.zt animation decoder — `ZtDecoder` reads the JPEG-sequence archives the Windows app emits via `UCVideoCut.BmpToThemeFile`; `MediaService.load_video` dispatches by suffix.
- Real cross-OS setup wizards: FreeBSD writes `devd` rules and restarts devd; Windows enumerates devices and prints a per-device Zadig recipe; macOS diagnoses codesign / quarantine / privilege state and prints the matching fix command. Each wizard does what its OS actually allows — never more, never less, never silently.

`next/` is still opt-in via `TRCC_NEXT=1` or the `trcc-next` console script. Default behaviour unchanged for every shipping user.

## v9.4.15

### Added
- **`next/` setup wizards for FreeBSD, Windows, and macOS** (Phase B follow-up): each `Platform.setup()` previously logged a "not yet wired" warning. Each is now a real wizard tailored to the OS's actual permission model — researched against current platform documentation, written so the script can never silently break a working install.
  - **FreeBSD** (`_devd.py`): generates a complete `/usr/local/etc/devd/trcc.conf` with `chmod 0666` rules for every TRCC VID:PID, atomic write, then `service devd restart`. Re-execs via `sudo` or `doas` when called as a normal user. Skipped with a useful pointer on OpenBSD/NetBSD (no devd).
  - **Windows** (`_winusb.py`): read-only diagnostic — Windows can't silently install kernel drivers (UAC + signed driver package required). Enumerates connected TRCC devices via pyusb, splits them into "visible" vs "needs WinUSB", and prints a copy-paste Zadig recipe naming each missing device. Detects the libusb-1.0.dll-on-PATH failure case separately and points users at the libusb download.
  - **macOS** (`_macos_setup.py`): read-only diagnostic — CLI tools can't hold the `com.apple.vm.device-access` entitlement without a provisioning profile, per Apple's developer forums. Checks codesign status, the `com.apple.quarantine` xattr, and `geteuid()`; prints the matching fix for whichever combination is failing (Gatekeeper "Open Anyway", `xattr -d`, or re-run with `sudo`).
- Pure Python; no legacy or shipping behaviour changed; only the experimental `TRCC_NEXT=1` build's `system setup` command is affected.

## v9.4.14

### Added
- **`next/` `.zt` animation decoder** (Phase A5): `next/services/media.py` gains a `ZtDecoder` class that reads Thermalright's `Theme.zt` JPEG-sequence archives (the format `UCVideoCut.BmpToThemeFile` writes). Parses the `0xDC` magic, frame_count, per-frame absolute timestamps, and back-to-back JPEG payloads; uses one `ffmpeg jpeg_pipe` invocation per frame to scale to the requested resolution; derives fps from the average inter-frame delay. `MediaService.load_video` dispatches by suffix — `.zt` → `ZtDecoder`, anything else → `VideoDecoder`. Synthetic round-trip test confirms 2-frame red/blue archive decodes to correct pixels at correct fps. No legacy or shipping behaviour changed; this only fills a gap in the experimental `TRCC_NEXT=1` build.

## v9.4.13

### Added
- **`next/` cloud-theme `0xDD` parser** (Phase A6): `next/services/_dc_reader.py` previously raised `ThemeError` on the cloud-theme variant of the DC config format. Now handles 0xDD's variable-length element list, translates the `(main_count, sub_count)` HARDWARE pairs straight to next/-shape sensor IDs (`cpu:temp`, `gpu:primary:usage`, etc.), surfaces CUSTOM elements as text, and emits `{time}`/`{weekday}`/`{date}` placeholders for the dynamic clock elements. Verified against 5 real cloud-theme `config1.dc` files — every element decoded with correct position, font, and color. No legacy or shipping behaviour changed; this only fills a gap in the experimental `TRCC_NEXT=1` build.

## v9.4.12

### Added
- **`next/` segment-display port**: 11 LED styles (AX120, PA120, AK120, LC1, LF8, LF12, LF10, CZ1, LC2, LF11, LF15) ported verbatim from `core/led_segment.py` to `next/services/led_segment.py` (~700 LOC). Uses a `LegacyMetricsView` adapter that exposes next/'s `dict[str, SensorReading]` via legacy attribute names through `__getattr__`, so the segment math copies across without touching the compute layer. Pure Python — runs identically on Linux/Windows/macOS/BSD. No legacy or shipping behaviour changed; this only fills a gap in the experimental `TRCC_NEXT=1` build.

## v9.4.11

### Maintenance
- **Stale-workflow no longer auto-closes fresh bug reports**: previous config closed any issue with no activity for 5 days regardless of who owed the response, so user reports could close before the maintainer ever replied. Now stale only fires for issues explicitly labeled `awaiting-reporter`, with a 14-day stale window and a 7-day grace period before close. Repo-only change, no functional impact for users.

## v9.4.10

### Fixes
- **Multi-device support: actually drive both coolers** (#128, completes v9.4.9): v9.4.9 made the detector return both same-VID:PID devices, but the transport open path still bound to the first physical device — every send went to one cooler. Now every transport (`PyUsbTransport`, `HidApiTransport`, `UsbBotScsiTransport` for macOS/BSD, plus `open_usb_device` for Bulk/LY) binds to the specific `(bus, address)` the detector observed via pyusb's `custom_match`. Two same-ID coolers each get their own physical device.

### Refactors
- **`UsbAddress` model** in `core/models/device.py` — frozen-slots dataclass that parses `usb:bus:address` strings, matches a pyusb device by `(bus, address)`, and stringifies cleanly. `DetectedDevice.addr` exposes it as a property derived from `usb_path`. Threaded through `UsbProtocol`, `_BulkLikeProtocol`, `BulkFrameDevice`, and the SCSI transport factory so every USB-class adapter binds by physical address rather than VID:PID alone.

## v9.4.9

### Fixes
- **Two devices with the same USB ID, only one detected** (#128): the detector called `usb.core.find()` which returns the first match — users running two coolers with identical VID:PID (e.g. Frozen Warframe 240 + Trofeo Vision 6.86", both `0416:5302`) only saw one. Now uses `find_all=True` and emits a `DetectedDevice` per physical USB; `usb_path` (`usb:bus:address`) disambiguates downstream. Same fix for the macOS detector.
- **Headless `led-mode temp_linked` / `load_linked` had wrong colors** (#130): on a headless box the CLI never started a metrics loop, so sensor-linked LED modes ran against a zeroed `HardwareMetrics` snapshot. The CLI composition root now bootstraps `SystemService` once and seeds every connected device with the current metrics before the command runs.

### Refactors
- **`Trcc` is now a container**: added `__iter__` (yields every connected LCD then LED) and `__len__` so call sites can write `for device in trcc:` and `len(trcc)` instead of poking `_lcd_devices` / `_led_devices` directly. `cleanup()` already migrated.

## v9.4.8

### Fixes
- **Cloud theme downloads silently failing**: `czhorde.bj` slug was being appended twice to the GitHub URL, producing 404s on every cloud theme/mask download. Now built once, correctly.
- **Cloud theme falls back across mirrors**: download tries every configured server in order (`czhorde.com`, then `czhorde.cc`) and returns the first success — single-server outages no longer break theme downloads.
- **Cloud theme/mask clicks no longer gated by in-flight state**: clicking a cloud item always triggers a download, never short-circuited by a stale `_downloading` flag.
- **Legacy GUI taskbar icon path**: corrected the resolution path so the taskbar icon shows on first launch.
- **LCD config tolerant of legacy ↔ next/ migration shapes**: `config.json` written by the experimental `trcc-next` is now safely read by the legacy GUI (list-shape config no longer crashes load).

### Added
- **Experimental clean-slate architecture** (`trcc.next`) available as an opt-in via `trcc-next <subcommand>` or `TRCC_NEXT=1 trcc <subcommand>`. Default path unchanged — legacy users see zero difference. See `memory/project_next_clean_slate.md` for scope; feature parity still in progress.

## v9.4.7

### Fixes
- **LED device blank after sleep/resume** (#108): LED protocol had no retry-with-reconnect — after sleep, USB handles go stale and sends fail silently forever. Now matches the Bulk/LY pattern: on first USB error, close transport, re-handshake, retry once. Self-heals regardless of DBus sleep monitor (fixes distrobox/container environments).
- **macOS theme downloads fail with SSL error** (#109): `data_repository.py` already used `certifi` for the Mozilla CA bundle, but `certifi` wasn't in `pyproject.toml` dependencies. On macOS where Python can't find system certs, downloads silently failed with `SSL_CERTIFICATE_VERIFY_FAILED`.

## v9.4.6

### Fixes
- **SELinux policy missing SCSI device coverage**: `trcc_usb.te` only granted access to `usb_device_t` (`/dev/hidraw`, `/dev/bus/usb`). SCSI LCD devices (`/dev/sg*`) use `scsi_generic_device_t` in Fedora's SELinux policy — users with 0402:3922, 0416:5406, 87cd:70db on SELinux enforcing were silently unprotected. Users who previously ran `setup-selinux` need to run it again.
- **Dev-mode first-run setup crash**: `sudo_reexec` computed wrong PYTHONPATH — `dirname` called 3 times instead of 5, resolving to `src/trcc/adapters/` instead of `src/`. Only affected dev installs (`PYTHONPATH=src`), not pip/RPM.

### Refactors
- **Eliminate config fsync thrashing**: New `Settings.save_device_settings(key, **updates)` batches multiple config writes into one load + save + fsync. Theme click: 3 fsyncs → 1. Mask apply: 2 → 1. Handshake: 2 → 1.
- **Fix double-save for rotation and split_mode**: Both were persisted twice per change — once via `Device._persist()` and again directly in `lcd_handler`. Now matches brightness pattern (single owner).
- **Delete dead code**: Vestigial `Settings._rotation`/`.rotation`/`.set_rotation()` (zero callers), Device self-aliases (`self.theme/frame/video/overlay/settings = self`, only 2 of 5 used), dead re-export files (`services/renderer.py`, `adapters/device/led_segment.py`), dead methods (`is_overlay_enabled`, `set_overlay_temp_unit`).
- **Replace dirname chains with pathlib**: All nested `os.path.dirname()` chains replaced with `Path(__file__).resolve().parents[N]`.
- **Deduplicate path constants**: `data_repository.py` redefined `_TRCC_PKG` as `_THIS_DIR` instead of importing from `core/paths.py`. Dead `SRC_DIR` removed.

## v9.4.5

### Fixes
- **setup-gui freeze on macOS** (#115): Window opened behind Terminal with no `^C` support. Added SIGINT handler + `raise_()`/`activateWindow()` for window visibility. Made checks platform-aware via `DoctorPlatformConfig` — skips udev/SELinux/desktop entry on macOS.
- **GPU shows 0 on LED after autostart** (#110): `pynvml.nvmlInit()` ran once at module import — if NVIDIA driver wasn't loaded yet (common on boot), GPU sensors were permanently unavailable. Now lazy-inits via `_ensure_nvml()` with retry each poll cycle. `_ensure_nvidia_ready()` handles late discovery + mapping invalidation.
- **Second device never connects in multi-device GUI** (#101): `DeviceInfo.from_detected()` hardcoded `hid:VID:PID` path format, but `scan_and_select()` received `usb:BUS:ADDR` from the detector — mismatch meant every device fell back to the first one. Now uses `DetectedDevice.path` directly.
- **macOS Tahoe sensor reads broken** (#109): `powermetrics --samplers smc` removed in macOS Tahoe, breaking all CPU temp, GPU temp, and fan reads on Apple Silicon. Replaced with direct IOKit SMC reads via ctypes. Expanded SMC key table covers Intel + Apple Silicon (M1-M5) with trial-and-error discovery. `powermetrics --samplers gpu_power` kept for GPU active residency/power/clock.

## v9.4.4

### Features
- **GPU selection in control center**: Users with multiple GPUs can now choose which one drives all `gpu_*` metrics (temp, usage, clock, power) across LCD overlays and LED segments. 1 GPU shows plain text, 2+ GPUs shows a dropdown sorted by VRAM. Saved to `config.json` as `gpu_device`. Cross-platform: Linux (NVIDIA/AMD/Intel via sysfs+pynvml), Windows (LHM), macOS (system_profiler), BSD (pynvml). i18n label "Graphics card" in all 38 languages.
- **GPU CLI commands**: `trcc gpu-list` shows all detected GPUs (`*` marks current), `trcc gpu-set <key>` sets the active GPU for metrics.
- **GPU API endpoints**: `GET /system/gpu` lists GPUs + current selection, `PUT /system/gpu?gpu_key=...` sets the active GPU.

## v9.4.3

### Fixes
- **macOS sensors completely empty** (#109): `read_all()` returned empty dict on CLI/first call — bootstrap now triggers synchronous poll. Computed I/O rates (disk/net) had no delta tracking. `mem_available` mapped to `psutil:mem_used` instead of `psutil:mem_available`. CPU percent returned 0.0 on cold start.
- **macOS Apple Silicon GPU metrics**: Parse GPU active residency (%), clock (MHz), power (W) from `powermetrics --samplers smc,gpu_power`. Register fans 0–3 for multi-fan Macs. Per-core CPU freq (max) published via psutil slot.
- **Idle sensors hidden in CLI/preview**: `trcc info` and ANSI preview hid any metric at 0% (idle CPU, idle GPU, fans off) because the display filter used `!= 0.0`. Now uses `_populated` set on `HardwareMetrics` — shows sensors that have data, hides sensors that don't exist.
- **Metric values not matching C# app**: Non-rate metrics (temps, percentages, RPM, MHz, watts) now int-truncated at the read boundary, matching C#'s `Substring(0, IndexOf("."))`. Rate/size fields (disk_read, net_up, mem_available) keep float for unit conversion.

### Refactors
- **SensorEnumeratorBase**: Extracted shared sensor logic into `adapters/system/_base.py` — polling lifecycle, computed I/O, nvidia, psutil, datetime, fan mapping, `_find_first` helper. 4 platform enumerators inherit and override only platform-specific discovery/polling/mapping. 656 lines eliminated (2405 → 1749).
- **Sensor test DI refactor**: All platform sensor tests rewritten with `MockIO` fixtures through public API (`discover()` → `read_all()` → `map_defaults()`). Mock at I/O boundary only. Internal method renames no longer break tests. 827 lines cut (2341 → 1514).

## v9.4.2

### Fixes
- **Mask overlay config not restored on reopen**: Applying a mask without saving as a custom theme, then closing and reopening, used the saved theme's overlay coordinates instead of the mask's `config1.dc`. Now `restore_last_theme()` loads the mask dir's DC when a mask is present.

## v9.4.1

### Fixes
- **LED circulate (zone_sync) not restored on startup** (#110): Setting saved correctly but UI checkbox never synced back on device activation. Added `load_sync_state()` on the LED panel and expanded handler `_sync_ui_from_state()`.
- **macOS PyInstaller SSL cert failure** (#109): Downloads failed with `CERTIFICATE_VERIFY_FAILED` — macOS Python can't access Keychain natively and PyInstaller removed the certifi runtime hook. Now uses `certifi.where()` explicitly.
- **macOS missing GUI assets** (#109): PyInstaller `--add-data` only bundled `src/trcc/assets` (icons/policy), not `src/trcc/gui/assets/` (buttons/backgrounds/panels).
- **Linux sensor module loading on macOS** (#109): `config.py` imported `linux.sensors.map_defaults` unconditionally, triggering `pynvml.nvmlInit()` on macOS. Replaced with `enumerator.map_defaults()` (every platform's enumerator has this method).
- **Install method detection for PyInstaller**: `detect_install_method()` now checks `sys.frozen` first, returning `'pyinstaller'` instead of falling through to `'pip'`.
- **macOS hidapi not bundled**: macOS CI now installs `".[nvidia,hid]"` so hidapi is included in the `.app` bundle.

### Refactors
- **Unified button image resolution**: Split `DEVICE_BUTTON_IMAGE` into `_LCD_BUTTON_IMAGE` + `_LED_BUTTON_IMAGE`. Single `get_button_image(pm, sub, *, is_led=False)` entry point replaces dual LED/LCD paths. Removed `PmRegistry.get_button_image()`. 8 PM values collide between LED and LCD namespaces.

## v9.4.0

### Features
- **Auto-detect best GPU**: Multi-GPU systems automatically use the GPU with the most VRAM (discrete over integrated). NVIDIA via pynvml, AMD via sysfs. Works for any count, any vendor mix.
- **`trcc report` works with GUI running**: Routes through running instance via IPC instead of opening devices directly. No more "in use by another process" warnings.

### Fixes
- **IPC status enriched**: `display.status` and `led.status` now return PM, SUB, FBL, model — full device identity for remote clients and diagnostics.

## v9.3.13

### Fixes
- **LED metrics not updating in GUI**: LED frame events were silently dropped — `_show_view('led')` cleared `_active_path`, so `FRAME_RENDERED` and `METRICS_UPDATED` never reached the LED handler. LED segment displays (CPU/GPU temp) and color preview now update correctly.
- **Close/help buttons disappearing**: Buttons were only visible in Control Center view. Now always visible on the golden bar regardless of active device or view.
- **LED button images showing as text**: Unknown LED devices fell back to text labels instead of the generic device image. Now uses `A1KVMALEDC6` (C# default) for unrecognized LED PM bytes, `A1CZTV` for unrecognized LCD PM bytes.
- **LCD button images not resolved for SCSI devices**: Handshake was skipped when resolution was already known, so PM/SUB were never set and button images stayed generic. Handshake now always runs to resolve device identity.
- **Missing `pm_byte` after HID handshake**: `_on_handshake_done` set `sub_byte` and `fbl_code` but not `pm_byte` — button image resolution used stale data.

### Refactors
- **Unified device enrichment**: `DeviceService._enrich_device()` resolves button images for all device types at detection time — LED via PmRegistry, LCD via DEVICE_BUTTON_IMAGE. Deleted duplicate GUI resolution block.
- **Single tick loop**: `TrccApp.start_metrics_loop()` owns the tick (50ms animation) and metrics poll (`refresh_interval` setting). Deleted API's `start_led_loop`/`stop_led_loop` and CLI's inline while loop. All UIs observe via `AppObserver`.
- **Handler `deactivate()` interface**: Renamed `stop_timers()` to `deactivate()` on `BaseHandler`. Collapsed `isinstance` dispatch in `_activate_device` to `prev.deactivate()`.
- **IPC `DeviceProxy` base class**: Extracted shared proxy logic. `DisplayProxy`/`LEDProxy` are thin subclasses. Unified factory via `_create_proxy()`.
- **Deleted `tick_with_result()`**: LED tick returns colors dict directly from `_tick_led()`. One tick, one compute — GUI observes via `FRAME_RENDERED` like LCD does.
- **`LCD_DEFAULT_BUTTON` / `LED_DEFAULT_BUTTON` constants**: Single source of truth for default sidebar button images, matching C# fallback behavior.

## v9.3.12

### Fixes
- **Display blank after sleep/resume**: Sleep handler stopped timers on suspend but did nothing on resume — USB handles go stale after re-enumeration, leaving the display blank. Now delays 2s for USB to settle, then rescans all devices which rebuilds handlers and restarts timers.

## v9.3.11

### Refactors
- **Unified Device class**: `LCDDevice` + `LEDDevice` merged into one `Device` class with `device_type` boolean (True=LCD, False=LED). One `build_device()` replaces `build_lcd()`/`build_led()`. TrccApp uses `_devices` dict — no separate LCD/LED fields.
- **Full device identity in logger**: Every log line now shows `[lcd:0 [0402:3922 FBL=100 PM=32 SUB=1]]` — VID:PID, FBL, PM, SUB from handshake. No more guessing which device produced which log line.

### Testing
- **Integration tests for ALL devices**: Every VID:PID in registry, every FBL resolution, every LED PM product — tested through the real app flow (MockPlatform + real builder + real services). 286 new tests.
- **User session tests**: send_color, set_brightness, set_mode, tick — what users actually do, tested for every device.
- **DI fixtures**: `lcd_device` and `led_device` fixtures build through real builder flow instead of constructing with MagicMock services.
- **MockPlatform shared**: `tests/mock_platform.py` — noop USB protocols, shared between test suite and dev/mock_gui.py.
- **mock_gui --report**: `PYTHONPATH=src python3 dev/mock_gui.py --report user_report.txt` — reproduce any user's exact device setup from their `trcc report` output.

## v9.3.8

### Fixes
- **Theme not restored on restart**: First-install guard only checked legacy `theme_path` config key, missed `theme_name` (current format) — auto-loaded theme #1 instead of user's saved theme.
- **Custom themes not found on restore**: `restore_last_theme()` only searched `~/.trcc/data/`, missed `~/.trcc-user/data/` where saved themes live.
- **`save_theme()` wrote legacy config key**: Wrote `theme_path` instead of `theme_name` + `theme_type`.
- **Multi-device: only active LCD restored on startup**: Inactive LCD devices now get `restore_device_settings()` + `restore_last_theme()` at scan time.
- **CLI `resume` misleading for animated themes**: Now shows "start GUI for playback" instead of "Sent".
- **Diagnostics reported stale config field**: Reported `theme_path` (usually absent) instead of `theme_name` + `theme_type`.

### Refactors
- **DisplayService encapsulation**: Public API for `mask_source_dir`, `clean_background`, `invalidate_video_cache()`, `convert_media_frames()`, `render_and_process()`. LCDDevice no longer accesses any DisplayService private attributes.
- **Centralized dir name helpers**: `theme_dir_name()`, `web_dir_name()`, `masks_dir_name()` in `core/paths.py` replace 30+ scattered format strings across 11 files.
- **Overlay save centralized**: `_save_overlay()` helper replaces 3 inline dict constructions.
- **Consistent user content layout**: Custom themes now save to `~/.trcc-user/data/` (was `~/.trcc-user/`), mirroring `~/.trcc/data/` exactly. Auto-migration on first startup.
- **Orientation simplified**: Stores 2 root paths + `has_portrait_themes` boolean. All dirs derived from resolution math — no stored dir lists, no `landscape_*`/`portrait_*` attributes. Removed `is_square`, `is_portrait`, `swaps_dirs`, `has_rotated_dirs`.
- **Removed dead code**: `DisplayService.path_resolver` property, `DisplayService.user_masks_dir()` method, `_has_any_content` duplicate, portrait dir persistence.

## v9.3.7

### Fixes
- **Comprehensive diagnostic logging**: Every silent decision point in the bootstrap, device connect, data download, theme load, overlay config, and LED send paths now has a log line. Multi-device issues (like #101) are now diagnosable from `~/.trcc/trcc.log` alone.
- **Pre-existing test warnings**: `_wire_device()` now guards against `settings=None` when `init_platform()` hasn't been called — eliminates `AttributeError` in integration test threads.
- **`ensure_all()` silently swallowed exceptions**: Individual `ensure_*()` failures now caught and logged per-step instead of crashing the entire data download.
- **Overlay config parse errors swallowed**: `load_overlay_config_from_dir()` `except: pass` blocks now log the parse error.
- **LED `send_led_data()` failures swallowed**: Exception now logged instead of returning `False` silently.

## v9.3.6

### Fixes
- **BA120 Vision pixelated display (#100)**: FBL=52 was missing from `FBL_PROFILES` — fell back to 320x320 default instead of 320x240. Added profile.
- **Bulk devices wrong encoding params (#78)**: Unknown PMs returned `model_id=PM` instead of `model_id=72` (bulk default). C# hardcodes FBL=72 for bulk; our code now matches.

## v9.3.5

### Refactors
- **match/case dispatch**: LED effects, IPC command router, rainbow phase, overlay metrics, display mode panels, handler type dispatch, package manager — replaces if/elif chains with structural pattern matching.
- **PmRegistry dunders**: Singleton with `__getitem__`, `__contains__`, `__iter__`. `PmRegistry[pm, sub]`, `pm in PmRegistry`, `str(entry)` — objects behave like the language.
- **PmEntry `__str__`**: Returns model name directly.

### Fixes
- **Portrait zt masks double-rotated at 90/270**: `_image_rotation` now checks overlay dimensions — when content is already portrait from dir switch, pixel rotation is 0. Was reading only portrait theme dir, ignoring mask/web dirs.
- **Cloud theme landscape after mask at 90**: Video frames decoded at `canvas_size` (landscape) instead of overlay dimensions (portrait). Now decodes at overlay's current resolution — mask/DC stay, background swaps.
- **Package manager dispatch duplicated**: `_provides_search()` checked `pm` string twice (command + parser). Now paired in single match block.

## v9.3.4

### Refactors
- **Unified `TrccApp.apply_temp_unit()`**: System-wide temp unit change — persists, updates all devices (LCD overlay + LED segment), fetches fresh converted metrics. CLI/API/GUI all call the same method.
- **`TrccApp.set_hdd_enabled()`**: Consistent entry point for HDD toggle across all adapters.
- **`trcc background-list`**: New CLI command matching GUI "Cloud Backgrounds" tab. `theme-list` is now local-only (dropped `--cloud`). Three parallel commands: `theme-list`, `mask-list`, `background-list`.
- **Screencast API**: `POST /display/screencast/start`, `/stop`, `GET /status`. Auto-detects X11 (ffmpeg) or Wayland (PipeWire).
- **API hexagonal purity**: `send_image` and `create_theme` route through LCDDevice. All 5 background pump loops use `LCDDevice.send_frame()`.
- **DI for `download_themes`**: `TrccApp` no longer imports adapters — callables injected via `builder.build_download_fns()`.
- **Test fixtures**: CLI + core tests use real `DisplayService` + `OverlayService` — behavioral assertions (pixel values) instead of mock call counts.
- **CLI help panels**: 7 groups (Device, LCD Display, Themes, LED, System, Diagnostics, Interfaces).

### Fixes
- **C/F toggle not updating LCD**: Overlay `temp_unit` wasn't set on all handlers (gated on `_active_path`). Now updates ALL LCD handlers.
- **C/F toggle stale metrics**: Render used cached metrics with old conversion. Now fetches fresh metrics before re-render.
- **Video overlay text not refreshing**: `update_video_cache_text()` was only called from GUI handler gated on `_active_path`. Now called from `TrccApp._loop()` for all playing devices.
- **System info panel showing 0/1 instead of °C/°F**: `set_temp_unit()` stored int directly as display suffix.
- **LinuxSetup.get_screencast_capture()** returned `x11grab` on pure Wayland (no `$DISPLAY`). Now returns `None`.
- **`trcc uninstall` logging crash**: File handlers removed before deleting `~/.trcc/`.
- **install.sh**: Uninstall removes `~/.trcc-user/`. Setup step fixes sudo PATH.

## v9.3.3

### Refactors
- **Centralized theme/mask listing**: `ThemeService.discover_local_merged()` + `discover_masks()` — CLI, API, GUI all go through the same service method. Scans both `~/.trcc/data/` (stock) and `~/.trcc-user/` (user-created) directories.
- **Unified theme save**: CLI, API, GUI all route through `lcd.save()` → `DisplayService.save_theme()`. Always saves to `~/.trcc-user/`. Dropped `data_dir` parameter — single code path, single destination.
- **CLI help panels**: Commands grouped into Device, LCD Display, Themes, LED, System, Diagnostics, Interfaces sections via `rich_help_panel`.

### Fixes
- **User themes not found by CLI/API**: `load_theme_by_name`, `list_themes`, `export_theme` now scan `~/.trcc-user/` in addition to `~/.trcc/data/`
- **Animated theme overlay missing**: `load_theme_by_name` now loads DC overlay config for animated themes (was skipped, only static themes got overlay)
- **`theme-load` animated themes not sending frames to LCD**: CLI now wires `on_frame` callback and resolves video file from theme directory
- **`theme-load -p` no live preview on static themes**: keep-alive loop now accepts `on_frame` for live ANSI terminal preview
- **CLI save to wrong directory**: Was saving to `~/.trcc/data/` instead of `~/.trcc-user/`
- **API `save_theme` was a stub**: Now fully implemented via `lcd.save()`
- **`trcc uninstall` logging crash**: File handlers removed before deleting `~/.trcc/` directory

### New
- **`trcc mask-list`**: List available mask overlays from both cloud cache and user directories
- **`MaskInfo` dataclass**: Domain object for mask discovery (service layer)
- **`Settings.user_masks_dir()`**: Public method for user mask directory resolution
- **`LCDDevice.set_mask_from_path()`**: Handles both file and directory mask paths

## v9.3.2

### Fixes
- **Fedora RPM install fails** (`nothing provides python3-sounddevice`): `sounddevice` not in Fedora/Arch/Ubuntu repos — now bundled via pip in RPM, DEB, and Arch packages. `portaudio` C library added as system dependency.
- **RPM `python3-prompt_toolkit` wrong name**: Fedora package is `python3-prompt-toolkit` (dash, not underscore)
- **NixOS flake missing deps**: Added `sounddevice`, `prompt-toolkit`, `python-multipart`, `portaudio`, `libusb1`, `p7zip`, `sg3_utils`, `ffmpeg`

### Improvements
- **Version-free install URLs**: CI now uploads fixed-name aliases (`trcc-linux-latest.*`) alongside versioned assets — install guide one-liners never need version bumps
- **README contributors grouped by device**: Flat list → organized by Thermalright product. Stars 43→57, forks 4→5.
- **Install guide portaudio**: All distro pip sections now include `portaudio`/`libportaudio2` for audio visualizer support

## v9.3.1

### Fixes
- **`sudo trcc setup-udev` fails on Ubuntu pip installs** (issue #96): `sudo_reexec` now injects `sys.path` directly into the Python snippet instead of relying on `PYTHONPATH` env var — immune to Ubuntu's `sudoers env_reset` which strips environment variables
- **macOS SCSI devices not working**: `MacOSScsiProtocol` created and wired into the device protocol factory — was silently falling back to Linux `sg_raw` which doesn't exist on macOS
- **BSD SCSI data never reaching device**: `BSDScsiTransport` rewritten from broken `camcontrol` subprocess (stdin pipe doesn't work for data-out) to pyusb USB BOT — same proven approach as macOS
- **Platform-specific error messages**: USB interface claim errors now show macOS/BSD-appropriate hints instead of Linux SELinux messages

### Improvements
- **Video progress bar**: Seek slider and time display now update during media player playback
- **Audio visualization on screencast**: Mic button on screencast panel — spectrum analyzer bars drawn at bottom of frame (requires `sounddevice`)
- **Documentation overhaul**: Rewrote New to Linux guide for Windows refugees with auto-detect one-liners, new comprehensive User Guide, added `trcc shell` to CLI reference, updated README counts
- **Dead code removal**: `BSDDeviceDetector` class removed (was duplicating the injected detect path)

## v9.3.0

### Fixes
- **Media player video not playing on LCD**: Theme animation cache was not cleared when loading a user video, and raw frames were not converted to native surfaces — video tick sent empty frames while the old theme cache kept displaying. Both fixed.
- **Overlay competing with video playback**: `_render_and_send` now skips when video is active — the video tick owns the device during playback

### Improvements
- **Remove 14 backward-compat aliases**: Dead alias functions removed from dc_writer, media_player, models, CLI diag, and CLI system. All callers updated to use canonical names.
- **`render_and_send` cleanup**: Removed unused `skip_if_video` parameter — guard moved to handler layer where it belongs

### Dependencies
- **`sounddevice`** added as core dependency (preparation for mic audio visualization on screencast)

## v9.2.10

### Fixes
- **Wrong sidebar button image for SCSI devices**: Button image now resolved from FBL at connect time — SCSI devices (PM=FBL, SUB=0) get the correct product PNG immediately. Confirmed by decompiling native USBLCD.exe via Ghidra. Fixed `DEVICE_BUTTON_IMAGE` parity with C# (PM=9 SUB split, PM=49, PM=65 sub=2), removed unreachable VID-keyed entries
- **LCD overlay blink**: `tick()` now caches the last composited overlay frame and resends it between metric changes — bare background never leaks to the device
- **Device button doesn't return to preview**: Navigating to About/SysInfo then clicking the device button did nothing because `_active_path` wasn't cleared. `_show_view` now clears it when leaving form view
- **USB autosuspend resets bulk devices** (issue #98): Linux kernel autosuspend can reset idle USB devices after ~30 seconds, causing the LCD to drop to its splash screen. Now disabled at runtime when the device is opened, and via udev rules (`sudo trcc setup-udev` to update)
- **Logging gaps**: Identity resolution flow (handshake → button image → sidebar update) now fully logged. Log format includes `%(funcName)s` for easier tracing

## v9.2.9

### Fixes
- **Legacy DEB wrapper scripts broken** (issue #96): `postinst` used `printf` with `"$@"` which expanded dpkg's own args (`configure`) into the wrapper instead of passing them through literally — `/usr/bin/trcc` ran `exec /opt/trcc-linux/bin/configure` instead of forwarding CLI args. Replaced with `echo` + proper escaping

## v9.2.8

_Version bump to retry PyPI publish (9.2.7 hit project size limit)._

## v9.2.7

### Fixes
- **Startup blink on first install**: `apply_device_config` auto-loaded the first theme via `_update_theme_directories()` (which persists `theme_path`), then `restore_last_theme()` loaded the same theme again — sending two frames where the second momentarily disabled the overlay. Now `_update_theme_directories()` returns whether it auto-loaded, and `apply_device_config` skips `restore_last_theme()` when it did
- **Wrong theme directory after switching devices**: Re-activating an already-initialized LCD device didn't update the global resolution, so theme/web/mask browsers showed content for the previous device's resolution. Now `_activate_device` sets resolution and refreshes theme directories on re-activation

## v9.2.6

### Fixes
- **Portrait web previews and masks not extracted for existing users**: `ensure_all()` previously skipped all extraction once a resolution was marked installed. Each `ensure_*` is already idempotent (checks before extracting), so the guard was redundant — now all archives are always ensured on every startup, including both orientations for non-square devices

## v9.2.5

### Fixes
- **Cloud mask download fails for portrait/rotated devices** (issue #95): `CLOUD_MASK_URLS` was missing all non-square resolutions — users with 1280x480 and other rotatable devices got "No cloud URL" when switching to portrait and trying to download masks. All landscape+portrait pairs now registered
- **Billboard devices missing from cloud mask URLs**: 640x172, 960x320, 1920x440 (and their portrait variants) can now download cloud masks
- **Portrait web+mask data not extracted on first install**: `ensure_all()` now extracts bundled web previews and masks for both orientations when a non-square device is first set up

## v9.2.4

### Fixes
- **`trcc detect` shows "No device path found" for LED controllers** (issue #90): LED protocol devices now display `vid:pid` path like HID/bulk devices
- **`trcc test` crashes on LED controllers** (issue #90): LED devices have no LCD panel — command now exits cleanly with a message directing users to `trcc led`
- **`trcc report` missing LED handshake data** (issue #90): LED devices (`protocol="led"`) were skipped in the Handshakes section — now included alongside SCSI/HID/Bulk/LY

### Internal
- **TrccApp god class extracted**: all inline handler closures moved to `core/handlers/` package — `LCDCommandHandler`, `LEDCommandHandler`, `LEDGuiCommandHandler`, `OSCommandHandler`. Each is a pure callable with injected dependencies; `TrccApp.build_*_bus` reduced to one-liner delegates
- **Hexagonal violation fixed**: `OSCommandHandler` no longer imports adapter modules inline — `list_themes_fn` and `download_pack_fn` are injected at construction time via `app.py` (composition root)
- **Python 3.10 compatibility**: `HandlerFn: TypeAlias = ...` replaces `type HandlerFn = ...` (3.12-only syntax); `@override` removed (3.12-only import); `match` statements require `target-version = "py310"` in ruff config
- **Duplicate test class removed**: `TestDownloadThemesHandler` existed in both `test_os_handler.py` and `test_app_lifecycle.py`; lifecycle test class deleted, handler test is the single source

## v9.2.3

### Internal
- **Clean LED protocol routing**: `LED_DEVICES` protocol changed from `'hid'` to `'led'` — single source of truth in `models.py`. Factory key updated accordingly; detector iterates `ALL_DEVICES` directly without per-registry overrides
- **Parallel device scan**: `TrccApp.scan()` connects devices concurrently (one thread per device) so USB handshakes run in parallel. `bootstrap()` added as single entry point for all UIs (GUI, API, CLI)
- **107 new tests**: per-OS platform contract tests via real adapter fixtures (no patching); per-device routing parametrized over `ALL_DEVICES`; `scan()`/`bootstrap()`/`device_connected`/`device_lost` coverage; `build_os_bus()` handler closures; metrics loop start/stop/tick/event dispatch; observer lifecycle
- **Parallel test flakiness fixed**: `_restore_renderer` autouse fixture in `conftest.py` saves and restores `ImageService._renderer` around every test — eliminates cross-worker contamination from class-level renderer state

## v9.2.2

### Fixes
- **`trcc uninstall` logging error on Python 3.14**: `autostart.disable()` logged after `~/.trcc/` was deleted, causing `RotatingFileHandler` to crash trying to reopen the log file. Autostart is now disabled before logging shutdown

## v9.2.1

### Fixes
- **First install shows blank preview**: after theme archives finish extracting, the first local theme (theme1) now auto-loads onto both the LCD and preview — including mask and metrics overlay from the theme's default config. Previously the preview stayed green until the user manually clicked a theme

## v9.2.0

### Enhancements
- **`trcc report` comprehensive diagnostics**: report now contains everything needed to diagnose user issues without follow-up questions — full config dump with all per-device settings (brightness, rotation, split_mode, theme_path, fbl per VID/PID), installed theme inventory per resolution, sensor availability (psutil CPU%, hwmon node list, NVIDIA/pynvml status), FBL encoding decoded inline (`FBL=100 → RGB565-BE`, `FBL=54 → JPEG`, `FBL=72 → RGB565-LE rotated`), and Linux user group membership (plugdev, dialout, disk)

### Internal
- **Model-parametrized tests**: `test_encoding.py`, `test_device.py`, `test_perf.py` now derive all domain values from `FBL_PROFILES`, `LED_STYLES`, `ALL_DEVICES` — adding a new device to models automatically adds test coverage. 171 new parametrized test cases
- **pytest-xdist coverage**: added `concurrency = ["multiprocessing", "thread"]` + `parallel = true` to coverage config so `--cov` collects correctly across xdist worker subprocesses

## v9.1.6

### Internal
- **Single render pipeline**: `LCDDevice.tick()` is now the sole render point — returns the rendered image which `TrccApp` routes to the preview via `AppEvent.FRAME_RENDERED`. Eliminates the race between background tick and GUI overlay tick that caused preview to show zero metrics after restart
- **`BaseHandler` ABC**: `LCDHandler` and `LEDHandler` share a common `BaseHandler` interface (`view_name`, `device_info`, `cleanup`, `stop_timers`). `TRCCApp._lcd_handlers`/`_led_handlers` collapsed into a single `_handlers: dict[str, BaseHandler]`
- **`LEDHandler` extracted**: moved from inline class in `trcc_app.py` to its own `gui/led_handler.py`
- **`PlatformAdapter` contract tests**: all four platform factories (`LinuxPlatform`, `WindowsPlatform`, `MacOSPlatform`, `BSDPlatform`) now have contract tests that run on Linux — covering the previously 0% `platform.py` files
- **Windows SCSI test fix**: `_wintypes_mock` autouse fixture keeps `ctypes.wintypes` alive for runtime calls; fixes 3 test failures on Python 3.14

## v9.1.5

### Fixes
- **`trcc theme-load` crashes on headless systems** (issue #78): Renderer was not initialized before `lcd_from_service()` on CLI — `theme-load`, `theme-save`, and `screencast` would throw `RuntimeError: ImageService.set_renderer() must be called before use`. Fixed by wiring the renderer at startup via `InitPlatformCommand` so it is always ready before any command runs

### Internal
- **Pure hexagonal composition root**: `InitPlatformCommand` now does real work — logging, OS setup, settings, renderer injection. `TrccApp.init()` is a minimal container. `ControllerBuilder.bootstrap()` is an instance method called by the command handler. `_ensure_renderer()` and all 10 scattered call sites eliminated. Each composition root (CLI, API, GUI) passes a `renderer_factory` when dispatching `InitPlatformCommand` — core never imports Qt

## v9.1.4

### Internal
- **Command Pattern (GoF)**: All three interfaces (CLI, API, GUI) dispatch through `CommandBus` — frozen dataclass commands as pure value objects, middleware chain (logging, timing, rate-limit), per-context buses with context-appropriate handlers. GUI LED bus calls `update_*` (state-only, preserved tick architecture); CLI/API buses call `set_*` (immediate send). `CommandBus` is required — handlers raise `ValueError` at construction if not injected

## v9.1.3

### Fixes
- **Bulk device screen flashes Thermalright logo randomly** (issue #82): `claim_interface()` EBUSY now raises `RuntimeError` instead of calling `dev.reset()`. The reset triggered a firmware restart and boot logo flash; preserving the existing claim skips it entirely
- **`trcc setup` pip commands fail on Ubuntu 24.04 (PEP 668)**: Setup wizard now uses `sys.executable -m pip install` instead of bare `pip install` — works inside pipx venvs and PEP 668 externally-managed environments (issue #84)
- **Polkit policy not found after pip/pipx install**: Path resolution corrected from 3 parent levels to 4 (was landing in `trcc/adapters/`, now correctly lands in `trcc/`) (issue #84)
- **`trcc doctor` hidapi install hint missing**: Now shows the correct system package name per distro (`python-hid` on Arch, `python3-hid` on Fedora, `python3-hidapi` on Debian/Ubuntu) with pip fallback (issue #84)

### Docs
- **Install guide and DEB descriptions clarified**: Standard DEB requires `python3-pyside6` in apt — only available on Debian 13+. Legacy DEB (venv + pip) works on Ubuntu 24.04 and Debian 12 (issue #83)

### Internal
- **`TrccLoggingConfigurator` ABC** (`adapters/infra/logging_setup.py`): Unified log format (includes `funcName`) wired into all 3 composition roots — CLI, GUI, API
- **`tools/diagnose.py`**: Parses `trcc report`, extracts device profile, runs matching protocol tests without real hardware

## v9.1.2

### Internal
- **`tools/diagnose.py`**: Rewrote report parser to use direct regex — OS field captures full string, VID:PID matched as exactly 4 hex chars. Tool routes to platform-specific SCSI test file (linux/windows/macos/bsd) based on OS from report
- **SCSI profile tests**: Added `test_scsi_handshake_all_devices` and `test_scsi_send_frame_all_devices` parametrised over all `SCSI_DEVICES` entries

## v9.1.1

### Fixes
- **Static theme blinks to USB standby symbol every ~30 s**: No frames were sent after the initial theme load when no dynamic overlay elements triggered a re-render. A keepalive fires every ~20 s to resend the current frame and prevent USB timeout
- **Device polled every 15 s while connected**: `find_lcd_devices()` ran a full sysfs USB scan on every poll tick regardless of connection state. The scan now only runs when no device is connected

### Internal
- **Startup log**: GUI logs `TRCC v{version} starting` on launch
- **Overlay tick guard**: Changed from `is_overlay_enabled OR is_background_active` to `device.connected` — simpler and correct for all theme types
- **Comprehensive debug logging**: CLI commands log entry/args/result via `_cli_handler`; API logs every request (method, path, status, latency); `_guarded_send` logs frame send success/failure; device disconnect detected and logged in GUI poll

## v9.1.0

### Internal
- **Strict hexagonal architecture**: All GUI views now receive infrastructure dependencies via injection — no view imports adapter-layer code directly
  - `UCDevice` — `find_lcd_devices` injected as `detect_fn` from composition root (`trcc_app.py`)
  - `UCThemeWeb` — `CloudThemeDownloader` injected as `download_fn`
  - `UCSystemInfo` — `SysInfoConfig` injected; `SensorBinding`/`PanelConfig` moved to `core/models`
  - `UCThemeMask` — `is_safe_archive_member` moved to `core/paths` (pure function, no adapter dep)
  - `UCLedControl` — `LED_STYLES`/`PmRegistry` imported from `core/models` (was adapters)
- **`SystemService._mem_clock_cache`**: Module-level mutable global eliminated — cache moved to instance variable, sentinel pattern preserved
- **CPU baseline logging**: `psutil.ImportError` now logs a warning instead of silently setting fn to None; `builder.py` gets a proper logger

## v9.0.9

### Fixes
- **`temp_linked` / `load_linked` LED modes show 0°C and display turns off**: These modes were not included in the animated set, so the CLI sent a single packet with uninitialised (zero) metrics and exited. The LED controller then timed out and powered off. Both modes now run a continuous loop with sensor metrics refreshed every ~1 second
- **`trcc serve` LED display turns off**: No background keepalive existed for LED in the API server. A background tick thread (50 ms ticks, 1 s metric refresh) now starts when an animated mode is set via `/led/mode` and stops on `/led/off` or static colour
- **JPEG images blurry on 1280×480 HID Type 2 devices**: JPEG size cap was 450 KB — too small for large displays at quality 95, forcing the encoder to reduce quality. The HID Type 2 transfer buffer is 691 200 bytes (~672 KB usable); cap raised to 650 KB. `JPEG_MAX_BYTES` constant centralised in `core/models.py`
- **`~/.trcc/trcc.log` not written for CLI / API commands**: File logging was only set up in the GUI path. All CLI commands (including `trcc serve`) now write to the log file via `_ensure_file_logging()` in the Typer callback

### Internal
- **PIL fully eliminated**: `ImageService`, `OverlayService`, screen capture, video decode, mask pipeline, IPC frame serialisation, and all tests now use Qt surfaces (QImage/QPainter) exclusively. `PilRenderer` and `FontResolver` deleted. ffmpeg replaces `PIL.ImageGrab` for screencast
- **`JPEG_MAX_BYTES` constant**: Single source of truth in `core/models.py`, imported by `core/ports.py`, `adapters/render/qt.py`, and `services/image.py`

## v9.0.8

### Fixes
- **`QT_QPA_PLATFORM` always forced to `offscreen` for CLI**: Previously set with `setdefault`, so a `QT_QPA_PLATFORM` already in the environment (e.g. `xcb`) would be respected. CLI never opens windows and must not negotiate a display platform — the variable is now unconditionally set to `offscreen`. Users no longer need to set it manually before running `trcc theme-load` or any other CLI command

## v9.0.7

### Fixes
- **CLI segfault on exit when using offscreen Qt (SSH / headless)**: `QApplication` created by `_ensure_renderer` was not stored in a module-level variable, so the Python wrapper was garbage-collected before program exit. PySide6 then tore down Qt internals in a bad order, causing a segmentation fault. The instance is now held in `_qt_app` for the lifetime of the process. Affects `trcc theme-load`, `trcc send`, and any CLI command that uses the Qt renderer over SSH or in a display-less environment (reproducible with PySide6 ≥ 6.10)

## v9.0.6

### Fixes
- **CMD window flash on Windows when clicking a theme**: All subprocess calls (`7z`, `ffmpeg`, `ffprobe`) in GUI code paths now use `CREATE_NO_WINDOW`. Centralised as `SUBPROCESS_NO_WINDOW` in `core/platform.py` (zero-value no-op on Linux/macOS)
- **`ModuleNotFoundError: trcc.adapters.system._shared` on Windows PyInstaller bundle**: `_shared.py` was untracked and missing from the bundle. Linux-specific CLI functions (`setup_udev`, `setup_selinux`, `setup_polkit`, `install_desktop`) now lazy-import `linux/setup.py` after the `_require_linux()` guard instead of at module level — prevents the import from running on Windows at all

### Internal
- **Hexagonal platform branching eliminated**: `doctor.py` and `debug_report.py` are now fully OS-blind — driven by `DoctorPlatformConfig` / `ReportPlatformConfig` dataclasses returned by each platform's `PlatformSetup` adapter. Zero `if LINUX/WINDOWS/MACOS/BSD` outside `adapters/system/`, `adapters/device/`, `core/platform.py`, and `core/builder.py`
- **`trcc_app.py` OS-blind**: Instance lock and signal raising delegated to `PlatformSetup.acquire_instance_lock()` / `raise_existing_instance()`
- **`adapters/system/_shared.py`**: Shared helpers (`_confirm`, `_print_summary`, `_copy_assets_to_user_dir`, `_psutil_process_usage_lines`, `_posix_acquire_instance_lock`, `_posix_raise_existing_instance`) consolidated from 4 platform adapters into one module
- **Logging gaps fixed**: Silent `except: pass` blocks in `_discover_resolution`, DC config parse, `_fetch_ipc_frame`, GIF animation check, autostart refresh, sysfs USB scan, LHM discovery, WMI thermal zones, NVIDIA poll, and computed I/O now emit `log.warning` / `log.debug` with context

## v9.0.5

### Features
- **API `POST /display/create-theme` — file uploads**: `background`, `mask`, and `overlay` are now uploaded directly as multipart files. No server paths needed — send an image/video, optional mask PNG, and optional overlay JSON config in one request from any client (phone, tablet, remote app)
- **API `POST /display/upload`**: standalone upload endpoint — saves any image or video to `~/.trcc/uploads/` and returns the server path for use in other endpoints

## v9.0.4

### Fixes
- **Bulk device disconnect on KVM USB passthrough (#78)**: `send_frame` was sending the entire frame as one bulk transfer (~460 KB for 480×480). On KVM virtual machines with USB passthrough, this overwhelmed the virtual USB controller and caused the device to reset mid-transfer (`[Errno 19] No such device`). Frame is now sent in 16 KiB chunks, matching the `_WRITE_CHUNK_SIZE` constant that was already defined but unused

## v9.0.3

### Features
- **API `POST /display/create-theme`**: Send a custom theme via REST — static image or video background, optional mask, repeatable `&metric=key:x,y[:color[:size[:font[:style]]]]` overlays, loop control. Auto-detects animated backgrounds (video, animated GIF)
- **Media player — direct video playback**: "Load Video" in the media player panel now plays the video directly on the LCD without opening the cutter. Competing sources (screencast, overlay, background) are stopped before playback begins
- **LCD clear on close**: On GUI shutdown, a black frame is sent to the display before disconnecting so the screen goes dark instead of freezing on the last image
- **Legacy DEB for Ubuntu 22.04**: CI now builds a second `.deb` (`*-1~legacy_all.deb`) targeting Ubuntu 22.04 and older. Installs Python dependencies into `/opt/trcc-linux` via pip+venv to avoid the `--break-system-packages` restriction on older distros

### Fixes
- **Spurious toggle signal on `set_enabled()`**: `DisplayModePanel.set_enabled()` blocked signals around `setChecked()` — prevents false toggle-off events when restoring state from config
- **Video cache built while disconnected**: `DisplayService.load_theme()` no longer builds the pre-baked frame cache when no device is connected — avoids wasted encoding on startup
- **`send_frame` / `send_image` called while disconnected**: `LCDHandler` now returns early if the device is not connected before attempting to push frames or pre-encoded data
- **Screen capture failure logged once**: Repeated "all capture methods failed" warnings are now deduplicated — first occurrence logged at WARNING, subsequent silent until capture recovers
- **pynvml per-sensor errors now at DEBUG**: Individual GPU sensor read failures (temp, util, clock, power, VRAM, fan) downgraded from silent swallow to `log.debug` — init-level failures stay at WARNING
- **`--last-one` renamed to `gui --resume`**: Autostart desktop entry and CLI now use `trcc gui --resume` instead of the ambiguous root-level `--last-one` flag

### Internal
- **`stop_send_worker()`**: New `DeviceService` method for clean async-worker shutdown before sending a final frame — prevents race between the worker and the shutdown black-frame send
- **Removed backward-compat aliases**: `TRCCMainWindowMVC`, `run_mvc_app` (gui), `_is_root` (_system.py) — all callers already use canonical names
- **`__version__` imported from `__version__.py`**: Package `__init__.py` no longer hardcodes version string

## v9.0.2

### Fixes
- **Duplicate log entries on startup**: Every log line was written twice because `__main__.py` set up an early `RotatingFileHandler` via `basicConfig`, then `cli/__init__.py` added a second one without clearing the first. Fixed by calling `root.handlers.clear()` before adding handlers in CLI init
- **pynvml error logging**: Split broad `except Exception` into `ImportError` (debug) vs init failure (warning) for clearer diagnostics when NVIDIA GPU sensors are unavailable

## v9.0.1

### Fixes
- **Windows SCSI PhysicalDrive detection — VID/PID only (#74)**: Removed all vendor string matching (`USBLCD`, `Xsail`, ctypes brute-force scan). `_find_physical_drive()` now confirms VID/PID via `Win32_USBControllerDevice` device instance path, then matches the first USBSTOR disk with size < 1 MB — works for any firmware vendor string including Xsail

## v9.0.0

### Architecture
- **Pure hexagonal restructure**: Eliminated all re-export stubs and mis-named directories. Real code now lives where it belongs — `adapters/device/` (detection + protocols + platform SCSI), `adapters/system/` (sensors + setup + hardware), `adapters/render/`, `adapters/infra/`. Each platform gets its own subdirectory (`linux/`, `bsd/`, `macos/`, `windows/`) — all four equal, no Linux bias
- **VID/PID only detection (#74)**: Removed unreliable vendor string scanning (`USBLCD`/`Xsail`). All SCSI device identification now uses VID/PID via sysfs — works regardless of what firmware string the device reports

## v8.8.2

### Fixes
- **LY devices missing udev rules (#73)**: `trcc setup-udev` didn't generate permissions for LY protocol devices (Trofeo Vision 9.16 — `0416:5408`, `0416:5409`). Users got "permission denied" on handshake even after running setup
- **`trcc report` handshake crash**: Report's handshake section failed with `'DetectedDevice' object has no attribute 'path'` — added `path` property to `DetectedDevice`
- **First-run theme browser empty**: On fresh install, theme browsers showed 0 themes because `DisplayService._setup_dirs` was never called after handshake resolved the resolution. GUI now calls `LCDDevice.set_resolution()` which triggers background download + refresh callback
- **Duplicate calls in `LCDDevice.set_resolution()`**: `media.set_target_size()` and `overlay.set_resolution()` were called twice — already done by `DisplayService.set_resolution()` internally

## v8.8.1

### Features
- **CLI overlay pipeline — `trcc theme` command**: Compose video/image + mask + live system metrics entirely from CLI. Hexagonal architecture — core domain (`build_overlay_config`), service (`DisplayService.run_video_loop`), and input port (`LCDDevice.play_video_loop`) shared by CLI, GUI, and API
  - `trcc theme --background animated.gif --mask /path/to/mask --metric "cpu_temp:10,20"` — play with live overlay
  - `trcc theme --save MyTheme ...` — save as reusable theme in one shot
  - Per-metric color/size overrides: `--metric "gpu_temp:10,20:ff0000:18"`
  - Global font/color/size: `--font Arial --font-style bold --font-size 18 --color 00ff00`
  - `trcc theme-save` deprecated — alias for `trcc theme --save`
- **`trcc theme-load` plays animated themes**: Previously animated themes just printed "use trcc video". Now loads and plays with full overlay + mask, same as GUI
- **GIF animated theme support**: Theme loader and discovery now detect multi-frame GIFs as animated (checks `n_frames > 1`), not just MP4/ZT/AVI

### Fixes
- **Mask not saved from CLI**: `theme-save --mask` passed the path but not the loaded mask image to `ThemeService.save()`, resulting in `"mask": null` in config.json

## v8.7.6

### Fixes
- **CLI video/test/screencast crash — renderer not initialized**: `trcc video`, `trcc test`, and `trcc screencast` failed with `ImageService.set_renderer() must be called before use` because they bypassed `_connect_lcd()` which wires the renderer. Added `_ensure_renderer()` to all three commands
- **CLI video/screencast crash — PIL Image passed to QtRenderer**: `encode_for_device()` received PIL Images from MediaService but QtRenderer's `encode_rgb565()` expected QImage, causing `'NoneType' object is not callable`. Added PIL→QImage conversion guard in `encode_for_device()`
- **Sensor picker missing DRM GPU sensors**: AMD/Intel GPU utilization sensors from `/sys/class/drm/` were discovered but never shown in the sensor picker dialog — `'drm'` source was missing from the display loop. Multi-GPU users couldn't select GPU utilization for either card

## v8.7.5

### Fixes
- **Windows 12-hour time format crashes overlay rendering**: `%-I` strftime directive is Unix-only — Windows Python raises `ValueError: Invalid format string`. Replaced with cross-platform `%I` + `lstrip('0')` in both overlay renderer (`core/models.py`) and settings card paint (`overlay_element.py`)

### Improvements
- **Debug logging for settings signal chain**: Added logging to color picker, format/position/font changes, overlay config propagation, and LCD handler overlay updates — enables diagnosing GUI event flow issues on all platforms

## v8.7.4

### Fixes
- **Custom theme save loses mask after loading cloud video background**: `load_cloud_theme()` unconditionally wiped `_mask_source_dir` to `None`. Cloud video backgrounds don't carry masks — the user's applied cloud theme (mask) should persist. Now only overwrites mask source dir when the loader provides a non-None value
- **Windows build script NativeCommandError spam**: PyInstaller writes INFO to stderr, PowerShell treats it as errors. Wrapped PyInstaller calls in `cmd /c` to suppress

## v8.7.3

### Fixes
- **Windows SCSI handshake skip read — eliminates theme blinking**: `IOCTL_SCSI_PASS_THROUGH_DIRECT` read direction times out (error 121) on USB mass storage LCD devices. Skipped the poll read and default to FBL=100 (320x320). Init write still wakes the device for frame sends. No more 15-second handshake timeout blocking the GUI
- **Hexagonal violation — debug_report bypassed factory**: `_handshake_scsi()` directly instantiated `ScsiProtocol` instead of using `DeviceProtocolFactory.create_protocol()`. On Windows this always used the Linux SCSI path (`fcntl`). Fixed to route through the factory which selects `WindowsScsiProtocol` on Windows
- **Hexagonal violation — _device.py bypassed factory**: `_probe()` directly instantiated `BulkProtocol` instead of using the factory. Fixed for consistency
- **Installer post-launch shellexec**: `CreateProcess` from an admin installer can't re-elevate a UAC-manifest exe. Changed to `ShellExecute` which triggers proper UAC prompt

## v8.7.2

### Fixes
- **Windows exe requests admin elevation (UAC manifest)**: C# TRCC uses `requireAdministrator` for SCSI passthrough. Added `--uac-admin` to PyInstaller builds so both `trcc.exe` and `trcc-gui.exe` auto-prompt for elevation
- **Windows SCSI persistent transport**: Keep `DeviceIoControl` handle open across frames instead of open/close per frame — eliminates theme blinking
- **Windows SCSI handshake read timeout**: Increased poll timeout to 15s for VM/USB latency. Graceful fallback to FBL=100 (320x320) if poll returns empty
- **Windows installer kills running processes**: `PrepareToInstall` callback force-kills `trcc-gui.exe`/`trcc.exe` via taskkill before overwriting files. `CloseApplications` alone was unreliable
- **Windows installer uninstall cleanup**: `[UninstallRun]` kills processes before file removal. `[UninstallDelete]` removes autostart desktop entry
- **Windows `tzdata` dependency**: Added conditional dep for timezone data (Linux provides via OS, Windows needs the package)
- **Build script auto-PATH**: `build_windows.ps1` auto-adds Python Scripts dir to PATH for `pyinstaller`

## v8.7.1

### Fixes
- **Windows PhysicalDrive detection fails in PyInstaller builds**: WMI queries silently fail in frozen .exe, preventing SCSI devices from connecting. Added ctypes-based strategy 3: scans PhysicalDrive0..15 via `DeviceIoControl` `IOCTL_STORAGE_QUERY_PROPERTY`, filters by USB bus type, matches Thermalright LCD vendor strings. Falls back to single-USB-drive heuristic. No WMI dependency.
- **Better debug logging for all Windows drive detection strategies**: Each strategy now logs disk paths, PNP IDs, vendor strings, and USB parent matches to aid troubleshooting

### Tests
- 4 new tests for ctypes PhysicalDrive scan strategy (5269 total across 92 files)

## v8.7.0

### Features
- **Windows SCSI device communication**: New `WindowsScsiProtocol` uses `DeviceIoControl` SCSI passthrough instead of Linux `fcntl`/`sg_raw`. Windows users with SCSI LCD devices (0402:3922, 87CD:70DB, 0416:5406) can now send images to their displays
- **Windows SCSI read support**: Added `WindowsScsiTransport.read_cdb()` for SCSI read operations (handshake polling), completing the Windows DeviceIoControl transport

### Fixes
- **Windows `AttributeError: AF_UNIX` crash**: `_gui_running()` in `instance.py` called `socket.AF_UNIX` without checking if it exists on Windows. Added `hasattr` guard matching the existing pattern in `ipc.py`
- **Windows uninstaller leaves processes running**: Added `[UninstallRun]` to Inno Setup to kill `trcc-gui.exe`/`trcc.exe` before removing files. Added `[UninstallDelete]` to clean up autostart desktop entry

## v8.6.9

### Fixes
- **Windows SCSI device not found — USBSTOR PNPDeviceID lacks VID/PID (#68)**: `_find_physical_drive()` only matched `VID_xxxx` in the disk's PNPDeviceID, but Windows USBSTOR devices use vendor names instead (e.g. `VEN_USBLCD`). Added two-stage lookup: first try direct VID match, then confirm USB VID/PID via `Win32_USBControllerDevice` and match USBSTOR disks by known Thermalright vendor strings, with a size-based fallback (<1MB) for unknown vendors

### Tests
- 5 new tests for USBSTOR physical drive detection (5265 total across 92 files)

## v8.6.8

### Fixes
- **Windows SSL certificate verification failure on PyInstaller builds**: `download_archive()` failed with `CERTIFICATE_VERIFY_FAILED` because PyInstaller sets `SSL_CERT_FILE` to a bundled `cacert.pem` that doesn't exist at runtime. Now temporarily clears the env var so `ssl.create_default_context()` loads certificates from the OS store instead

### Tests
- 2 new tests for SSL cert env handling in download_archive (5260 total across 92 files)

## v8.6.7

### Fixes
- **Windows `NoBackendError` — pyusb can't find libusb (#68)**: Python 3.8+ on Windows doesn't search the exe's directory for ctypes DLLs. Added `os.add_dll_directory()` at startup so the bundled `libusb-1.0.dll` is found by pyusb
- **Windows `'NoneType' has no attribute 'hdd_enabled'` (#68)**: `_ensure_system()` created SystemService without initializing Settings first. Any CLI command touching metrics (`trcc test-lcd`, `trcc test-led`, `trcc info`) crashed. Now calls `_ensure_settings()` before SystemService creation
- **Wrong device button image for bulk device 87AD:70DB (#68)**: `_BULK_DEVICES` entry for GrandVision 360 AIO inherited the default `button_image="A1CZTV"` instead of `"A1GRAND VISION"`. On Windows where assets may not resolve, this caused a text fallback with the wrong device name

## v8.6.6

### Features
- **CLI/API parity**: Closed feature gaps between CLI and API adapters
  - **API**: `POST /themes/export` — download a theme as `.tr` archive
  - **API**: `POST /display/test` — run color cycle test on connected LCD
  - **API**: `POST /led/test` — software LED preview with real metrics (no device needed)
  - **CLI**: `trcc lang` — show current language
  - **CLI**: `trcc lang-set <code>` — set application language by ISO 639-1 code
  - **CLI**: `trcc lang-list` — list all available languages

### Tests
- 17 new tests covering all new endpoints and commands (5258 total across 92 files)

## v8.6.5

### Fixes
- **macOS .dmg broken — APFS case-insensitive symlink**: `ln -sf TRCC .../trcc` on case-insensitive APFS deleted the 12MB TRCC executable (macOS treats `trcc` and `TRCC` as the same file). Removed the CLI symlink from the .app bundle

### Improvements
- **Linux distro build logging**: RPM (Fedora), DEB (Ubuntu), and Arch CI builds now log build environment (distro version, Python, packager) and verify output (package size, entry points)
- **macOS CI verification**: uses `find -L` consistently for symlink-aware bundle inspection

## v8.6.4

### Fixes
- **Theme browsers empty after background extraction**: Background `ensure_all()` (v8.6.3) didn't notify the GUI when done. Now fires `on_data_ready` callback which refreshes theme browsers on the main thread via `QTimer.singleShot`

## v8.6.3

### Fixes
- **GUI unresponsive on startup (#70)**: `DataManager.ensure_all()` downloaded and extracted theme archives synchronously on the Qt main thread, freezing the GUI. Now runs in a background thread
- **Overlay scaled wrong on resume (#70)**: `_restore_overlay()` and `_load_theme_overlay_config()` called `set_config()` without `set_config_resolution()`. The overlay scale factor used a stale default resolution, causing elements to render at wrong size on non-square displays (e.g. 320x240 → 75% scale)

## v8.6.2

### Improvements
- **Early crash logging on all OS's**: `__main__.py` sets up `~/.trcc/trcc.log` before any imports — startup crashes are captured on Linux, Windows, macOS, and BSD
- **CI build logging**: Windows, macOS, and Linux workflows log build environment, bundle contents with sizes, and structured pass/fail verification

## v8.6.1

### Fixes
- **Windows: GUI doesn't launch from installer**: `trcc-gui.exe` ran `main()` which showed help and exited. Now detects `trcc-gui` executable name and auto-launches GUI

## v8.6.0

### Architecture
- **Settings DI**: `Settings` receives a `PlatformSetup` path resolver via constructor — no module-level singleton, no hardcoded path imports. `init_settings(resolver)` called by composition roots (CLI, GUI, API)
- **Path resolution through adapters**: `conf.py` no longer imports `get_web_dir`/`get_web_masks_dir`/`USER_DATA_DIR` from `paths.py`. All path resolution goes through `PlatformSetup` adapter methods
- **No import binding issues**: All files that access `settings` use `import trcc.conf as _conf` (module reference) instead of `from ..conf import settings` (bound import) — safe across `init_settings` re-binding
- **Pure tests**: No patches on path constants. Tests use DI via `init_settings(mock_resolver)` in conftest. 5241 tests, 0 failures

## v8.5.7

### Fixes
- **Windows: 7z extraction blocked by zip-slip check**: Archive path comparison failed due to forward/backslash mismatch — `os.path.normpath` normalizes both sides

## v8.5.6

### Improvements
- **Windows/macOS: ffmpeg bundled**: Video playback works out of the box — no manual ffmpeg install. Windows uses gyan.dev essentials build, macOS uses Homebrew
- **`ffmpeg_install_help()` on PlatformSetup ABC**: Each platform adapter provides its own install instructions — no hardcoded Linux messages
- **CI verifies ffmpeg in bundle**: Build fails if ffmpeg missing from installer package

## v8.5.5

### Improvements
- **Windows/macOS installers bundle libusb**: pyusb loads libusb via ctypes — PyInstaller misses it. Now bundled in both `.exe` and `.dmg`
- **CI verifies bundles**: Build fails if 7z, libusb, or app executables missing from installer package

## v8.5.4

### Improvements
- **Windows installer bundles 7z.exe**: 7-Zip standalone (LGPL) downloaded and included in the `.exe` installer. Theme extraction works out of the box

## v8.5.3

### Fixes
- **Windows: Qt CSS backslash escapes**: All paths passed to Qt use forward slashes — `QPixmap` and stylesheet `url()` no longer mangle Windows backslashes
- **macOS/BSD: asset copy tested**: `resolve_assets_dir()` verified on all 4 platforms

## v8.5.2

### Fixes
- **Windows: assets not loading**: Microsoft Store Python sandboxes packages in a deep path that mangles Chinese filenames. Each platform adapter resolves assets via `resolve_assets_dir()` ABC method — non-Linux adapters copy to `~/.trcc/assets/gui/` on first run
- **Windows: `wmi` auto-install**: Moved from optional to main dependency with `sys_platform == 'win32'` marker — installs automatically on Windows

### Architecture
- **`resolve_assets_dir()` on PlatformSetup ABC**: Pure hexagonal — no platform checks in `assets.py`. Linux adapter returns package dir, others copy to user dir. Wired via `ControllerBuilder.build_setup()` in `run_app()`

## v8.5.1

## v8.5.0

### Architecture
- **Pure hexagonal DI**: `lcd_device.py` and `led_device.py` have zero adapter imports — strict `RuntimeError` without deps. Only `builder.py` imports adapters. Architecture test enforces this
- **SensorEnumerator ABC**: `core/ports.py` — 9 abstract methods. All 4 platforms (Linux, Windows, macOS, BSD) inherit from it
- **PlatformSetup ABC**: Each OS has its own setup adapter (`LinuxSetup`, `WindowsSetup`, `MacOSSetup`, `BSDSetup`). `trcc setup` dispatches via `ControllerBuilder.build_setup()`
- **Cross-platform package managers**: Doctor dep system supports `winget` (Windows), `brew` (macOS), `pkg` (BSD) for auto-installing 7z, ffmpeg, libusb

### Fixes
- **Windows: sensor crashes**: `WindowsSensorEnumerator` was missing `map_defaults()`, `read_one()`, `set_poll_interval()`, and disk/network I/O. GUI and API hardcoded the Linux enumerator instead of routing through the builder
- **Windows: `charmap` codec crash**: CLI now forces UTF-8 stdout/stderr on Windows
- **Windows: GUI not showing**: `socket.AF_UNIX`, `signal.SIGUSR1`, `/tmp` lock path, and IPC sockets all crash on Windows. Guarded with platform checks. Lock file uses `~/.trcc/` on all platforms. IPC skipped on Windows
- **Rotation crushed on non-square displays**: 90°/270° on landscape devices (640x480, 800x480, etc.) sent swapped dimensions. `encode_for_device()` now resizes back to native dims before encoding

### Improvements
- **`trcc report` shows install method**: pip, pipx, pacman, dnf, apt, or .exe — helps triage immediately
- **5247 tests** across all hexagonal layers with ABC contract verification

## v8.4.12

### Improvements
- **`trcc report` shows install method**: Reports pip, pipx, pacman, dnf, apt, or .exe — helps triage user issues immediately

## v8.4.11

### Fixes
- **Rotation crushed on non-square displays**: 90°/270° rotation on landscape devices (640x480, 800x480, 1280x480, 1600x720, etc.) produced a squished image. The rotated image (480x640) was sent as-is but the device firmware expects native dimensions (640x480). Now `encode_for_device()` resizes back to native dims before encoding — matching C# behavior. Preview shows portrait orientation correctly

## v8.4.10

### Fixes
- **Windows: GUI crash on AF_UNIX/SIGUSR1**: `run_app()` used `socket.AF_UNIX` and `signal.SIGUSR1` unconditionally — both don't exist on Windows. Guarded with platform check
- **Windows: GUI silent exit on lock path**: Instance lock used `/tmp` (doesn't exist on Windows). Now uses `~/.trcc/` on all platforms
- **Windows: IPC server crash**: `IPCServer.start()` and `IPCClient` used `AF_UNIX` sockets. Skipped on Windows (no Unix domain sockets)
- **Windows: bare LEDDevice in IPC**: `run_app()` created `LEDDevice()` without builder deps. Now uses `ControllerBuilder.build_led()`

### Architecture
- **PlatformSetup ABC**: New port in `core/ports.py` — each OS has its own setup adapter (`LinuxSetup`, `WindowsSetup`, `MacOSSetup`, `BSDSetup`). `trcc setup` dispatches to the right one via `ControllerBuilder.build_setup()`
- **Cross-platform package managers**: Doctor dep system now supports `winget` (Windows), `brew` (macOS), `pkg` (BSD) alongside all Linux package managers
- **Platform-aware dep checks**: `check_system_deps()` skips Linux-only deps (sg_raw, libusb, libxcb-cursor) on other platforms
- **7z install help from adapter**: `data_repository.py` gets 7z install instructions from the platform setup adapter instead of hardcoded strings

## v8.4.8

### Fixes
- **Windows: GUI not showing**: `run_app()` used `socket.AF_UNIX` and `signal.SIGUSR1` unconditionally — both don't exist on Windows. The window was created but crashed before `window.show()` was reached. Guarded with platform check
- **Windows: bare LEDDevice in IPC**: `run_app()` created `LEDDevice()` without dependencies for IPC server. Now uses `ControllerBuilder.build_led()`
- **Windows: SIGUSR1 in raise_existing_instance**: Skipped on Windows (no Unix signals)

## v8.4.7

### Fixes
- **Windows: `map_defaults` crash**: `WindowsSensorEnumerator` was missing `map_defaults()`, `read_one()`, `set_poll_interval()`, and disk/network I/O reading — any command touching system metrics (`trcc test-lcd`, `trcc info`, GUI startup) crashed with `AttributeError`. Added all missing methods with Windows-native sensor mapping (LHM > pynvml > psutil)
- **Windows: `charmap` codec crash**: `trcc report` and `trcc setup-winusb` crashed with Unicode encoding error on Windows consoles using default codepage. CLI entry point now forces UTF-8 stdout/stderr on Windows
- **Windows: GUI used Linux sensor enumerator**: GUI and API hardcoded the Linux `SensorEnumerator` import instead of using `ControllerBuilder` which routes to the platform-correct adapter. Fixed all composition roots to use the builder

### Architecture
- **SensorEnumerator ABC**: New port in `core/ports.py` — 9 abstract methods enforcing the contract all platform sensor adapters must implement. All 4 platforms (Linux, Windows, macOS, BSD) now inherit from this ABC
- **Pure hexagonal DI**: `lcd_device.py` and `led_device.py` no longer import from `adapters/` — strict DI with `RuntimeError` if dependencies aren't injected via `ControllerBuilder`. Zero escape hatches
- **Architecture test tightened**: Only `builder.py` is exempted from the "no adapter imports in core/" rule (was 3 files). ABC contract tests verify all platform enumerators implement the interface

## v8.4.6

### Improvements
- **Windows: Zadig driver guidance**: `trcc setup-winusb` now prints step-by-step Zadig instructions with download link and lists all devices needing WinUSB. Removed unsigned `.inf` auto-install from installer (requires paid EV code signing certificate)
- **Windows: doctor WinUSB detection**: `trcc doctor` detects connected devices needing WinUSB and shows their driver status (installed vs needs install via Zadig)
- **Windows: detect hint**: `trcc detect` shows WinUSB guidance when no devices found on Windows

## v8.4.5

### Fixes
- **GUI not showing on Windows**: `_ensure_qt()` set `QT_QPA_PLATFORM=offscreen` for headless CLI rendering — `trcc gui` inherited it and rendered to a hidden surface. Now cleared before GUI launch. Affects all platforms but primarily Windows where the exe always routes through CLI
- **Windows installer upgrade**: Closes running TRCC before upgrade (avoids locked file errors). Cleans stale PyInstaller `_internal/` directory from previous version to prevent DLL conflicts. User data (`~/.trcc/`) is never touched

## v8.4.4

### Features
- **WinUSB driver installer**: Bundled `trcc-usb.inf` with all non-SCSI VID/PIDs (HID LCD, LED, Bulk, LY). Windows installer auto-installs via `pnputil`. Manual install: `trcc setup-winusb` (run as Administrator)

### Fixes
- **Windows: `os.geteuid()` crash**: Replaced all bare `os.geteuid()` calls with cross-platform `_is_root()` helper. Linux-only commands (`setup-udev`, `setup-selinux`, `setup-polkit`, `install-desktop`) now show "Linux only" message on Windows instead of crashing
- **Windows: hardware.py guard**: `_build_cmd()` guarded against missing `os.geteuid` on Windows

## v8.4.3

### Fixes
- **Windows: doctor shows Linux-only checks**: Skips libusb, sg_raw, 7z, udev, SELinux, RAPL, and polkit checks on non-Linux platforms
- **Windows: process usage crash**: `ps` command doesn't exist on Windows — uses `psutil` for cross-platform process listing
- **Windows: ANSI escape codes raw in PowerShell**: Enables virtual terminal processing via `SetConsoleMode` so colored output renders correctly
- **Windows: doctor distro name**: Shows `platform.platform()` instead of "Unknown" on non-Linux

## v8.4.2

### Fixes
- **Windows: `fcntl` module not found**: Deferred Unix-only `fcntl` import in `scsi.py` (moved to function level). Instance lock in `trcc_app.py` uses `msvcrt.locking` on Windows
- **Windows: `python-multipart` missing**: Moved from dev to main dependencies — FastAPI requires it at runtime for form/file uploads, PyInstaller `collect-submodules` failed without it
- **macOS: missing `app.icns`**: CI now generates `.icns` from `trcc.png` via `iconutil` at build time

## v8.4.1

### Fixes
- **Stream Vision device identity (#69)**: GUI showed "Frozen Warframe Pro" instead of "Stream Vision" for 87AD:70DB bulk devices — `_resolve_device_identity` used FBL (64) for button image lookup instead of raw PM (7) + SUB (1). Added `pm_byte`/`sub_byte` to `HandshakeResult` across all protocols (Bulk, LY, HID Type 2/3)
- **Windows bulk detection (#68)**: CLI `detect`, `report`, and other code paths imported the Linux-only detector directly, silently returning no devices on Windows/macOS/BSD. Made `detect_devices` alias platform-aware — routes to WMI (Windows), pyusb (macOS/BSD), or sysfs (Linux)
- **Debug report misleading PM**: `trcc report` printed FBL as "PM" for bulk/LY devices — now shows actual PM, SUB, and FBL separately
- **Debug report cross-platform**: Skips Linux-only sections (lsusb, udev, SELinux, RAPL, /dev/sg*) on non-Linux platforms; `_distro_name()` returns `platform.platform()` on Windows/macOS

### Infrastructure
- Added `wmi` to Windows optional deps (`[windows]` extra) and PyInstaller `--hidden-import`
- Guarded udev rules check in CLI `detect` for Linux only

### Tests
- 10 new tests for pm_byte/sub_byte propagation and button image lookup
- Updated mock targets from `DeviceDetector.detect` to platform-aware `detect_devices`
- 5217 total tests

## v8.4.0

### Features
- **FreeBSD/BSD adapter scaffolds**: USB detection (pyusb), SCSI passthrough (camcontrol `/dev/pass*`), sensors (sysctl coretemp/amdtemp + ACPI thermal zones + psutil + pynvml), hardware info (sysctl + geom)
- **Cross-platform install docs**: README now has install sections for Windows (.exe), macOS (.dmg), and FreeBSD (pip)
- **Clean gold bar assets**: Removed baked-in "TRCC" text from 10 device/panel background PNGs — title rendered via QLabel overlay as "TRCC-Linux"

### Tests
- 37 new tests for BSD adapters (mocked sysctl/camcontrol/pyusb — run on Linux CI)
- 5207 total tests across 4 platforms (Linux, Windows, macOS, BSD)

## v8.3.13

### Improvements
- **Clean gold bar assets**: Removed baked-in "TRCC" text from 10 device/panel background PNGs — title now rendered purely via QLabel overlay as "TRCC-Linux"

## v8.3.12

### Features
- **macOS port scaffold**: Platform-conditional adapters for macOS — USB detection (pyusb), SCSI passthrough (USB BOT with kernel driver detach), hardware info (system_profiler), sensor enumerator (IOKit SMC for Intel, powermetrics for Apple Silicon, pynvml for eGPU)
- **macOS CI pipeline**: GitHub Actions builds macOS `.dmg` installer (PyInstaller + create-dmg) on tag push — universal app bundle with drag-to-Applications

### Tests
- 37 new tests for macOS adapters (mocked IOKit/powermetrics/pyusb — run on Linux CI)
- 5170 total tests

## v8.3.11

### Features
- **Windows port scaffold**: Platform-conditional adapters for Windows — USB detection (WMI), SCSI passthrough (DeviceIoControl), hardware info (WMI), sensor enumerator (LibreHardwareMonitor + pynvml fallback)
- **LibreHardwareMonitor GPU sensors**: Windows-exclusive sensors via NVAPI — GPU Hotspot temp, Memory Junction temp, GPU Core Voltage — not available on Linux or via pynvml
- **Windows installer pipeline**: GitHub Actions builds Windows installer (PyInstaller + Inno Setup) on tag push — `trcc.exe` (CLI) + `trcc-gui.exe` (GUI)
- **Platform detection**: `core/platform.py` — `WINDOWS`, `LINUX`, `MACOS`, `BSD` flags for conditional adapter wiring

### Tests
- 83 new tests for Windows adapters (mocked WMI/LHM/pynvml — run on Linux CI)
- 5133 total tests

## v8.3.10

### Features
- **Language API endpoints**: `GET /system/languages`, `GET /system/language`, `PUT /system/language/{code}` — list, get, and set application language via REST API (46 total endpoints)

## v8.3.9

### Features
- **Standard i18n architecture**: Restructured `core/i18n.py` from 44 separate per-string translation dicts to standard `TRANSLATIONS[lang_code][english_key]` pattern — `tr('Layer Mask', lang)` instead of `tr(MASK_TITLE, lang)`
- **8 new languages**: Bengali, Urdu, Farsi, Tagalog, Tamil, Punjabi, Swahili, Burmese — 38 languages total, covering major and secondary world languages

### Internal
- **Simplified call sites**: `trcc_app.py` and `display_mode_panels.py` use English string keys directly — no more importing 44 dict constants
- **Cleaner `_i18n_labels`**: Stores `str | None` (English key) instead of `dict[str, str] | None` (entire translation table reference)

## v8.3.8

### Features
- **i18n text overlays**: All GUI panel text now rendered at runtime via QLabel overlays from `core/i18n.py` — supports 30 languages without per-language PNG files
- **Custom mask upload**: File picker → crop → save to `~/.trcc-user/` (survives uninstall), with right-click delete in mask browser
- **Language dropdown**: Combo selector replaces 10 language checkboxes on About/Control Center panel
- **Mask panel X/Y controls**: Position inputs with +/- buttons and eye toggle for mask visibility

### Improvements
- **Language-neutral GUI PNGs**: Replaced 129 localized background PNGs with 8 language-neutral versions — text rendered at runtime
- **Consistent panel titles**: All display mode panels (mask, background, screencast, video) now have title label next to toggle at top
- **Clean Ctrl+C exit**: SIGINT handler calls `app.quit()` instead of Python traceback

## v8.3.7

### Internal
- **Hexagonal purity**: `services/perf.py` no longer imports from `adapters/device/` — `DeviceDetector.detect`, `DeviceProtocolFactory.get_protocol`/`get_protocol_info`, and `probe_led_model` are now injected via DI by composition roots (CLI `trcc perf --device` and API `GET /system/perf/device`)

## v8.3.6

### Bug Fixes
- **Fixed**: Saved mask overlay missing on first launch after package update — reference-based themes with embedded mask paths caused a double-load: theme loader loaded the mask and built the video cache, then `_restore_mask` re-loaded the same mask and invalidated the cache. Now skips redundant mask restore when theme already loaded it
- **Fixed**: Activity sidebar (hardware sensors) appeared when clicking any overlay element type (Time, Weekday, Date, Custom Text) — now only shows when "Hardware Data" is selected. Non-hardware types add directly without the sidebar
- **Fixed**: Activity sidebar stayed visible when switching tabs or views — now hides on tab switch (Local/Cloud/Masks) and view switch (About/System Info/LED)

## v8.3.5

### Bug Fixes
- **Fixed**: Frozen Warframe 240 (FBL 51/53, PM=51) showing wrong/inverted colors — byte order was incorrectly set to big-endian (SPIMode=2) but HID Type 2 devices use little-endian. C# only sets SPIMode=2 for SPI mode 1 devices (not supported on Linux). Affects Frozen Warframe 240 and LF20 variants (#65, #67)

## v8.3.4

### Bug Fixes
- **Fixed**: `POST /themes/init` downloads theme data but static file mounts (`/static/themes/`, `/static/web/`, `/static/masks/`) return 404 — `mount_static_dirs()` was not called after `DataManager.ensure_all()`. Now remounts FastAPI static directories immediately after download

## v8.3.3

### Features
- **Device pairing for TRCC Remote**: `trcc serve` displays a 6-character pairing code in the terminal. Phone enters the code once → receives a persistent API token → stays paired across server restarts. No re-scanning needed
- **Persistent API token**: Token auto-generated on first run and stored in `~/.trcc/config.json`. Survives restarts — phone pairs once, connects forever. `--token` flag overrides with explicit token
- **`POST /pair` endpoint**: Auth-exempt endpoint that validates the pairing code and returns the persistent token. Case-insensitive code matching
- **`POST /themes/init` endpoint**: Remote apps call this on startup to pre-download theme/web/mask archives for a resolution. No-op if already cached
- **Standalone theme data init**: `DataManager.ensure_all()` called during device select in standalone mode — theme directories are populated before the phone asks for them
- **Lazy theme data download**: `GET /themes`, `GET /themes/web`, `GET /themes/masks` each call their respective `ensure_*()` before scanning — handles resolution queries for devices not yet selected

### Internal
- **Auth middleware refactored**: `_AUTH_EXEMPT` set for paths that bypass token auth (`/health`, `/pair`)
- **Tests**: 4802 total (+18 new) — `TestPairing` (5), `TestPersistentToken` (7), `TestStandaloneThemeInit` (6)

## v8.3.2

### Features
- **Bidirectional instance detection with DI**: Any trcc instance (CLI, API) automatically detects if another instance (GUI or API) is already running and routes commands through it instead of touching USB directly. Uses dependency injection — `find_active_fn` and `proxy_factory_fn` injected into `LCDDevice`/`LEDDevice` by composition roots
- **`InstanceKind` enum + `find_active()`**: Pure core module (`core/instance.py`) for instance detection — checks GUI IPC socket > API health endpoint > None. Priority: GUI > API
- **Proxy factory functions**: `create_lcd_proxy(kind)` and `create_led_proxy(kind)` in `ipc.py` return correct proxy type (IPC for GUI, HTTP for API)
- **`@_forward_to_proxy` decorator**: Transparent method forwarding on `LEDDevice` — 13 public methods auto-forward to proxy when connected through another instance

### Internal
- **Eliminated `IPCClient.available()`** from all `src/` code — replaced with `find_active()` everywhere (CLI, API, perf benchmarks)
- **Composition root wiring**: CLI `_display.py`/`_led.py` and API `devices.py` inject `find_active` + proxy factories into core devices
- **Tests**: 4784 total (+25 new) — `TestInstanceKind` (2), `TestFindActive` (4), `TestCreateLcdProxy` (2), `TestCreateLedProxy` (2), `TestLCDDeviceProxyRouting` (5), `TestLEDDeviceProxyRouting` (6), updated API/CLI instance detection tests (4)

## v8.3.1

### Features
- **QR code on `trcc serve`**: Terminal QR code at startup containing `{"host","port","token","tls"}` JSON — scan with TRCC Remote to connect instantly. Auto-detects LAN IP when listening on all interfaces. Requires optional `qrcode` package (`pip install trcc-linux[remote]`)
- **`ServerInfo` DTO**: Frozen dataclass in `core/models.py` for QR code payload serialization
- **`get_lan_ip()` adapter**: Network infrastructure adapter for LAN IP auto-detection (`adapters/infra/network.py`)

### Bug Fixes
- **Fixed**: `POST /themes/load` crashes API server — endpoint was calling services directly instead of routing through `LCDDevice` dispatcher. Now delegates to `lcd.load_theme_by_name()`, works in both standalone and IPC (GUI daemon) modes
- **Fixed**: `POST /display/mask` returns "unknown command" in IPC mode — `load_mask_standalone` was missing from `_DISPLAY_ROUTES` in `ipc.py`
- **Fixed**: `load_theme_by_name` IPC route missing — added to `_DISPLAY_ROUTES` for GUI daemon passthrough

### Internal
- **Tests**: Added 25 tests across 3 hexagonal layers — `TestServerInfo` (4), `TestGetLanIp` (2), `TestPrintServeQr` (7), `TestLoadThemeByName` (4), `TestIPCDisplayRoutes` (2), updated API theme tests (6)
- **Architecture**: `POST /themes/load` reduced from ~70 lines of inline business logic to ~20-line thin adapter over `LCDDevice.load_theme_by_name()`

## v8.3.0

### Bug Fixes
- **Fixed**: Theme browsers (local, cloud, masks) empty on first run — `_update_theme_directories()` was only called on resolution change, but first run default (320x320) matched device so refresh never triggered. Now always refreshes after device configuration.

## v8.2.10

### Features
- **Device benchmarks**: `trcc perf --device` / `trcc perf -d` — benchmark connected LCD/LED hardware (USB handshake, frame encode, send latency, sustained FPS)
- **IPC pause/resume**: Device benchmarks automatically pause the GUI daemon's display refresh for exclusive USB access, resume on completion (even on crash via `try/finally`)
- **API endpoint**: `GET /system/perf/device` — device I/O benchmarks via REST API

### Internal
- **Tests**: Added 74 tests for device benchmarks (`tests/core/test_perf.py`, `tests/services/test_perf.py`, CLI + API integration) — 4741 total tests
- **Domain**: `PerfReport` extended with `device` section (record, serialize, format as Valgrind-style terminal report)

## v8.2.5

### Internal
- **Tests**: Added 184 service-level unit tests across 6 new test files (`test_led_config.py`, `test_led_effects.py`, `test_media.py`, `test_device.py`, `test_system.py`, `test_theme_loader.py`) — 4660 total tests
- **Refactor**: Split `uc_theme_setting.py` (1563 lines, 8 classes) into 5 SRP-focused modules: `overlay_element.py`, `overlay_grid.py`, `color_and_add_panels.py`, `display_mode_panels.py`, + thin orchestrator with backward-compatible re-exports

## v8.2.4

### Bug Fixes
- **Fixed**: `trcc uninstall` fails with PEP 668 `externally-managed-environment` on Arch/CachyOS (#63) — now detects install method (pacman/dnf/apt/pipx/pip), only runs pip when appropriate, adds `--break-system-packages` on PEP 668 distros
- **Fixed**: `trcc uninstall` cleans stale `~/.local/bin/trcc` shadow binary from old pip/pipx installs
- **Cleanup**: Removed redundant local `import shutil` statements in `_system.py` — single module-level import

## v8.2.3

### Security
- **Fixed**: CodeQL `py/path-injection` alert (#15) in `/display/overlay` — replaced `pathlib.resolve()`/`is_relative_to()` with `os.path.realpath()`/`startswith()` sanitizer pattern that CodeQL recognizes

## v8.2.2

### Bug Fixes
- **Fixed**: Config writes now use `fsync` to survive unexpected shutdowns

## v8.2.1

### Bug Fixes
- **Fixed**: RPM shebang normalization — `release.yml` uses `find + sed` to fix non-standard shebangs from build containers (e.g. `/usr/sbin/python3` on immutable distros)

## v8.2.0

### Security
- **Fixed**: Path traversal in `/display/overlay` — `dc_path` now validated against `USER_DATA_DIR` with null byte + `..` prevention
- **Fixed**: Stack trace leakage in `/themes/import` — exceptions no longer expose internal paths or tracebacks to clients
- **Fixed**: Theme ID injection in `/themes/web/{theme_id}/download` — regex-validated alphanumeric only
- **Fixed**: Unsanitized download filename in update handler — `Path.name` strips traversal attempts
- **Fixed**: `shlex.split()` for subprocess commands in CLI system module (prevents shell injection)
- **Fixed**: PYTHONPATH ordering in sudo re-exec — site-packages first, dev clone last (#47)
- **Added**: Dedicated security test suite (`tests/api/test_api_security.py`) — 18 tests covering path traversal, info leakage, upload limits, input validation

### Improvements
- **Added**: Release trigger words (`patch`, `minor`, `major`) in CLAUDE.md for streamlined release workflow

## v8.1.1

### Bug Fixes
- **Fixed**: Sub-screen mask overlays (000a, 000b, 000d, etc.) placed at top-left (0,0) instead of correct position. `LCDDevice._parse_mask_position()` never read the DC file's center coordinates; `ThemeService._parse_mask_position()` returned `None` instead of centering as fallback. Both now read DC `mask_position` and convert center→top-left, or center the mask by default (matching C# `ThemeMask` panel behavior).

### Docs
- **Added**: `doc/REFERENCE_API.md` — full reference for all 43 REST API endpoints (devices, display, LED, themes, system, WebSocket preview stream).

## v8.1.0

### Architecture
- **Strict Dependency Injection**: All service constructors now raise `RuntimeError` if required adapter dependencies are not provided. No lazy fallback imports in services — hexagonal purity enforced.
- **Composition roots fully wired**: `builder.py`, `cli/` functions, `api/__init__.py`, and `lcd_device.py:_build_services()` explicitly inject all adapter deps (detector, factory, decoders, DC config, data repository).
- **Services never import adapters**: Only one accepted exception — `SystemService._get_instance()` acts as a mini composition root for the convenience singleton.
- **LCDDevice stores DC deps**: `dc_config_cls` and `load_config_json_fn` injected at construction, used by `render_overlay_from_dc()` and `load_mask_standalone()` without adapter imports.
- **CLI call sites fixed**: `_display.py`, `_led.py`, `_theme.py` all inject concrete adapter deps instead of bare service construction.
- **API call sites fixed**: `api/__init__.py` and `api/themes.py` inject DC deps into OverlayService and ThemeService.
- **LEDDevice wiring**: `led_device.py:initialize()` passes `get_protocol` to LEDService.
- **conftest fixtures**: Shared DI-wired service fixtures in `tests/services/conftest.py`.
- 4112 tests across 57 files in 9 directories

### Bug Fixes
- **Fixed**: Cloud theme thumbnail blank after download — `_on_download_complete()` now calls `_set_movies_running(True)` when panel is visible (QMovie lifecycle fix).

## v8.0.1

### Bug Fixes
- **Fixed**: Theme save missing mask, double overlay, stale state — `load_mask_standalone()` now wires `_mask_source_dir` on DisplayService
- **Fixed**: Saved config.json had `mask: null` — theme reload lost overlay
- **Fixed**: Save was passing overlay-composited image as `00.png` (double overlay on reload)

### Architecture
- **Hexagonal purity (v8.0.0)**: Dependencies point inward only (adapters → services → core). No fallback imports in services. Composition roots wire concrete adapters.
- **Capability classes inlined**: ThemeOps, VideoOps, OverlayOps, FrameOps, DisplaySettings dissolved into LCDDevice directly
- **DeviceProfile table**: Replaces scattered encoding logic with single data-driven lookup
- **CPU optimization**: 34% → 9% idle CPU — eliminated double sensor polling, invisible widget updates, bulk video pre-encoding
- **Test restructuring (v8.0.2)**: 53 test files reorganized into hexagonal directories (`tests/{core,services,adapters/{device,infra,system},cli,api,gui}/`). Merged duplicates, dissolved `hid_testing/`, deleted 22 mock-wiring tests.
- 4022 tests across 53 files in 9 directories

## v7.1.1

### Bug Fixes
- **Fixed**: PermissionError on system-wide installs (#51) — `_find_data_dir()`, `get_web_dir()`, `get_web_masks_dir()` fell back to read-only package path (`/usr/lib/python3.x/.../trcc/data/`). Now falls back to `~/.trcc/data/` (user-writable)
- **Fixed**: CodeQL stack-trace-exposure alert (CWE-209) — restructured preview endpoint exception handling

## v7.1.0

### Bug Fixes
- **Fixed**: Scrambled display on bulk USB devices (#54) — `BulkDevice.handshake()` and `LyDevice.handshake()` returned `model_id=PM` instead of `model_id=FBL`. PM=32 mapped to JPEG encoding instead of RGB565, sending JPEG bytes with cmd=3 header
- **Fixed**: Theme not restoring on boot — `_restore_theme()` fallback used `persist=False`, so `--last-one` autostart never saved. Now uses `persist = not saved`
- **Fixed**: Duplicate concurrent handshakes from device poll timer — added `_handshake_pending` guard
- **Fixed**: Per-frame DEBUG log spam (~30/sec) rotated out useful INFO messages within seconds of video playback. Removed hot-path debug logging from display, device, image services, and factory
- **Added**: All C# v2.1.2 bulk PM values to `_BULK_KNOWN_PMS`

## v7.0.10

### Bug Fixes & Cloud Parity
- **Fixed**: Bulk protocol RGB565 encoding — compute `use_jpeg` from protocol+FBL, not mutable field
- **Fixed**: Stack trace exposure in API preview endpoint (CWE-209) — wrapped `_encode_frame` in try/except
- **Fixed**: Missing dependencies in all distro packages (RPM, DEB, Arch inline specs in `release.yml`)
- **Added**: Full C# v2.1.2 cloud theme resolution parity — all 32 resolutions in `theme_cloud.py` RESOLUTION_URLS (landscape, portrait, u/l split variants)
- **Added**: `tools/check_pkg_deps.py` — queries Arch/Fedora/Debian repos to verify PyPI dep availability per distro
- 4157 tests across 56 files

## v7.0.6

### SOLID Device ABCs — Replace Controller Layer
- **Added**: `Device` ABC in `core/ports.py` — 4 methods (connect, connected, device_info, cleanup)
- **Added**: `LCDDevice` in `core/lcd_device.py` — composed capabilities (ThemeOps, VideoOps, OverlayOps, FrameOps, DisplaySettings), each delegates to services
- **Added**: `LEDDevice` in `core/led_device.py` — direct methods (set_color, set_mode, tick, zone/segment ops), delegates to LEDService
- **Added**: `ControllerBuilder` in `core/builder.py` — fluent builder, returns concrete `LCDDevice`/`LEDDevice` types
- **Added**: `TRCCApp` in `gui/trcc_app.py` — thin QMainWindow shell (C# Form1 equivalent)
- **Added**: `LCDHandler` in `gui/lcd_handler.py` — one per LCD device (C# FormCZTV equivalent)
- **Deleted**: `core/controllers.py` (LCDDeviceController + LEDDeviceController), backward compat aliases (DisplayDispatcher, LEDDispatcher), 197 dead tests
- **Slimmed**: CLI `_display.py` and `_led.py` — thin print wrappers using `_connect_or_fail()` → call device method → print result
- 4157 tests across 56 files

## v7.0.5

### QtRenderer — Eliminate PIL from Hot Path
- **Added**: Expanded `Renderer` ABC in `core/ports.py` — apply_brightness, apply_rotation, encode_rgb565, encode_jpeg, open_image, surface_size
- **Added**: `QtRenderer` in `adapters/render/qt.py` — full QImage/QPainter implementation for compositing, text, rotation, brightness, RGB565/JPEG encoding, font resolution. Zero PIL in hot path
- **Added**: Same new methods in `PilRenderer` (`adapters/render/pil.py`) as fallback
- **Refactored**: `ImageService` is now a thin facade — all methods delegate to `_renderer` via `set_renderer()` / `_r()`. Defaults to QtRenderer
- **Fixed**: Font pixel sizing — `QFont.setPixelSize(size)` instead of `QFont(family, size)` which interprets as points
- **Added**: Test infrastructure — `conftest.py` helpers `make_test_surface()`, `surface_size()`, `get_pixel()`
- 4157 tests across 56 files

## v7.0.4

### API DRY Refactoring
- **Refactored**: Extracted `require_connected()` into `api/models.py` — eliminated 4 duplicated dispatcher guard patterns across `display.py`, `led.py`, `themes.py`
- **Removed**: Unused `HTTPException` import from `led.py`
- 4646 tests across 54 files

## v7.0.3

### Explicit Click Dependency
- **Fixed**: `ModuleNotFoundError: No module named 'click'` on CachyOS — we import `click.exceptions` directly but only declared `typer` (transitive dep). Some install methods don't resolve transitive deps
- **Added**: `click` as explicit dependency in `pyproject.toml` and all 5 distro packaging files (Arch, RPM, DEB, Gentoo)
- Addresses #50
- 4646 tests across 54 files

## v7.0.2

### SOLID Device Architecture
- **ISP**: Split `DeviceProtocol` god interface into `LCDMixin` (send_image, send_pil) + `LEDMixin` (send_led_data) — LCD callers no longer see LED methods and vice versa
- **LSP**: Removed `LedProtocol.send_image()` returning False and `DeviceProtocol.send_led_data()` default — no more lying interfaces
- **DIP**: Injected protocol factory into `DeviceService` via `get_protocol` param + `_get_proto()` method — no more hardcoded imports
- **SRP**: Moved `detect_lcd_resolution()` from `DeviceService` to `ScsiDevice.detect_resolution()` — SCSI-specific code in SCSI adapter
- **OCP**: Added `@DeviceProtocolFactory.register()` decorator for self-registering protocols — new protocols don't edit the factory class
- 4646 tests across 54 files

## v7.0.1

### GoF File Renames
- **Renamed**: 13 files in `adapters/device/` to `{pattern}_{name}.py` format:
  - `factory.py` → `abstract_factory.py`
  - `frame.py` → `template_method_device.py`
  - `hid.py` → `template_method_hid.py`
  - `scsi.py` → `adapter_scsi.py`
  - `bulk.py` → `adapter_bulk.py` (+ `_template_method_bulk.py` base)
  - `ly.py` → `adapter_ly.py`
  - `led.py` → `adapter_led.py`
  - `led_kvm.py` → `adapter_led_kvm.py`
  - `lcd.py` → `facade_lcd.py`
  - `detector.py` → `registry_detector.py`
  - `led_segment.py` → `strategy_segment.py`
  - `hr10.py` → `adapter_hr10.py`
- 4646 tests across 54 files

## v7.0.0

### Major Architecture Overhaul
- GoF file renames (v7.0.1) + SOLID refactoring (v7.0.2) — complete device protocol architecture cleanup
- Every adapter file named by its primary design pattern
- Protocol interfaces properly segregated (ISP), no lying defaults (LSP), factory self-registers (OCP)
- 4646 tests across 54 files

## v6.6.3

### Metrics Mediator + CPU Optimization
- **Added**: `MetricsMediator` — single timer for all sensor polling, replaces per-widget timers
- **Added**: Persistent USB send worker thread (reused across frames, idle timeout 30s)
- **Added**: Preview throttle (4 FPS) to reduce PIL→QPixmap conversions
- **Fixed**: LCD blink bug — identity check was skipping sends when overlay cache hit
- **Added**: `on_frame_sent` callback on `DeviceService` for frame capture (API preview)
- 4496 tests across 54 files

## v6.6.1

### LCD Preview Stream + API Video Control
- **Added**: WebSocket `/display/preview/stream` — steady-fps JPEG stream of LCD frame
- **Added**: Direct IPC frame read from GUI daemon (no poll thread)
- **Added**: Video playback background thread for standalone API mode
- **Added**: Overlay metrics loop for standalone themes
- **Added**: API spec doc + Flutter remote guide
- 4494 tests across 54 files

## v6.5.2

### Video Background Save Fix
- **Fixed**: Custom theme save renamed all video files to `Theme.zt` regardless of format — MP4 files got wrong decoder on reload, causing black screen
- **Fixed**: Save now preserves original video extension (`.mp4` stays `.mp4`, `.zt` stays `.zt`)
- **Added**: Fallback decoder for `.zt` files that fail magic check (handles old broken saves)
- Addresses #42
- 4440 tests across 54 files

## v6.5.1

### CodeQL Fix
- **Fixed**: CodeQL false positive — URL substring check in test replaced with `urlparse().hostname`
- 4440 tests across 54 files

## v6.5.0

### IPC Daemon — GUI-as-Server
- **Added**: Unix domain socket IPC — when GUI is running, it owns USB device exclusively. CLI commands auto-route through socket instead of fighting over device
- **Added**: `IPCServer`, `IPCClient`, `IPCDisplayProxy`, `IPCLEDProxy` in new `src/trcc/ipc.py`
- **Added**: Method whitelist routing with `_DISPLAY_METHODS` and `_LED_METHODS` for security
- **Added**: QSocketNotifier integration for non-blocking socket I/O in Qt event loop
- **Changed**: Info module (sensor metrics bar) decoupled from overlay toggle
- **Added**: `show_info_module` config setting (default: false) in `~/.config/trcc/config.json`
- Addresses #48
- 4440 tests across 54 files

## v6.4.1

### Info Module Decoupling
- **Changed**: Sensor metrics bar visibility no longer tied to overlay enable/disable
- **Added**: `show_info_module` config toggle (default: off)
- 4440 tests across 54 files

## v6.4.0

### Test Suite Expansion
- **Added**: 1995 new tests (2445 → 4440), 18% coverage increase (58% → 76%), 15 new test files (39 → 54)
- **Added**: Session-scoped `qapp` fixture for headless Qt testing
- **Added**: `@pytest.fixture` patterns with `autouse=True` for module-wide mocking
- 4440 tests across 54 files

## v6.3.7

### Codebase Minimization
- **Refactored**: Trimmed 694 lines of shipped source — collapsed glue code, generic dispatch helpers
- 4440 tests across 54 files

## v6.3.6

### CLI-Centric Simplification
- **Refactored**: Generic dispatch helpers collapse CLI glue code
- 2523 tests across 39 files

## v6.3.5

### DRY Refactoring
- **Refactored**: USB Device Factory, shared API helpers, LED dispatcher cleanup
- 2523 tests across 39 files

## v6.3.4

### Bug Fixes
- **Fixed**: Minor fixes and improvements
- 2523 tests across 39 files

## v6.3.3

### Single-Instance Window Raise
- **Improved**: Second `trcc gui` launch raises existing window instead of silently exiting — uses lock file + signal
- 2523 tests across 39 files

## v6.3.2

### Device Button Image & Product Name Resolution
- **Improved**: PM-based device button image selection after handshake (C# `SetButtonImage` parity)
- **Improved**: Product name resolution from PM byte — sidebar button shows correct product name instead of generic VID:PID label
- 2523 tests across 39 files

## v6.3.1

### Device Naming Fix
- **Fixed**: Device naming for `0402:3922` — now shows "Frozen Warframe / Elite Vision" instead of hardcoded "FROZEN WARFRAME". Both products share the same USB ID. Vendor corrected from "ALi Corp" to "Thermalright".
- Addresses #46
- 2523 tests across 39 files

## v6.3.0

### SOLID Refactoring
- **Refactored**: Data-driven protocol configuration — protocol parameters (chunk sizes, headers, encoding modes) stored as data instead of scattered across code branches
- **Refactored**: Dependency inversion across adapters — constructor injection, ABC ports at boundaries
- **Refactored**: SRP splits across oversized modules
- 2523 tests across 39 files

## v6.2.5

### SCSI Detection Fix (CachyOS/Arch)
- **Fixed**: SCSI detection on distros without `sg` kernel module — CachyOS, Arch, and others don't autoload `sg`, so `/dev/sg*` doesn't exist even when the device is connected
- **Added**: Block device fallback — detector now tries `/dev/sd*` with USBLCD vendor check when `/dev/sg*` is unavailable (`SG_IO` ioctl works on both)
- **Added**: `trcc setup-udev` writes `/etc/modules-load.d/trcc-sg.conf` to autoload `sg` on boot + loads it immediately
- **Refactored**: `_resolve_usblcd_vid_pid()` extracted to DRY the vendor/product check
- Addresses #46
- 2517 tests across 39 files

## v6.2.4

### DRY Refactoring
- **Refactored**: `parse_hex_color()` → `core/models.py` — was duplicated in CLI, API display, and API LED modules
- **Refactored**: `dispatch_result()` → `api/models.py` — was duplicated in API display and API LED modules
- **Refactored**: `ImageService.encode_for_device()` → `services/image.py` — Strategy pattern, was duplicated in device service and display service
- 16 files changed, -50 net lines of duplicated logic, +28 tests with fixtures
- 2509 tests across 39 files

## v6.2.3

### HiDPI Scaling & Theme Restore Fix
- **Fixed**: HiDPI scaling override — force-set `QT_ENABLE_HIGHDPI_SCALING=0` to prevent GUI layout corruption on HiDPI displays (CachyOS, KDE Plasma)
- **Fixed**: Stale background path on custom theme restore — saved themes with deleted backgrounds showed black instead of falling back gracefully
- Addresses #42
- 2481 tests across 39 files

## v6.2.2

### LY Protocol Integration & PM/FBL Overrides
- **Fixed**: LY protocol integration gaps — GUI poll, JPEG encoding, udev rules, display path, and debug report all missed `ly` protocol type. 7 code paths that branched on protocol string now include LY.
- **Added**: PM→FBL overrides (PM 13-17, 50, 66, 68, 69) + FBL 192/224 disambiguation from C# v2.1.2
- **Refactored**: `discover_resolution()` extracted for unified PM→FBL→resolution pipeline
- Addresses #45
- 2481 tests across 39 files

## v6.2.1

### `trcc api` CLI Command
- **New**: `trcc api` command — lists all 41 REST API endpoints with method, path, and description
- 2445 tests across 39 files

## v6.2.0

### REST API Static File Serving
- **New**: Serve theme/web/mask images via REST API `StaticFiles` — resolution-aware mounts on device select
- **New**: `GET /themes/web` and `GET /themes/masks` endpoints
- **New**: `ThemeResponse` includes `preview_url`, `WebThemeResponse` and `MaskResponse` models
- 2445 tests across 39 files

## v6.1.10

### FastAPI Base Dependencies
- **Changed**: Moved `fastapi` + `uvicorn` from optional `[api]` extra to base dependencies — REST API is always available, no separate install needed
- Prepares for Android companion app integration
- 2439 tests across 39 files

## v6.1.9

### TLS Support for REST API
- **New**: `--tls` flag auto-generates self-signed certificate for HTTPS
- **New**: `--cert` / `--key` flags for custom TLS certificates
- **New**: Plaintext token warning when running without TLS
- 2439 tests across 39 files

## v6.1.8

### LY Protocol Support
- **New**: LY protocol handler for `0416:5408` (Peerless Vision) and `0416:5409` (variant) — chunked 512-byte bulk transfer with 16-byte header + 496 data bytes
- **New**: `LyDevice` class with handshake, PM extraction, and JPEG frame encoding from TRCC v2.1.2 `USBLCDNEW.dll`
- **New**: Two PID variants with different PM formulas (LY: `64+resp[20]`, LY1: `50+resp[36]`)
- Addresses #45
- 2439 tests across 39 files

## v6.1.7

### Bulk Encoding, HiDPI & Theme Background Fix
- **Fixed**: Bulk PM=32 distorted colors — `use_jpeg` DTO field wasn't propagated, raw RGB565 sent when device expected JPEG
- **Fixed**: HiDPI GUI scaling — set `QT_ENABLE_HIGHDPI_SCALING=0` environment variable before Qt init
- **Fixed**: Black background on saved custom themes — `background_path` was null when theme had no explicit background
- 2408 tests across 39 files

## v6.1.6

### RAPL Power Sensor Permissions
- **Added**: RAPL power sensor permission check and fix in `trcc setup` pipeline — Intel CPU power sensors (`/sys/class/powercap/intel-rapl/`) need read permission for non-root users
- 2399 tests across 39 files

## v6.1.5

### Portrait Cloud Directory Fix
- **Fixed**: Non-square displays (e.g. 1280x480 Trofeo Vision) mounted vertically were loading cloud backgrounds/masks from the landscape directory instead of portrait directory
- **Added**: `Settings.resolve_cloud_dirs(rotation)` — swaps width/height for web_dir and masks_dir when rotation is 90/270 on non-square displays
- Addresses #1
- 2399 tests across 39 files

## v6.1.4

### LED GUI Settings & Theme Restore Fix
- **Fixed**: LED GUI settings not syncing on startup — `load_config()` correctly restored LED state from `config.json` (effects worked), but `panel.initialize()` reset all controls to defaults. Added `_sync_ui_from_state()` to push loaded state into UI controls after initialization.
- **Fixed**: `--last-one` theme restore overwriting saved preference — auto-fallback (first available theme when saved path missing) was persisting to config via `_select_theme_from_path()`, silently overwriting the user's saved theme. Now fallback loads for display only (`persist=False`).
- Note: v6.1.3 was the original release; v6.1.4 is a re-release because PyPI rejects reuse of version+filename after a tag was moved.
- Addresses #15
- 2394 tests across 34 files

## v6.1.2

### AK120 & LC1 LED Wire Remap Fix
- **Fixed**: AK120 (style 3) LED wire remap — all 64 entries wrong, indices up to 68 (beyond valid range 0-63). Same root cause as v6.1.1: remap tables built using constructor default `UCScreenLED` indices instead of style-specific `ReSetUCScreenLED3()` overrides.
- **Fixed**: LC1 (style 4) LED wire remap — 29 of 31 entries wrong, indices up to 37 (beyond valid range 0-30). Same root cause, `ReSetUCScreenLED4()` overrides not applied.
- **Improved**: Tightened remap range guard test (`test_all_remap_indices_in_range`) — checks `idx < style.led_count` to catch this class of bug automatically.
- 2394 tests across 34 files

## v6.1.1

### PA120 LED Wire Remap Fix
- **Fixed**: PA120 (style 2) LED wire remap — was built using default `UCScreenLED` class indices (Cpu1=2, Cpu2=3, SSD=6, HSD=7, BFB=8, digits start at 9) instead of PA120-specific `ReSetUCScreenLED2()` indices (Cpu1=0, Cpu2=1, SSD=4, HSD=5, BFB=6, digits start at 10). Every indicator and first 3 digit segments mapped to wrong wire positions — cut-off numbers and missing % signs on physical display.
- Addresses #15
- 2393 tests across 34 files

## v6.1.0

### REST API Full CLI Parity
- **New**: Refactored `api.py` → `api/` package (7 modules): `__init__`, `models`, `devices`, `display`, `led`, `themes`, `system`
- **New**: 28 new endpoints (35 total): display (8), LED (14), themes (4), system (3), devices (6)
- **New**: 16 Pydantic request/response models for type-safe API contracts
- **Architecture**: Reuses `DisplayDispatcher` + `LEDDispatcher` from CLI — zero duplicated business logic. Device select auto-initializes the right dispatcher. 409 Conflict if no device selected when calling display/LED endpoints.
- 67 API tests (44 new), 2393 tests across 34 files

## v6.0.6

### FBL Resolution Table Completion
- **Fixed**: Triple/overlapping images on Frozen Warframe SE (PM=58, FBL=58) — FBL 58 was missing from `FBL_TO_RESOLUTION` table, defaulting to 320x320 instead of 320x240. Wrong resolution cascaded into: no pre-rotation (square displays skip it), big-endian byte order (320x320 triggers it for HID), and 33% too much pixel data sent. Addresses #24.
- **Added**: FBL 53 → (320, 240) with big-endian byte order (HID Type 3 SPIMode=2) — completes FBL table to full C# parity (16 entries)
- 2349 tests across 34 files

## v6.0.5

### LED Circulate Rotation & Color Fix
- **Fixed**: LED circulate not rotating zones — `zone_sync_zones` was never initialized in `configure_for_style()`, stayed empty, so zone toggles during circulate silently failed
- **Fixed**: Color/mode not applied during circulate — C# uses global `rgbR1`/`myLedMode` for non-2/7 styles (zones only drive segment data rotation, not LED color), but `tick()` was reading per-zone color
- **Fixed**: Color/mode routing to always set global state (C# always sets `rgbR1`/`G1`/`B1` + per-zone)
- 2349 tests across 34 files

## v6.0.4

### LED Circulate Zone Buttons
- **Fixed**: Zone buttons now toggle zones in/out when circulate is active (C# radio-select sets clicked zone, user adds more by clicking buttons)
- **Fixed**: Interval input fires on every keystroke (`textChanged`, not `editingFinished`), default 2 seconds matching C#
- **Fixed**: Accurate seconds-to-ticks formula (`round(s*1000/150ms)`)
- **Fixed**: Zone uncheck guard (can't disable last zone)
- **Fixed**: Select All not propagating mode changes to all zones (PA120/LF10)
- **Fixed**: `zone_sync_interval` default (36→13 ticks = 2 seconds)
- 2349 tests across 34 files

## v6.0.3

### LF13 & PA120 Segment Fixes
- **Fixed**: LF13 (style 12) LED preview — DLF13 overlay had opaque black center covering the LED color fill, made center transparent so colors show through
- **Fixed**: LF13 mode numbering — rainbow image shown for Temp Linked (mode 4) instead of Rainbow (mode 3) due to C# 1-based vs our 0-based mode indexing
- **Fixed**: PA120 segment display indices — off-by-one from C# (indicators at 2-8 instead of 0-9, digits starting at 9 instead of 10). GPU indicators SSD1/HSD1/BFB1 were aliased to CPU indices — now have own positions. Zone coverage 81→84/84.
- **Improved**: LED test harness — real LEDService for segment rendering, zone-aware signal wiring matching LEDHandler
- 2349 tests across 34 files

## v6.0.2

### Video Persistence & CLI Error Handling
- **Fixed**: Video background not persisting after reboot — `ThemeService.save()` stored video path pointing to temp dir (`/tmp/trcc_work_*/Theme.zt`), now copies video into theme directory as `Theme.zt` so it survives reboots. Addresses #34.
- **Improved**: CLI graceful errors — catch typos and usage errors (missing args, bad types, unknown commands) with clean one-liner + "Did you mean?" suggestions instead of Python tracebacks
- 2349 tests across 34 files

## v6.0.1

### CLI Dispatchers & Metrics Observer
- **New**: `LEDDispatcher` + `DisplayDispatcher` classes — single authority for programmatic LED/LCD operations. Return result dicts (never print). CLI functions are thin presentation wrappers. GUI and API can import dispatchers directly.
- **New**: `--preview` / `-p` flag on all LCD and LED CLI commands — renders ANSI true-color art in terminal for headless/SSH users (`ImageService.to_ansi()`, `LEDService.zones_to_ansi()`)
- **New**: `UCLedControl.update_metrics()` — Observer pattern. Panel subscribes to metrics and dispatches to style-specific update methods internally. `qt_app_mvc._poll_sensors()` reduced from 15-line if/elif chain to 2 lines.
- **New**: LED visual test harness (`tests/test_led_panel_visual.py`) — standalone Qt app for testing all 12 LED device styles with live metrics, device buttons, index overlay, and full signal wiring
- **Fixed**: Color wheel mirror (flip canvas horizontally to match C# gradient)
- **Fixed**: SCSI 320x240 chunk size (`0x10000` → `0xE100` matching C# USBLCD.exe Mode 1/2)
- **Refactored**: CLI shared utilities — `_parse_hex()`, `_MODE_MAP` class constant, `@_cli_handler` decorator consistency
- 2349 tests across 34 files

## v6.0.0

### GoF Refactoring — 5-Phase OOP Overhaul
- **Phase 1**: Segment display collapse — `led_segment.py` 1109→687 lines (-38%). Properties→class attrs, 4 encode methods→unified `_encode_digits()` + `_encode_7seg()`, LF12 delegates to LF8. Flyweight + Strategy patterns.
- **Phase 2**: HID subclasses — SKIPPED (logic genuinely differs between Type2/Type3, ~20 line savings not worth it)
- **Phase 3**: Controller layer elimination — `controllers.py` 699→608 lines (-91). Deleted 5 thin wrapper controllers (ThemeController, DeviceController, VideoController, OverlayController, LEDController). LCDDeviceController = Facade over 4 services (~35 methods). LEDDeviceController absorbed LEDController. ~50 GUI call sites rewritten, 7 test files updated. Law of Demeter enforced: GUI→Facade→Services only. Facade pattern.
- **Phase 4**: UsbProtocol base — `factory.py` 874→846 lines (-28). Extracted shared transport lifecycle (open/close/ensure) from HidProtocol + LedProtocol into `UsbProtocol` base class. Template Method pattern.
- **Phase 5**: LED config serialization — `services/led.py` save/load driven by `_PERSIST_FIELDS` dict + `_ALIASES` dict. Single source of truth for which fields persist. Memento pattern.
- **Total**: 24 files changed, -1203 net lines
- 2306 tests across 34 files

## v5.3.3

### Polkit & Sudo Fix
- **Fixed**: `_sudo_reexec` now includes user site-packages in PYTHONPATH (sudo strips `~/.local/lib/`)
- **Fixed**: `os.path.realpath()` for binary paths in polkit (UsrMerge symlink canonicalization)
- **Fixed**: JavaScript `.rules` file for cross-DE polkit support (XFCE `allow_active=yes` doesn't work)
- **Fixed**: `restorecon` after file writes for SELinux context reset
- **Fixed**: `pkexec` uses absolute binary paths
- 2291 tests across 34 files

## v5.3.2

### DRY Refactor
- Extract shared helpers from 7 duplicate patterns across 5 files
- -67 net lines
- 2291 tests across 34 files

## v5.3.1

### LED Mask & Zone Fix
- **Fixed**: LED mask_size (AK120 64, LC1 31) — was wrong for some device models
- **Fixed**: LED zone_count for styles 9/12 → 0 (no zone cycling)
- 2291 tests across 34 files

## v5.3.0

### Full Data Flow Audit
- **Fixed**: API encoding path for REST adapter
- **Fixed**: SCSI FBL 50 byte order correction
- **Fixed**: FBL code propagation from handshake through entire pipeline
- 2291 tests across 34 files

## v5.2.3

### FBL Propagation Fix
- **Fixed**: `fbl_code` not propagated from handshake to device info — resolution was falling back to default
- 2288 tests across 34 files

## v5.2.2

### HID Diagnostics
- HID frame timeout scaling for large-resolution devices
- `--test-frame` flag for `hid-debug` — sends a solid color test frame
- SCSI raw bytes in `trcc report` for protocol debugging
- 2286 tests across 34 files

## v5.1.1

### OOP/KISS Refactor
- `cli.py` → `cli/` package (6 submodules)
- DcWriter class → module functions
- Controller `__getattr__` delegation (LEDController 26→6 methods, OverlayController 19→5)
- LEDEffectEngine Strategy extraction
- Hardware info → `adapters/system/hardware.py`
- Net -69 lines
- 2286 tests across 34 files

## v5.1.0

### Remove HR10 NVMe Support
- Removed HR10 NVMe temperature daemon — Linux-only feature, not in C# reference, broken preview
- -1762 lines removed
- LED styles 1-12 (13 removed)
- 2286 tests across 34 files

## v5.0.10

### LED & Bulk Fixes
- **Fixed**: LED static/load-linked timeout (remove skip-unchanged keepalive)
- **Fixed**: Font style bold/italic passthrough from QFontDialog
- **Fixed**: Bulk rotation regression (skip pre-rotation for JPEG)
- 2372 tests across 35 files

## v5.0.9

### LED GUI Overhaul
- Match C# FormLED exactly — memory/disk panels at C# positions with golden text
- Fix LF13 background_base localization
- Fix sensor gauge visibility (only hide for styles 4/10)
- Remove Linux-only CPU/GPU source toggle
- HR10 renders as standard LED panel
- GPU LED phase rotation fix
- 2372 tests across 35 files

## v5.0.8

### HID Type 2 Color & Rotation Fix
- **Fixed**: RGB565 byte order wrong for HID Type 2 devices at 320x240 — was sending big-endian, device expects little-endian. C# only uses big-endian for `is320x320` (FBL 100-102), not for HID at other resolutions
- **Fixed**: Non-square displays (320x240, etc.) missing 90° CW pre-rotation — LCD panels are physically portrait-mounted, C# `ImageTo565` rotates before encoding. Added `apply_device_rotation()` to both `DisplayService` and `DeviceService` send paths
- Affects: Frozen Warframe 360 HID Type 2 (`0416:5302`), Assassin Spirit 120 Vision ARGB (#28, #16)
- 2359 tests across 35 files

## v5.0.7

### PA120 LED Segment Display Fix
- **Fixed**: PA120 wire remap table had SSD/HSD/C11/B11 displaced to end, shifting zones 4+ by 4 positions
- **Fixed**: PA120Display indices didn't match C# (indicators 0-9 vs 2-8, digit 1 at 10 vs 9)
- 2359 tests across 35 files

## v5.0.6

### Video Hot Path Optimization
- Commit Renderer ABC + PilRenderer for display pipeline
- Delete dead files, clean up unused code
- 2359 tests across 35 files

## v5.0.5

### FBL 50 Resolution & Overlay Caching Fix
- **Fixed**: FBL 50 resolution was 240x320 (wrong) — corrected to 320x240
- **Fixed**: Overlay caching not invalidating on element changes
- **Fixed**: Cloud theme download path resolution
- 2395 tests across 35 files

## v5.0.4

### HID Type 2 Frame Header Fix
- **Fixed**: HID Type 2 frame header was sending all-zero 16-byte prefix — device firmware expects `DA DB DC DD` magic + command type (`0x02`) + mode flags matching C# `FormCZTV.ImageTo565()` mode 3. Without the magic, firmware rejects frames causing USB disconnect (#16) or stuck-on-logo (#28)
- Affects all HID Type 2 LCD devices (`0416:5302`): Assassin Spirit, Frozen Warframe, etc.
- 2353 tests across 35 files

## v5.0.3

### LED Wire Remap & SCSI Byte Order Fix
- **Fixed**: LED wire remap skipped — `LEDService.initialize()` never called `protocol.handshake()`, so style info was never cached and wire remap was silently skipped. Affects all LED devices (#19 Phantom Spirit EVO, #15 PA120)
- **Fixed**: SCSI byte order — removed 240x320 from big-endian set (C# FBL 50 uses little-endian, not SPIMode=2)
- **Added**: SCSI handshake section in `trcc report` (FBL byte + resolution) for resolution diagnostics (#17)
- 2352 tests across 35 files

## v5.0.0 — v5.0.2

### Complete Windows C# Feature Parity
- v5.0.0: Full gap audit — 35 items resolved (wire remap, zone carousel, LED test mode, DDR multiplier, split mode, video fit-mode, DRAM/SMART info, SPI byte order)
- v5.0.1: Fix SELinux detection without root (sesearch fallback)
- v5.0.2: Fix LED auto-detection (probe PM during enumeration), config version tracking, LED timer optimization
- 2319 tests across 35 files

## v4.2.0

### SELinux Support
- **New**: `trcc setup-selinux` command — installs SELinux policy module (`trcc_usb`) that allows USB device access on SELinux-enforcing systems (Bazzite, Silverblue, Fedora Atomic)
- **New**: SELinux check integrated into setup wizard (CLI `trcc setup` + GUI `trcc setup-gui`)
- **New**: Distro-specific install hints for `checkmodule`/`semodule_package` across all package managers
- **Fixed**: Bulk devices (87AD:70DB) failing with EBUSY on SELinux — detect silent `detach_kernel_driver()` blocking, skip `set_configuration()` if device already configured, clear error message pointing to `trcc setup-selinux`
- **Fixed**: CI workflows now trigger on `stable` branch (was `main/master`)
- Confirmed working: Wonder Vision Pro 360 on Bazzite (SELinux enforcing) — PM=64 → 1600x720
- 2300 tests across 35 files

## v4.1.0

### Setup Wizard
- **New**: `trcc setup` — interactive CLI wizard that checks deps, GPU, udev, desktop entry and offers to install anything missing
- **New**: `trcc setup-gui` — PySide6 GUI wizard with check panel, Install buttons, and embedded terminal output streaming
- **New**: `setup.sh` bootstrap script — one-liner to install trcc-linux and launch the setup wizard (auto-detects GUI/CLI)
- **New**: `trcc uninstall --yes` flag for non-interactive use (GUI uses pkexec for graphical auth)
- Expanded distro support: Solus (eopkg), Clear Linux (swupd), SteamOS, Artix, ArcoLinux, PostmarketOS, Funtoo, Calculate
- PM native "provides" search fallback (dnf/pacman/zypper/apk/xbps) for unmapped dependencies
- Structured check functions in doctor.py (`check_system_deps`, `check_gpu`, `check_udev`, `check_desktop_entry`) for programmatic consumers
- GPU vendor detection via PCI sysfs (NVIDIA, AMD, Intel)
- libxcb-cursor check for apt-based distros (prevents PySide6 segfault on Ubuntu)
- 2290 tests across 35 files

## v4.0.0

### Hexagonal Adapters Restructure
- Moved 24 flat files into `adapters/device/` (10 files), `adapters/system/` (3 files), `adapters/infra/` (11 files)
- Domain data consolidation: all static mappings centralized in `core/models.py` (LED styles, button images, protocol names, category data)
- Assets centralization: eliminate 19 duplicate `.png` calls via `Assets` class
- Language state unified: `settings.lang` singleton replaces 5 widget `self._lang` copies
- Clean hexagonal boundary: `core/` + `services/` (pure Python) → `adapters/` (device/system/infra I/O)
- 2290 tests across 35 files

## v3.0.10

### UCScreenLED Rewrite
- Rewrite UCScreenLED: exact CS paint order (dark fill → decorations → LED rectangles → overlay mask)
- 12 ledPosition arrays with exact Rectangle(x,y,w,h) coordinates, 460×460 widget
- Unified device sidebar buttons: remove DeviceButton class, all buttons use `create_image_button` with `setChecked()` toggle
- 2288 tests across 35 files

## v3.0.9

### PA120 Remap Fix & HID Type 2 Transport Fix
- **Fixed**: PA120 LED remap table — misplaced SSD/HSD/C11/B11 block shifted zones 4+ by 4 wire positions
- **Fixed**: HID Type 2 `open()` — skip redundant `set_configuration()` if already configured, preventing USB bus reset on Linux
- Note: HID Type 2 frame send was later changed to single transfer in v4.2.7, then frame header fixed in v5.0.4
- 2290 tests across 35 files

## v3.0.8

### LED Segment Display Fix
- **Fixed**: LED segment display °C/°F toggle now propagates to segment renderer
- **Fixed**: CPU/GPU sensor source selector now filters phase cycling (show only CPU or GPU instead of always both)
- OOP refactor: moved loose functions into classes across 5 files (conf.py, device_scsi.py, device_led_hr10.py, device_factory.py, device_hid.py)
- 2290 tests across 35 files

## v3.0.7

### Unified Segment Display Renderer
- Unified segment display renderer for all 11 LED device styles
- OOP class hierarchy: `SegmentDisplay` ABC + 10 subclasses (AX120, PA120, AK120, LC1, LF8, LF12, LF10, CZ1, LC2, LF11)
- Data/logic separation with `CHAR_7SEG` and `CHAR_13SEG` encoding tables
- `LEDService` generalized for all digit-display styles (not just AX120)
- 2291 tests across 35 files

## v3.0.6

### Single-Instance Guard
- Single-instance guard: prevent duplicate systray entries on launch
- Font size spinbox in overlay color picker (independent of font dialog)
- 2167 tests across 35 files

## v3.0.5

### LED Mode Button Image Fix
- **Fixed**: LED mode button images — asset filename typo caused buttons to show plain text instead of icons (`D2灯光1{i+1}` → `D2灯光{i+1}`)
- 2167 tests across 35 files

## v3.0.4

### Bulk Frame Encoding Fix
- **Fixed**: Bulk frame encoding — JPEG (cmd=2) instead of raw RGB565 for all USBLCDNew devices (87AD:70DB), matching C# `ImageToJpg` protocol
- PM=32 remains RGB565 (cmd=3) for raw-mode devices
- Added PM=5 to bulk resolution table (Mjolnir Vision → 240×320)
- 2166 tests across 35 files

## v3.0.3

### Background Display Mode Fix
- **Fixed**: Background display mode — continuous LCD sending via metrics timer (C# `myBjxs`/`isToTimer` parity), toggle OFF renders black+overlays, theme click resets all mode toggles
- Security hardening: timing-safe auth, PIL bomb cap, zip-slip guard, TOCTOU fix
- Tooltips on all user-facing buttons
- Distro name in debug report
- Help → troubleshooting guide
- 2162 tests across 33 files

## v3.0.2

### Bulk Protocol Fix
- **Fixed**: Bulk protocol frame header (correct magic bytes, kernel driver detach, chunked writes)
- **Fixed**: Legacy autostart cleanup — glob desktop files, remove duplicates
- Auto-detect GPU vendor for sensor mapping: NVIDIA > AMD > Intel
- CI: added FastAPI test dependencies (httpx, python-multipart) to dev extras
- 2156 tests across 33 files

## v3.0.1

### Full CLI Parity
- **36 Typer commands** expose all service methods — CLI, GUI, and REST API now have full feature parity
- New theme commands: `theme-save`, `theme-export`, `theme-import`
- New LED commands: `led-color`, `led-mode`, `led-brightness`, `led-off`, `led-sensor`
- New display commands: `brightness`, `rotation`, `screencast`, `mask` (with `--clear`), `overlay`
- New media command: `video` (plays video/GIF/ZT on LCD)
- Theme commands: `theme-list` (local + cloud), `theme-load` (find by name + send)
- All commands follow detect → service → print → return 0/1 pattern
- Backward-compatible module-level aliases for all new commands
- 2148 tests across 31 files

## v3.0.0

### Hexagonal Architecture
- **Services layer** (`src/trcc/services/`) — 8 service classes shared by GUI, CLI, and REST API:
  - `DeviceService` — detect, select, send_pil, send_rgb565
  - `DisplayService` — high-level display orchestration
  - `ImageService` — solid_color, resize, brightness, rotation, byte_order
  - `LEDService` — LED RGB control via LedProtocol
  - `MediaService` — GIF/video frame extraction
  - `OverlayService` — overlay rendering
  - `SystemService` — system sensor access
  - `ThemeService` — theme loading/saving/export/import
- **CLI refactored to Typer** — OOP command classes (DeviceCommands, DisplayCommands, ThemeCommands, LEDCommands, DiagCommands, SystemCommands)
- **REST API adapter** (`api.py`) — FastAPI endpoints for headless/remote control (optional `[api]` extra)
- **Module renames**: `paths`→`data_repository`, `sensor_enumerator`→`system_sensors`, `sysinfo_config`→`system_config`, `cloud_downloader`→`theme_cloud`, `driver_lcd`→`device_lcd`
- Dead code removed: `theme_io.py`, `constants.py`, `device_base.py`
- 2081 tests across 31 files

## v2.0.1

### FBL/PM Resolution Fix
- Fixed PM=36 (240x240) wrong resolution: unified FBL/PM tables into `constants.py` (single source of truth)
- PM=FBL default instead of hardcoded 320x320
- Fixed PyQt6 version in `trcc report`
- CI: add ffmpeg, auto-publish to PyPI on tag push
- Deleted dead shim files

## v2.0.0

### Major Refactor
- **Module renaming**: Consistent `device_*` / `driver_*` naming convention across all backend modules:
  - `bulk_device` → `device_bulk`, `hid_device` → `device_hid`, `led_device` → `device_led`
  - `scsi_device` → `device_scsi`, `lcd_driver` → `driver_lcd`, `kvm_led_device` → `device_kvm_led`
  - `gif_animator` → `media_player`
- **New modules**: `constants.py` (shared constants), `debug_report.py` (diagnostic tool), `device_led_hr10.py` (HR10 LED backend)
- All imports updated across 49 source files and 29 test files
- 2105 tests passing, ruff + pyright clean

## v1.2.16

### SELinux/Immutable Distro Fix
- **Fixed**: udev rules using `TAG+="uaccess"` which fails on SELinux-enforcing distros (Bazzite, Silverblue, Fedora Atomic)
- Now uses `MODE="0666"` for all udev rules — works universally across all Linux distros

## v1.2.15

### Stale Udev Detection
- **Auto-detect stale udev rules**: `trcc detect` now warns when USB storage quirk is missing and prompts `sudo trcc setup-udev` + reboot
- Prevents confusing "no device found" errors after adding new device support

## v1.2.14

### GrandVision 360 AIO Support
- Added GrandVision 360 AIO (VID `87AD:70DB`) as a known SCSI device
- Fixed sysfs VID readback for non-standard vendor IDs
- Device layer dedup: lcd_driver delegates to scsi_device, extracted `_create_usb_transport()`, `get_frame_chunks()` single source of truth
- Fixed LED PM/SUB byte offset (`pm=resp[5], sub=resp[4]`) — was off by one due to Windows Report ID prepend

## v1.2.13

### Format Button Fix
- **Fixed**: Format buttons (time/date/temp) not updating preview on fresh install
- Set `overlay_enabled` on theme load so format changes render immediately
- Persist format preferences (time_format, date_format, temp_unit) across theme changes via `conf.save_format_pref()`

## v1.2.12

### Fresh Install Overlay Fix
- **Fixed**: Overlay/mask changes not updating preview or LCD on fresh install (no saved device config)
- Root cause: `OverlayModel.enabled` defaults to `False` and was only set `True` by `start_metrics()`. On fresh install, no saved config → overlay disabled → `render_overlay_and_preview()` returned raw background unchanged
- `render_overlay_and_preview()` now uses `force=True` to always render through the renderer, bypassing the `enabled` check (preview path should always show edits)
- `_on_overlay_changed()` auto-enables overlay and starts metrics timer when user is actively editing elements

## v1.2.11

### LCD Send Pipeline Fix
- **Overlay changes**: Editing overlay elements (color, position, font, format, text) now sends the rendered frame to the LCD immediately
- **Mask toggle/reset**: Toggling or clearing mask now sends to LCD
- **Background toggle**: Switching to static background now sends to LCD
- **Image crop**: Cropped image now sends to LCD after crop completes
- **Video first frame**: Loading a video theme now shows the first frame on LCD immediately (was blank until timer fired)
- **send_current_image**: Now applies overlay before sending (was sending raw background)
- **_render_and_send**: Fixed to send overlay-rendered image, not raw `current_image`
- **DRY**: Extracted `_load_and_play_video()` — eliminates 5 scattered video load+play patterns

## v1.2.10

### First-Launch Preview Fix
- **Fixed**: Theme previews not showing on first GUI launch after `pip install` or upgrade
- Root cause: `Settings` singleton resolved paths at import time (before data existed), and `set_resolution()` short-circuited when resolution was unchanged — paths were never refreshed after `ensure_all_data()` downloaded archives

## v1.2.9

### HID Handshake Protocol Fix
- **LED routing fixed**: `hid-debug` now routes LED devices (Type 1) through `LedProtocol` instead of `HidProtocol` — was returning None immediately for all LED devices
- **Retry logic**: 3-attempt handshake with 500ms delay between retries (matching Windows UCDevice.cs `Timer_event`)
- **Timeout increased**: Handshake uses 5000ms timeout (was 100ms) — frame I/O stays at 100ms
- **Relaxed Type 2 validation**: Removed strict `resp[16] == 0x10` check (Windows doesn't require it)
- **Relaxed LED validation**: Bad magic/cmd bytes now warn instead of reject (matching Windows `DeviceDataReceived1`)
- **PM/SUB offset fix**: Type 2 LCD now reads `pm=resp[5], sub=resp[4]` (matching Windows Report ID offset)
- **Endpoint auto-detection**: `PyUsbTransport` enumerates actual device endpoints instead of hardcoding EP 0x02
- **Error visibility**: Actual USB exceptions logged and exposed via `protocol.last_error` instead of generic "None"
- **Hex dump in diagnostics**: `hid-debug` now shows raw handshake response bytes for bug reports

### OOP Refactor
- **DcConfig**: Merged `dc_parser.py` + `dc_writer.py` into single `DcConfig` class in `dc_config.py`
- **conf.py**: Moved all config persistence from `paths.py` into dedicated `conf.py` module
- **device_base.py**: Added `DeviceHandler` ABC and `HandshakeResult` base dataclass
- **Dataclasses**: `ThemeItem`/`LocalThemeItem`/`CloudThemeItem`/`MaskItem` replace raw dicts in theme browsers
- **DownloadableThemeBrowser**: Template method base class in `base.py` for cloud/mask/web browsers
- **gif_animator.py**: Simplified with cleaner class structure, removed dead code
- **paths.py**: Facade pattern with `ensure_all_data()`, cleaner on-demand download

## v1.2.8

### KISS Refactor
- Consolidated 5 duplicate Settings tab handlers (`_on_color_changed`, `_on_position_changed`, `_on_font_changed`, `_on_format_changed`, `_on_text_changed`) into single `_update_selected(**fields)` method in `uc_theme_setting.py`
- Each handler is now a one-liner delegating to `_update_selected` with keyword args
- Removed dead `set_format_options()` method from `overlay_renderer.py` (never called)
- Removed 6 dead LED legacy stubs from `models.py` (`_tick_static`, `_tick_breathing`, `_tick_colorful`, `_tick_rainbow`, `_tick_temp_linked`, `_tick_load_linked`) — production dispatches via `_tick_single_mode` directly

## v1.2.7

### Strip Theme Data from Wheel
- Removed all theme data from the pip wheel — themes download on first run only
- `_has_actual_themes()` now requires PNG files (ignores leftover `.dc` files)

## v1.2.6

### Config Marker Verification
- Fixed stale config marker: `is_resolution_installed()` now verifies data exists on disk
- Added debug logging for theme setup, tab switches, and directory verification

## v1.2.5

### One-Time Data Setup
- Download themes/previews/masks once per resolution, tracked in `~/.trcc/config.json`
- `is_resolution_installed()` / `mark_resolution_installed()` skip re-download on subsequent launches
- Custom themes saved to `~/.trcc/data/` (survives pip upgrades)

## v1.2.4

### Fix pip Upgrade Data Loss
- Theme archives now extract to `~/.trcc/data/` instead of site-packages
- `install-desktop` generates `.desktop` file inline for pip installs (no bundled template needed)

## v1.2.3

### Logging Refactor
- Replaced `print()` with `logging` across 12 modules
- Thread-safe device send with lock
- Extracted `_setup_theme_dirs()` helper
- Suppressed `pyusb` deprecation warning (`_pack_` in ctypes)

## v1.2.2

### Fix Local Theme Loading
- Fixed local themes not loading from pip install (`Custom_*` dirs blocked on-demand download)

## v1.2.1

### Debug Logging & HID Fix
- Fixed RGB565 byte order for non-320x320 SCSI devices
- Fixed GUI crash on HID handshake failure
- Added verbose debug logging (`trcc -vv gui`)

## v1.2.0

### HR10 2280 PRO Digital Support
- 7-segment display renderer (`hr10_display.py`) — converts text + unit into 31-LED color array
- NVMe temperature daemon (`hr10-tempd` CLI command) — reads sysfs temp, breathe animation, thermal color gradient
- LED diagnostic command (`led-debug`) — handshake + test colors for LED devices
- Interactive HSV color wheel widget (`uc_color_wheel.py`) for LED hue selection
- 7-segment preview widget (`uc_seven_segment.py`) — QPainter rendering matching physical HR10 display
- Unified LED panel — all LED device styles (1-13) handled by single `UCLedControl` panel, matching Windows FormLED.cs
- HR10-specific widgets (drive metrics, display selection, circulate mode) shown conditionally in the LED panel
- LED segment visualization widget (`uc_screen_led.py`) with colored circles at segment positions
- Contributor: [Lcstyle](https://github.com/Lcstyle) (GitHub PR #9)

### Controller Naming
- Renamed `FormCZTVController` → `LCDDeviceController` (LCD orchestrator)
- Renamed `FormLEDController` → `LEDDeviceController` (LED orchestrator)
- Consistent naming convention: `{Domain}Controller` for state engines, `{Protocol}DeviceController` for orchestrators

### Autostart on Login
- Auto-enable autostart on first GUI launch (matches Windows `KaijiQidong()` behavior)
- Creates `~/.config/autostart/trcc.desktop` with `trcc --last-one` on first run
- `--last-one` launches GUI minimized to system tray and sends last-used theme
- Settings panel checkbox reflects and toggles the actual autostart state
- `trcc resume` command for headless theme send (scripting/cron)

### Reference Theme Save
- Custom saved themes use `config.json` with path references (no file copies)
- On save: writes `config.json` + `Theme.png` thumbnail to `Custom_{name}/`
- On load: resolves background/mask/overlay paths from `config.json`
- Fixes overlay-on-resume: overlay config persisted in theme JSON

### Protocol Reverse Engineering
- Complete SCSI protocol reference from USBLCD.exe (Ghidra decompilation)
- Complete USB bulk protocol reference from USBLCDNEW.exe (.NET/ILSpy)
- Documented all CDB commands, handshake sequences, frame transfer formats
- New docs: [USBLCD_PROTOCOL.md](USBLCD_PROTOCOL.md), [PROTOCOL_USBLCDNEW.md](PROTOCOL_USBLCDNEW.md)

### LCD Blink Mitigation
- SCSI init now checks poll response for `0xA1A2A3A4` boot signature (matching USBLCD.exe)
- If device is still booting, waits 3s and re-polls (up to 5 retries)
- Added 100ms post-init delay to let display controller settle before first frame

### Code Quality
- `ruff` linting enforced in CI (E/F/W/I rules) — 0 violations across 26 files
- Fixed 122 ruff violations (unsorted imports, unused vars, f-strings, lambda, class redefinition)
- Removed unused imports across 10 files
- Sorted all import blocks (isort)
- Removed redundant `requirements.txt` (`pyproject.toml` is single source of truth)
- Deduplicated magic bytes: `led_device.LED_MAGIC` now imports `TYPE2_MAGIC` from `hid_device` (was copy-pasted)
- Extracted `_select_item()` into `BaseThemeBrowser` — shared visual selection logic for local, cloud, and mask browsers
- Centralized `pil_to_pixmap()` usage in `UCImageCut` (was inline 5-line conversion)
- Added `_create_info_panel()` factory in `UCLedControl` — eliminates duplicate QFrame+label creation for mem/disk panels
- Consolidated 5 repeated stylesheet strings into module-level constants (`_STYLE_INFO_BG`, `_STYLE_INFO_NAME`, etc.)

### On-Demand Download
- All 15 LCD resolutions now have bundled `.7z` theme archives (was 4)
- New resolutions: 240x320, 360x360, 800x480, 854x480, 960x540, 1280x480, 1600x720, 1920x462, plus portrait variants
- On-demand download for themes, cloud previews, and mask archives from GitHub on first use
- 33 Web archives (cloud previews + masks) for all resolutions
- Downloaded archives stored in `~/.trcc/data/` when package dir is read-only (pip install)
- Cross-distro 7z install help shown when system `7z` is not available

### HID Device Identification
- PM→FBL→resolution mapping from Windows FormCZTV.cs (all known PM byte values)
- `pm_to_fbl()` and `fbl_to_resolution()` functions in `hid_device.py`
- `PM_TO_BUTTON_IMAGE` mapping for dynamic sidebar button updates after handshake
- New `trcc hid-debug` CLI command — hex dump diagnostic for HID bug reports

### Packaging
- Assets and data moved into `trcc` package (`src/trcc/assets/`, `src/trcc/data/`)
- `pip install .` now produces a complete wheel with all GUI images, fonts, and themes
- HID devices auto-detected — `--testing-hid` flag no longer needed

### Bug Fixes
- Fixed resume RGB565 conversion to match GUI (big-endian + masked)
- Fixed LEDMode int-to-enum conversion crash on config save
- Fixed device config persistence for autostart theme state
- Fixed json import ordering in controllers
- Fixed saved theme overlay not rendering (overlay must enable before background load)
- Fixed tab 2 mask not persisting in saved themes (mask source directory tracking)
- Fixed `install_desktop()` icon path (`src/assets/` → `src/trcc/assets/`)
- Fixed `install.sh` desktop shortcut using generic icon instead of TRCC icon
- Fixed CI workflow missing libEGL and QT_QPA_PLATFORM for PyQt6 tests

### Documentation
- Added [Supported Devices](REFERENCE_DEVICES.md) page with all USB IDs
- Added [Development Status](DEVELOPMENT_STATUS.md) tracking page
- Expanded [Technical Reference](REFERENCE_TECHNICAL.md) with full SCSI command table
- Removed `--testing-hid` references from all docs (HID is auto-detected)

### Testing
- 2029 tests across 27 test files (up from 1777)
- 96% coverage on non-Qt backend

## v1.1.3

### LED RGB Control (`hid-protocol-testing` branch)
- RGB LED control panel (FormLED equivalent) for HID LED devices (e.g. AX120 DIGITAL)
- 7 LED effect modes: Static, Breathing, Rainbow, Cycle, Wave, Flash, Music
- Per-segment and global on/off toggles, brightness slider, color picker
- `LedProtocol` in device_factory.py routes LED devices separately from LCD HID
- `FormLEDController` with tick-based animation loop (30ms, matching Windows timer1)
- `led_device.py`: LED styles, packet builder, HID sender, effect engine
- `uc_led_control.py`: PyQt6 LED control panel with segment grid and mode buttons
- Device routing: sidebar auto-routes HID LED devices to LED view instead of LCD form
- 376 new LED tests (245 led_device + 131 led_controller)

### Save Custom Theme Fixes
- Fixed font size DPI corruption on save — sizes no longer inflate each save cycle (36pt → 48pt → 64pt)
- Fixed font charset lost on save — preserves GB2312 charset (134) instead of defaulting to 0
- Fixed font style default — Regular (0) instead of Bold (1), matching Windows `new Font()` defaults
- Lossless DC config round-trip: original parsed DC data preserved in `OverlayModel._dc_data` and merged on save instead of reconstructing from scratch
- Cloud video themes now copy MP4 to working dir for inclusion in saved custom themes
- `ThemeInfo.from_directory()` now detects `.mp4` files in theme directories (not just Theme.zt)
- Saved custom themes with MP4 backgrounds properly reload as video on reopen

### Cross-Distro Compatibility
- Centralized all platform-specific helpers in `paths.py` (single source of truth)
- `require_sg_raw()` with install instructions for 8+ distro families (Fedora, Debian, Arch, openSUSE, Void, Alpine, Gentoo, NixOS)
- Dynamic SCSI device scan via `/sys/class/scsi_generic/` (replaces hardcoded `range(16)`)
- `FONT_SEARCH_DIRS` covering 20+ font directories across all major Linux distros
- Replaced `os.system()` with `subprocess.run()` in cli.py for security/correctness
- Install guide expanded to cover 25+ Linux distributions

### CI / Testing
- Added `hid-protocol-testing` branch to GitHub Actions test workflow (Python 3.10, 3.11, 3.12)
- 563 HID/LED tests (114 device protocol + 73 factory/routing + 245 LED device + 131 LED controller) — total 1777 tests across all branches
- Fixed Python 3.12 `mock.patch` failure for optional `pynvml` import
- Added `ruff` to dev dependencies for CI lint step

### Documentation
- Added HID device PIDs (`0416:5302`, `0418:5303`, `0418:5304`) to supported devices
- Split README device tables into SCSI (stable) and HID (testing) sections with USB IDs
- Added `lsusb` example to help users identify their device
- Created [Device Testing Guide](GUIDE_DEVICE_TESTING.md) with install, switch, and reporting instructions
- Added CI badge to README
- Added [CLI Reference](REFERENCE_CLI.md) with all commands, options, and troubleshooting
- Updated Documentation table on all branches

### Autostart on Login
- Auto-enable autostart on first GUI launch (matches Windows `KaijiQidong()` behavior)
- Creates `~/.config/autostart/trcc.desktop` with `trcc --last-one` on first run
- Checkbox in Settings panel reflects and toggles the actual autostart state
- Subsequent launches refresh the `.desktop` file if the install path changed
- 15 new autostart tests covering first-launch, toggle, path refresh, and edge cases

### Bug Fixes
- Theme.png preview now includes rendered overlays and masks (was showing raw background only)
- `dc_writer.py` only writes fallback Theme.png if one doesn't already exist (controller writes the better rendered version)
- Fixed cli.py `--version` flag (was stuck at 1.1.0)

## v1.1.2

### Bug Fixes
- Fixed LCD send: init handshake (poll + init) was being skipped on first frame send
- Dynamic frame chunk calculation for all resolutions (was hardcoded to 320x320)
- Local themes grid now sorts default themes (Theme1-5) first
- Added Qt6 installation docs for additional distros

### Test Suite (1209 tests, 96% coverage)
- Expanded from 880 → 1209 tests across 6 coverage sprints
- All 18 non-Qt backend modules now 92-100% covered (combined 96%)
- Added 3 Qt component test files (test_qt_constants, test_qt_base, test_qt_widgets)

## v1.1.1

### Test Suite (298 tests)
- Added test_dc_writer (18 tests): binary write, roundtrip, overlay_config_to_theme, carousel, .tr export/import
- Added test_paths (25 tests): config persistence, per-device config, path helpers, resolution/temp unit
- Added test_sysinfo_config (18 tests): config load/save, defaults, auto_map
- Added test_device_implementations (25 tests): RGB565 conversion, resolution, commands, registry
- Added test_scsi_device (18 tests): CRC32, header building, frame chunking
- Added test_models (30 tests): ThemeInfo, ThemeModel, DeviceModel, VideoState, OverlayModel
- Added test_theme_io (14 tests): C# string encoding, .tr export/import roundtrip
- Removed 7 dead test files (1490 lines) importing non-existent modules
- Fixed 3 RGBA→RGB assertion mismatches in overlay renderer tests
- Existing tests: test_dc_parser (133), test_device_detector (17), test_overlay_renderer (25)

### Bug Fixes
- Fixed `dc_writer.py`: `overlay_config_to_theme()` and `import_theme()` called `DisplayElement()` without required positional args — runtime crash
- Fixed `theme_io.py`: `export_theme()` missing `bg_len` write when no background image — caused import to read past EOF

## v1.1.0

- Per-device configuration — each LCD remembers its theme, brightness, rotation, overlay, and carousel
- Carousel mode — auto-rotate through up to 6 themes on a timer
- Theme export/import — save/load themes as `.tr` files
- Video trimmer — trim videos and export as `Theme.zt` frame packages
- Image cropper — crop and resize images for any LCD resolution
- Fullscreen color picker — eyedropper tool for picking screen colors
- Dynamic font and coordinate scaling across resolutions
- Font picker dialog for overlay elements
- Mask toggle to hide/show instead of destroying mask data
- Mask reset/clear functionality
- Screen cast with PipeWire/Portal support for Wayland
- Sensor customization dashboard with reassignable sensor slots
- Overlay element cards matching Windows UCXiTongXianShiSub exactly
- Font name and style preservation when loading themes
- Fixed disabled overlay elements being re-enabled on property changes
- Fixed 12-hour time format (2:58 PM instead of 02:58 PM)
- Video resume when toggling video display back on

## v1.0.0

- Initial release
- Full GUI port of Windows TRCC 2.0.3
- Local and cloud theme support
- Video/GIF playback with FFmpeg
- Theme editor with overlay elements
- System info dashboard with 77+ sensors
- Screen cast functionality
- Multi-device and multi-resolution support
