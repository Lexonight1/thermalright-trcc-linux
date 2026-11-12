"""Shared CLI entry point — runs ``trcc`` (the new top-level tree).

Used by both invocations:

    python -m trcc        →  __main__.py  →  this _entry.main()
    trcc (console script) →  pyproject `trcc = "trcc._entry:main"`  →  this

The legacy tree (and its ``TRCC_LEGACY=1`` escape hatch) was moved to the
``legacy`` branch; this entry point dispatches straight to the new tree.
"""
from __future__ import annotations


def put_bundled_tools_on_path() -> str | None:
    """A frozen app runs the tools it ships: its own folder goes first on PATH.

    The macOS DMG and the Windows installer both put 7-Zip and ffmpeg beside
    the executable, and the toolchain finds tools through PATH -- which never
    included that folder.  An app launched from Finder gets a PATH of
    /usr/bin:/bin:/usr/sbin:/sbin, so the macOS DMG's bundled tools were never
    run at all (#219).  Returns the folder added, or None when not frozen.
    """
    import logging
    import os
    import sys
    from pathlib import Path

    if not getattr(sys, "frozen", False):
        return None
    here = str(Path(sys.executable).parent)
    os.environ["PATH"] = here + os.pathsep + os.environ.get("PATH", "")
    logging.getLogger(__name__).info("put_bundled_tools_on_path: %s", here)
    return here


def without_launch_services_psn(args: list[str]) -> list[str]:
    """Drop ``-psn_*`` tokens older Launch Services passed to a double-clicked app.

    They are not options of ours.  Left in place, ``with_default_gui`` would
    treat one as a ``gui`` argument and the windowed app would exit on it.
    """
    import logging

    kept = [arg for arg in args if not arg.startswith("-psn_")]
    if len(kept) != len(args):
        logging.getLogger(__name__).info(
            "without_launch_services_psn: dropped %s",
            [arg for arg in args if arg.startswith("-psn_")])
    return kept


def has_a_console(stdout: object | None = None) -> bool:
    """Whether this process can show the CLI help on a terminal.

    A Finder launch of a windowed app has no console: ``stdout`` is missing
    or is not a tty, so the help text is invisible and the process just
    exits.  A terminal run of the same binary is a tty.  Pass *stdout* to
    test a stream; the default reads ``sys.stdout``, which itself may be
    ``None``.
    """
    import logging
    import sys

    log = logging.getLogger(__name__)
    stream = sys.stdout if stdout is None else stdout
    if stream is None:
        log.info("has_a_console: no stdout")
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        log.info("has_a_console: stdout cannot say whether it is a terminal")
        return False
    try:
        tty = bool(isatty())
    except OSError as exc:
        log.info("has_a_console: isatty failed: %s", exc)
        return False
    log.info("has_a_console: %s", tty)
    return tty


def argv_for_this_executable(
    args: list[str], *, executable: str, frozen: bool, platform: str,
    console: bool,
) -> list[str]:
    """The arguments this process should run.

    ``trcc-gui*`` is the Windows windowed twin of ``trcc.exe``: ``gui`` unless
    a subcommand is already named.  The Mac app has one binary, named
    ``TRCC`` — a case-insensitive volume cannot also hold ``trcc`` — so a
    Finder launch (frozen, no console, no subcommand) takes the same default.
    A terminal run of that binary keeps the CLI, and ``python -m trcc`` is
    not frozen, so it is left alone.  ``kill``, ``daemon``, ``version`` and
    ``--version`` stay themselves.
    """
    import logging
    from pathlib import Path

    log = logging.getLogger(__name__)
    cleaned = without_launch_services_psn(args) if platform == "darwin" else list(args)
    name = Path(executable).name.lower()
    windowed_twin = name.startswith("trcc-gui")
    mac_app = frozen and platform == "darwin" and not console
    chosen = with_default_gui(cleaned) if windowed_twin or mac_app else cleaned
    log.info("argv_for_this_executable: frozen=%s platform=%s console=%s "
             "name=%s %s -> %s", frozen, platform, console, name, args, chosen)
    return chosen


def with_default_gui(args: list[str]) -> list[str]:
    """*args* for a windowed launch: ``gui`` when they name no subcommand.

    The GUI exe is the CLI with ``gui`` as its default.  It used to insert
    ``gui`` FIRST whenever the first argument was an option, so
    ``trcc-gui.exe -vvv daemon`` ran ``gui -vvv daemon`` and
    ``trcc-gui.exe -v`` ran ``gui -v`` -- both refused by the CLI, silently,
    since the windowed exe has no console (win11 VM, 2026-10-20).

    The ROOT options (``-v``..., ``--verbose``, ``-V``, ``--version``) stay in
    front; ``gui`` goes where the next argument is not a subcommand, so the
    options after it are ``gui``'s own -- ``trcc-gui.exe --resume`` is
    ``gui --resume``, as older sign-in entries still run it.
    """
    import logging
    import re

    lead = 0
    while lead < len(args) and re.fullmatch(r"-v+|--verbose|-V|--version", args[lead]):
        lead += 1
    if lead < len(args) and not args[lead].startswith("-"):
        return args
    logging.getLogger(__name__).info("with_default_gui: no subcommand in %s -- gui", args)
    return [*args[:lead], "gui", *args[lead:]]


def main() -> int | None:
    """Dispatch to the new top-level CLI, with startup crashes recorded.

    ``python -m trcc`` gets crash logging from ``__main__.py``.  The console
    script does NOT go through that file — ``[project.scripts]`` binds ``trcc``
    straight here — so until now every packaged install (rpm, deb, pacman,
    pipx, PyPI) had none.  Measured with a simulated import failure:
    ``python -m trcc`` wrote a log with the CRITICAL in it; ``trcc`` wrote
    nothing at all.

    Buffering here closes that, and costs one light import: the logging adapter
    pulls 7 trcc modules and no third-party package, ~6.6 ms.  Everything
    heavy — and every import that can realistically fail — happens on the line
    below, inside the guard.
    """
    import logging

    from trcc.adapters.infra.logging import ensure_configured, start_early_logging

    start_early_logging()
    put_bundled_tools_on_path()
    log = logging.getLogger(__name__)
    log.info("main: dispatching to the CLI")
    try:
        from trcc.ui.cli.main import main as _next_main
        return _next_main()
    except Exception:
        log.critical("Fatal startup error", exc_info=True)
        try:
            ensure_configured()
        except Exception:
            log.exception("Fatal startup error: could not write the log either")
        raise
