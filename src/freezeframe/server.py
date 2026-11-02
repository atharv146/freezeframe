"""The dashboard: a small local web server and a JSON API over the recording.

It only listens on 127.0.0.1 and rejects requests whose Host or Origin isn't
itself, so other websites can't read your recording through it.
"""

from __future__ import annotations

import json
import mimetypes
import sys
import threading
import time
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .catalog import Catalog
from .diagnose import diagnose
from .models import INCIDENT_KINDS, Event
from .storage import StorageScan
from .store import Store

STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}


def _event_json(e: Event, with_diagnosis: bool = False) -> dict[str, Any]:
    d = asdict(e)
    diag = (e.detail or {}).get("diagnosis")
    d["detail"] = {k: v for k, v in (e.detail or {}).items() if k != "diagnosis"}
    if diag:
        d["short"] = diag.get("short")
        d["confidence"] = diag.get("confidence")
        if with_diagnosis:
            d["diagnosis"] = diag
    d["incident"] = e.kind in INCIDENT_KINDS
    return d


class Api:
    def __init__(self, db: Path, platform: str | None = None):
        self.db = db
        self.platform = platform
        self._machine: tuple[float, dict[str, Any]] | None = None
        self._catalog: Catalog | None = None
        self._hardware: tuple[float, dict[str, Any]] | None = None
        self.storage = StorageScan()

    def catalog(self, s: Store) -> Catalog:
        if self._catalog is None:
            # Demo recordings describe made-up apps: don't look them up on this computer.
            self._catalog = Catalog(self.platform or s.platform(sys.platform), use_details=not s.meta().get("demo"))
        return self._catalog

    def machine(self, s: Store) -> dict[str, Any]:
        """This computer's facts, or the recording's own for demo recordings."""
        meta = s.meta()
        if meta.get("demo") and meta.get("machine"):
            return meta["machine"]
        if self._machine is None or time.time() - self._machine[0] > 60:
            from .insights import machine

            self._machine = (time.time(), machine())
        return self._machine[1]

    def now(self) -> dict[str, Any]:
        from .insights import insights, snapshot

        s = self._store()
        try:
            info = self.machine(s)
            catalog = self.catalog(s)
            snap = snapshot(s, info, catalog, None if s.meta().get("demo") else self.db)
            return {**snap, "insights": [asdict(i) for i in insights(s, info, snap=snap)], "ai": catalog.ai_status()}
        finally:
            s.close()

    def _store(self) -> Store:
        return Store(self.db)

    def overview(self) -> dict[str, Any]:
        s = self._store()
        try:
            first, last = s.bounds()
            meta = s.meta()
            rec = meta.get("recorder") or {}
            platform = self.platform or meta.get("platform") or rec.get("platform") or sys.platform
            live = last is not None and time.time() - last < 3 * float(rec.get("interval", 2)) + 5
            return {
                "first": first, "last": last, "now": time.time(), "live": live, "platform": platform,
                "recorder": rec, "demo": bool(meta.get("demo")),
                "incidents": [_event_json(e) for e in s.incidents(200)],
            }
        finally:
            s.close()

    def timeline(self, start: float, end: float, points: int) -> dict[str, Any]:
        s = self._store()
        try:
            step = max(1.0, (end - start) / max(50, min(points, 2000)))
            series = s.series(start, end, step)
            counts = {p["t"]: p["n"] for p in series}
            apps = s.top_apps(start, end, 12)
            names = [a["app"] for a in apps]  # the dashboard picks which of these get a colour
            per_app = s.app_series(start, end, step, names, counts)
            ncores = (s.meta().get("recorder") or {}).get("cpus") or 8
            # Every recorded app, sorted into "your apps" and "background and system", per bucket.
            rows = s.bucket_apps(start, end, step)
            top_names = {r["app"]: r["top_name"] for r in rows}
            described = self.catalog(s).describe(s, list(top_names.items()))
            kinds = {a: d["kind"] for a, d in described.items()}
            by_t: dict[float, dict[str, Any]] = {}
            for r in rows:
                b = by_t.setdefault(r["t"], {"t": r["t"], "apps": 0.0, "background": 0.0, "top": None, "top_cpu": 0.0})
                cpu = r["cpu_sum"] / max(1, counts.get(r["t"], 1))
                b["apps" if kinds.get(r["app"]) == "app" else "background"] += cpu / ncores
                if cpu > b["top_cpu"]:
                    b["top"], b["top_cpu"] = r["app"], cpu
            kind_series = [{**b, "top_cpu": b["top_cpu"] / ncores} for _, b in sorted(by_t.items())]
            events = [_event_json(e) for e in s.events(start, end)]
            return {"start": start, "end": end, "step": step, "series": series, "apps": apps, "app_series": per_app,
                    "kinds": kinds, "kind_series": kind_series, "cpus": ncores, "events": events}
        finally:
            s.close()

    def strip(self, points: int = 360) -> dict[str, Any]:
        """The whole recording, coarsely: for the overview strip above the timeline."""
        s = self._store()
        try:
            first, last = s.bounds()
            if first is None:
                return {"first": None, "last": None, "series": []}
            step = max(10.0, (last - first) / max(50, min(points, 1000)))
            series = [{"t": p["t"], "cpu": p["cpu_max"], "mem": 1 - p["mem_available"] / p["mem_total"] if p["mem_total"] else 0,
                       "pressure": p["pressure"]} for p in s.series(first, last, step)]
            return {"first": first, "last": last, "step": step, "series": series}
        finally:
            s.close()

    def what(self, q: dict[str, str]) -> dict[str, Any]:
        from .what import what_happened
        from .when import NotUnderstood, Window, parse

        s = self._store()
        try:
            platform = self.platform or s.platform(sys.platform)
            if "start" in q:
                window = Window(float(q["start"]), float(q["end"]), q.get("label") or "in this stretch")
            else:
                try:
                    window = parse(q.get("q", ""))
                except NotUnderstood:
                    raise ValueError("Freezeframe didn't understand that time. Try something like 3:40pm, 20 minutes ago, or yesterday evening.") from None
            return what_happened(s, window, platform)
        finally:
            s.close()

    def hardware(self) -> dict[str, Any]:
        s = self._store()
        try:
            meta = s.meta()
        finally:
            s.close()
        if meta.get("demo") and meta.get("hardware"):
            return meta["hardware"]
        from .hardware import hardware

        if self._hardware is None or time.time() - self._hardware[0] > 600:
            self._hardware = (time.time(), hardware())
        return self._hardware[1]

    def storage_status(self, start: bool = False, private: bool = False) -> dict[str, Any]:
        s = self._store()
        try:
            meta = s.meta()
        finally:
            s.close()
        if meta.get("demo") and meta.get("storage"):
            return meta["storage"]
        if start:
            self.storage.start(private)
        return self.storage.status()

    def set_ai_key(self, body: dict[str, Any]) -> dict[str, Any]:
        from . import ai

        key = str(body.get("key") or "").strip()
        if body.get("forget"):
            ai.forget_key()
        elif not key.startswith("sk-ant-") or len(key) > 300:
            raise ValueError("That doesn't look like an Anthropic API key (they start with sk-ant-).")
        else:
            ai.save_key(key)
        if self._catalog is not None:
            self._catalog.ai_error = None
            self._catalog._ai_tried.clear()
        return ai.status()

    def diagnose(self, q: dict[str, str]) -> dict[str, Any]:
        s = self._store()
        try:
            platform = self.platform or s.platform(sys.platform)
            if "id" in q:
                e = s.event(int(q["id"]))
                if e is None:
                    raise KeyError("no such incident")
                d = diagnose(s, e.ts, e.end_ts, e, platform)
                return {"event": _event_json(e), "diagnosis": d}
            t = float(q["at"])
            d = diagnose(s, t, float(q.get("end", t)), None, platform)
            return {"event": None, "diagnosis": d}
        finally:
            s.close()

    def mark(self, body: dict[str, Any]) -> dict[str, Any]:
        from .recorder import mark

        s = self._store()
        try:
            minutes_ago = max(0.0, (time.time() - float(body["ts"])) / 60) if body.get("ts") else 0.0
            e = mark(s, str(body.get("note", ""))[:200], minutes_ago)
            return {"event": _event_json(e, with_diagnosis=True)}
        finally:
            s.close()


def make_handler(api: Api, port_ref: list[int]):
    web = resources.files("freezeframe") / "web"

    class Handler(BaseHTTPRequestHandler):
        server_version = "Freezeframe"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def _allowed(self) -> bool:
            port = port_ref[0]
            ok_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in ok_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://{h}" for h in ok_hosts}

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802
            if not self._allowed():
                return self._send(403, b"forbidden", "text/plain")
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in STATIC:
                    name = STATIC[url.path]
                    data = (web / name).read_bytes()
                    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    if name.endswith(".js"):
                        ctype = "text/javascript"
                    return self._send(200, data, ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
                if url.path == "/api/overview":
                    return self._json(api.overview())
                if url.path == "/api/timeline":
                    return self._json(api.timeline(float(q["start"]), float(q["end"]), int(q.get("points", 800))))
                if url.path == "/api/diagnose":
                    return self._json(api.diagnose(q))
                if url.path == "/api/now":
                    return self._json(api.now())
                if url.path == "/api/strip":
                    return self._json(api.strip(int(q.get("points", 360))))
                if url.path == "/api/what":
                    return self._json(api.what(q))
                if url.path == "/api/hardware":
                    return self._json(api.hardware())
                if url.path == "/api/storage":
                    return self._json(api.storage_status())
                return self._send(404, b"not found", "text/plain")
            except (KeyError, ValueError) as e:
                return self._json({"error": str(e)}, 400)

        def do_POST(self) -> None:  # noqa: N802
            if not self._allowed() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._send(403, b"forbidden", "text/plain")
            length = min(int(self.headers.get("Content-Length", 0)), 10_000)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/mark":
                    return self._json(api.mark(body))
                if path == "/api/ai":
                    return self._json(api.set_ai_key(body))
                if path == "/api/storage":
                    return self._json(api.storage_status(start=True, private=bool(body.get("private"))))
                if path == "/api/reveal":
                    return self._json(api.storage.reveal(str(body.get("path", ""))))
                return self._send(404, b"not found", "text/plain")
            except (KeyError, ValueError) as e:
                return self._json({"error": str(e)}, 400)

    return Handler


def start_server(db: Path, port: int = 0, platform: str | None = None) -> tuple[ThreadingHTTPServer, str]:
    port_ref = [port]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Api(db, platform), port_ref))
    port_ref[0] = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{port_ref[0]}/"


def serve(db: Path, port: int = 0, open_browser: bool = True) -> None:
    httpd, url = start_server(db, port)
    print(f"Freezeframe dashboard: {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
