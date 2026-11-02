"""A realistic four-hour demo recording, generated through the real pipeline.

Scenarios (minutes from the start):
   30-55  the laptop sleeps
   70-82  Spotlight re-indexes after an update; you report "everything feels slow"
   95-110 a Zoom call heats the Mac until it throttles, and it freezes for 4 s
   125    Microsoft Excel hangs on its own (an OS hang report, system calm)
  140-200 a browser tab leaks memory until the Mac swaps hard and freezes for 9 s
   215    a 5 s freeze with nothing busy at all
"""

from __future__ import annotations

import math
import random
import tempfile
from pathlib import Path

from .detect import Detectors
from .diagnose import diagnose, incident_title
from .models import AppUsage, Event, Sample
from .store import Store

GB = 1024**3
MB = 1024**2
INTERVAL = 2.0
TOTAL = 16 * GB
CPUS = 8


def _apps(m: float, rng: random.Random) -> list[AppUsage]:
    """App usage at minute m."""
    def noise(scale: float) -> float:
        return rng.uniform(-scale, scale)

    brave_rss = 2.4 * GB + 0.15 * GB * math.sin(m / 9)
    if 140 <= m < 199:
        brave_rss += (m - 140) / 59 * 7.2 * GB * (1 + noise(0.01))
    brave_cpu = 22 + 12 * abs(math.sin(m / 3)) + noise(6)
    if 140 <= m < 199:
        brave_cpu += 35
    apps = [
        AppUsage("Brave Browser", max(0, brave_cpu), int(brave_rss), 24, "Brave Browser Helper (Renderer)"),
        AppUsage("Slack", max(0, 3 + noise(2)), int(780 * MB + noise(20 * MB)), 6, "Slack Helper"),
        AppUsage("Spotify", max(0, 4 + noise(2)), int(420 * MB), 5, "Spotify Helper"),
        AppUsage("Visual Studio Code", max(0, 8 + 6 * abs(math.sin(m / 5)) + noise(3)), int(1.1 * GB + noise(30 * MB)), 9, "Code Helper (Plugin)"),
        AppUsage("WindowServer", max(0, 11 + noise(3)), int(140 * MB), 1, "WindowServer"),
        AppUsage("Microsoft Excel", max(0, 1 + noise(0.5)), int(610 * MB), 1, "Microsoft Excel"),
        AppUsage("Notion", max(0, 2 + noise(1)), int(690 * MB), 7, "Notion Helper"),
    ]
    if 70 <= m < 82:
        apps.append(AppUsage("mds_stores", 190 + noise(50), int(320 * MB), 1, "mds_stores"))
        apps.append(AppUsage("mdworker_shared", 60 + noise(20), int(90 * MB), 6, "mdworker_shared"))
    if 95 <= m < 110:
        apps.append(AppUsage("zoom.us", 160 + noise(20), int(1.3 * GB), 3, "zoom.us"))
    if 101 <= m < 110:
        apps.append(AppUsage("kernel_task", 150 + (m - 101) * 9 + noise(15), int(40 * MB), 1, "kernel_task"))
    return apps


def _sample(ts: float, m: float, apps: list[AppUsage], rng: random.Random) -> Sample:
    used = sum(a.rss for a in apps) + 5.2 * GB
    available = max(0.25 * GB, TOTAL - used)
    avail_pct = available / TOTAL * 100
    pressure = 4 if avail_pct < 6 else 2 if avail_pct < 13 else 1
    swap_rate = 0.0
    if avail_pct < 13:
        swap_rate = (13 - avail_pct) * 22 * MB * rng.uniform(0.6, 1.3)
    cpu = min(100.0, sum(a.cpu for a in apps) / CPUS + 3 + rng.uniform(0, 2))
    disk_read = rng.uniform(0.2, 3) * MB + swap_rate * 0.6
    disk_write = rng.uniform(0.3, 4) * MB + swap_rate * 0.5
    if 70 <= m < 82:
        disk_read += rng.uniform(220, 340) * MB
    thermal = 0
    if 99 <= m < 103:
        thermal = 1
    elif 103 <= m < 109:
        thermal = 2
    return Sample(
        ts=ts, interval=INTERVAL, cpu=round(cpu, 1), mem_total=TOTAL, mem_available=int(available),
        swap_used=int(2.1 * GB + max(0, (m - 180)) * 60 * MB if 180 <= m < 200 else 2.1 * GB),
        swap_in=swap_rate * 0.45, swap_out=swap_rate * 0.55, disk_read=disk_read, disk_write=disk_write,
        net_rx=rng.uniform(5, 60) * 1024 * (40 if 95 <= m < 110 else 1), net_tx=rng.uniform(2, 20) * 1024 * (30 if 95 <= m < 110 else 1),
        pressure=pressure, thermal=thermal, load1=cpu / 100 * CPUS, battery=None, plugged=True, self_cpu=round(rng.uniform(0.6, 1.1), 2),
    )


def build_demo(path: str | Path | None = None, end: float | None = None, seed: int = 7) -> Path:
    import time

    path = Path(path) if path else Path(tempfile.gettempdir()) / "freezeframe-demo.db"
    for p in path.parent.glob(path.name + "*"):
        p.unlink()
    store = Store(path)
    rng = random.Random(seed)
    end = end if end is not None else time.time() - 60
    start = end - 240 * 60
    stalls = {106.0: 4.0, 197.0: 9.0, 215.0: 5.0}
    detectors = Detectors(interval=INTERVAL)
    events: list[Event] = []
    rows = []

    m = 0.0
    while m <= 240:
        if 30 <= m < 55:
            events.append(Event(start + 30 * 60, "sleep", "Asleep for 25 min", 0, end_ts=start + 55 * 60))
            m = 55.0
            continue
        ts = start + m * 60
        apps = _apps(m, rng)
        s = _sample(ts, m, apps, rng)
        for stall_at, dur in stalls.items():
            if abs(m - stall_at) < INTERVAL / 120:
                s.interval = INTERVAL + dur
                events.append(Event(ts - dur, "stall", f"Froze for {dur:.0f} s", 3 if dur >= 9 else 2, end_ts=ts))
        rows.append((s, apps))
        events += detectors.observe(s, apps)
        m += INTERVAL / 60
        if 199 <= m < 199 + INTERVAL / 60:
            pass

    with store.db:
        for s, apps in rows:
            store.db.execute(
                "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (s.ts, s.interval, s.cpu, s.mem_total, s.mem_available, s.swap_used, s.swap_in, s.swap_out, s.disk_read, s.disk_write,
                 s.net_rx, s.net_tx, s.pressure, s.thermal, s.load1, s.battery, int(bool(s.plugged)), s.self_cpu),
            )
            store.db.executemany(
                "INSERT INTO apps VALUES (?,?,?,?,?,?,?,?)",
                [(s.ts, a.app, a.cpu, a.rss, a.nproc, a.top_name, a.read_bps, a.write_bps) for a in apps],
            )

    excel = start + 125 * 60
    events.append(Event(excel, "hang", "Microsoft Excel stopped responding for 23 s", 2, end_ts=excel + 23, app="Microsoft Excel",
                        source="demo:excel.hang", detail={"report": "/Library/Logs/DiagnosticReports/Microsoft Excel_demo.hang"}))
    events.append(Event(start + 150 * 60, "resource", "macOS flagged Brave Browser Helper for heavy disk writing (2.1 GB)", 0,
                        app="Brave Browser", source="demo:brave.diag"))
    mark_ts = start + 78 * 60
    events.append(Event(mark_ts, "mark", "You reported a problem", 2, end_ts=mark_ts, detail={"note": "everything feels slow"}))

    for e in sorted(events, key=lambda e: e.ts):
        e.id = store.add_event(e)
    for e in store.events(start, end + 60):
        if e.kind in ("stall", "mark", "hang"):
            d = diagnose(store, e.ts, e.end_ts, e, "darwin")
            store.update_event(e.id, title=incident_title(e, d), detail={**e.detail, "diagnosis": d})

    store.set_meta("demo", True)
    store.set_meta("platform", "darwin")
    store.set_meta("recorder", {"platform": "darwin", "host": "demo-macbook", "cpus": CPUS, "memory": TOTAL, "interval": INTERVAL,
                                "os": "macOS 26 (demo data)", "started": start})
    store.set_meta("clean_exit", True)
    store.close()
    return path
