"""The device Settings panel -- one panel for every GUI.

Separated by concern, as the rest of the hexagon is (his rule, 2026-11-10: a
GUI is only a skin):

* behaviour -- what a control sends and shows: ``ui/presentation/theme_settings``;
* layout -- where every control sits: :mod:`.settings_layout`;
* look -- injected: :class:`.settings_skin.SettingsSkin`, which each GUI builds
  from its own styles (the gui's ``settings_look``, hudgui's ``settings_look``);
* the widgets here, which draw at the layout's places in the skin they are given.
"""
