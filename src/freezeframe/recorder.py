"""The background recorder: sample, detect, import OS reports, tidy up."""

from __future__ import annotations

import logging
import os
import platform
import signal
import socket
import sys
import threading
import time
from typing import Callable

import psutil

from . import __version__, reports
from .detect import Detectors, classify_gap
from .diagnose import diagnose, incident_title
from .models import Event
from .notify import notify
from .reports import fmt_duration
from .sampler import Sampler
from .store import Store

log = logging.getLogger("freezeframe")

REPORT_SCAN_EVERY = 60.0
NOTIFY_MIN_FREEZE = 3.0  # seconds; shorter hiccups are recorded but not announced
NOTIFY_COOLDOWN = 120.0
PRUNE_EVERY = 3600.0
FIRST_IMPORT_DAYS = 7


class Recorder:
    def __init__(self, store: Store, interval: float = 2.0, sampler: Sampler | None = None,
                 scan_reports: Callable[[float], list[Event]] | None = reports.scan, notify_enabled: bool = False):
        self.store = store
        self.interval = interval
        self.sampler = sampler or Sampler()
        self.scan_reports = scan_reports
        self.detectors = Detectors(interval=interval)
        self._prev: tuple[float, float] | None = None  # (wall, monotonic)
        self._last_scan = 0.0
        self._last_prune = 0.0
        self._pending: list[tuple[int, Event, float]] = []  # incidents to re-diagnose once more data exists
        self.notify = notify_enabled
        self._last_notice = 0.0
        self._saved_paths: set[str] = set()

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        meta = self.store.meta()
        now = time.time()
        _, last_ts = self.store.bounds()
        boot = psutil.boot_time()
        if last_ts is not None:
            if boot > last_ts and not meta.get("clean_exit", True):
                self.store.add_event(Event(
                    last_ts, "shutdown", "The computer restarted while Freezeframe was running", 2, end_ts=boot,
                    detail={"explain": "Freezeframe was not shut down normally before the restart, which usually means the computer "
                                       "was force-restarted (for example by holding the power button after a freeze), crashed, or lost power."},
                    source=f"restart:{int(boot)}",
                ))
            elif now - last_ts > 600:
                self.store.add_event(Event(last_ts, "gap", "Freezeframe wasn't recording", 0, end_ts=now))
        self._last_scan = meta.get("last_scan", now - FIRST_IMPORT_DAYS * 86400)
        self.store.set_meta("clean_exit", False)
        self.store.set_meta("recorder", {
            "pid": os.getpid(), "started": now, "interval": self.interval, "version": __version__,
            "platform": sys.platform, "machine": platform.machine(), "host": socket.gethostname(),
            "os": platform.platform(terse=True), "cpus": psutil.cpu_count(), "memory": psutil.virtual_memory().total,
        })

    def stop(self) -> None:
        self.store.set_meta("clean_exit", True)

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.start()
        try:
            next_at = time.monotonic()
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:  # keep recording even if one tick fails
                    log.exception("tick failed")
                next_at += self.interval
                delay = next_at - time.monotonic()
                if delay < -self.interval:
                    next_at = time.monotonic()  # fell behind (e.g. after a freeze or sleep): resynchronise
                    delay = 0
                stop.wait(max(0.0, delay))
        finally:
            self.stop()

    # ------------------------------------------------------------------ work

    def tick(self) -> None:
        sample, apps = self.sampler.sample()
        mono = time.monotonic()
        if self._prev is not None:
            kind, seconds = classify_gap(self._prev[0], self._prev[1], sample.ts, mono, self.interval)
            if kind == "stall":
                self._stall(self._prev[0] + self.interval, sample.ts)
            elif kind == "sleep":
                self.store.add_event(Event(self._prev[0], "sleep", f"Asleep for {fmt_duration(seconds)}", 0, end_ts=sample.ts))
                sample.interval = self.interval  # don't let the sleep look like a freeze in charts
        self._prev = (sample.ts, mono)
        self.store.add_sample(sample, apps)
        known = getattr(self.sampler, "paths", {})
        new_paths = {a.app: known[a.app] for a in apps if a.app in known and a.app not in self._saved_paths}
        if new_paths:
            self.store.set_app_paths(new_paths)
            self._saved_paths.update(new_paths)

        for e in self.detectors.observe(sample, apps):
            self.store.add_event(e)

        now = sample.ts
        self._rediagnose(now)
        if self.scan_reports and now - self._last_scan >= REPORT_SCAN_EVERY:
            self._import_reports(now)
        if now - self._last_prune >= PRUNE_EVERY:
            self._last_prune = now
            self.store.prune(now)

    def _stall(self, start: float, end: float) -> None:
        incident = Event(start, "stall", f"Froze for {fmt_duration(end - start)}", 3 if end - start >= 10 else 2, end_ts=end)
        incident.id = self.store.add_event(incident)
        if incident.id is not None:
            self._diagnose(incident, announce=end - start >= NOTIFY_MIN_FREEZE)
            # Diagnose again shortly, once the moments after the freeze are recorded too.
            self._pending.append((incident.id, incident, end + 10))

    def _diagnose(self, incident: Event, announce: bool = False) -> None:
        d = diagnose(self.store, incident.ts, incident.end_ts, incident, sys.platform)
        title = incident_title(incident, d)
        self.store.update_event(incident.id, title=title, detail={**incident.detail, "diagnosis": d})
        if announce and self.notify and time.time() - self._last_notice >= NOTIFY_COOLDOWN:
            self._last_notice = time.time()
            notify("Freezeframe", f"{title}. Run  freezeframe why  for details.")

    def _rediagnose(self, now: float) -> None:
        due = [p for p in self._pending if p[2] <= now]
        self._pending = [p for p in self._pending if p[2] > now]
        for _, incident, _ in due:
            self._diagnose(incident)

    def _import_reports(self, now: float) -> None:
        since = self._last_scan - 120
        self._last_scan = now
        for e in self.scan_reports(since):
            e.id = self.store.add_event(e)
            if e.id is not None and e.kind in ("hang", "crash", "panic", "hardware") and now - e.ts < 3600:
                self._diagnose(e, announce=now - e.ts < 900)
        self.store.set_meta("last_scan", now)


def mark(store: Store, note: str = "", minutes_ago: float = 0.0) -> Event:
    """Records "something just went wrong" and diagnoses the moments before it."""
    ts = time.time() - minutes_ago * 60
    e = Event(ts, "mark", "You reported a problem", 2, end_ts=ts, detail={"note": note} if note else {})
    e.id = store.add_event(e)
    d = diagnose(store, ts, ts, e, store.platform(sys.platform))
    store.update_event(e.id, title=incident_title(e, d), detail={**e.detail, "diagnosis": d})
    e.title = incident_title(e, d)
    e.detail = {**e.detail, "diagnosis": d}
    return e


def install_signal_handlers(stop: threading.Event) -> None:
    def handler(signum, frame):  # noqa: ARG001
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
