"""TRCC Linux — Thermalright LCD Control Center.

A Linux implementation of the Thermalright LCD Control Center,
matching the Windows TRCC 2.0.3 protocol.

This package is the clean-slate rebuild (formerly ``trcc.next``)
promoted to the project root.  The original implementation was moved
to the ``legacy`` branch.

Features:
- LCD display control via SCSI / HID / Bulk
- System monitoring (CPU, GPU, RAM temperatures)
- Theme + mask support with DC binary format
- Video and image background playback
- Real-time sensor overlays
"""
from trcc.__version__ import __version__

__author__ = "TRCC Linux Contributors"

__all__ = ["__version__"]
