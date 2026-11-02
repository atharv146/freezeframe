"""Command line: freezeframe record | why | list | mark | open | install | demo ..."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from . import __version__
from .detect import cores, human_bytes
from .diagnose import diagnose
from .paths import db_path, log_path
from .reports import fmt_duration
from .store import Store

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if USE_COLOR else text


bold = lambda t: _c("1", t)  # noqa: E731
dim = lambda t: _c("2", t)  # noqa: E731
red = lambda t: _c("31", t)  # noqa: E731
green = lambda t: _c("32", t)  # noqa: E731


def when(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    day = "Today" if d.date() == today else "Yesterday" if d.date() == today - timedelta(days=1) else d.strftime("%a %b %-d") if os.name != "nt" else d.strftime("%a %b %d")
    return f"{day} {d.strftime('%I:%M:%S %p').lstrip('0')}"


def parse_moment(text: str) -> float:
    """'15:42', '3:42pm', '10m', '2h', '90s' (ago) or an ISO date-time."""
    text = text.strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr)s?(?:\s*ago)?", text)
    if m:
        mult = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600}[m.group(2)]
        return time.time() - float(m.group(1)) * mult
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?", text)
    if m:
        hour, minute, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if m.group(4) == "pm" and hour < 12:
            hour += 12
        if m.group(4) == "am" and hour == 12:
            hour = 0
        t = datetime.now().replace(hour=hour, minute=minute, second=sec, microsecond=0)
        if t > datetime.now():
            t -= timedelta(days=1)
        return t.timestamp()
    return datetime.fromisoformat(text).timestamp()


def wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text, width=min(92, os.get_terminal_size().columns - 2) if sys.stdout.isatty() else 92,
                         initial_indent=indent, subsequent_indent=indent)


def print_diagnosis(d: dict[str, Any], title: str | None = None) -> None:
    conf = {"high": green("high confidence"), "medium": "medium confidence", "low": dim("low confidence")}[d["confidence"]]
    top = d["findings"][0]
    print()
    print(bold(title or d["headline"]))
    print(dim(f"{when(d['start'])}" + (f", lasting {fmt_duration(d['end'] - d['start'])}" if d["end"] - d["start"] >= 1 else "")))
    print()
    print("  " + bold(top["headline"]) + ("  " + conf if top["score"] > 0 else ""))
    print(wrap(top["explanation"]))
    if top["evidence"]:
        print()
        print(bold("  What the recording shows"))
        for e in top["evidence"]:
            print(wrap("- " + e, "    ").replace("    - ", "  - ", 1))
    if top["actions"]:
        print()
        print(bold("  What you can do"))
        for i, a in enumerate(top["actions"], 1):
            print(wrap(f"{i}. {a}", "     ").replace("     ", "  ", 1))
    others = d["findings"][1:]
    if others:
        print()
        print(bold("  Also noticed"))
        for f in others:
            print(f"  - {f['headline']} {dim('(' + ('likely' if f['score'] >= 0.5 else 'possible') + ')')}")
    if d["culprits"]:
        print()
        print(bold("  Busiest apps in the 2 minutes before"))
        print(dim(f"  {'App':<28} {'Memory':>9}  {'Change':<10} {'CPU':>16}"))
        for a in d["culprits"][:5]:
            growth = f"{'+' if a['growth'] >= 0 else '-'}{human_bytes(abs(a['growth']))}" if abs(a["growth"]) >= 100 * 1024**2 else ""
            print(f"  {a['app'][:28]:<28} {human_bytes(a['rss']):>9}  {growth:<10} {cores(a['cpu_avg']):>16}")
    print()


# ---------------------------------------------------------------- commands

def cmd_record(args: argparse.Namespace) -> int:
    from .recorder import Recorder, install_signal_handlers

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler()] if not args.quiet else [logging.FileHandler(log_path())])
    store = Store(db_path())
    meta = store.meta()
    other = (meta.get("recorder") or {}).get("pid")
    if other and other != os.getpid() and not meta.get("clean_exit", True):
        import psutil
        if psutil.pid_exists(other):
            try:
                cmdline = " ".join(psutil.Process(other).cmdline())
            except Exception:
                cmdline = ""
            if "freezeframe" in cmdline:
                print(f"Freezeframe is already recording (process {other}).")
                return 1
    stop = threading.Event()
    install_signal_handlers(stop)
    if not args.quiet:
        print(f"Recording every {args.interval:g} s to {db_path()}. Press Ctrl+C to stop.")
    Recorder(store, interval=args.interval).run(stop)
    if not args.quiet:
        print("\nStopped.")
    return 0


def _open_store() -> Store | None:
    if not db_path().exists():
        print("There's no recording yet. Start one with  freezeframe record  (or  freezeframe install  to run it in the background).")
        return None
    return Store(db_path())


def cmd_why(args: argparse.Namespace) -> int:
    store = _open_store()
    if store is None:
        return 1
    if args.at:
        t = parse_moment(args.at)
        d = diagnose(store, t, t, None, store.platform(sys.platform))
        print_diagnosis(d, f"What happened around {when(t)}: {d['short']}")
        return 0
    recent = [e for e in store.incidents(5) if time.time() - (e.end_ts or e.ts) < args.within * 60]
    if recent:
        e = recent[0]
        d = (e.detail or {}).get("diagnosis") if args.cached else None
        d = d or diagnose(store, e.ts, e.end_ts, e, store.platform(sys.platform))
        print_diagnosis(d, e.title)
        return 0
    now = time.time()
    d = diagnose(store, now, now, None, store.platform(sys.platform))
    print_diagnosis(d, f"The last 2 minutes: {d['short']}")
    if d["findings"][0]["cause"] in ("none", "quiet"):
        print(dim("  No freeze was detected recently. To look at an earlier moment:  freezeframe why --at 3:42pm\n"))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = _open_store()
    if store is None:
        return 1
    incidents = store.incidents(args.limit)
    if not incidents:
        print("No incidents recorded yet. That's good news.")
        return 0
    for e in incidents:
        sev = red("!") if e.severity >= 3 else "-"
        print(f" {sev} {dim(when(e.ts)):<28} {e.title}  {dim('#' + str(e.id))}")
    print(dim("\n  Details:  freezeframe show <number>"))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = _open_store()
    if store is None:
        return 1
    e = store.event(args.id)
    if e is None:
        print(f"No incident #{args.id}.")
        return 1
    d = diagnose(store, e.ts, e.end_ts, e, store.platform(sys.platform))
    print_diagnosis(d, e.title)
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    from .recorder import mark

    store = _open_store()
    if store is None:
        return 1
    e = mark(store, " ".join(args.note), args.ago)
    print_diagnosis(e.detail["diagnosis"], e.title)
    return 0


def cmd_status(args: argparse.Namespace) -> int:  # noqa: ARG001
    from . import autostart

    if not db_path().exists():
        print("Not recording yet. Run  freezeframe install  to record in the background.")
        return 0
    store = Store(db_path())
    first, last = store.bounds()
    meta = store.meta()
    rec = meta.get("recorder") or {}
    live = last is not None and time.time() - last < 3 * float(rec.get("interval", 2)) + 5
    print(bold("Recording: ") + (green("running") if live else red("stopped")) + dim(f"  (background login item {'installed' if autostart.is_installed() else 'not installed'})"))
    if first:
        print(f"Covers:    {when(first)}  to  {when(last)}")
    size = db_path().stat().st_size + sum(p.stat().st_size for p in db_path().parent.glob("recording.db-*"))
    print(f"Storage:   {human_bytes(size)} at {db_path()}")
    series = store.series(time.time() - 600, time.time(), 600)
    if series and series[-1].get("self_cpu") is not None:
        print(f"Overhead:  {series[-1]['self_cpu']:.1f}% of one CPU core")
    print(f"Incidents: {len(store.incidents(1000))}")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    from .server import serve

    if args.demo:
        from .demo import build_demo
        path = build_demo()
    else:
        path = db_path()
        if not path.exists():
            Store(path).close()
    serve(path, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import build_demo

    path = build_demo(args.path)
    print(f"Wrote a demo recording with realistic incidents to {path}")
    print("Explore it with  freezeframe open --demo")
    return 0


def cmd_install(args: argparse.Namespace) -> int:  # noqa: ARG001
    from . import autostart

    print(autostart.install())
    print("Open the dashboard any time with  freezeframe open  or ask  freezeframe why  after a freeze.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:  # noqa: ARG001
    from . import autostart

    print(autostart.uninstall())
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    if not args.yes:
        print(f"This deletes the whole recording at {db_path()}. Run again with --yes to confirm.")
        return 1
    for p in db_path().parent.glob("recording.db*"):
        p.unlink()
    print("Deleted the recording.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="freezeframe", description="A flight recorder for your computer: find out why it froze.")
    p.add_argument("--version", action="version", version=f"freezeframe {__version__}")
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("record", help="record in the foreground (install does this in the background)")
    r.add_argument("--interval", type=float, default=2.0, help="seconds between samples (default 2)")
    r.add_argument("--quiet", action="store_true", help="log to a file instead of the terminal")
    r.set_defaults(func=cmd_record)

    w = sub.add_parser("why", help="explain the latest freeze, or a moment you choose")
    w.add_argument("--at", help="a moment to explain: 3:42pm, 15:42, 10m (ago), or an ISO time")
    w.add_argument("--within", type=float, default=30, help="look for incidents in the last N minutes (default 30)")
    w.add_argument("--cached", action="store_true", help=argparse.SUPPRESS)
    w.set_defaults(func=cmd_why)

    ls = sub.add_parser("list", help="list recorded incidents")
    ls.add_argument("--limit", type=int, default=20)
    ls.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="explain one incident from the list")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_show)

    m = sub.add_parser("mark", help="note that something just went wrong and explain it")
    m.add_argument("note", nargs="*", help="optional description, e.g. zoom froze")
    m.add_argument("--ago", type=float, default=0, help="it happened N minutes ago")
    m.set_defaults(func=cmd_mark)

    o = sub.add_parser("open", help="open the timeline dashboard in your browser")
    o.add_argument("--port", type=int, default=0)
    o.add_argument("--no-browser", action="store_true")
    o.add_argument("--demo", action="store_true", help="explore a demo recording instead of yours")
    o.set_defaults(func=cmd_open)

    st = sub.add_parser("status", help="is the recorder running, and how much does it store?")
    st.set_defaults(func=cmd_status)

    d = sub.add_parser("demo", help="generate a demo recording")
    d.add_argument("--path", default=None)
    d.set_defaults(func=cmd_demo)

    sub.add_parser("install", help="record in the background, starting at login").set_defaults(func=cmd_install)
    sub.add_parser("uninstall", help="stop recording in the background").set_defaults(func=cmd_uninstall)
    f = sub.add_parser("forget", help="delete the recording")
    f.add_argument("--yes", action="store_true")
    f.set_defaults(func=cmd_forget)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
