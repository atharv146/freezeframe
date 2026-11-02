"""Everything Freezeframe can say about an app, and whether it's fine to leave open.

Three sources, best first: the built-in write-ups (knowledge.py), a
description Claude wrote earlier (only if you turned that on), and the app's
own details (appinfo.py). Every app also gets a verdict on staying open,
based on what it actually used over the last hour.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import ai
from .appinfo import AppInfo, guess_kind, summary
from .detect import GB, human_bytes
from .knowledge import lookup
from .store import Store

log = logging.getLogger("freezeframe.catalog")

AI_RETRY_AFTER = 600.0
# Lower-case one-word names (mdworker, cloudd) and *agent / *helper names are background programs.
_DAEMONISH = re.compile(r"^[a-z0-9_.\-]+$|(Agent|Daemon|Helper|Service|Server|Worker)$")


def kind_of(app: str, top_name: str | None, note: dict[str, Any] | None, meta: dict[str, Any] | None) -> str:
    """app (something you use) | background (helpers, sync, menu-bar tools) | system (the OS itself)."""
    k = lookup(app, top_name)
    if k:
        return k.kind
    if note and note.get("kind") in ("app", "background", "system"):
        return note["kind"]
    guessed = guess_kind(meta or {})
    if guessed:
        return guessed
    return "background" if _DAEMONISH.search(app) else "app"


def verdict(app: str, kind: str, hour: dict[str, Any] | None, rss: int, machine: dict[str, Any],
            menubar: bool = False, device: str = "your computer") -> dict[str, str]:
    """Is it fine to leave this open? level: fine | ok | heavy, plus one plain sentence."""
    ncores = machine.get("cores") or 8
    total = machine.get("memory") or 16 * GB
    cpu_avg = (hour or {}).get("cpu_avg", 0.0) / ncores  # percent of the whole computer
    mem = rss / total
    pct = f"{cpu_avg:.1f}%" if cpu_avg < 10 else f"{cpu_avg:.0f}%"
    if kind == "system":
        return {"level": "fine", "label": "Part of the system",
                "text": f"Part of {device} itself. It's always running and isn't something you close."}
    if cpu_avg >= 8 or (hour or {}).get("cpu_avg", 0) >= 60:
        return {"level": "heavy", "label": "Close when not using",
                "text": f"It has averaged {pct} of {device}'s processing power over the last hour. "
                        "If you're not using it, quitting it will cool things down and save battery."}
    if mem >= 0.25:
        return {"level": "heavy", "label": "Close when not using",
                "text": f"It's holding {human_bytes(rss)} ({mem * 100:.0f}% of your memory). Fine while you're using it; "
                        "quit it when you're done to give that memory back."}
    if mem >= 0.10:
        return {"level": "ok", "label": "OK to leave open",
                "text": f"Light on the processor (about {pct} of {device} over the last hour), but it's holding {human_bytes(rss)} "
                        f"({mem * 100:.0f}% of your memory). Quit it when you're done if memory gets tight."}
    quiet = cpu_avg < 2 and mem < 0.05
    if menubar and quiet:
        return {"level": "fine", "label": "Made to stay open",
                "text": f"It lives in the menu bar and is meant to run all the time. Over the last hour it used about {pct} of {device}, "
                        "so leaving it open costs almost nothing."}
    if kind == "background" and quiet:
        return {"level": "fine", "label": "Made to stay open",
                "text": f"It works in the background on purpose and has been light: about {pct} of {device} over the last hour."}
    if quiet:
        return {"level": "fine", "label": "Fine to leave open",
                "text": f"It barely uses anything while you're not using it: about {pct} of {device} over the last hour and {human_bytes(rss)} of memory."}
    return {"level": "ok", "label": "OK to leave open",
            "text": f"It uses a bit: about {pct} of {device} on average over the last hour and {human_bytes(rss)} of memory. "
                    "Leave it if you use it often; quit it if things feel slow."}


class Catalog:
    """Describes apps. Keep one per process: it caches app details and runs AI requests in the background."""

    def __init__(self, platform: str = sys.platform, use_details: bool = True):
        self.platform = platform
        self.details = AppInfo(platform) if use_details else None
        self.ai_error: str | None = None
        self._ai_busy = False
        self._ai_tried: dict[str, float] = {}
        self._lock = threading.Lock()

    def describe(self, store: Store, apps: list[tuple[str, str | None]], db: Path | None = None) -> dict[str, dict[str, Any]]:
        notes = store.app_notes()
        unknown = [(a, t) for a, t in apps if lookup(a, t) is None]
        meta: dict[str, dict[str, Any]] = {}
        if self.details is not None and unknown:
            paths = store.app_paths()
            meta = self.details.lookup([(a, paths.get(a)) for a, _ in unknown])
        out: dict[str, dict[str, Any]] = {}
        for a, t in apps:
            k = lookup(a, t)
            note = notes.get(a)
            m = meta.get(a, {})
            d: dict[str, Any] = {"kind": kind_of(a, t, note, m), "menubar": bool(m.get("menubar")), "source": None,
                                 "what": None, "normal": None, "tip": None, "about": summary(a, m, self.platform)}
            for key in ("developer", "category", "version", "app_store", "electron"):
                if m.get(key):
                    d[key] = m[key]
            if k:
                d.update(what=k.what, normal=k.normal, tip=k.tip, source="builtin")
            elif note:
                d.update(what=note.get("what"), normal=note.get("normal"), stay_open=note.get("stay_open"),
                         source="ai", ai_known=note.get("known", False))
            elif d["about"]:
                d["source"] = "details"
            out[a] = d
        if db is not None:
            self._maybe_ask_ai(db, [(a, t, meta.get(a, {})) for a, t in unknown if a not in notes])
        return out

    # ------------------------------------------------------------------ AI

    def ai_status(self) -> dict[str, Any]:
        return {**ai.status(), "busy": self._ai_busy, "error": self.ai_error}

    def _maybe_ask_ai(self, db: Path, todo: list[tuple[str, str | None, dict[str, Any]]]) -> None:
        if not todo or not ai.installed() or ai.api_key() is None:
            return
        now = time.time()
        with self._lock:
            if self._ai_busy:
                return
            todo = [x for x in todo if now - self._ai_tried.get(x[0], 0) > AI_RETRY_AFTER][: ai.MAX_APPS]
            if not todo:
                return
            for a, _, _ in todo:
                self._ai_tried[a] = now
            self._ai_busy = True
        threading.Thread(target=self._ask_ai, args=(db, todo), daemon=True).start()

    def _ask_ai(self, db: Path, todo: list[tuple[str, str | None, dict[str, Any]]]) -> None:
        try:
            items = [{
                "name": a,
                "process": t if t and t != a else None,
                "bundle_id": m.get("bundle_id"),
                "developer": m.get("developer"),
                "category": m.get("category"),
                "from_app_store": m.get("app_store"),
                "menu_bar_app": m.get("menubar"),
                "electron": m.get("electron"),
                "file_description": m.get("description"),
            } for a, t, m in todo]
            notes = ai.describe(items, self.platform)
            if notes:
                s = Store(db)
                try:
                    s.set_app_notes(notes)
                finally:
                    s.close()
            self.ai_error = None
        except ai.AIError as e:
            self.ai_error = str(e)
        except Exception as e:  # never let a description request take the dashboard down
            log.exception("AI description failed")
            self.ai_error = f"Describing apps failed: {e}"
        finally:
            self._ai_busy = False


def hour_usage(store: Store, now: float) -> dict[str, dict[str, Any]]:
    return {a["app"]: a for a in store.top_apps(now - 3600, now, 500)}

