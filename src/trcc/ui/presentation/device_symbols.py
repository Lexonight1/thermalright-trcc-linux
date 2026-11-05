"""Which picture -- the device's SYMBOL -- a graphical UI shows for a device.

The gui's device buttons are product pictures (``A1<MODEL>.png``, and
``A1<MODEL>a.png`` for the selected state) from its asset tree.  The rule that
picks one is toolkit-free: only "does this asset exist?" needs the skin, so the
skin hands that in (*exists*).  Moved here from ``ui/gui/uc_device.py``
(2026-11-05) when hudgui's Devices menu needed the same symbols, "just like gui
does" (his words) -- one rule, both skins.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

log = logging.getLogger(__name__)

#: Device model names -> their symbol's base asset name (without ``.png``).
DEVICE_IMAGE_MAP: Mapping[str, str] = {
    'CZTV': 'A1CZTV',
    'CZ1': 'A1CZ1',
    'FROZEN_WARFRAME': 'A1FROZEN WARFRAME',
    'FROZEN_WARFRAME_PRO': 'A1FROZEN WARFRAME PRO',
    'FROZEN_WARFRAME_SE': 'A1FROZEN WARFRAME SE',
    'FROZEN_HORIZON_PRO': 'A1FROZEN HORIZON PRO',
    'FROZEN_MAGIC_PRO': 'A1FROZEN MAGIC PRO',
    'FROZEN_VISION_V2': 'A1FROZEN VISION V2',
    'AX120_DIGITAL': 'A1AX120 DIGITAL',
    'AK120_DIGITAL': 'A1AK120 Digital',
    'PA120_DIGITAL': 'A1PA120 DIGITAL',
    'RK120_DIGITAL': 'A1RK120 DIGITAL',
    'AS120_VISION': 'A1AS120 VISION',
    'BA120_VISION': 'A1BA120 VISION',
    'RP130_VISION': 'A1RP130 VISION',
    'CORE_VISION': 'A1CORE VISION',
    'ELITE_VISION': 'A1ELITE VISION',
    'GRAND_VISION': 'A1GRAND VISION',
    'HYPER_VISION': 'A1HYPER VISION',
    'Mjolnir_VISION': 'A1Mjolnir VISION',
    'Mjolnir_VISION_PRO': 'A1Mjolnir VISION PRO',
    'Stream_Vision': 'A1Stream Vision',
    'KVMALEDC6': 'A1KVMALEDC6',
    'LC1': 'A1LC1',
    'LC2': 'A1LC2',
    'LC2JD': 'A1LC2JD',
    'LC3': 'A1LC3',
    'LC5': 'A1LC5',
    'LF8': 'A1LF8',
    'LF10': 'A1LF10',
    'LF11': 'A1LF11',
    'LF12': 'A1LF12',
    'LF13': 'A1LF13',
    'LF14': 'A1LF14',
    'LF15': 'A1LF15',
    'LF16': 'A1LF16',
    'LF18': 'A1LF18',
    'LF19': 'A1LF19',
    'LM16SE': 'A1LM16SE',
    'LM22': 'A1LM22',
    'LM24': 'A1LM24',
    'LM26': 'A1LM26',
    'LM27': 'A1LM27',
}


def device_symbols(device_info: Mapping[str, Any],
                   exists: Callable[[str], bool]) -> tuple[str | None, str | None]:
    """The device's symbol: ``(normal, active)`` asset names, or
    ``(None, None)`` -- show its text name instead.

    *device_info* carries ``button_image``, ``protocol``, ``model`` and
    ``name`` (the gui's device dict); *exists* says whether the skin has an
    asset by that name.  For a HID device still on the generic default
    (``A1CZTV``) there is no symbol, so the text name shows rather than a
    misleading picture -- the handshake names the real product.
    """
    button_image = device_info.get('button_image', '')
    protocol = device_info.get('protocol', 'scsi')

    # For HID devices, skip the generic A1CZTV default — show text name
    # until the handshake identifies the actual product.
    if protocol == 'hid' and button_image == 'A1CZTV':
        log.debug("device_symbols: HID default A1CZTV, showing text until handshake")
        return None, None

    # Try button_image field first (from DetectedDevice)
    if button_image:
        if exists(button_image):
            log.debug("device_symbols: found %s (exact)", button_image)
            return button_image, f"{button_image}a"

        spaced = button_image.replace('_', ' ')
        if exists(spaced):
            log.debug("device_symbols: found %s (spaced)", spaced)
            return spaced, f"{spaced}a"

        underscored = button_image.replace(' ', '_')
        if exists(underscored):
            log.debug("device_symbols: found %s (underscored)", underscored)
            return underscored, f"{underscored}a"

        log.debug("device_symbols: button_image=%r not found in assets", button_image)

    # Try model field
    model = device_info.get('model', '')
    if model in DEVICE_IMAGE_MAP:
        base = DEVICE_IMAGE_MAP[model]
        if exists(base):
            log.debug("device_symbols: found %s via model=%s", base, model)
            return base, f"{base}a"

    # Try name field as fallback
    name = device_info.get('name', '')
    for model_key, img_base in DEVICE_IMAGE_MAP.items():
        if model_key.lower() in name.lower():
            if exists(img_base):
                log.debug("device_symbols: found %s via name match %s", img_base, model_key)
                return img_base, f"{img_base}a"

    # Default to CZTV for non-HID devices
    if protocol != 'hid' and exists('A1CZTV'):
        log.debug("device_symbols: falling back to A1CZTV")
        return 'A1CZTV', 'A1CZTVa'

    return None, None
