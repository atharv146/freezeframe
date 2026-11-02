"""The "right now" view: what the computer is doing, and short notes worth knowing.

`machine()` reads facts about this computer (disk, uptime, battery health).
`snapshot()` combines them with the latest recorded sample and the apps
running at that moment. `insights()` turns the last day of recording into a
handful of plain-English notes, most important first.
"""

from __future__ import annotations

import platform
import plistlib
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from .catalog import Catalog, hour_usage, verdict
from .detect import GB, MB, cores, human_bytes
from .models import INCIDENT_KINDS
from .reports import fmt_duration
from .store import Store

LEVELS = {"warning": 0, "tip": 1, "info": 2, "good": 3}


@dataclass
class Insight:
    level: str  # warning | tip | info | good
    title: str
    body: str
    app: str | None = None


# --------------------------------------------------------------- machine

def _mac_battery() -> dict[str, Any]:
    try:
        out = subprocess.run(["ioreg", "-r", "-c", "AppleSmartBattery", "-a"], capture_output=True, timeout=5).stdout
        data = plistlib.loads(out)[0] if out else {}
    except Exception:
        return {}
    design = data.get("DesignCapacity")
    raw_max = data.get("AppleRawMaxCapacity")
    max_cap = data.get("MaxCapacity")
    info: dict[str, Any] = {"cycles": data.get("CycleCount"), "design_cycles": data.get("DesignCycleCount9C")}
    if isinstance(max_cap, int) and 0 < max_cap <= 100:
        info["health"] = max_cap  # Apple silicon: the same "Maximum Capacity" System Settings shows
    elif design and raw_max:
        info["health"] = round(100 * raw_max / design)
    return info


def _sysctl(name: str) -> str | None:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def machine() -> dict[str, Any]:
    """Facts about this computer that change slowly."""
    root = "C:\\" if sys.platform == "win32" else "/"
    disk = psutil.disk_usage(root)
    info: dict[str, Any] = {
        "platform": sys.platform,
        "os": f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else platform.platform(terse=True),
        "cpu": (_sysctl("machdep.cpu.brand_string") if sys.platform == "darwin" else platform.processor()) or platform.machine(),
        "cores": psutil.cpu_count(logical=True),
        "memory": psutil.virtual_memory().total,
        "disk_total": disk.total,
        "disk_free": disk.free,
        "uptime": time.time() - psutil.boot_time(),
    }
    try:
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is not None:
        info["battery"] = {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
        if sys.platform == "darwin":
            info["battery"].update(_mac_battery())
    return info


# -------------------------------------------------------------- snapshot

def snapshot(store: Store, info: dict[str, Any], catalog: Catalog | None = None, db: Path | None = None) -> dict[str, Any]:
    _, last = store.bounds()
    if last is None:
        return {"sample": None, "apps": [], "machine": info}
    s = store.samples(last - 0.001, last + 0.001)[-1]
    apps = [a for _, a in store.app_rows(last - 0.001, last + 0.001)]
    apps.sort(key=lambda a: a.rss + a.cpu * 20 * MB, reverse=True)
    catalog = catalog or Catalog(store.platform(sys.platform), use_details=False)
    described = catalog.describe(store, [(a.app, a.top_name) for a in apps], db)
    hour = hour_usage(store, last)
    device = {"darwin": "your Mac", "win32": "your PC"}.get(info.get("platform", sys.platform), "your computer")
    out_apps = []
    for a in apps:
        d = described[a.app]
        h = hour.get(a.app)
        out_apps.append({
            "app": a.app, "cpu": round(a.cpu, 1), "rss": a.rss, "nproc": a.nproc, "top_name": a.top_name, **d,
            "hour": {"cpu_avg": round(h["cpu_avg"], 2), "cpu_max": round(h["cpu_max"], 1), "rss_max": h["rss_max"]} if h else None,
            "verdict": verdict(a.app, d["kind"], h, a.rss, info, d.get("menubar", False), device),
        })
    return {"sample": asdict(s), "apps": out_apps, "machine": info}


# -------------------------------------------------------------- insights

def _minutes(n_samples: int, step: float) -> str:
    return fmt_duration(n_samples * step)


def insights(store: Store, info: dict[str, Any], now: float | None = None, snap: dict[str, Any] | None = None) -> list[Insight]:
    _, last = store.bounds()
    now = last if now is None else now
    out: list[Insight] = []
    device = {"darwin": "your Mac", "win32": "your PC"}.get(info.get("platform", sys.platform), "your computer")
    Device = device[0].upper() + device[1:]

    # Storage space: below ~10% free, swapping and updates start to suffer.
    total, free = info.get("disk_total"), info.get("disk_free")
    if total and free is not None:
        pct_used = 100 * (1 - free / total)
        if free < 10 * GB or pct_used >= 90:
            out.append(Insight("warning", f"Your disk is {pct_used:.0f}% full ({human_bytes(free)} free)",
                               "The system needs free space for swap, updates and caches; when it runs low, everything slows down and updates can fail. "
                               "Delete large files you don't need, empty the trash, or move photos and videos to an external drive."))
        elif pct_used >= 80:
            out.append(Insight("tip", f"Your disk is {pct_used:.0f}% full", f"{human_bytes(free)} free. Worth clearing some space before it gets tight."))

    # Battery health (macOS reports it without admin rights).
    battery = info.get("battery") or {}
    health = battery.get("health")
    if health is not None:
        cycles = battery.get("cycles")
        cycles_txt = f" after {cycles} charge cycles" if cycles else ""
        if health < 80:
            out.append(Insight("warning", f"Battery health is {health}%{cycles_txt}",
                               "Below 80%, batteries hold noticeably less charge and Macs may slow down to protect them. A battery replacement restores both."))
        else:
            out.append(Insight("good", f"Battery health is {health}%{cycles_txt}", "That's in good shape. Batteries are designed for around 1,000 cycles."))

    # Uptime: long-running sessions accumulate leaks and pending updates.
    uptime = info.get("uptime")
    if uptime and uptime > 7 * 86400:
        out.append(Insight("tip", f"{Device} hasn't restarted in {uptime / 86400:.0f} days",
                           "Restarting now and then clears memory that apps have leaked, finishes installing updates, and often fixes slowness that has built up."))

    if last is None:
        return sorted(out, key=lambda i: LEVELS[i.level])

    day_start = max(now - 86400, store.bounds()[0] or now)
    step = 60.0
    series = store.series(day_start, now, step)
    covered = fmt_duration(now - day_start)

    # Incidents worth mentioning this week.
    recent = [e for e in store.events(now - 7 * 86400, now) if e.kind in INCIDENT_KINDS and e.kind != "mark" and e.severity >= 2]
    if recent:
        labels = {"stall": ("freeze", "freezes"), "hang": ("app hang", "app hangs"), "crash": ("app crash", "app crashes")}
        counts: dict[str, int] = {}
        for e in recent:
            counts[e.kind if e.kind in labels else "system"] = counts.get(e.kind if e.kind in labels else "system", 0) + 1
        parts = []
        for kind, (one, many) in labels.items():
            if counts.get(kind):
                parts.append(f"{counts[kind]} {one if counts[kind] == 1 else many}")
        if counts.get("system"):
            n = counts["system"]
            parts.append(f"{n} system error{'s' if n != 1 else ''}")
        freezes = [e for e in recent if e.kind == "stall"]
        causes: dict[str, int] = {}
        for e in freezes:
            short = (e.detail.get("diagnosis") or {}).get("short", "")
            for key, label in (("memory", "memory ran out"), ("overheat", "it overheated"), ("CPU", "the CPU was maxed out"), ("disk", "the disk was overloaded")):
                if key in short:
                    causes[label] = causes.get(label, 0) + 1
        why = ""
        ranked = sorted(causes.items(), key=lambda kv: kv[1], reverse=True)
        if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            why = f" Most freezes happened because {ranked[0][0]}."
        out.append(Insight("warning" if freezes or counts.get("system") else "info", "In the last 7 days: " + ", ".join(parts),
                           "Open them in the incident list for the full explanation of each." + why))

    # Memory over the day.
    if series:
        tight = sum(1 for p in series if (p.get("pressure") or 0) >= 2 or (p["mem_total"] and p["mem_available"] / p["mem_total"] < 0.08))
        if tight * step >= 600:
            hogs = store.top_apps(day_start, now, 3)
            hog = max(hogs, key=lambda a: a["rss_max"], default=None)
            who = f" The biggest user was {hog['app']} at up to {human_bytes(hog['rss_max'])}." if hog else ""
            out.append(Insight("warning" if tight * step >= 3600 else "tip", f"Memory was tight for {_minutes(tight, step)} in the last {covered}",
                               "While memory is tight the system swaps to disk, which is what makes apps stutter and beachball." + who,
                               hog["app"] if hog else None))
        hot = sum(1 for p in series if (p.get("thermal") or 0) >= 1)
        if hot * step >= 300:
            out.append(Insight("tip", f"{Device} was hot enough to slow itself down for {_minutes(hot, step)}",
                               "Heat throttling makes everything slower. Keep vents clear and avoid soft surfaces like beds and sofas."))
        busy = sum(1 for p in series if (p.get("cpu") or 0) >= 90)
        if busy * step >= 600:
            out.append(Insight("tip", f"The CPU was maxed out for {_minutes(busy, step)} in the last {covered}",
                               "Check the timeline for which app was responsible."))

    # Slow leaks seen today.
    for e in store.events(now - 86400, now, kinds=["leak"]):
        out.append(Insight("tip", e.title, f"If {e.app} gets slow or the computer starts swapping, quitting and reopening it gives the memory back.", e.app))

    # What's happening right now.
    snap = snap or snapshot(store, info)
    s = snap["sample"]
    if s:
        for a in snap["apps"]:
            if a["kind"] == "background" and a["cpu"] >= 50:
                about = f"It's {a['what']}" if a["what"] else (a["about"] or "")
                out.append(Insight("info", f"{a['app']} is busy right now ({cores(a['cpu'])})", " ".join(x for x in (about, a["normal"], a["tip"]) if x), a["app"]))
        apps = snap["apps"]
        if apps and s["mem_total"]:
            big = max(apps, key=lambda a: a["rss"])
            share = 100 * big["rss"] / s["mem_total"]
            if share >= 20:
                procs = f" across {big['nproc']} processes" if big["nproc"] > 1 else ""
                out.append(Insight("info", f"{big['app']} is using {human_bytes(big['rss'])}{procs}",
                                   f"That's {share:.0f}% of your memory. " + (big["normal"] or "") + (" " + big["tip"] if big["tip"] and share >= 35 else ""),
                                   big["app"]))
        if s["swap_used"] >= max(2 * GB, 0.25 * s["mem_total"]):
            out.append(Insight("info", f"{human_bytes(s['swap_used'])} of memory is parked on disk (swap)",
                               "That's normal on its own, but it means memory has been tight at some point. A restart clears it."))

    if not any(i.level in ("warning", "tip") for i in out):
        out.append(Insight("good", "Nothing needs your attention", f"No freezes, memory problems or overheating in the last {covered}."))
    return sorted(out, key=lambda i: LEVELS[i.level])
