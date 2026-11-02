"""What the menu-bar / tray icon shows. Pure logic, no GUI, so it can be tested."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .detect import human_bytes
from .insights import insights, snapshot
from .models import Event
from .store import Store

ATTENTION_WINDOW = 30 * 60  # a freeze in the last half hour puts a badge on the icon


@dataclass
class TrayState:
    recording: bool
    tooltip: str
    attention: bool
    lines: list[str] = field(default_factory=list)
    latest: Event | None = None
    latest_text: str | None = None
    tip: str | None = None


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def _short(text: str, n: int = 64) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def tray_state(store: Store, info: dict[str, Any], now: float | None = None) -> TrayState:
    now = time.time() if now is None else now
    first, last = store.bounds()
    meta = store.meta()
    interval = float((meta.get("recorder") or {}).get("interval", 2))
    recording = last is not None and (now - last < 3 * interval + 5 or bool(meta.get("demo")))
    if last is None:
        return TrayState(False, "Freezeframe: not recording yet", False,
                         ["Not recording yet.", "Run  freezeframe install  to start."])

    snap = snapshot(store, info)
    s = snap["sample"]
    lines = []
    attention = False
    if s:
        used = s["mem_total"] - s["mem_available"]
        lines.append(f"CPU {s['cpu']:.0f}%    Memory {human_bytes(used)} of {human_bytes(s['mem_total'])}")
        pressure = s.get("pressure")
        if pressure:
            label = {1: "normal", 2: "elevated", 4: "critical"}.get(pressure, "normal")
            lines.append(f"Memory pressure: {label}")
            attention = attention or pressure >= 4
        if (s.get("thermal") or 0) >= 2:
            lines.append("Running hot: the CPU is being slowed down")
            attention = True
        for a in sorted(snap["apps"], key=lambda a: a["rss"], reverse=True)[:3]:
            lines.append(f"{a['app']}: {human_bytes(a['rss'])}")

    incidents = [e for e in store.incidents(10) if e.ts >= now - 86400 or meta.get("demo")]
    latest = incidents[0] if incidents else None
    latest_text = None
    if latest is not None:
        latest_text = _short(f"{_clock(latest.ts)}  {latest.title}")
        attention = attention or (now - (latest.end_ts or latest.ts) < ATTENTION_WINDOW and latest.severity >= 2)

    top_tip = next((i for i in insights(store, info, now=last) if i.level == "warning"), None)
    if not recording:
        tooltip = "Freezeframe: not recording"
    elif latest is not None and attention:
        tooltip = _short(f"Freezeframe: {latest.title}", 120)
    else:
        tooltip = "Freezeframe: " + (lines[0] if lines else "recording")
    return TrayState(recording, tooltip, attention, lines, latest, latest_text, _short(top_tip.title) if top_tip else None)
