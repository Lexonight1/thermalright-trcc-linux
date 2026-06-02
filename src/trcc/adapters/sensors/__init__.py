"""Sensor source adapters.

Each module wraps one backend (psutil, pynvml, hwmon, LHM, SMC, ...)
and returns concrete implementations of the hardware ABCs from
`core.ports`:

    CpuSource      cpu.py       (psutil + hwmon)
    MemorySource   memory.py    (psutil)
    GpuSource      nvml.py, hwmon.py (NVIDIA / AMD / Intel impls)
    FanSource      hwmon.py

The aggregator (`LinuxSensors` / platform-specific enumerators) composes
these into the `SensorEnumerator` the rest of the app talks to.
"""
