"""Startup prompt — offer to install the NVIDIA sensor reader when a card
is detected without it.

Detection (``GetGpuReaderStatus``) and the install (``InstallGpuReader``,
which runs the package manager via ``pkexec``) both go through the Command
bus; this module owns only the Qt consent dialog and the "remember the
choice" wiring.  Naturally Linux-GUI only — on other OSes the GPU probe
returns no offer, so the dialog never shows; headless / CLI rely on the
doctor's WARN (detect-and-guide).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def maybe_offer_gpu_reader_install(app: Any, parent: Any) -> None:
    """Offer a one-click install of the NVIDIA reader if one is needed.

    No-op unless an NVIDIA GPU is present, the reader is missing, and the
    user hasn't already declined.  Consent is explicit (a dialog); only
    after the user clicks *Install* does :class:`InstallGpuReader` run the
    privileged package install.
    """
    from ...core.commands import GetGpuReaderStatus, InstallGpuReader

    if app.settings.app.gpu_reader_offer_suppressed:
        log.debug("maybe_offer_gpu_reader_install: user opted out — skipping")
        return
    status = app.dispatch(GetGpuReaderStatus())
    if not status.offer_install:
        log.debug(
            "maybe_offer_gpu_reader_install: no offer (present=%s reader=%s)",
            status.nvidia_present, status.reader_installed,
        )
        return

    from PySide6.QtWidgets import (  # type: ignore[import-not-found]
        QCheckBox,
        QMessageBox,
    )

    log.info("maybe_offer_gpu_reader_install: offering reader install to the user")
    ask = QMessageBox(parent)
    ask.setWindowTitle("NVIDIA GPU detected")
    ask.setIcon(QMessageBox.Icon.Question)
    ask.setText("An NVIDIA GPU was detected but its sensor reader isn't "
                "installed, so GPU metrics will be empty.")
    ask.setInformativeText("Install GPU sensor support now? You'll be asked "
                           "for your password.")
    dont_ask = QCheckBox("Don't ask again")
    ask.setCheckBox(dont_ask)
    install_btn = ask.addButton("Install", QMessageBox.ButtonRole.AcceptRole)
    ask.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
    ask.exec()

    if ask.clickedButton() is not install_btn:
        # A plain "Not now"/dismissal re-offers next launch; only an explicit
        # "Don't ask again" suppresses future offers.  Guards against a stray
        # Escape permanently trapping a user who wants GPU metrics (#161).
        if dont_ask.isChecked():
            log.info("maybe_offer_gpu_reader_install: opted out — suppressing offers")
            app.settings.set_gpu_reader_offer_suppressed(True)
        else:
            log.info("maybe_offer_gpu_reader_install: not now — will re-offer next launch")
        return

    log.info("maybe_offer_gpu_reader_install: accepted — dispatching install")
    result = app.dispatch(InstallGpuReader())
    done = QMessageBox(parent)
    done.setWindowTitle("GPU sensor support")
    done.setIcon(QMessageBox.Icon.Information if result.ok else QMessageBox.Icon.Warning)
    done.setText(result.message)
    done.exec()
