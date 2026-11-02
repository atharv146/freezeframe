"""macOS readers that work without administrator rights.

- Process CPU and memory come from `ps`, which (unlike psutil without root)
  can see system processes such as WindowServer and Spotlight's mds_stores.
- Memory pressure is the kernel's own verdict (normal / warning / critical),
  the same signal behind Activity Monitor's memory pressure graph.
- Thermal pressure comes from the system notification that macOS publishes
  when it starts slowing the machine down to cool it.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
from dataclasses import dataclass

_libc = None
_thermal_token: int | None = None


def _lib():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libSystem.dylib")
    return _libc


def sysctl_int(name: str) -> int | None:
    try:
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if _lib().sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        return value.value
    except (OSError, AttributeError):
        return None


def memory_pressure() -> int | None:
    """1 = normal, 2 = warning, 4 = critical."""
    return sysctl_int("kern.memorystatus_vm_pressure_level")


def thermal_pressure() -> int | None:
    """0 nominal, 1 moderate, 2 heavy, 3 trapping, 4 sleeping."""
    global _thermal_token
    try:
        lib = _lib()
        if _thermal_token is None:
            token = ctypes.c_int(0)
            if lib.notify_register_check(b"com.apple.system.thermalpressurelevel", ctypes.byref(token)) != 0:
                return None
            _thermal_token = token.value
        state = ctypes.c_uint64(0)
        if lib.notify_get_state(ctypes.c_int(_thermal_token), ctypes.byref(state)) != 0:
            return None
        return int(state.value)
    except (OSError, AttributeError):
        return None


@dataclass
class PsRow:
    pid: int
    ppid: int
    cpu_seconds: float
    rss: int
    path: str


def parse_cputime(text: str) -> float:
    """Parses ps TIME values: '12:34.56', '1:02:03', '3-01:02:03'."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = text.split(":")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return days * 86400 + seconds


def parse_ps(output: str) -> list[PsRow]:
    rows = []
    for line in output.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 5:
            continue
        try:
            rows.append(PsRow(int(fields[0]), int(fields[1]), parse_cputime(fields[2]), int(fields[3]) * 1024, fields[4].strip()))
        except ValueError:
            continue
    return rows


def ps_processes() -> list[PsRow]:
    out = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,time=,rss=,comm="], capture_output=True, text=True, timeout=10
    ).stdout
    return parse_ps(out)
