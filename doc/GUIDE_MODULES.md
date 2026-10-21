# Modules

A **module** is a feature you can switch on or off on the **Modules** page, in every TRCC window. A module that is off costs nothing: TRCC does not even load its code.

> **Where this stands.** The module system is built; the features themselves are still moving into it. RGB Lighting and the Audio Visualizer become modules next, and installing, updating and deleting modules comes after that. Until then the Modules page may be empty.

---

## Using the Modules page

Open it from the Control Center's title bar (gui) or the sidebar (qtgui). Each module is one row:

- **Its symbol** on the left, its **name**, and one sentence on **what it does**.
- **Guide ›** opens that module's section of this guide.
- **On / Off** switches it, at once and in every open window — no restart.
- When a module cannot run on your computer, or a switch was refused, the row says **why, and what to do**, in plain words.

From the command line: `trcc system modules list`, `trcc system modules on <key>`, `trcc system modules off <key>`. From the API: `GET /system/modules`, `POST /system/modules/{key}`.

When a module is off, whatever belongs to it is refused in every window, with a line saying it is off and where to turn it on. Your choices stay saved: switching a module back on picks up where it left off. With RGB Lighting and the Audio Visualizer, an off module's buttons, tabs and themes will also be hidden, and come back the moment you switch it on.

### Coming next

- **RGB Lighting** and the **Audio Visualizer** as modules, on by default, so nothing changes for anyone who uses them.
- **Install, Update and Delete**, from TRCC's own GitHub releases only. Every download is checked against a list signed by the maintainer (see [Verify Your Download](GUIDE_INSTALL.md#verify-your-download)); a failed or tampered download is never installed, and a failed update keeps the old version working.

---

## Writing a module

This part is for contributors. Modules are written by the TRCC project itself — there are no third-party modules yet.

### Start from the skeleton

```bash
PYTHONPATH=src python3.12 dev/tools/new_module.py weather --title "Weather" \
    --summary "Today's forecast as a panel background."
```

That copies the `hello` skeleton (`dev/module_template/`) into the two places a built-in module lives:

| Where | What |
|---|---|
| `src/trcc/assets/modules/<key>/` | `module.json` and `icon.svg` — the only data folder every build carries, so the Windows and macOS apps get it too |
| `src/trcc/adapters/modules/<key>/` | the code, imported only when the module is switched on |

`hello` is a real, working module: the tests load it through the same catalog as every built-in, so the skeleton cannot silently go stale.

### module.json

The Modules page lists a module from this file alone.

| Field | Meaning |
|---|---|
| `key` | its name everywhere — the folder name, the Python package name, the Command tag; lowercase letters, digits and `_` |
| `title` | the name on its row |
| `summary` | one plain sentence on what it does for the user |
| `entry` | the Python package whose import registers its class (`trcc.adapters.modules.<key>`) |
| `version` | its version |
| `default_on` | on for someone who never chose (new modules start `false`) |
| `icon` | a file beside `module.json`, drawn 40 × 40 on its row |
| `guide` | a link to its section of this guide |
| `capabilities` | what it may ask TRCC for — see below; anything else is refused |

### When each hook runs

A module's class extends `BaseModule` and names its key in the class line: `class WeatherModule(BaseModule, key="weather")`.

| Hook | When | What belongs there |
|---|---|---|
| `pre_enable` | before switching on | can it run here? Refuse with `ModuleUnavailable` |
| `_enable` | switching on — in **every** TRCC process, a one-shot command's too | **register only**: subscriptions, entries. Never hardware, never a thread |
| `start` | when the session starts, or when switched on inside one | hardware and threads |
| `stop` | when the session ends, or before switching off | stop what `start` began |
| `_disable` | switching off | take out what `_enable` registered (subscriptions are taken back for you) |

A module that raises in any hook is switched back off and its row says why — it can never break TRCC or be left half on.

### Capabilities

A module never gets the App itself. It gets a **host** that offers only what its `module.json` declares:

| Capability | What it lets the module do |
|---|---|
| `events` | hears what happens in the app |

More arrive as modules need them — one at a time, each with a reason.

### Rules

1. **Plain words with a next step.** Every refusal is shown to the user as written: *"Needs working graphics drivers — see the guide"*, not *"no OpenGL"*.
2. **Register in `_enable`, run in `start`.** `_enable` runs even for a one-shot command; hardware grabbed there would be grabbed by every `trcc …` call.
3. **Everything through the host.** If you need something the host does not offer, that is a new capability, added to TRCC itself.
4. **Tag your Commands.** A Command that belongs to your module sets `MODULE = "<key>"`; TRCC then refuses it, in every window, while the module is off.
5. **Log every function**, as all TRCC code does — a user's `trcc report` is often all there is to go on.
6. **Test it** through the App, the way `tests/test_module_skeleton.py` tests `hello`.
