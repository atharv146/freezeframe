"""Spotting freezes and unusual activity as samples arrive.

Freeze detection is a watchdog: the recorder asks to be woken every couple of
seconds. When the whole machine locks up it cannot run either, so it wakes up
late, and the delay is the length of the freeze. Sleep also makes it wake up
late, but two clocks tell them apart: the wall clock keeps counting while the
computer sleeps, the monotonic clock does not, and during a freeze both do.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .models import AppUsage, Event, Sample
from .reports import fmt_duration

GB = 1024**3
MB = 1024**2

STALL_THRESHOLD = 2.5  # seconds late before a wake-up counts as a freeze


def classify_gap(prev_wall: float, prev_mono: float, wall: float, mono: float, interval: float,
                 threshold: float = STALL_THRESHOLD) -> tuple[str, float]:
    """Returns ('ok' | 'stall' | 'sleep', seconds)."""
    d_mono = mono - prev_mono
    d_wall = wall - prev_wall
    if d_wall - d_mono > 5.0:
        return "sleep", d_wall  # the monotonic clock paused: the machine was asleep
    if d_mono > 600:
        return "sleep", d_mono  # platforms whose monotonic clock runs during sleep
    late = d_mono - interval
    if late >= threshold:
        return "stall", late
    return "ok", 0.0


def human_bytes(n: float) -> str:
    if n >= GB:
        return f"{n / GB:.1f} GB"
    if n >= MB:
        return f"{n / MB:.0f} MB"
    return f"{n / 1024:.0f} KB"


def human_rate(n: float) -> str:
    return human_bytes(n) + "/s"


def cores(cpu: float) -> str:
    c = cpu / 100
    return f"{c:.1f} cores" if c >= 1.15 else f"{cpu:.0f}% of a core"


@dataclass
class _Run:
    start: float
    peak: float
    last: float


@dataclass
class Detectors:
    """Feed every sample to `observe`; it returns events worth recording."""

    interval: float = 2.0
    cooldown: float = 600.0
    _last_fired: dict[tuple[str, str], float] = field(default_factory=dict)
    _cpu_runs: dict[str, _Run] = field(default_factory=dict)
    _sys_cpu_run: _Run | None = None
    _disk_run: _Run | None = None
    _swap_run: _Run | None = None
    _pressure_warn_since: float | None = None
    _thermal_since: float | None = None
    _rss: dict[str, deque] = field(default_factory=dict)
    _hourly: dict[str, deque] = field(default_factory=dict)
    _last_leak_check: float = 0.0

    def _fire(self, key: tuple[str, str], ts: float, cooldown: float | None = None) -> bool:
        last = self._last_fired.get(key)
        if last is not None and ts - last < (cooldown if cooldown is not None else self.cooldown):
            return False
        self._last_fired[key] = ts
        return True

    def observe(self, s: Sample, apps: list[AppUsage]) -> list[Event]:
        out: list[Event] = []
        ts = s.ts
        by_cpu = sorted(apps, key=lambda a: a.cpu, reverse=True)
        top = by_cpu[0] if by_cpu else None

        # Memory pressure, as judged by the OS (macOS) or by free memory + swapping.
        critical = s.pressure == 4 or (s.mem_available_pct < 4 and s.swap_out > 20 * MB)
        if critical and self._fire(("pressure", "critical"), ts):
            biggest = max(apps, key=lambda a: a.rss, default=None)
            extra = f" {biggest.app} was using {human_bytes(biggest.rss)}." if biggest else ""
            out.append(Event(ts, "pressure", "Memory ran out: the system reported critical memory pressure." + extra, 2,
                             app=biggest.app if biggest else None))
        if s.pressure == 2:
            self._pressure_warn_since = self._pressure_warn_since or ts
            if ts - self._pressure_warn_since >= 60 and self._fire(("pressure", "warn"), ts):
                out.append(Event(self._pressure_warn_since, "pressure", "Memory was running low for over a minute", 1, end_ts=ts))
        elif s.pressure is not None:
            self._pressure_warn_since = None

        # Heavy swapping: the system is shuffling memory to and from the disk.
        swap = s.swap_in + s.swap_out
        self._swap_run = self._track(self._swap_run, swap >= 40 * MB, ts, swap)
        if self._swap_run and ts - self._swap_run.start >= 3 * self.interval and self._fire(("swap", ""), ts, 1800):
            out.append(Event(self._swap_run.start, "swap", f"Heavy swapping: up to {human_rate(self._swap_run.peak)} of memory moved to and from disk", 1))

        # Overheating.
        if s.thermal is not None and s.thermal >= 1:
            self._thermal_since = self._thermal_since or ts
            heavy = s.thermal >= 2
            if (heavy or ts - self._thermal_since >= 120) and self._fire(("thermal", str(heavy)), ts):
                out.append(Event(self._thermal_since, "thermal",
                                 "The computer is slowing itself down to cool off" + (" (heavy thermal pressure)" if heavy else ""),
                                 2 if heavy else 1))
        else:
            self._thermal_since = None

        # Whole-machine CPU maxed out.
        self._sys_cpu_run = self._track(self._sys_cpu_run, s.cpu >= 95, ts, s.cpu)
        if self._sys_cpu_run and ts - self._sys_cpu_run.start >= 30 and self._fire(("cpu", "*"), ts):
            who = f", mostly {top.app}" if top and top.cpu >= 100 else ""
            out.append(Event(self._sys_cpu_run.start, "cpu", f"The CPU was maxed out for {fmt_duration(ts - self._sys_cpu_run.start)}{who}", 1,
                             app=top.app if top and top.cpu >= 100 else None))

        # One app hogging the CPU: reported once its burst ends (or every 10 minutes).
        busy = {a.app: a for a in apps if a.cpu >= 200}
        for app, run in list(self._cpu_runs.items()):
            if app not in busy or ts - run.start >= 600:
                if run.last - run.start >= 20 and self._fire(("cpu", app), ts, 300):
                    out.append(Event(run.start, "cpu", f"{app} used {cores(run.peak)} for {fmt_duration(run.last - run.start)}", 1, end_ts=run.last, app=app))
                del self._cpu_runs[app]
        for app, a in busy.items():
            run = self._cpu_runs.get(app)
            if run is None:
                self._cpu_runs[app] = _Run(ts, a.cpu, ts)
            else:
                run.peak, run.last = max(run.peak, a.cpu), ts

        # Sudden memory growth.
        for a in apps:
            hist = self._rss.setdefault(a.app, deque())
            hist.append((ts, a.rss))
            while hist and ts - hist[0][0] > 120:
                hist.popleft()
            low_ts, low = min(hist, key=lambda x: x[1])
            if a.rss - low >= 1.5 * GB and low_ts < ts and self._fire(("memory", a.app), ts):
                out.append(Event(low_ts, "memory", f"{a.app} grew by {human_bytes(a.rss - low)} in {fmt_duration(ts - low_ts)}", 1, end_ts=ts, app=a.app,
                                 detail={"from": low, "to": a.rss}))
            hourly = self._hourly.setdefault(a.app, deque())
            if not hourly or ts - hourly[-1][0] >= 30:
                hourly.append((ts, a.rss))
            while hourly and ts - hourly[0][0] > 3 * 3600:
                hourly.popleft()

        # Slow leaks: steady growth over the last hours.
        if ts - self._last_leak_check >= 300:
            self._last_leak_check = ts
            for app, hist in self._hourly.items():
                window = [p for p in hist if ts - p[0] <= 3600]
                leak = leak_rate(window)
                if leak and self._fire(("leak", app), ts, 3 * 3600):
                    rate, total = leak
                    out.append(Event(window[0][0], "leak", f"{app} has been growing steadily (+{human_bytes(rate)} per hour); it may be leaking memory", 1,
                                     end_ts=ts, app=app, detail={"rate_per_hour": rate, "growth": total}))

        # Disk flat out.
        disk = max(s.disk_read, s.disk_write)
        self._disk_run = self._track(self._disk_run, disk >= 250 * MB, ts, disk)
        if self._disk_run and ts - self._disk_run.start >= 15 and self._fire(("disk", ""), ts):
            io_top = max((a for a in apps if a.write_bps is not None), key=lambda a: (a.write_bps or 0) + (a.read_bps or 0), default=None)
            who = f" (mostly {io_top.app})" if io_top else ""
            out.append(Event(self._disk_run.start, "disk", f"The disk was busy at up to {human_rate(self._disk_run.peak)}{who}", 1,
                             app=io_top.app if io_top else None))
        return out

    @staticmethod
    def _track(run: _Run | None, active: bool, ts: float, value: float) -> _Run | None:
        if not active:
            return None
        if run is None:
            return _Run(ts, value, ts)
        run.peak, run.last = max(run.peak, value), ts
        return run


def leak_rate(points: list[tuple[float, int]]) -> tuple[float, float] | None:
    """(bytes per hour, total growth) if memory rose steadily for 30+ minutes, else None."""
    if len(points) < 40 or points[-1][0] - points[0][0] < 1800:
        return None
    n = len(points)
    xs = [(t - points[0][0]) / 3600 for t, _ in points]
    ys = [float(r) for _, r in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    slope = sxy / sxx
    r2 = sxy * sxy / (sxx * syy)
    growth = ys[-1] - ys[0]
    if slope >= 300 * MB and r2 >= 0.85 and growth >= 500 * MB and ys[-1] >= GB:
        return slope, growth
    return None
