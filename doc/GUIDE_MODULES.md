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

That copies the `hello` skeleton (`dev/module_template/hello/`) to `src/trcc/modules/<key>/`, the one folder a module lives in. `hello` is a real, working module with one of everything: the tests load it through the same catalog as every built-in and drive each part, so the skeleton cannot silently go stale.

### One folder, fixed names

A module is a small copy of TRCC's own layout. Each part has a fixed name, and TRCC finds it there — nothing lists it:

| Where | What | Loaded |
|---|---|---|
| `module.json`, `icon.svg`, `i18n.json` | its row on the Modules page, and its words in every language | always — without importing anything |
| `core/` | its Commands, Results and events | always, so its Commands are known in every window and refused while it is off |
| `app.py` | its root: the `Module` class, holding its own state | when it is switched on |
| `services/`, `adapters/` | its own logic and hardware code | by `app.py` |
| `ui/gui/` | exports `PAGE`: a widget that also extends `ModulePage` — the page its mode tab opens | while it is on |
| `ui/qtgui/` | exports `PANEL`: a `BasePanel` with a `LABEL` — its sidebar entry | while it is on |
| `ui/cli/` | exports `GROUP` (the `trcc` group, `""` for the top) and `COMMANDS` (a Typer app) | always, on or off |
| `ui/api/` | exports `ROUTERS`, the FastAPI routers it adds | always, on or off |

The windows show a module's tab or entry only while it is on, and follow it being switched in any window. The CLI and the API carry an installed module's commands whether it is on or off; while it is off, they answer that it is off and where to turn it on.

The same layer rules apply inside a module as in TRCC itself, and the same tests check them. TRCC never imports a module by name, and a module never imports TRCC's App or another module.

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
4. **Your Commands live in your module.** They extend `ModuleCommand` and set `MODULE = "<key>"`; TRCC then refuses them, in every window, while the module is off.
5. **Log every function**, as all TRCC code does — a user's `trcc report` is often all there is to go on.
6. **Test it** through the App and each UI, the way `tests/test_module_skeleton.py` tests `hello`.
