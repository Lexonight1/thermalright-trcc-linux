# Modules

A **module** is a feature you can switch on or off on the **Modules** page, in every TRCC window. A module that is off costs nothing: TRCC does not even load its code.

> **Where this stands.** Two features are modules: the **Audio Visualizer** and **RGB Lighting**. Installing, updating and deleting modules comes next.

---

## Using the Modules page

Open it from the Control Center's title bar (gui) or the sidebar (qtgui). Each module is one row:

- **Its symbol** on the left, its **name**, and one sentence on **what it does**.
- **Guide ›** opens that module's section of this guide.
- **On / Off** switches it, at once and in every open window — no restart.
- When a module cannot run on your computer, a switch was refused, or switching it off could not undo something, the row says **why, and what to do**, in plain words.

From the command line: `trcc system modules list`, `trcc system modules on <key>`, `trcc system modules off <key>`. From the API: `GET /system/modules`, `POST /system/modules/{key}`.

When a module is off, its tab or sidebar entry is hidden in every window and comes back the moment you switch it on. Anything you still ask of it — from the command line, the API or another window — is refused, with a line saying it is off and where to turn it on. Your choices stay saved: switching a module back on picks up where it left off.

A module's **themes are kept with it**: a theme whose background is an audio visual is saved with the Audio Visualizer and listed only while it is on.

### Coming next

- **Install, Update and Delete**, from TRCC's own GitHub releases only. Every download is checked against a list signed by the maintainer (see [Verify Your Download](GUIDE_INSTALL.md#verify-your-download)); a failed or tampered download is never installed, and a failed update keeps the old version working.

---

## Audio Visualizer

Draws what your computer is playing as a panel's background — TRCC Pipes, the Spike Circle, Cavalier's eleven modes, KarmaViz and MilkDrop — with the theme's mask and readings on top. **On by default.**

- **gui:** the audio-bars tab beside Cloud backgrounds. Click a tile and it plays on the panel you have selected; its sliders and the centre picture are below the tiles.
- **qtgui:** *Audio visuals* in the sidebar.
- **Command line:** `trcc display visuals` lists them; `trcc display visual <key> <name>` plays one, and `trcc display visual <key>` stops it. `visual-set`, `visual-picture`, `visual-center` and `visual-tile` do the rest.
- **API:** `POST /devices/{key}/display/visual`, `GET /display/visuals`, and the other `/display/visual-*` routes.

**What it needs to hear the music:** on Linux, ffmpeg with PulseAudio (or PipeWire's PulseAudio layer), or PipeWire's `pw-record`; on Windows, the `soundcard` package; on a Mac, macOS 14.2 or later and the `catap` package. A visual that cannot hear or draw on your computer says why on its tile. MilkDrop presets go in `~/.trcc/data/visuals/`.

**Switching it off:** every panel playing a visual goes back to its theme, mask and readings still on top, and keeps its choice. Switch it on again and each panel plays its visual again. A theme whose background is a visual, loaded while the Visualizer is off, shows its mask and readings and says it needs the Audio Visualizer; its visual plays the moment you switch it on. To stop a panel's visual for good, pick any other background, or run `trcc display visual <key>`. That works whether the Visualizer is on or off.

---

## RGB Lighting

Lights your RGB memory, your motherboard's RGB (ASUS Aura headers and onboard LEDs) and the devices OpenRGB runs — with built-in effects, a colour that follows a temperature or a load, or the colours of a cooler or an LCD. **On by default.**

- **gui:** the **RGB** button in the sidebar opens its page — [how to use it](GUIDE_USER.md#rgb-lighting).
- **qtgui:** *RGB Lighting* in the sidebar.
- **Command line:** `trcc system rgb` lists the lights (`--scan` looks again); `ram-effect`, `aura-effect`, `aura-leds`, `aura-blink`, `linked-color` and `follow` set them; `ram-lighting` shows or switches the memory's permission.
- **API:** `GET /system/rgb`, `POST /system/rgb/scan`, `/system/ram-effect`, `/system/aura-effect`, `/system/aura-leds`, `/system/aura-blink`, `/system/linked-color`, `/system/rgb-follow` and `/system/ram-lighting`.

**What it needs:** memory lighting is off until you allow it on the page, which asks for your password (Linux only, for now). OpenRGB's devices need OpenRGB running with its SDK server on (`127.0.0.1:6742` unless you change it).

**Switching it off:** following stops. The motherboard's lights go back to the board's own effect, OpenRGB's devices keep what OpenRGB shows, and memory keeps the last colours it was sent — an effect saved on a stick stays saved on it. If you allowed memory lighting, switching RGB off takes that permission back too — with RGB off, nothing in TRCC could remove it later. Where TRCC's polkit files are installed — a distro package, or `trcc system setup` — this needs no password at the computer itself; otherwise it asks for yours. Close that prompt and RGB still goes off; its row then says the permission is still there, and switching RGB on and off again removes it. Closing TRCC, logging out or restarting never removes it. Your choices stay saved — switch RGB on again and it picks up where it left off.

---

## Writing a module

This part is for anyone who wants to make one. You can write a module today in your own copy of TRCC, starting from the skeleton below. **A website for TRCC modules is on the way**, for finding and sharing them; until then TRCC does not install modules from anywhere, so a module you write runs in your own copy.

### Start from the skeleton

```bash
PYTHONPATH=src python3.12 dev/tools/new_module.py weather --title "Weather" \
    --summary "Today's forecast as a panel background."
```

That copies the `hello` skeleton (`dev/module_template/hello/`) to `src/trcc/modules/<key>/`, the one folder a module lives in. `hello` is a real, working module with one of everything: the tests load it through the same catalog as every built-in and drive each part, so the skeleton cannot silently go stale.

### One folder, fixed names

A module is a small copy of TRCC's own layout. Each part has a fixed name, and TRCC finds it there — nothing lists it:

| Where | What | Loaded |
|---|---|---|
| `module.json`, `icon.svg`, `i18n.json` | its row on the Modules page, and its words in every language | always — without importing anything |
| `core/` | its Commands, Results and events | always, so its Commands are known in every window and refused while it is off |
| `app.py` | its root: the `Module` class, holding its own state | when it is switched on |
| `services/`, `adapters/` | its own logic, and the protocols it speaks to hardware the host lets it reach | by `app.py` |
| `ui/gui/` | exports `PAGE`: a widget that also extends `ModulePage` — the page its mode tab opens; and/or `VIEW`: a widget that also extends `ModuleView` — a whole page its sidebar button opens (built with `app=` and `bridge=`, the window's events: `bridge.frame_sent`, `bridge.forward(MyEvent, "module_event")`, `bridge.add_preview(widget)`) | while it is on |
| `ui/qtgui/` | exports `PANEL`: a `BasePanel` with a `LABEL` — its sidebar entry | while it is on |
| `ui/cli/` | exports `GROUP` (the `trcc` group, `""` for the top) and `COMMANDS` (a Typer app) | always, on or off |
| `ui/api/` | exports `ROUTERS`, the FastAPI routers it adds | always, on or off |

The windows show a module's tab, button or entry only while it is on, and follow it being switched in any window. The CLI and the API carry an installed module's commands whether it is on or off; while it is off, they answer that it is off and where to turn it on.

The same layer rules apply inside a module as in TRCC itself, and the same tests check them. TRCC never imports a module by name, and a module never imports another module, or anything of TRCC's but its core, its UI and the module SDK.

### Its Commands

A module's Commands extend `ModuleCommand` (or `ModuleQuery` for a question), name the module in `MODULE`, and do their work in `run(self, module)`. That method gets the module's own root, its `app.py` class, never the App. Their names travel between windows and the shared App, so a name TRCC already uses is refused.

### Its words

`i18n.json` holds `{"<language>": {"<English>": "<translation>"}}` for every language TRCC offers. `dev/tools/i18n_gaps.py` lists what is missing and merges new translations; a test fails while any language lacks a word.

### module.json

The Modules page lists a module from this file alone.

| Field | Meaning |
|---|---|
| `key` | its name everywhere — the folder name, the Python package (`trcc.modules.<key>`), the Command tag; lowercase letters, digits and `_` |
| `title` | the name on its row |
| `summary` | one plain sentence on what it does for the user |
| `version` | its version |
| `default_on` | on for someone who never chose (new modules start `false`) |
| `icon` | a file beside `module.json`, drawn 40 × 40 on its row |
| `guide` | a link to its section of this guide |
| `capabilities` | what it may ask TRCC for — see below; anything else is refused |
| `backgrounds` | the theme background refs it draws (`<prefix>/<id>`), so a theme can name the module it needs even while that module is off |
| `network_hosts` | the internet sites it fetches from, as public hostnames (`api.open-meteo.com`) — required with the `network` capability, refused without it; never `localhost` or an IP address |
| `author` | who wrote it, by name — shown on its tile and by `trcc system modules list`; for a module from the index it must be the name TRCC reviewed, and only TRCC's own modules say `Lexonight` |
| `devices` | the USB devices it needs you to reach, as `lsusb` prints their ids (`0b05:19af`) — needs the `hardware` capability; see [USB devices](#usb-devices) |

### When each hook runs

A module's class extends `BaseModule` and names its key in the class line: `class WeatherModule(BaseModule, key="weather")`.

| Hook | When | What belongs there |
|---|---|---|
| `pre_enable` | before switching on | can it run here? Refuse with `ModuleUnavailable` |
| `_enable` | switching on — in **every** TRCC process, a one-shot command's too | **register only**: subscriptions, entries. Never hardware, never a thread |
| `start` | when the session starts, or when switched on inside one | hardware and threads |
| `stop` | when the session ends, or before switching off | stop what `start` began |
| `turning_off` | before switching off — **only** when the user does it, never when TRCC closes | undo what that choice alone should undo, such as a permission the user granted; return a note for the row, or `""` |
| `_disable` | switching off, and when TRCC closes | take out what `_enable` registered (subscriptions are taken back for you) |

A module that raises in any hook is switched back off and its row says why — it can never break TRCC or be left half on.

### Capabilities

A module never gets the App itself. It gets a **host** that offers only what its `module.json` declares. Beside the events (`host.subscribe`, `host.publish`), each capability is one small port on the host — `host.panels`, `host.background`, `host.settings`, `host.themes`, `host.sound`, `host.gpu`, `host.files`, `host.frames`, `host.readings`, `host.hardware`, `host.password`, `host.network` — and reaching one its manifest does not declare is refused:

| Capability | What it lets the module do |
|---|---|
| `events` | hears what happens in the app |
| `panels` | knows which LCD panels are attached, and their size |
| `background` | draws a panel's background (the theme's mask and metrics stay on top) |
| `settings` | keeps its own settings, for the app and for each panel |
| `themes` | lets themes save and load a background it draws |
| `sound` | hears what the computer is playing |
| `gpu` | draws with the graphics card |
| `files` | reads its own files in TRCC's data folder, and makes PNG pictures |
| `frames` | sees the picture each LCD panel shows |
| `readings` | reads the computer's sensor readings |
| `hardware` | reaches lighting chips: memory, the motherboard, OpenRGB |
| `password` | may ask for your password, to reach hardware the system guards |
| `network` | fetches from the internet sites its module.json names, and no others |

`host.network.fetch(url)` fetches over https from the sites in `network_hosts` only — the default port, no user name in the URL, at most 2 MB, 20 seconds — and only when the site's name resolves to an address on the public internet, never this computer or the local network. A redirect is held to the same rule, so an allowed site cannot send the module anywhere else. A module that opens sockets or imports a network library itself is not listed.

#### USB devices

A device the system guards — on Linux, any USB device without a udev rule — is reachable only by root. A module that drives one names it under `devices`, and its install screen names each one: *unlocks USB device 0b05:19af for every program you run*. That is what it does: once you accept, TRCC's root helper `trcc-module-devices` writes one udev rule for the module (`/etc/udev/rules.d/70-trcc-module-<key>.rules`), asking for your password, and from then on you — and every program you run — can open those devices. The helper builds that rule itself from the ids alone, so a module cannot put anything else in it. An update that adds a device asks again; removing the module, or TRCC taking it back, takes the devices back at once. On Windows and macOS any program may already open a USB HID device, so nothing is asked. On NixOS, where `/etc` is built from your configuration, TRCC names the rule to add. A module installed from a folder in developer mode never gets one — write your own rule while you develop it.

When it is switched off, TRCC takes back what it still holds: its subscriptions, its frame watchers, its background kinds, and the panels it draws, each keeping its choice. More capabilities arrive as modules need them — one at a time, each with a reason.

### Rules

1. **Plain words with a next step.** Every refusal is shown to the user as written: *"Needs working graphics drivers — see the guide"*, not *"no OpenGL"*.
2. **Register in `_enable`, run in `start`.** `_enable` runs even for a one-shot command; hardware grabbed there would be grabbed by every `trcc …` call.
3. **Everything through the host.** From TRCC itself a module imports only `trcc.core` (shared types), `trcc.ui` (for its faces) and `trcc.adapters.modules` (`BaseModule`). The App, the Platform, any service or adapter comes through the host. If you need something the host does not offer, that is a new capability, added to TRCC itself. A test enforces this, on the skeleton too.
4. **Your Commands live in your module.** They extend `ModuleCommand` and set `MODULE = "<key>"`; TRCC then refuses them, in every window, while the module is off.
5. **Log every function**, as all TRCC code does — a user's `trcc report` is often all there is to go on.
6. **Test it** through the App and each UI, the way `tests/test_module_skeleton.py` tests `hello`.
