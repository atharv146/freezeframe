"""The recording: a SQLite file with machine samples, per-app usage and events.

Raw samples roll off after a couple of days, but the minutes around every
incident are kept for a month so you can still look at last week's freeze.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .models import AppUsage, Event, INCIDENT_KINDS, Sample

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  ts REAL PRIMARY KEY,
  interval REAL, cpu REAL,
  mem_total INTEGER, mem_available INTEGER, swap_used INTEGER,
  swap_in REAL, swap_out REAL, disk_read REAL, disk_write REAL,
  net_rx REAL, net_tx REAL,
  pressure INTEGER, thermal INTEGER, load1 REAL,
  battery REAL, plugged INTEGER, self_cpu REAL
);
CREATE TABLE IF NOT EXISTS apps (
  ts REAL, app TEXT, cpu REAL, rss INTEGER, nproc INTEGER, top_name TEXT,
  read_bps REAL, write_bps REAL
);
CREATE INDEX IF NOT EXISTS apps_ts ON apps (ts);
CREATE INDEX IF NOT EXISTS apps_app_ts ON apps (app, ts);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL, end_ts REAL, kind TEXT, app TEXT, severity INTEGER,
  title TEXT, detail TEXT, source TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS app_paths (app TEXT PRIMARY KEY, path TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS app_notes (app TEXT PRIMARY KEY, note TEXT, ts REAL);
"""

SAMPLE_COLUMNS = [
    "ts", "interval", "cpu", "mem_total", "mem_available", "swap_used", "swap_in", "swap_out",
    "disk_read", "disk_write", "net_rx", "net_tx", "pressure", "thermal", "load1", "battery",
    "plugged", "self_cpu",
]

DAY = 86400.0


class Store:
    def __init__(self, path: str | Path, readonly: bool = False):
        self.path = Path(path).resolve()
        uri = self.path.as_uri() + ("?mode=ro" if readonly else "")  # file:///C:/... works on Windows too
        self.db = sqlite3.connect(uri, uri=True, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ writing

    def add_sample(self, s: Sample, apps: Iterable[AppUsage]) -> None:
        row = asdict(s)
        row["plugged"] = None if s.plugged is None else int(s.plugged)
        with self.db:
            self.db.execute(
                f"INSERT OR REPLACE INTO samples ({', '.join(SAMPLE_COLUMNS)}) VALUES ({', '.join('?' * len(SAMPLE_COLUMNS))})",
                [row[c] for c in SAMPLE_COLUMNS],
            )
            self.db.executemany(
                "INSERT INTO apps (ts, app, cpu, rss, nproc, top_name, read_bps, write_bps) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(s.ts, a.app, a.cpu, a.rss, a.nproc, a.top_name, a.read_bps, a.write_bps) for a in apps],
            )

    def add_event(self, e: Event) -> int | None:
        """Inserts an event. Events with a `source` already present are ignored."""
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO events (ts, end_ts, kind, app, severity, title, detail, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (e.ts, e.end_ts, e.kind, e.app, e.severity, e.title, json.dumps(e.detail), e.source),
            )
            return cur.lastrowid if cur.rowcount else None

    def update_event(self, event_id: int, title: str | None = None, detail: dict | None = None) -> None:
        with self.db:
            if title is not None:
                self.db.execute("UPDATE events SET title = ? WHERE id = ?", (title, event_id))
            if detail is not None:
                self.db.execute("UPDATE events SET detail = ? WHERE id = ?", (json.dumps(detail), event_id))

    def set_meta(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def set_app_paths(self, paths: dict[str, str]) -> None:
        """Where each app's program lives, so it can be identified later."""
        with self.db:
            self.db.executemany(
                "INSERT INTO app_paths (app, path, ts) VALUES (?, ?, ?) ON CONFLICT(app) DO UPDATE SET path = excluded.path, ts = excluded.ts",
                [(a, p, time.time()) for a, p in paths.items()],
            )

    def set_app_notes(self, notes: dict[str, dict[str, Any]]) -> None:
        """Descriptions written by an AI model, kept so each app is only described once."""
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO app_notes (app, note, ts) VALUES (?, ?, ?)",
                [(a, json.dumps(n), time.time()) for a, n in notes.items()],
            )

    def prune(self, now: float | None = None, keep_days: float = 2, keep_incident_days: float = 30) -> int:
        """Drops old samples, except around incidents, which are kept much longer."""
        now = time.time() if now is None else now
        cutoff = now - keep_days * DAY
        incident_cutoff = now - keep_incident_days * DAY
        kinds = ",".join(f"'{k}'" for k in INCIDENT_KINDS)
        protected = f"""EXISTS (SELECT 1 FROM events e WHERE e.kind IN ({kinds}) AND e.ts >= {incident_cutoff}
                        AND {{t}}.ts BETWEEN e.ts - 300 AND COALESCE(e.end_ts, e.ts) + 120)"""
        with self.db:
            n = self.db.execute(f"DELETE FROM samples WHERE ts < ? AND NOT {protected.format(t='samples')}", (cutoff,)).rowcount
            self.db.execute(f"DELETE FROM apps WHERE ts < ? AND NOT {protected.format(t='apps')}", (cutoff,))
            self.db.execute("DELETE FROM samples WHERE ts < ?", (incident_cutoff,))
            self.db.execute("DELETE FROM apps WHERE ts < ?", (incident_cutoff,))
            self.db.execute("DELETE FROM events WHERE ts < ? AND kind NOT IN (" + kinds + ")", (cutoff,))
            self.db.execute("DELETE FROM events WHERE ts < ?", (now - 90 * DAY,))
        return n

    # ------------------------------------------------------------ reading

    def meta(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT key, value FROM meta")}

    def platform(self, default: str) -> str:
        """The OS the recording was made on (it can be opened elsewhere)."""
        meta = self.meta()
        return meta.get("platform") or (meta.get("recorder") or {}).get("platform") or default

    def app_paths(self) -> dict[str, str]:
        return {r["app"]: r["path"] for r in self.db.execute("SELECT app, path FROM app_paths")}

    def app_notes(self) -> dict[str, dict[str, Any]]:
        return {r["app"]: json.loads(r["note"]) for r in self.db.execute("SELECT app, note FROM app_notes")}

    def bounds(self) -> tuple[float | None, float | None]:
        r = self.db.execute("SELECT min(ts), max(ts) FROM samples").fetchone()
        return r[0], r[1]

    def samples(self, start: float, end: float) -> list[Sample]:
        rows = self.db.execute(
            f"SELECT {', '.join(SAMPLE_COLUMNS)} FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts", (start, end)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["plugged"] = None if d["plugged"] is None else bool(d["plugged"])
            out.append(Sample(**d))
        return out

    def app_rows(self, start: float, end: float, app: str | None = None) -> list[tuple[float, AppUsage]]:
        sql = "SELECT * FROM apps WHERE ts BETWEEN ? AND ?"
        args: list[Any] = [start, end]
        if app is not None:
            sql += " AND app = ?"
            args.append(app)
        rows = self.db.execute(sql + " ORDER BY ts", args).fetchall()
        return [
            (r["ts"], AppUsage(r["app"], r["cpu"], r["rss"], r["nproc"], r["top_name"], r["read_bps"], r["write_bps"]))
            for r in rows
        ]

    def series(self, start: float, end: float, step: float) -> list[dict[str, Any]]:
        """Samples averaged into buckets of `step` seconds (peaks kept for CPU and gaps)."""
        rows = self.db.execute(
            """SELECT CAST(ts / :step AS INTEGER) * :step AS t,
                      avg(cpu) AS cpu, max(cpu) AS cpu_max,
                      avg(mem_total) AS mem_total, min(mem_available) AS mem_available,
                      avg(swap_used) AS swap_used, max(swap_in) AS swap_in, max(swap_out) AS swap_out,
                      max(disk_read) AS disk_read, max(disk_write) AS disk_write,
                      max(net_rx) AS net_rx, max(net_tx) AS net_tx,
                      max(pressure) AS pressure, max(thermal) AS thermal,
                      max(interval) AS interval, avg(self_cpu) AS self_cpu, min(battery) AS battery, count(*) AS n
               FROM samples WHERE ts BETWEEN :start AND :end GROUP BY t ORDER BY t""",
            {"start": start, "end": end, "step": step},
        ).fetchall()
        return [dict(r) for r in rows]

    def top_apps(self, start: float, end: float, limit: int = 12) -> list[dict[str, Any]]:
        """Apps ranked by how much they used in the window."""
        n = self.db.execute("SELECT count(*) FROM samples WHERE ts BETWEEN ? AND ?", (start, end)).fetchone()[0] or 1
        rows = self.db.execute(
            """SELECT app, sum(cpu) / ? AS cpu_avg, max(cpu) AS cpu_max, max(rss) AS rss_max,
                      min(rss) AS rss_min, max(nproc) AS nproc, max(top_name) AS top_name,
                      max(write_bps) AS write_max, max(read_bps) AS read_max, count(*) AS seen
               FROM apps WHERE ts BETWEEN ? AND ? GROUP BY app""",
            (n, start, end),
        ).fetchall()
        apps = [dict(r) for r in rows]
        apps.sort(key=lambda a: a["cpu_avg"] / 100 + a["rss_max"] / 2e9, reverse=True)
        return apps[:limit]

    def app_series(self, start: float, end: float, step: float, apps: list[str],
                   counts: dict[float, int] | None = None) -> dict[str, list[dict[str, Any]]]:
        """Per-app CPU and memory in buckets. CPU is averaged over every sample in the bucket,
        counting samples where the app wasn't among the recorded ones as zero."""
        out: dict[str, list[dict[str, Any]]] = {}
        for app in apps:
            rows = self.db.execute(
                """SELECT CAST(ts / :step AS INTEGER) * :step AS t, sum(cpu) AS cpu, count(*) AS n, max(rss) AS rss
                   FROM apps WHERE app = :app AND ts BETWEEN :start AND :end GROUP BY t ORDER BY t""",
                {"step": step, "app": app, "start": start, "end": end},
            ).fetchall()
            out[app] = [{"t": r["t"], "cpu": r["cpu"] / max(r["n"], (counts or {}).get(r["t"], r["n"])), "rss": r["rss"]} for r in rows]
        return out

    def bucket_apps(self, start: float, end: float, step: float) -> list[dict[str, Any]]:
        """Total CPU per app per bucket (summed over samples), for every recorded app."""
        rows = self.db.execute(
            """SELECT CAST(ts / :step AS INTEGER) * :step AS t, app, sum(cpu) AS cpu_sum, max(top_name) AS top_name
               FROM apps WHERE ts BETWEEN :start AND :end GROUP BY t, app""",
            {"step": step, "start": start, "end": end},
        ).fetchall()
        return [dict(r) for r in rows]

    def events(self, start: float, end: float, kinds: Iterable[str] | None = None, min_severity: int = 0) -> list[Event]:
        sql = "SELECT * FROM events WHERE COALESCE(end_ts, ts) >= ? AND ts <= ? AND severity >= ?"
        args: list[Any] = [start, end, min_severity]
        if kinds is not None:
            kinds = list(kinds)
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        rows = self.db.execute(sql + " ORDER BY ts", args).fetchall()
        return [self._event(r) for r in rows]

    def event(self, event_id: int) -> Event | None:
        r = self.db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._event(r) if r else None

    def incidents(self, limit: int = 50) -> list[Event]:
        kinds = list(INCIDENT_KINDS)
        rows = self.db.execute(
            f"SELECT * FROM events WHERE kind IN ({','.join('?' * len(kinds))}) ORDER BY ts DESC LIMIT ?", (*kinds, limit)
        ).fetchall()
        return [self._event(r) for r in rows]

    @staticmethod
    def _event(r: sqlite3.Row) -> Event:
        return Event(
            ts=r["ts"], end_ts=r["end_ts"], kind=r["kind"], app=r["app"], severity=r["severity"],
            title=r["title"], detail=json.loads(r["detail"] or "{}"), source=r["source"], id=r["id"],
        )
