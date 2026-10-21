"""The RGB page both windows draw -- its choices and refusals, no Qt."""
from __future__ import annotations

import pytest

from trcc.core.models import (
    EFFECT_TRAITS,
    EffectDirection,
    EffectSpeed,
    FollowColors,
    FollowMapping,
    LightKind,
    RamAccessState,
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbLight,
)
from trcc.core.results import DeviceEntry, RgbFollowResult, RgbLightsResult
from trcc.ui.presentation.rgb_page import (
    BAD_ADDRESS,
    DEFAULT_COLORS,
    EFFECT_LABELS,
    FindLights,
    FollowDevice,
    LeaveLights,
    LinkColor,
    RgbDevice,
    RgbPage,
    RgbSource,
    RunBoardEffect,
    SaveEffect,
    strip_legend,
)

STICK_A = RgbLight("i2c-3/0x19", "Vengeance A", 10, LightKind.RAM)
STICK_B = RgbLight("i2c-3/0x1b", "Vengeance B", 10, LightKind.RAM)
FAN = RgbLight("Fan hub", "Fan hub", 8, LightKind.OPENRGB)
LCD = DeviceEntry(key="0402:3922", product="Frozen Warframe", kind="lcd",
                  connected=True)
COOLER = DeviceEntry(key="0416:8001", product="AX120", kind="led",
                     connected=True)


def _page(*lights: RgbLight, follow: RgbFollowResult | None = None,
          access: RamAccessState = RamAccessState.ON,
          scanned: bool = True) -> RgbPage:
    page = RgbPage()
    page.load(RgbLightsResult(lights=lights, scanned=scanned,
                              ram_access=access),
              follow or RgbFollowResult(host="127.0.0.1", port=6742),
              (LCD, COOLER))
    return page


# ── What it starts on ────────────────────────────────────────────────

def _on(page: RgbPage, device: RgbDevice, source: RgbSource | None = None) -> RgbPage:
    """*page* showing *device*, on *source* when given."""
    page.set_device(device)
    if source is not None:
        page.set_source(source)
    return page


# ── What it starts on ────────────────────────────────────────────────

def test_nothing_running_opens_on_the_first_device_with_lights() -> None:
    page = _page(STICK_A, STICK_B, FAN)
    assert (page.device, page.source) == (RgbDevice.MEMORY, RgbSource.LEAVE)
    assert [r.checked for r in page.rows(LightKind.RAM)] == [True, True]
    assert _page(FAN).device is RgbDevice.OPENRGB


def test_it_opens_on_the_device_trcc_drives() -> None:
    page = _page(STICK_A, LIT_HEADER, follow=RgbFollowResult(
        mode=RgbFollowMode.BOARD, host="127.0.0.1", port=6742,
        targets=(LIT_HEADER.ref,)))
    assert (page.device, page.source) == (RgbDevice.MOTHERBOARD, RgbSource.FOLLOW)


def test_a_device_the_user_picked_stays_through_a_reload() -> None:
    page = _on(_page(STICK_A, FAN), RgbDevice.OPENRGB)
    page.load(RgbLightsResult(lights=(STICK_A, FAN), scanned=True,
                              ram_access=RamAccessState.ON), RgbFollowResult(), ())
    assert page.device is RgbDevice.OPENRGB


def test_a_saved_effect_starts_on_that_effect() -> None:
    saved = RamEffectSettings(RamEffect.RAIN, EffectSpeed.FAST,
                              EffectDirection.UP, ((1, 2, 3),), False, 90)
    page = _page(RgbLight(STICK_A.ref, STICK_A.name, 10, LightKind.RAM, saved),
                 STICK_B)
    assert page.source is RgbSource.EFFECT
    picks = page.effect_picks
    assert (picks.effect, picks.speed, picks.direction, picks.brightness) == (
        RamEffect.RAIN, EffectSpeed.FAST, EffectDirection.UP, 90)
    # One saved colour; the second slot keeps its default.
    assert picks.colors == [(1, 2, 3), DEFAULT_COLORS[1]]
    assert page.rows(LightKind.RAM)[0].detail == (
        f"direct · 10 LEDs · {EFFECT_LABELS[RamEffect.RAIN]} saved")


def test_following_starts_on_follow_with_its_saved_lights() -> None:
    follow = RgbFollowResult(mode=RgbFollowMode.RAM, host="10.0.0.2",
                             port=7000, source=LCD.key,
                             mapping=FollowMapping.SINGLE,
                             targets=(STICK_B.ref, "i2c-9/0x50"))
    page = _page(STICK_A, STICK_B, follow=follow)
    assert page.source is RgbSource.FOLLOW
    assert [r.checked for r in page.rows(LightKind.RAM)] == [False, True]
    picks = page.follow_picks
    assert (picks.source, picks.mapping, page.address) == (
        LCD.key, FollowMapping.SINGLE, "10.0.0.2:7000")
    assert picks.mapping_applies


def test_load_drops_what_the_user_picked() -> None:
    page = _page(STICK_A)
    page.set_source(RgbSource.EFFECT)
    page.set_checked(STICK_A.ref, False)
    page.load(RgbLightsResult(lights=(STICK_A,), scanned=True,
                              ram_access=RamAccessState.ON),
              RgbFollowResult(), ())
    assert page.source is RgbSource.LEAVE
    assert page.rows(LightKind.RAM)[0].checked


# ── The lights ───────────────────────────────────────────────────────

@pytest.mark.parametrize("access, reachable", [
    (RamAccessState.ON, True), (RamAccessState.ELSEWHERE, True),
    (RamAccessState.OFF, False), (RamAccessState.NO_BUS, False),
    (RamAccessState.UNSUPPORTED, False), (RamAccessState.NOT_APPLIED, False),
])
def test_the_ram_is_reachable_only_with_access(access: RamAccessState,
                                               reachable: bool) -> None:
    assert _page(access=access).ram_reachable is reachable


def test_the_note_says_whether_the_device_was_looked_for() -> None:
    assert _page(scanned=False).note == "Not searched yet -- press Find lights."
    assert _page().note == "No memory lights found."
    assert _on(_page(), RgbDevice.OPENRGB).note == "No openrgb lights found."
    assert _page(STICK_A).note == ""
    page = _page(FAN)
    page.openrgb_error = "refused"
    assert _on(page, RgbDevice.OPENRGB).note == "not reachable -- refused"


def test_lights_from_the_last_find_say_so() -> None:
    """Listed from the settings after a restart: the note says so, per
    device -- one searched this run says nothing."""
    from dataclasses import replace

    from trcc.ui.presentation.rgb_page import REMEMBERED_NOTE

    old_stick = replace(STICK_A, remembered=True)
    old_fan = replace(FAN, remembered=True)
    assert _page(old_stick, scanned=False).note == REMEMBERED_NOTE
    assert _on(_page(old_fan, scanned=False), RgbDevice.OPENRGB).note == REMEMBERED_NOTE
    assert _page(STICK_A, old_fan).note == ""


def test_find_carries_the_address_or_refuses_it() -> None:
    page = _page()
    page.address = "192.168.1.5:6800"
    assert page.find() == FindLights("192.168.1.5", 6800)
    page.address = "http://x/"
    assert page.find() is None
    assert page.message == BAD_ADDRESS


def test_each_device_shows_only_its_own_lights() -> None:
    page = _on(_page(STICK_A, FAN, HEADER, MYSTIC), RgbDevice.MOTHERBOARD)
    assert page.preview_lights == ()
    page.set_checked(HEADER.ref, True)
    assert page.preview_lights == (HEADER.name,)
    assert page.effect_strips() == ((HEADER.name, None),)


# ── The sources ──────────────────────────────────────────────────────

def test_effects_are_for_memory_and_the_board_and_following_needs_a_light() -> None:
    bare = _page()
    assert [bare.source_enabled(s) for s in RgbSource] == [True, False, False]
    fans = _on(_page(FAN), RgbDevice.OPENRGB)
    assert [fans.source_enabled(s) for s in RgbSource] == [True, False, True]
    board = _on(_page(LIT_HEADER), RgbDevice.MOTHERBOARD)
    assert [board.source_enabled(s) for s in RgbSource] == [True, True, True]
    # Following that already runs can always be seen, and switched off.
    running = _page(follow=RgbFollowResult(mode=RgbFollowMode.OPENRGB,
                                           host="h", port=1))
    assert running.device is RgbDevice.OPENRGB
    assert running.source_enabled(RgbSource.FOLLOW)


def test_leave_alone_stops_what_trcc_sends_to_this_device() -> None:
    follow = RgbFollowResult(mode=RgbFollowMode.RAM, host="h", port=1)
    page = _on(_page(STICK_A, FAN, follow=follow), RgbDevice.MEMORY, RgbSource.LEAVE)
    assert page.apply() == LeaveLights()


def test_leave_alone_on_a_device_trcc_does_not_drive_has_nothing_to_do() -> None:
    """Stopping the follower from another device's panel would stop the wrong
    lights.

    MUTATION CHECK: drop the LEAVE check from ``problem`` -- OpenRGB's Apply
    stops the RAM following."""
    follow = RgbFollowResult(mode=RgbFollowMode.RAM, host="h", port=1)
    page = _on(_page(STICK_A, FAN, follow=follow), RgbDevice.OPENRGB, RgbSource.LEAVE)
    assert page.problem == "TRCC is not sending to these lights."
    assert page.apply() is None


# ── A built-in effect ────────────────────────────────────────────────

def test_an_effect_on_every_stick_names_none_so_new_sticks_count() -> None:
    page = _page(STICK_A, STICK_B, FAN)
    page.set_source(RgbSource.EFFECT)
    page.effect_picks.set_effect(RamEffect.COLOR_WAVE)
    plan = page.apply()
    assert plan == SaveEffect((), RamEffectSettings(
        RamEffect.COLOR_WAVE, colors=DEFAULT_COLORS))
    page.set_checked(STICK_A.ref, False)
    plan = page.apply()
    assert isinstance(plan, SaveEffect) and plan.refs == (STICK_B.ref,)


@pytest.mark.parametrize("effect", list(RamEffect))
def test_each_effect_sends_only_what_it_takes(effect: RamEffect) -> None:
    traits = EFFECT_TRAITS[effect]
    page = _page(STICK_A)
    page.set_source(RgbSource.EFFECT)
    page.effect_picks.set_effect(effect)
    page.effect_picks.random_colors = True
    plan = page.apply()
    assert isinstance(plan, SaveEffect)
    assert len(plan.settings.colors) == traits.colors
    assert plan.settings.random_colors is traits.random
    assert plan.settings.direction is None


def test_a_direction_the_new_effect_cannot_take_falls_back() -> None:
    page = _page(STICK_A)
    page.effect_picks.set_effect(RamEffect.RAINBOW_WAVE)
    page.effect_picks.direction = EffectDirection.LEFT
    page.effect_picks.set_effect(RamEffect.RAIN)            # up / down only
    assert page.effect_picks.direction is None
    assert page.effect_picks.shown_direction is EffectDirection.DOWN
    page.effect_picks.set_effect(RamEffect.STATIC)
    assert page.effect_picks.shown_direction is None


def test_an_effect_with_no_light_ticked_is_refused() -> None:
    page = _page(STICK_A, FAN)
    page.set_source(RgbSource.EFFECT)
    page.set_checked(STICK_A.ref, False)
    assert page.problem == "Tick the lights the effect runs on."
    assert page.apply() is None


def _headers(*leds: int) -> tuple[RgbLight, ...]:
    return tuple(RgbLight(f"aura/argb{n}", f"ARGB header {n}", count, LightKind.BOARD)
                 for n, count in enumerate(leds, 1))


def test_an_effect_on_a_ticked_header_runs_on_the_board() -> None:
    """The maintainer's exhaust fan: a Static colour on header 1, from the
    Motherboard panel -- the sticks, ticked on their own panel, untouched.

    MUTATION CHECK: plan SaveEffect for every device -- the sticks get it."""
    one, two = _headers(8, 4)
    page = _on(_page(STICK_A, one, two), RgbDevice.MOTHERBOARD, RgbSource.EFFECT)
    page.set_checked(one.ref, True)
    plan = page.apply()
    assert isinstance(plan, RunBoardEffect)
    assert plan.refs == ("aura/argb1",)


def test_a_header_with_no_led_count_cannot_run_an_effect() -> None:
    (one,) = _headers(0)
    page = _on(_page(one), RgbDevice.MOTHERBOARD, RgbSource.EFFECT)
    page.set_checked(one.ref, True)
    assert page.problem == "Give ARGB header 1 its number of LEDs first."


def test_openrgbs_lights_take_no_effect() -> None:
    page = _on(_page(FAN), RgbDevice.OPENRGB)
    assert not page.source_enabled(RgbSource.EFFECT)


def test_a_board_running_trccs_effect_opens_on_that_effect() -> None:
    """Following with the effect clock as the source is shown as the effect
    it runs -- not as following a device called "effect"."""
    from trcc.core.models import EFFECT_SOURCE
    rainbow = RamEffectSettings(effect=RamEffect.RAINBOW)
    one = RgbLight("aura/argb1", "ARGB header 1", 8, LightKind.BOARD, rainbow)
    page = _page(STICK_A, one, follow=RgbFollowResult(
        mode=RgbFollowMode.BOARD, source=EFFECT_SOURCE, targets=("aura/argb1",),
        host="127.0.0.1", port=6742))
    assert (page.device, page.source) == (RgbDevice.MOTHERBOARD, RgbSource.EFFECT)
    assert page.effect_picks.settings().effect is RamEffect.RAINBOW
    assert [r.checked for r in page.rows(LightKind.BOARD)] == [True]
    assert page.follow_picks.source == ""


def test_the_effect_rules_are_the_core_ones() -> None:
    page = _page(STICK_A)
    page.set_source(RgbSource.EFFECT)
    page.effect_picks.brightness = 300
    assert page.problem == "brightness is 0-255, not 300"


def test_taking_the_one_follower_from_another_device_is_said() -> None:
    """TRCC sends to one device at a time: a board effect stops the RAM
    following, and the panel says so before Apply.

    MUTATION CHECK: drop the warning from ``hint``."""
    (one,) = _headers(8)
    follow = RgbFollowResult(mode=RgbFollowMode.RAM, host="h", port=1)
    page = _on(_page(STICK_A, one, follow=follow), RgbDevice.MOTHERBOARD,
               RgbSource.EFFECT)
    assert page.hint.endswith("Memory stops when you apply this.")
    page.set_device(RgbDevice.MEMORY)
    assert "stops when you apply" not in page.hint


# ── Following ────────────────────────────────────────────────────────

def test_follow_choices_are_the_cooler_every_device_and_a_missing_one() -> None:
    page = _page(STICK_A, follow=RgbFollowResult(
        mode=RgbFollowMode.RAM, host="h", port=1, source="0402:9999"))
    assert [(c.key, c.label, c.is_lcd) for c in page.follow_picks.choices()] == [
        ("", "The first LED cooler", False),
        (LCD.key, "Frozen Warframe (LCD)", True),
        (COOLER.key, "AX120 (LED cooler)", False),
        ("0402:9999", "0402:9999 (not connected)", True),
    ]


def test_mapping_is_only_for_a_picture() -> None:
    picks = _page(STICK_A).follow_picks
    for key, applies in (("", False), (COOLER.key, False), (LCD.key, True)):
        picks.source = key
        assert picks.mapping_applies is applies


def test_ticked_sticks_follow_the_ram_way() -> None:
    page = _page(STICK_A, STICK_B)
    page.set_source(RgbSource.FOLLOW)
    picks = page.follow_picks
    picks.source, picks.mapping, picks.colors = (LCD.key, FollowMapping.SINGLE,
                                                 FollowColors.SMOOTH)
    assert page.apply() == FollowDevice(RgbFollowMode.RAM, "127.0.0.1", 6742,
                                        LCD.key, FollowMapping.SINGLE,
                                        FollowColors.SMOOTH, ())
    page.set_checked(STICK_A.ref, False)
    plan = page.apply()
    assert isinstance(plan, FollowDevice) and plan.targets == (STICK_B.ref,)


def test_openrgb_devices_follow_through_openrgb() -> None:
    page = _on(_page(STICK_A, FAN), RgbDevice.OPENRGB, RgbSource.FOLLOW)
    page.address = "10.1.1.1:6743"
    assert page.apply() == FollowDevice(RgbFollowMode.OPENRGB, "10.1.1.1",
                                        6743, "", FollowMapping.HALVES,
                                        FollowColors.VIVID, ())


def test_following_with_nothing_ticked_is_refused() -> None:
    page = _page(STICK_A, FAN)
    page.set_source(RgbSource.FOLLOW)
    page.set_checked(STICK_A.ref, False)
    assert page.problem == "Tick the lights that should follow."
    assert page.apply() is None


def test_a_bad_address_stops_openrgb_from_following() -> None:
    page = _on(_page(FAN), RgbDevice.OPENRGB, RgbSource.FOLLOW)
    page.address = "no spaces allowed"
    assert page.problem == BAD_ADDRESS


@pytest.mark.parametrize("follow, status", [
    (RgbFollowResult(), "Not following."),
    (RgbFollowResult(mode=RgbFollowMode.OPENRGB, error="refused"),
     "OpenRGB is not reachable -- refused"),
    (RgbFollowResult(mode=RgbFollowMode.RAM, connected=True,
                     devices=("A", "B")), "Following on: A, B"),
    (RgbFollowResult(mode=RgbFollowMode.RAM), "The RAM is waiting for colours."),
])
def test_follow_status_says_what_the_app_said(follow: RgbFollowResult,
                                              status: str) -> None:
    assert _page(follow=follow).follow_status == status


# ── The effect preview's strips ──────────────────────────────────────

def test_the_effect_preview_runs_the_picked_effect_on_ticked_sticks() -> None:
    page = _page(STICK_A, STICK_B)
    page.set_source(RgbSource.EFFECT)
    page.effect_picks.set_effect(RamEffect.RAIN)
    page.set_checked(STICK_A.ref, False)
    assert page.effect_strips() == (
        (STICK_B.name, page.effect_picks.settings()),)


def test_leave_alone_previews_what_trcc_last_saved_on_each_stick() -> None:
    saved = RamEffectSettings(RamEffect.VISOR)
    page = _page(STICK_A, RgbLight(STICK_B.ref, STICK_B.name, 10,
                                   LightKind.RAM, saved), FAN)
    page.set_source(RgbSource.LEAVE)
    assert page.effect_strips() == ((STICK_A.name, None),
                                    (STICK_B.name, saved))


def test_the_legend_says_what_each_letter_runs() -> None:
    """Letters only -- the names are on the cards, in the same order.

    MUTATION CHECK: put the names back -- "A: Vengeance A -- Rain"."""
    legend = strip_legend(((STICK_A.name, RamEffectSettings(RamEffect.RAIN)),
                           (STICK_B.name, None)))
    assert legend == (
        "A: Rain\n"
        "B: not known\n\n"
        "Not known: its factory default, or what OpenRGB or the motherboard's "
        "own lighting control last set.")
    assert strip_legend(((STICK_A.name, RamEffectSettings(RamEffect.RAIN)),
                         (STICK_B.name, RamEffectSettings(RamEffect.RAIN)))) == (
        "A: Rain\nB: Rain")


HEADER = RgbLight("aura/argb1", "ASUS PRIME Z790-V AX -- ARGB header 1", 0,
                  LightKind.BOARD)
ONBOARD = RgbLight("aura/onboard", "ASUS PRIME Z790-V AX -- board LEDs", 1,
                   LightKind.BOARD)
MYSTIC = RgbLight("usb/1462:7e06", "MSI MAG B650 (MSI Mystic Light MS_7E06)",
                  0, LightKind.FOUND)
LIT_HEADER = RgbLight("aura/argb2", "ASUS PRIME Z790-V AX -- ARGB header 2",
                      12, LightKind.BOARD)


def test_motherboard_lights_start_unticked() -> None:
    """Which header has a fan on it is the user's to say.  A found board
    never takes part.

    MUTATION CHECK: tick every tickable light at first again in load."""
    page = _on(_page(STICK_A, HEADER, ONBOARD, MYSTIC), RgbDevice.MOTHERBOARD)
    rows = page.rows(LightKind.BOARD) + page.rows(LightKind.FOUND)
    assert [(r.detail, r.checked, r.checkable) for r in rows] == [
        ("direct · LED count not set", False, True),
        ("direct · 1 LED", False, True),
        ("found -- TRCC does not drive it; another app may", False, False)]


def test_a_ticked_header_with_leds_follows_the_board_way() -> None:
    """Only the ticked header follows; the other board light is left alone.

    MUTATION CHECK: drop BOARD from DEVICE_FOLLOWER."""
    page = _on(_page(STICK_A, HEADER, LIT_HEADER), RgbDevice.MOTHERBOARD,
               RgbSource.FOLLOW)
    page.set_checked(LIT_HEADER.ref, True)
    assert page.problem is None
    assert page.apply() == FollowDevice(RgbFollowMode.BOARD, "127.0.0.1", 6742,
                                        "", FollowMapping.HALVES,
                                        FollowColors.VIVID, (LIT_HEADER.ref,))
    assert page.preview_lights == (LIT_HEADER.name,)


def test_a_ticked_header_without_leds_is_refused() -> None:
    """MUTATION CHECK: drop the ``silent`` branch from ``problem``."""
    page = _on(_page(HEADER, LIT_HEADER), RgbDevice.MOTHERBOARD, RgbSource.FOLLOW)
    page.set_checked(HEADER.ref, True)
    page.set_checked(LIT_HEADER.ref, True)
    assert page.problem == f"Give {HEADER.name} its number of LEDs first."
    assert page.apply() is None


def test_following_the_board_starts_on_its_saved_headers() -> None:
    follow = RgbFollowResult(mode=RgbFollowMode.BOARD, host="127.0.0.1",
                             port=6742, targets=(LIT_HEADER.ref,))
    page = _page(STICK_A, HEADER, LIT_HEADER, follow=follow)
    assert page.source is RgbSource.FOLLOW
    assert [r.checked for r in page.rows(LightKind.BOARD)] == [False, True]
    # Each device keeps its own ticks: the sticks start ticked as ever.
    assert page.rows(LightKind.RAM)[0].checked


# ── Linked colours: the motherboard's alone ──────────────────────────

def test_a_header_can_follow_the_gpu_load() -> None:
    """MUTATION CHECK: ignore ``linked`` in ``light_effect`` -- the plan
    carries a built-in effect."""
    from trcc.core.models import LinkedEffect
    from trcc.ui.presentation.rgb_page import effect_label
    page = _on(_page(LIT_HEADER), RgbDevice.MOTHERBOARD, RgbSource.EFFECT)
    page.set_checked(LIT_HEADER.ref, True)
    picks = page.effect_picks
    picks.linked, picks.link_source = "load", "gpu"
    plan = page.apply()
    assert plan == LinkColor((LIT_HEADER.ref,), LinkedEffect(metric="load", source="gpu"))
    assert effect_label(LinkedEffect("temp", "cpu")) == "Temperature-linked (CPU)"
    assert page.effect_strips() == ((LIT_HEADER.name, LinkedEffect("load", "gpu")),)


def test_memory_takes_a_linked_colour_live() -> None:
    """A stick cannot read a sensor, so its linked colour is TRCC's to send:
    the plan names the ticked sticks, and the hint says it is live.

    MUTATION CHECK: keep linked colours to the Motherboard -- a SaveEffect."""
    from trcc.core.models import LinkedEffect
    from trcc.ui.presentation.rgb_page import LINKED_HINT
    page = _on(_page(STICK_A, STICK_B), RgbDevice.MEMORY, RgbSource.EFFECT)
    page.effect_picks.linked = "temp"
    assert page.apply() == LinkColor((STICK_A.ref, STICK_B.ref), LinkedEffect("temp", "cpu"))
    assert page.hint.startswith(LINKED_HINT)


def test_only_a_sticks_own_effect_is_drawn_as_a_guess() -> None:
    """The stick runs its saved effect itself; a linked colour is TRCC's,
    drawn exactly.

    MUTATION CHECK: approximate on Memory whatever runs -- the linked case."""
    page = _on(_page(STICK_A, LIT_HEADER), RgbDevice.MEMORY, RgbSource.EFFECT)
    assert page.preview_approximate
    page.effect_picks.linked = "load"
    assert not page.preview_approximate
    page.set_device(RgbDevice.MOTHERBOARD)
    page.set_source(RgbSource.EFFECT)
    page.effect_picks.set_effect(RamEffect.RAIN)
    assert not page.preview_approximate
