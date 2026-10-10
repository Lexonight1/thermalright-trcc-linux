"""The RGB page both windows draw -- its choices and refusals, no Qt."""
from __future__ import annotations

import pytest

from trcc.core.models import (
    EFFECT_TRAITS,
    EffectDirection,
    EffectSpeed,
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
    RgbPage,
    RgbSource,
    SaveEffect,
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

def test_nothing_running_starts_on_leave_alone_with_every_light_ticked() -> None:
    page = _page(STICK_A, STICK_B, FAN)
    assert page.source is RgbSource.LEAVE
    assert [r.checked for r in page.rows(LightKind.RAM)] == [True, True]
    assert [r.ref for r in page.rows(LightKind.OPENRGB)] == ["Fan hub"]


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
    assert (page.follow_source, page.mapping, page.address) == (
        LCD.key, FollowMapping.SINGLE, "10.0.0.2:7000")
    assert page.mapping_applies


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


def test_notes_say_whether_anything_was_looked_for() -> None:
    assert _page(scanned=False).ram_note == (
        "Press Find lights to look for RGB memory.")
    assert _page(scanned=False).openrgb_note == (
        "not searched yet -- press Find lights")
    assert _page().ram_note == "No RGB memory found."
    assert _page(FAN).openrgb_note == "1 device(s) found"
    assert _page(STICK_A).ram_note == ""


def test_find_carries_the_address_or_refuses_it() -> None:
    page = _page()
    page.address = "192.168.1.5:6800"
    assert page.find() == FindLights("192.168.1.5", 6800)
    page.address = "http://x/"
    assert page.find() is None
    assert page.message == BAD_ADDRESS


# ── The sources ──────────────────────────────────────────────────────

def test_effects_need_a_stick_and_following_needs_a_light() -> None:
    bare = _page()
    assert [bare.source_enabled(s) for s in RgbSource] == [True, False, False]
    fan_only = _page(FAN)
    assert [fan_only.source_enabled(s) for s in RgbSource] == [True, False, True]
    # Following that already runs can always be seen, and switched off.
    running = _page(follow=RgbFollowResult(mode=RgbFollowMode.OPENRGB,
                                           host="h", port=1))
    assert running.source_enabled(RgbSource.FOLLOW)


def test_leave_alone_turns_following_off() -> None:
    assert _page(STICK_A).apply() == LeaveLights()


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


def test_an_effect_with_no_stick_ticked_is_refused() -> None:
    page = _page(STICK_A, FAN)
    page.set_source(RgbSource.EFFECT)
    page.set_checked(STICK_A.ref, False)
    assert page.problem == "Tick a RAM stick -- effects run on the memory itself."
    assert page.apply() is None


def test_the_effect_rules_are_the_core_ones() -> None:
    page = _page(STICK_A)
    page.set_source(RgbSource.EFFECT)
    page.effect_picks.brightness = 300
    assert page.problem == "brightness is 0-255, not 300"


# ── Following ────────────────────────────────────────────────────────

def test_follow_choices_are_the_cooler_every_device_and_a_missing_one() -> None:
    page = _page(STICK_A, follow=RgbFollowResult(
        mode=RgbFollowMode.RAM, host="h", port=1, source="0402:9999"))
    assert [(c.key, c.label, c.is_lcd) for c in page.follow_choices()] == [
        ("", "The first LED cooler", False),
        (LCD.key, "Frozen Warframe (LCD)", True),
        (COOLER.key, "AX120 (LED cooler)", False),
        ("0402:9999", "0402:9999 (not connected)", True),
    ]


def test_mapping_is_only_for_a_picture() -> None:
    page = _page(STICK_A)
    for key, applies in (("", False), (COOLER.key, False), (LCD.key, True)):
        page.follow_source = key
        assert page.mapping_applies is applies


def test_ticked_sticks_follow_the_ram_way() -> None:
    page = _page(STICK_A, STICK_B)
    page.set_source(RgbSource.FOLLOW)
    page.follow_source = LCD.key
    page.mapping = FollowMapping.SINGLE
    assert page.apply() == FollowDevice(RgbFollowMode.RAM, "127.0.0.1", 6742,
                                        LCD.key, FollowMapping.SINGLE, ())
    page.set_checked(STICK_A.ref, False)
    plan = page.apply()
    assert isinstance(plan, FollowDevice) and plan.targets == (STICK_B.ref,)


def test_ticked_openrgb_devices_follow_through_openrgb() -> None:
    page = _page(STICK_A, FAN)
    page.set_source(RgbSource.FOLLOW)
    page.set_checked(STICK_A.ref, False)
    page.address = "10.1.1.1:6743"
    assert page.apply() == FollowDevice(RgbFollowMode.OPENRGB, "10.1.1.1",
                                        6743, "", FollowMapping.HALVES, ())


@pytest.mark.parametrize("untick, problem", [
    ((), "Following drives RAM or OpenRGB lights, one at a time -- untick "
         "one kind."),
    ((STICK_A.ref, FAN.ref), "Tick the lights that should follow."),
])
def test_following_both_kinds_or_none_is_refused(untick: tuple[str, ...],
                                                 problem: str) -> None:
    page = _page(STICK_A, FAN)
    page.set_source(RgbSource.FOLLOW)
    for ref in untick:
        page.set_checked(ref, False)
    assert page.problem == problem
    assert page.apply() is None


def test_following_with_nothing_listed_keeps_the_mode_that_runs() -> None:
    page = _page(follow=RgbFollowResult(mode=RgbFollowMode.OPENRGB,
                                        host="h", port=9))
    assert page.apply() == FollowDevice(RgbFollowMode.OPENRGB, "h", 9, "",
                                        FollowMapping.HALVES, ())


def test_a_bad_address_stops_following_from_applying() -> None:
    page = _page(FAN)
    page.set_source(RgbSource.FOLLOW)
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
