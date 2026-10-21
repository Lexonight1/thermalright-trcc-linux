"""TRCC's Windows service: SYSTEM, serving SCSI and PawnIO to the App.

``trcc-gui.exe service`` is the service's command line (the installer
registers it; no extra exe).  Windows starts it as SYSTEM, it serves
``_service_broker.SERVICE_PIPE`` until stopped, and each client gets only
what ``_service_broker`` allows.  ``trcc service --foreground`` runs the
same server in an elevated terminal, for testing without installing it.

The PyInstaller + pywin32 service mechanics were proved first on the win11
VM (2026-10-16): a one-folder exe, ``StartServiceCtrlDispatcher`` when run
by the service manager, ``win32timezone`` imported, as SYSTEM.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, cast

from ...__version__ import __version__
from ..infra.named_pipe import NamedPipeChannel
from ._service_broker import (
    SERVICE_NAME,
    SERVICE_PIPE,
    SERVICE_SDDL,
    PrivilegedBroker,
    serve_connection,
)
from ._windows_drives import find_physical_drive

try:
    import servicemanager  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32service  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32serviceutil  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32timezone  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    _ServiceBase: Any = win32serviceutil.ServiceFramework
    #: pywin32's service host needs it at run time and imports it by name, so
    #: PyInstaller must see it imported (step-0 spike, 2026-10-16).
    _NEEDED_AT_RUN_TIME = (win32timezone,)
except ImportError:
    # Off Windows the module still imports (the ports page is generated from
    # every module); running the service there is refused in run_service.
    servicemanager = win32service = win32serviceutil = cast(Any, None)
    _ServiceBase = object

log = logging.getLogger(__name__)

#: StartServiceCtrlDispatcher's answer when the service manager did not start us.
_NOT_STARTED_BY_SCM = 1063
#: ``run_service``'s exit code for that case, so the CLI can say what to do.
NOT_STARTED_BY_WINDOWS = 3


def _open_panel(path: str) -> Any:
    """The panel's drive, opened -- as SYSTEM, which may."""
    from .windows import WindowsScsiTransport
    log.info("_open_panel: %s", path)
    transport = WindowsScsiTransport(path)
    if not transport.open():
        raise OSError(f"could not open {path}")
    return transport


def _open_module(name: str) -> Any:
    """A bundled PawnIO module, from this install's own ``pawnio`` folder."""
    from ..sensors._pawnio_cpu import _open_bundled
    log.debug("_open_module: %s", name)
    return _open_bundled(f"{name}.bin")


def _open_superio() -> Any:
    """The Super I/O reader on the bundled ``LpcIO`` module -- one per slot it
    reads -- under the ISA-bus mutex, as SYSTEM, which PawnIO admits."""
    from functools import partial

    from ..sensors._pawnio import IsaBusLock
    from ..sensors._superio import SuperIoReader
    log.info("_open_superio")
    return SuperIoReader(partial(_open_module, "LpcIO"), IsaBusLock().held)


def make_broker() -> PrivilegedBroker:
    """The broker wired to this machine's drives, PawnIO and Super I/O."""
    log.debug("make_broker")
    return PrivilegedBroker(version=__version__, find_drive=find_physical_drive,
                            open_scsi=_open_panel, open_module=_open_module,
                            open_superio=_open_superio)


class ServiceServer:
    """Accepts clients on the service pipe until :meth:`stop`."""

    def __init__(self, broker: PrivilegedBroker,
                 channel: NamedPipeChannel | None = None) -> None:
        self._broker = broker
        self._channel = channel or NamedPipeChannel.for_service(
            SERVICE_PIPE, SERVICE_SDDL)
        self._stop = threading.Event()
        self._listener: Any = None
        log.info("ServiceServer: %s", self._channel.address)

    def serve(self) -> None:
        """Serve until stopped; one thread per client."""
        log.info("ServiceServer.serve: listening on %s", self._channel.address)
        self._listener = self._channel.listen()
        while not self._stop.is_set():
            try:
                stream, _ = self._listener.accept()
            except OSError as e:
                if self._stop.is_set():
                    break
                log.warning("ServiceServer: accept failed (%s) -- still "
                            "serving", e)
                self._stop.wait(0.05)
                continue
            threading.Thread(target=serve_connection,
                             args=(self._broker, stream, self._stop),
                             name="trcc-service-client", daemon=True).start()
        self._listener.close()
        log.info("ServiceServer.serve: stopped")

    def stop(self) -> None:
        log.info("ServiceServer.stop")
        self._stop.set()
        if self._listener is not None:
            self._listener.wake()


class TrccService(_ServiceBase):
    """The Windows service the installer registers as ``TRCCService``."""

    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "TRCC hardware access"
    _svc_description_ = ("Lets TRCC drive SCSI LCD panels and read CPU "
                         "temperature through PawnIO without running as "
                         "administrator.")

    def __init__(self, args: Any) -> None:
        log.info("TrccService.__init__")
        super().__init__(args)
        self._server = ServiceServer(make_broker())

    def SvcStop(self) -> None:
        log.info("TrccService.SvcStop")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._server.stop()

    def SvcDoRun(self) -> None:
        log.info("TrccService.SvcDoRun: version %s", __version__)
        self._server.serve()


def run_service(foreground: bool = False) -> int:
    """``trcc service``: hand this process to the service manager -- or,
    with *foreground*, serve in this terminal until Ctrl+C."""
    log.info("run_service: foreground=%s", foreground)
    if servicemanager is None:
        log.error("run_service: the TRCC service exists on Windows only")
        return 2
    if foreground:
        server = ServiceServer(make_broker())
        try:
            server.serve()
        except KeyboardInterrupt:
            server.stop()
        return 0
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(TrccService)
    try:
        servicemanager.StartServiceCtrlDispatcher()
    except Exception as e:      # pywintypes.error, not importable here
        if getattr(e, "winerror", None) == _NOT_STARTED_BY_SCM:
            log.error("run_service: started from a terminal, not by Windows "
                      "-- use --foreground to test it")
            return NOT_STARTED_BY_WINDOWS
        raise
    return 0
