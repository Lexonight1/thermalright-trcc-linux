"""What the panel is showing, in words -- one sentence every UI prints.

The App decides the source once (``LcdSnapshot.display_source``: the C#'s one
UI-mode choice between background, screen cast and video player); this turns
that answer into the line the qtgui Display panel and the CLI's status text
show, so the two cannot describe the same panel differently.
"""
from __future__ import annotations

import logging
from pathlib import PurePath

log = logging.getLogger(__name__)

_BACKGROUND = {
    "theme": "the theme's background",
    "color": "a solid colour background",
    "transparent": "no background",
}


def describe_source(source: str, background_mode: str,
                    media_player_uri: str | None) -> str:
    """``LcdSnapshot``'s source fields as one short phrase."""
    log.debug("describe_source: %s mode=%s uri=%s",
              source, background_mode, media_player_uri)
    if source == "screencast":
        return "a screen cast"
    if source == "media":
        uri = media_player_uri or ""
        shown = uri if "://" in uri else PurePath(uri).name
        return f"the media player: {shown}"
    return _BACKGROUND.get(background_mode, f"background ({background_mode})")
