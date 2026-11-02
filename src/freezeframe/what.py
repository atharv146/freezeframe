"""'What happened around 3pm?' Looks over a stretch of time and says what stood out.

Unlike diagnose(), which digs into one moment, this skims a whole window:
freezes and crashes first, then whether the processor, memory, disk or heat
were under strain and which app was responsible. When nothing stood out it
says so plainly, along with what Freezeframe can't see.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Any

from .detect import GB, MB, human_bytes, human_rate
from .models import INCIDENT_KINDS, Sample
from .reports import fmt_duration
from .store import Store
from .when import Window


def clock(ts: float, now: float | None = None) -> str:
    d = datetime.fromtimestamp(ts)
    text = d.strftime("%I:%M %p").lstrip("0")
    ref = datetime.fromtimestamp(now) if now else datetime.now()
    if d.date() == (ref - timedelta(days=1)).date():
        return f"{text} yesterday"
    if d.date() != ref.date():
        return d.strftime(f"{text} on %A")
    return text


def _span(samples: list[Sample]) -> float:
    return sum(min(s.interval, 10.0) for s in samples)


def _top_at(store: Store, ts: float, key: str = "cpu") -> tuple[str, float, int] | None:
    rows = store.app_rows(ts - 0.001, ts + 0.001)
    if not rows:
        return None
    a = max((r for _, r in rows), key=lambda r: getattr(r, key) or 0)
    return a.app, a.cpu, a.rss


def what_happened(store: Store, window: Window, platform: str = sys.platform, now: float | None = None) -> dict[str, Any]:
    start, end = window.start, window.end
    first, last = store.bounds()
    device = {"darwin": "your Mac", "win32": "your PC"}.get(platform, "your computer")
    Device = device[0].upper() + device[1:]
    meta = store.meta()
    ncores = (meta.get("recorder") or {}).get("cpus") or 8
    samples = store.samples(start, end)
    events = store.events(start, end)
    incidents = sorted((e for e in events if e.kind in INCIDENT_KINDS), key=lambda e: (-e.severity, e.ts))
    lines: list[dict[str, Any]] = []
    for e in sorted(incidents, key=lambda e: e.ts):
        lines.append({"level": "problem", "text": f"{clock(e.ts, now)}: {e.title}", "ts": e.ts, "incident": e.id})
    sleeps = [e for e in events if e.kind == "sleep"]
    for e in sleeps:
        lines.append({"level": "note", "text": f"{Device} was asleep from {clock(e.ts, now)} to {clock(e.end_ts or e.ts, now)}.", "ts": e.ts})

    base = {"start": start, "end": end, "label": window.label, "apps": [], "stats": {}}
    if not samples:
        if first is None:
            why = "Freezeframe hasn't recorded anything yet. Once it's running in the background, it can answer this for any time in the last two days."
        elif end < first:
            why = (f"The recording starts at {clock(first, now)}. Freezeframe keeps two days of detail, "
                   "plus the minutes around every incident for a month.")
        elif start > last:
            why = "That's after the latest recording."
        elif sleeps:
            why = f"{Device} was asleep, so there was nothing to record."
        else:
            why = "Freezeframe wasn't running then, so there are no measurements to look at."
        focus = {"incident": incidents[0].id} if incidents else None
        headline = incidents[0].title if incidents else "Freezeframe wasn't recording then"
        lines.append({"level": "note", "text": why})
        return {**base, "verdict": "problem" if incidents else "no-data", "headline": headline, "lines": lines, "focus": focus}

    n = len(samples)
    busy_lines: list[dict[str, Any]] = []
    worst: tuple[float, float] | None = None  # (strain score, ts)

    def strain(score: float, ts: float) -> None:
        nonlocal worst
        if worst is None or score > worst[0]:
            worst = (score, ts)

    # Processor
    cpu_avg = sum(s.cpu for s in samples) / n
    peak = max(samples, key=lambda s: s.cpu)
    maxed = [s for s in samples if s.cpu >= 85]
    top = _top_at(store, peak.ts)
    who = f"; {top[0]} was using the most ({top[1] / ncores:.0f}% of the computer)" if top and top[1] >= 10 else ""
    if _span(maxed) >= 20:
        busy_lines.append({"level": "busy", "ts": peak.ts,
                           "text": f"The processor was maxed out for {fmt_duration(_span(maxed))}, peaking at {peak.cpu:.0f}% around {clock(peak.ts, now)}{who}."})
        strain(0.6 + _span(maxed) / 600, peak.ts)
    elif peak.cpu >= 60:
        lines.append({"level": "note", "ts": peak.ts,
                      "text": f"The processor got busy for a moment: {peak.cpu:.0f}% at {clock(peak.ts, now)}{who}."})
        strain(0.3, peak.ts)
    else:
        mood = "calm" if cpu_avg < 25 else "moderately busy"
        lines.append({"level": "ok", "text": f"The processor was {mood}: {cpu_avg:.0f}% on average, never above {peak.cpu:.0f}%."})

    # Memory
    tight = [s for s in samples if (s.pressure or 0) >= 2 or s.mem_available_pct < 8]
    low = min(samples, key=lambda s: s.mem_available)
    if tight:
        hog = max(store.top_apps(start, end, 5), key=lambda a: a["rss_max"], default=None)
        by = f"; {hog['app']} was using {human_bytes(hog['rss_max'])}" if hog and hog["rss_max"] >= 1 * GB else ""
        busy_lines.append({"level": "busy", "ts": low.ts,
                           "text": f"Memory ran low for {fmt_duration(max(_span(tight), low.interval))}, down to {human_bytes(low.mem_available)} free around {clock(low.ts, now)}{by}."})
        strain(0.7 + _span(tight) / 600, low.ts)
    else:
        lines.append({"level": "ok", "text": f"Memory was fine: at least {human_bytes(low.mem_available)} stayed free."})

    swapping = [s for s in samples if s.swap_in + s.swap_out >= 20 * MB]
    if _span(swapping) >= 10:
        s = max(swapping, key=lambda s: s.swap_in + s.swap_out)
        busy_lines.append({"level": "busy", "ts": s.ts,
                           "text": f"{Device} was moving memory to and from the disk (swapping) for {fmt_duration(_span(swapping))}. That's what makes apps stutter and beachball."})
        strain(0.65, s.ts)

    hot = [s for s in samples if (s.thermal or 0) >= 1]
    if _span(hot) >= 30:
        busy_lines.append({"level": "busy", "ts": hot[0].ts,
                           "text": f"{Device} was hot enough to slow itself down for {fmt_duration(_span(hot))}, starting around {clock(hot[0].ts, now)}."})
        strain(0.5 + _span(hot) / 1200, hot[0].ts)

    disky = [s for s in samples if max(s.disk_read, s.disk_write) >= 150 * MB]
    if _span(disky) >= 20:
        s = max(disky, key=lambda s: max(s.disk_read, s.disk_write))
        rows = [r for _, r in store.app_rows(s.ts - 0.001, s.ts + 0.001) if (r.write_bps or 0) + (r.read_bps or 0) > 0]
        top_io = max(rows, key=lambda r: (r.write_bps or 0) + (r.read_bps or 0), default=None)
        by = f", mostly {top_io.app}" if top_io else ""
        long = _span(disky) >= 60
        (busy_lines if long else lines).append({
            "level": "busy" if long else "note", "ts": s.ts,
            "text": f"The disk was very busy for {fmt_duration(_span(disky))} ({human_rate(max(s.disk_read, s.disk_write))} at {clock(s.ts, now)}{by})"
                    + (". Apps that need the disk wait their turn, which feels like slowness." if long else ".")})
        strain(0.45 if long else 0.3, s.ts)
    covered_kinds = {"disk": any("disk was very busy" in x["text"] for x in busy_lines + lines),
                     "swap": any("swapping" in x["text"] for x in busy_lines),
                     "thermal": any("hot enough" in x["text"] for x in busy_lines),
                     "memory": bool(tight), "pressure": bool(tight), "cpu": _span(maxed) >= 20}

    # Other things the recorder noticed, once per kind ("Memory ran out (and 2 more times)").
    seen: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.kind in INCIDENT_KINDS or e.kind in ("sleep", "gap") or e.severity < 1 or covered_kinds.get(e.kind):
            continue  # already said above, in more detail
        if e.kind in seen:
            seen[e.kind]["more"] += 1
            continue
        seen[e.kind] = {"level": "note", "ts": e.ts, "text": f"{clock(e.ts, now)}: {e.title}", "more": 0}
    for item in list(seen.values())[:5]:
        more = item.pop("more")
        if more:
            item["text"] += f" (and {more} more time{'s' if more > 1 else ''})"
        lines.append(item)

    covered = _span(samples) + sum(min(e.end_ts or e.ts, end) - max(e.ts, start) for e in sleeps)
    if covered < 0.6 * (end - start) and end - start > 120:
        lines.append({"level": "note", "text": f"Freezeframe only recorded {fmt_duration(_span(samples))} of this {fmt_duration(end - start)} stretch."})

    apps = []
    for a in store.top_apps(start, end, 5):
        if a["cpu_avg"] >= 1 or a["rss_max"] >= 500 * MB:
            apps.append({"app": a["app"], "cpu_pct": round(a["cpu_avg"] / ncores, 1), "cpu_peak_pct": round(a["cpu_max"] / ncores), "rss_max": a["rss_max"]})

    order = {"problem": 0, "busy": 1, "note": 2, "ok": 3}
    lines = sorted(busy_lines + lines, key=lambda line: order[line["level"]])  # stable: keeps time order within a level
    if incidents:
        verdict = "problem"
        top_e = incidents[0]
        headline = top_e.title if len(incidents) == 1 else f"{len(incidents)} problems {window.label}, the worst: {top_e.title}"
        focus: dict[str, Any] | None = {"incident": top_e.id}
    elif busy_lines:
        verdict = "busy"
        headline = f"{Device} was under strain {window.label}"
        focus = {"at": worst[1]} if worst else None
    else:
        verdict = "quiet"
        headline = f"Nothing unusual happened {window.label}"
        focus = None
        lines.append({"level": "note", "text": "If something still felt slow, it may have been a website, the network, or one app waiting on something, "
                                               "which Freezeframe can't see. Next time, press “Something went wrong” right when it happens."})
    stats = {"cpu_avg": round(cpu_avg, 1), "cpu_peak": round(peak.cpu, 1), "mem_free_min": low.mem_available, "samples": n}
    return {**base, "verdict": verdict, "headline": headline, "lines": lines, "focus": focus, "apps": apps, "stats": stats}
