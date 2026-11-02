"""Takes one snapshot of the machine: totals plus the busiest apps."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field

import psutil

from .apps import app_name
from .models import AppUsage, Sample

IS_MAC = sys.platform == "darwin"
if IS_MAC:
    from .platforms import macos


@dataclass
class _ProcState:
    cpu_seconds: float
    io_read: int | None = None
    io_write: int | None = None


@dataclass
class _AppAcc:
    cpu: float = 0.0
    rss: int = 0
    nproc: int = 0
    read: float = 0.0
    write: float = 0.0
    has_io: bool = False
    top_name: str = ""
    top_cpu: float = -1.0
    names: set[str] = field(default_factory=set)


def _delta(new: float, old: float | None) -> float:
    if old is None or new < old:
        return 0.0
    return new - old


class Sampler:
    """Call `sample()` periodically; rates are computed against the previous call."""

    def __init__(self, top_n: int = 8):
        self.top_n = top_n
        self._mono: float | None = None
        self._swap: tuple[int, int] | None = None
        self._disk: tuple[int, int] | None = None
        self._net: tuple[int, int] | None = None
        self._procs: dict[int, _ProcState] = {}
        self._names: dict[int, str] = {}
        self._self = psutil.Process()
        self._self.cpu_percent(None)
        psutil.cpu_percent(None)

    def sample(self) -> tuple[Sample, list[AppUsage]]:
        mono = time.monotonic()
        wall = time.time()
        dt = (mono - self._mono) if self._mono is not None else 0.0
        self._mono = mono
        rate = (lambda d: d / dt) if dt > 0 else (lambda d: 0.0)

        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        swap_in = swap_out = 0.0
        if self._swap is not None:
            swap_in, swap_out = rate(_delta(sw.sin, self._swap[0])), rate(_delta(sw.sout, self._swap[1]))
        self._swap = (sw.sin, sw.sout)

        disk_read = disk_write = 0.0
        try:
            d = psutil.disk_io_counters()
        except Exception:
            d = None
        if d is not None:
            if self._disk is not None:
                disk_read, disk_write = rate(_delta(d.read_bytes, self._disk[0])), rate(_delta(d.write_bytes, self._disk[1]))
            self._disk = (d.read_bytes, d.write_bytes)

        net_rx = net_tx = 0.0
        try:
            n = psutil.net_io_counters()
        except Exception:
            n = None
        if n is not None:
            if self._net is not None:
                net_rx, net_tx = rate(_delta(n.bytes_recv, self._net[0])), rate(_delta(n.bytes_sent, self._net[1]))
            self._net = (n.bytes_recv, n.bytes_sent)

        try:
            load1 = psutil.getloadavg()[0]
        except (OSError, AttributeError):
            load1 = None
        battery = plugged = None
        try:
            b = psutil.sensors_battery()
            if b is not None:
                battery, plugged = float(b.percent), bool(b.power_plugged)
        except Exception:
            pass

        apps = self._apps(dt)
        sample = Sample(
            ts=wall,
            interval=dt,
            cpu=psutil.cpu_percent(None),
            mem_total=vm.total,
            mem_available=vm.available,
            swap_used=sw.used,
            swap_in=swap_in,
            swap_out=swap_out,
            disk_read=disk_read,
            disk_write=disk_write,
            net_rx=net_rx,
            net_tx=net_tx,
            pressure=macos.memory_pressure() if IS_MAC else None,
            thermal=macos.thermal_pressure() if IS_MAC else None,
            load1=load1,
            battery=battery,
            plugged=plugged,
            self_cpu=self._self.cpu_percent(None),
        )
        return sample, apps

    # ------------------------------------------------------------- processes

    def _apps(self, dt: float) -> list[AppUsage]:
        accs: dict[str, _AppAcc] = {}
        seen: dict[int, _ProcState] = {}

        def add(pid: int, name: str, cpu_seconds: float | None, rss: int, io: tuple[int, int] | None) -> None:
            prev = self._procs.get(pid)
            cpu = 0.0
            if cpu_seconds is not None:
                if prev is not None and dt > 0:
                    cpu = 100.0 * _delta(cpu_seconds, prev.cpu_seconds) / dt
                seen[pid] = _ProcState(cpu_seconds, io[0] if io else None, io[1] if io else None)
            app = app_name(name)
            acc = accs.setdefault(app, _AppAcc())
            acc.cpu += cpu
            acc.rss += rss
            acc.nproc += 1
            if io is not None and prev is not None and prev.io_read is not None and dt > 0:
                acc.read += _delta(io[0], prev.io_read) / dt
                acc.write += _delta(io[1], prev.io_write) / dt
                acc.has_io = True
            short = os.path.basename(name.replace("\\", "/")) or name
            if cpu > acc.top_cpu:
                acc.top_cpu, acc.top_name = cpu, short

        if IS_MAC:
            for row in macos.ps_processes():
                add(row.pid, row.path, row.cpu_seconds, row.rss, None)
        else:
            for p in psutil.process_iter(["pid", "name", "exe", "cpu_times", "memory_info", "io_counters"], ad_value=None):
                info = p.info
                times = info.get("cpu_times")
                mem = info.get("memory_info")
                ioc = info.get("io_counters")
                name = info.get("exe") or info.get("name") or ""
                if p.pid == 0 and sys.platform == "win32":
                    continue  # "System Idle Process" is idle time, not work
                add(
                    p.pid,
                    name,
                    (times.user + times.system) if times else None,
                    mem.rss if mem else 0,
                    (ioc.read_bytes, ioc.write_bytes) if ioc else None,
                )

        self._procs = seen
        apps = [
            AppUsage(
                app=name, cpu=round(a.cpu, 2), rss=a.rss, nproc=a.nproc, top_name=a.top_name,
                read_bps=a.read if a.has_io else None, write_bps=a.write if a.has_io else None,
            )
            for name, a in accs.items()
        ]
        return select_notable(apps, self.top_n)


def select_notable(apps: list[AppUsage], top_n: int) -> list[AppUsage]:
    """Keeps the apps worth recording: top by CPU, top by memory, and anything busy."""
    keep: dict[str, AppUsage] = {}
    for a in sorted(apps, key=lambda a: a.cpu, reverse=True)[:top_n]:
        keep[a.app] = a
    for a in sorted(apps, key=lambda a: a.rss, reverse=True)[:top_n]:
        keep[a.app] = a
    for a in apps:
        if a.cpu >= 25 or (a.write_bps or 0) + (a.read_bps or 0) >= 5e6:
            keep[a.app] = a
    return list(keep.values())
