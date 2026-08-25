"""Commands — UI contract dispatched through App.dispatch."""
from __future__ import annotations

from trcc.app import App
from trcc.core.commands import (
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    GetPlatformInfo,
    ReadSensors,
)


def test_read_sensors_returns_merged_descriptors_and_live_values(fake_platform) -> None:
    """ReadSensors must enrich discover() with read_all() values."""
    app = App(fake_platform)
    result = app.dispatch(ReadSensors())

    assert result.ok is True
    assert result.readings, "at least some readings expected"

    by_id = {r.sensor_id: r for r in result.readings}
    # CPU temp from the Fake CPU source
    assert by_id["cpu:temp"].value == 42.0
    # GPU temp from the Fake NVIDIA GPU
    assert by_id["gpu:primary:temp"].value == 55.0


def test_get_platform_info_returns_fake_platform_fields(fake_platform) -> None:
    app = App(fake_platform)
    r = app.dispatch(GetPlatformInfo())

    assert r.ok is True
    assert r.distro_name == "Fake Linux"
    assert r.install_method == "test"
    # Paths all derive from the FakePaths root
    assert r.config_dir.endswith(str(fake_platform.paths().config_dir()))
    # The hint the gui shows when nothing is attached.  Asserted against the
    # platform's OWN answer, not a literal: the point is that the Query
    # CARRIES it, so the gui never has to reach ``app.platform`` — which is
    # an AttributeError under TRCC_DAEMON=1.
    assert r.no_devices_hint == fake_platform.no_devices_hint()
    assert r.no_devices_hint, "platform hint came back empty"


def test_autostart_enable_then_status_reports_enabled(fake_platform) -> None:
    app = App(fake_platform)
    # Baseline
    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is False

    app.dispatch(EnableAutostart())
    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is True


def test_autostart_disable_clears_state(fake_platform) -> None:
    app = App(fake_platform)
    app.dispatch(EnableAutostart())

    app.dispatch(DisableAutostart())

    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is False


# =========================================================================
# GetPaths — the diagnostic that has to name the directory the app opens
#
# Users answer "where did my theme go?" from this output and paste it into
# issues, so a generic answer for a per-SKU cooler does not merely read
# imprecisely -- it points the reporter, and us, at a directory the app never
# opens.  ``key`` is what gives the resolution-addressed query a device to ask.
# =========================================================================


def _attach_sku_device(app: App, key: str, fbl: int, pm: int, sub: int) -> None:
    """Attach a device whose handshake selects a per-SKU artwork library."""
    from types import SimpleNamespace

    from trcc.core.protocol import get_profile

    app.devices[key] = SimpleNamespace(          # type: ignore[assignment]
        profile=get_profile(fbl, pm),
        handshake=SimpleNamespace(sub_byte=sub, pm_byte=pm),
        info=SimpleNamespace(key=key,
                             native_resolution=get_profile(fbl, pm).resolution),
        is_connected=True,
    )


def test_get_paths_without_a_key_stays_generic(fake_platform) -> None:
    """No key → exactly the answer this always gave.

    ``key`` is additive: the resolution-only contract is what every existing
    caller depends on, so it must not move.
    """
    from trcc.core.commands import GetPaths

    r = App(fake_platform).dispatch(GetPaths(resolution=(1600, 720)))

    assert r.ok is True
    assert r.theme_dir is not None
    assert r.theme_dir.endswith("theme1600720")
    assert r.cloud_theme_dir is not None
    assert r.cloud_theme_dir.endswith("/1600720")
    assert r.cloud_mask_dir is not None
    assert r.cloud_mask_dir.endswith("zt1600720")


def test_get_paths_with_a_key_names_that_devices_libraries(fake_platform) -> None:
    """A SUB-3 1600x720 cooler reads ``…l`` libraries — say so.

    1600x720 ships six theme libraries picked by SUB crossed with orientation
    (FormCZTV.cs:1290-1353).  The three LIBRARY dirs follow the device; the
    three USER dirs must NOT -- the user's own art has no per-SKU split.

    MUTATION CHECK -- put ``p.theme_dir`` back in place of ``libs.theme_dir``
    in ``GetPaths.execute`` and this fails.
    """
    from trcc.core.commands import GetPaths

    app = App(fake_platform)
    key = "0416:5408"
    _attach_sku_device(app, key, fbl=114, pm=64, sub=3)
    # The variant library must exist on disk or DeviceLibraries deliberately
    # falls back to the generic one (covered separately below).
    for variant in ("l",):
        fake_platform.paths().theme_dir(1600, 720, variant).mkdir(
            parents=True, exist_ok=True)
        fake_platform.paths().cloud_theme_dir(1600, 720, variant).mkdir(
            parents=True, exist_ok=True)
        fake_platform.paths().cloud_mask_dir(1600, 720, variant).mkdir(
            parents=True, exist_ok=True)

    r = app.dispatch(GetPaths(resolution=(1600, 720), key=key))

    assert r.ok is True
    assert r.theme_dir is not None and r.theme_dir.endswith("theme1600720l")
    assert (r.cloud_theme_dir is not None
            and r.cloud_theme_dir.endswith("/1600720l"))
    assert (r.cloud_mask_dir is not None
            and r.cloud_mask_dir.endswith("zt1600720l"))
    # User content is one directory per resolution, never per SKU.
    assert r.user_theme_dir is not None and r.user_theme_dir.endswith("1600720")
    assert not r.user_theme_dir.endswith("1600720l")


def test_get_paths_key_alone_supplies_the_resolution(fake_platform) -> None:
    """``key`` with no ``resolution`` answers for that device's canvas.

    The device knows its own size; making the caller retype it is how the two
    drift apart.  Nothing is scoped without either one.
    """
    from trcc.core.commands import GetPaths

    app = App(fake_platform)
    key = "0416:5408"
    _attach_sku_device(app, key, fbl=114, pm=64, sub=3)

    unscoped = app.dispatch(GetPaths())
    assert unscoped.theme_dir is None, "no device, no resolution → nothing scoped"

    r = app.dispatch(GetPaths(key=key))
    assert r.theme_dir is not None and "1600720" in r.theme_dir


def test_get_paths_falls_back_when_the_sku_library_is_absent(
    fake_platform,
) -> None:
    """Variant dir not on disk → the generic name, not a path nobody has.

    The suffixed libraries are a separate download.  A diagnostic that named
    ``theme1600720l`` on an install where it never landed would send a reporter
    looking for a directory that does not exist.
    """
    from trcc.core.commands import GetPaths

    app = App(fake_platform)
    key = "0416:5409"
    _attach_sku_device(app, key, fbl=114, pm=64, sub=3)

    r = app.dispatch(GetPaths(resolution=(1600, 720), key=key))

    assert r.theme_dir is not None and r.theme_dir.endswith("theme1600720")
