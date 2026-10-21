"""Hello -- the smallest module there is, and the skeleton every new module
starts from (``dev/tools/new_module.py``).

One folder, fixed names, each part found by its place (doc/GUIDE_MODULES.md):

* ``module.json``, ``icon.svg``, ``i18n.json`` -- its row on the Modules page;
* ``app.py`` -- its root, imported only when it is switched on;
* ``core/`` -- its Commands and Results, known in every process;
* ``ui/<face>/`` -- what it adds to the gui, qtgui, CLI and API.

This file stays empty: importing ``core/`` imports it first.
"""
