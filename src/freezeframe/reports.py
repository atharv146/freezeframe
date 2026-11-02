"""Imports the problem reports the operating system already writes.

macOS:   ~/Library/Logs/DiagnosticReports and /Library/Logs/DiagnosticReports
         (.ips crashes and panics, .hang/.spin hangs, .diag resource reports)
Windows: the Application and System event logs (application hangs and
         crashes, WHEA hardware errors, graphics driver resets, disk errors,
         unexpected restarts, low-memory warnings)
Linux:   the kernel journal (out-of-memory kills, soft lockups, hung tasks,
         machine-check hardware errors, GPU hangs)

Each report becomes an Event; its file path or log record id is the event's
`source`, so importing the same report twice is harmless.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .apps import app_name
from .models import Event

MAC_DIRS = [Path.home() / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports")]
MAC_SUFFIXES = {".ips", ".hang", ".spin", ".diag", ".panic", ".crash", ".shutdownStall"}


def fmt_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def parse_time(text: str) -> float | None:
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


_FILE_TIME = re.compile(r"_(\d{4}-\d{2}-\d{2}-\d{6})")


def time_from_filename(name: str) -> float | None:
    m = _FILE_TIME.search(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d-%H%M%S").timestamp()


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    m = re.match(r"\s*([\d.]+)\s*s", value)
    return float(m.group(1)) if m else None


# ------------------------------------------------------------------ macOS

def parse_text_header(text: str) -> dict[str, str]:
    """Reads the 'Key:   value' header lines of a spindump-style report."""
    fields: dict[str, str] = {}
    for line in text.splitlines()[:120]:
        m = re.match(r"^([A-Za-z][A-Za-z /()_-]{1,40}):\s+(.*)$", line)
        if m and m.group(1) not in fields:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def parse_mac_report(path: Path, text: str | None = None) -> Event | None:
    suffix = path.suffix
    if suffix not in MAC_SUFFIXES:
        return None
    if text is None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(200_000)
        except OSError:
            return None
    source = str(path)
    if suffix == ".shutdownStall":
        ts = time_from_filename(path.name) or path.stat().st_mtime
        return Event(ts, "shutdown", "Shutting down took unusually long", 1, source=source,
                     detail={"report": source, "explain": "macOS recorded a stall while shutting down; an app or service did not quit in time."})
    if suffix == ".ips":
        return _parse_ips(path, text)
    f = parse_text_header(text)
    if not (f.get("Command") or f.get("Process")) and suffix == ".diag":
        return None  # whole-system daily statistics, not about any one app
    ts = parse_time(f.get("Date/Time", "")) or time_from_filename(path.name) or path.stat().st_mtime
    end = parse_time(f.get("End time", ""))
    command = f.get("Command") or f.get("Process", "").split("[")[0].strip() or path.name.split("_")[0]
    app = app_name(f["Path"]) if f.get("Path") and ".app/" in f["Path"] else app_name(command)
    event_kind = f.get("Event", "").lower()
    duration = _seconds(f.get("Duration"))
    detail = {"report": source, "fields": {k: f[k] for k in list(f)[:30]}}
    if suffix in (".hang", ".spin") or event_kind == "hang":
        dur = f" for {fmt_duration(duration)}" if duration else ""
        return Event(ts, "hang", f"{app} stopped responding{dur}", 2, end_ts=end, app=app, source=source, detail=detail)
    if suffix == ".panic":
        return Event(ts, "panic", "The Mac restarted after a kernel panic", 3, source=source, detail=detail)
    if event_kind == "cpu usage":
        m = re.search(r"\((\d+)% cpu average\)", f.get("CPU", ""))
        avg = f" ({m.group(1)}% of a core)" if m else ""
        dur = f" for {fmt_duration(duration)}" if duration else ""
        return Event(ts, "resource", f"macOS flagged {command} for heavy CPU use{avg}{dur}", 1, end_ts=end, app=app, source=source, detail=detail)
    if event_kind == "disk writes":
        m = re.match(r"([\d.]+ [KMGT]?B)", f.get("Writes", ""))
        amount = f" ({m.group(1)})" if m else ""
        return Event(ts, "resource", f"macOS flagged {command} for heavy disk writing{amount}", 0, end_ts=end, app=app, source=source, detail=detail)
    if event_kind == "wakeups":
        return Event(ts, "resource", f"macOS flagged {command} for waking the CPU too often", 0, end_ts=end, app=app, source=source, detail=detail)
    if event_kind:
        return Event(ts, "resource", f"macOS report for {command}: {event_kind}", 0, end_ts=end, app=app, source=source, detail=detail)
    return None


def _parse_ips(path: Path, text: str) -> Event | None:
    first, _, rest = text.partition("\n")
    try:
        header = json.loads(first)
    except json.JSONDecodeError:
        return None
    try:
        body = json.loads(rest) if rest.strip() else {}
    except json.JSONDecodeError:
        body = {}
    ts = parse_time(str(header.get("timestamp", ""))) or time_from_filename(path.name) or path.stat().st_mtime
    bug = str(header.get("bug_type", ""))
    proc = header.get("app_name") or header.get("name") or body.get("procName") or path.name.split("-")[0]
    app = app_name(body.get("procPath", "")) if ".app/" in str(body.get("procPath", "")) else app_name(str(proc))
    source = str(path)
    if bug == "210" or "panicString" in body or "panic" in path.name.lower():
        panic = str(body.get("panicString", "")).strip().splitlines()[:1]
        return Event(ts, "panic", "The Mac restarted after a kernel panic", 3, source=source,
                     detail={"report": source, "panic": panic[0][:300] if panic else ""})
    exc = body.get("exception") or {}
    if exc or bug in ("309", "109"):
        reason = exc.get("type", "")
        if exc.get("signal"):
            reason += f" ({exc['signal']})"
        term = (body.get("termination") or {}).get("indicator", "")
        return Event(ts, "crash", f"{app} crashed" + (f": {reason}" if reason else ""), 2, app=app, source=source,
                     detail={"report": source, "exception": reason, "termination": term})
    if bug in ("228", "211", "409"):
        return Event(ts, "hang", f"{app} stopped responding", 2, app=app, source=source, detail={"report": source})
    return None


def scan_macos(since: float, dirs: Iterable[Path] = MAC_DIRS) -> list[Event]:
    events = []
    for d in dirs:
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if p.suffix not in MAC_SUFFIXES or p.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            e = parse_mac_report(p)
            if e is not None:
                events.append(e)
    return events


# ---------------------------------------------------------------- Windows

WINDOWS_QUERY = r"""
$since = (Get-Date).AddSeconds(-{seconds});
$app = Get-WinEvent -FilterHashtable @{{LogName='Application'; Id=1000,1002; StartTime=$since}} -ErrorAction SilentlyContinue;
$sys = Get-WinEvent -FilterHashtable @{{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger','Microsoft-Windows-Kernel-Power','Display','disk','Ntfs','Microsoft-Windows-Resource-Exhaustion-Detector','Microsoft-Windows-WER-SystemErrorReporting','nvlddmkm','amdkmdag','igfx'; StartTime=$since}} -ErrorAction SilentlyContinue;
@($app) + @($sys) | Where-Object {{ $_ }} | Select-Object @{{n='Time';e={{$_.TimeCreated.ToUniversalTime().ToString('o')}}}}, Id, ProviderName, LogName, RecordId, @{{n='Message';e={{ (($_.Message -split "`n") | Select-Object -First 4) -join ' ' }}}} | ConvertTo-Json -Compress
"""


def _iso(ts: str) -> float | None:
    """ISO 8601 from PowerShell, e.g. 2026-10-30T14:02:11.1234567Z (7 fractional digits)."""
    text = ts.strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def parse_windows_events(raw: str) -> list[Event]:
    if not raw.strip():
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    out = []
    for r in data:
        ts = _iso(str(r.get("Time", "")))
        if ts is None:
            continue
        rid, prov, msg = int(r.get("Id", 0)), str(r.get("ProviderName", "")), str(r.get("Message") or "")
        source = f"win:{r.get('LogName')}:{r.get('RecordId')}:{rid}"
        detail = {"provider": prov, "event_id": rid, "message": msg[:600]}
        e: Event | None = None
        if prov == "Application Hang" or rid == 1002:
            m = re.search(r"program (.+?\.exe)", msg, re.IGNORECASE)
            app = app_name(m.group(1)) if m else "An app"
            e = Event(ts, "hang", f"{app} stopped responding and was closed", 2, app=app if m else None, source=source, detail=detail)
        elif prov == "Application Error" or rid == 1000:
            m = re.search(r"Faulting application name:\s*([^,]+)", msg, re.IGNORECASE)
            app = app_name(m.group(1).strip()) if m else "An app"
            e = Event(ts, "crash", f"{app} crashed", 2, app=app if m else None, source=source, detail=detail)
        elif "WHEA" in prov:
            fatal = rid in (1, 18, 20, 46)
            e = Event(ts, "hardware", "Your hardware reported a " + ("fatal" if fatal else "corrected") + " error (WHEA)", 3 if fatal else 2, source=source, detail=detail)
        elif prov == "Microsoft-Windows-Kernel-Power" and rid == 41:
            e = Event(ts, "shutdown", "Windows restarted without shutting down cleanly", 3, source=source,
                      detail={**detail, "explain": "This is logged after a hard freeze, a blue screen, holding the power button, or a power loss."})
        elif prov in ("Display", "nvlddmkm", "amdkmdag", "igfx") or rid == 4101:
            e = Event(ts, "hardware", "The graphics driver stopped responding and was restarted", 2, source=source, detail=detail)
        elif prov in ("disk", "Ntfs") and rid in (7, 11, 51, 55, 153, 157):
            e = Event(ts, "hardware", "Windows logged a disk error", 3, source=source, detail=detail)
        elif prov == "Microsoft-Windows-Resource-Exhaustion-Detector":
            e = Event(ts, "pressure", "Windows ran low on memory", 2, source=source, detail=detail)
        elif prov == "Microsoft-Windows-WER-SystemErrorReporting" or rid == 1001:
            e = Event(ts, "panic", "Windows recovered from a blue screen (bug check)", 3, source=source, detail=detail)
        if e is not None:
            out.append(e)
    return out


def scan_windows(since: float) -> list[Event]:
    seconds = max(60, int(time.time() - since))
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_QUERY.format(seconds=seconds)],
            capture_output=True, text=True, timeout=60,
        )
        return parse_windows_events(res.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


# ------------------------------------------------------------------ Linux

LINUX_PATTERNS: list[tuple[re.Pattern[str], str, int, str]] = [
    (re.compile(r"Out of memory: Killed process \d+ \(([^)]+)\)"), "pressure", 3, "Linux ran out of memory and killed {0}"),
    (re.compile(r"oom-kill:.*task=([^,\s]+)"), "pressure", 3, "Linux ran out of memory and killed {0}"),
    (re.compile(r"watchdog: BUG: soft lockup"), "stall", 3, "The kernel detected a CPU lockup"),
    (re.compile(r"task (\S+?):\d+ blocked for more than (\d+) seconds"), "stall", 2, "{0} was stuck waiting for {1} s (hung task)"),
    (re.compile(r"\[Hardware Error\]|Machine check events logged|mce: "), "hardware", 3, "The CPU reported a hardware error (machine check)"),
    (re.compile(r"GPU HANG|gpu hang|amdgpu.*ring .* timeout|NVRM: Xid"), "hardware", 2, "The graphics driver reported a GPU hang"),
    (re.compile(r"I/O error, dev (\S+)"), "hardware", 3, "Disk I/O error on {0}"),
]


def parse_journal(lines: Iterable[str]) -> list[Event]:
    out = []
    for line in lines:
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        msg = parts[1]
        for pattern, kind, sev, title in LINUX_PATTERNS:
            m = pattern.search(msg)
            if m:
                app = m.group(1) if m.groups() and kind == "pressure" else None
                out.append(Event(ts, kind, title.format(*m.groups()), sev, app=app,
                                 source=f"journal:{ts}:{abs(hash(msg)) % 10**9}", detail={"message": msg[:600]}))
                break
    return out


def scan_linux(since: float) -> list[Event]:
    try:
        res = subprocess.run(
            ["journalctl", "-k", "--no-pager", "-o", "short-unix", f"--since=@{int(since)}"],
            capture_output=True, text=True, timeout=30,
        )
        return parse_journal(res.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return []


def scan(since: float) -> list[Event]:
    if sys.platform == "darwin":
        return scan_macos(since)
    if sys.platform == "win32":
        return scan_windows(since)
    return scan_linux(since)
