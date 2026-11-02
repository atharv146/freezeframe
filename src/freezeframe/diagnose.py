"""Explains an incident in plain English.

Given a moment ("it froze at 3:42", or a stall the recorder caught), this
looks at the two minutes before it and scores each possible cause from the
evidence: memory exhaustion, CPU saturation, an overloaded disk, overheating,
an app hanging on its own, or a hardware/driver error the OS logged. Every
verdict comes with the numbers behind it and what to do about it, and when
nothing was busy it says so, because a calm machine that still froze points
at drivers or hardware rather than apps.
"""

from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .detect import GB, MB, cores, human_bytes, human_rate
from .knowledge import BROWSERS, lookup
from .models import AppUsage, Event, Sample
from .reports import fmt_duration
from .store import Store

LOOKBACK = 120.0


@dataclass
class Finding:
    cause: str
    score: float
    headline: str
    short: str
    explanation: str
    evidence: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


@dataclass
class AppStats:
    app: str
    cpu_avg: float = 0.0
    cpu_peak: float = 0.0
    rss_first: int = 0
    rss_last: int = 0
    rss_max: int = 0
    io_peak: float = 0.0
    nproc: int = 0
    top_name: str = ""

    @property
    def growth(self) -> int:
        return self.rss_last - self.rss_first


def _device(platform: str) -> str:
    return {"darwin": "your Mac", "win32": "your PC"}.get(platform, "your computer")


def _cap(text: str) -> str:
    if text.startswith("mac") or text.startswith("iCloud"):
        return text  # product names keep their own capitalisation
    return text[:1].upper() + text[1:]


def app_stats(rows: list[tuple[float, AppUsage]], n_samples: int) -> dict[str, AppStats]:
    out: dict[str, AppStats] = {}
    for _, a in rows:
        st = out.get(a.app)
        if st is None:
            st = out[a.app] = AppStats(a.app, rss_first=a.rss)
        st.cpu_avg += a.cpu / max(1, n_samples)
        st.cpu_peak = max(st.cpu_peak, a.cpu)
        st.rss_last = a.rss
        st.rss_max = max(st.rss_max, a.rss)
        st.io_peak = max(st.io_peak, (a.read_bps or 0) + (a.write_bps or 0))
        st.nproc = max(st.nproc, a.nproc)
        st.top_name = a.top_name or st.top_name
    return out


def _tip_actions(app: str, top_name: str = "") -> list[str]:
    info = lookup(app, top_name)
    return [info.tip] if info and info.tip else []


def _what(app: str, top_name: str = "") -> str:
    info = lookup(app, top_name)
    return f" ({_cap(info.what.rstrip('.'))}.)" if info else ""


def hardware_actions(platform: str) -> list[str]:
    if platform == "darwin":
        return [
            "Install any pending macOS updates (System Settings > General > Software Update).",
            "Unplug accessories such as USB hubs, docks, external drives and displays, and see whether the freezes stop.",
            "Run Apple Diagnostics: shut down, then press and hold the power button until startup options appear, then press Command-D (on Intel Macs, hold D while starting up).",
        ]
    if platform == "win32":
        return [
            "Update the graphics, chipset and storage drivers from your PC maker's or GPU maker's website.",
            "Run Windows Memory Diagnostic (search for it in the Start menu) to test the RAM.",
            "Check the drive: open Terminal as administrator and run  chkdsk C: /scan",
            "Check temperatures and clean dust out of the fans and vents.",
        ]
    return [
        "Update the kernel and graphics drivers.",
        "Test memory with memtest86+ and check drives with  smartctl -a /dev/<disk>",
    ]


def diagnose(store: Store, start: float, end: float | None = None, incident: Event | None = None,
             platform: str = sys.platform) -> dict[str, Any]:
    end = start if end is None else end
    w0, w1 = start - LOOKBACK, end + 5
    samples = store.samples(w0, w1)
    baseline = store.samples(start - 1800, w0)
    n = len(samples)
    stats = app_stats(store.app_rows(w0, w1), n)
    base_stats = app_stats(store.app_rows(start - 1800, w0), max(1, len(baseline)))
    related = [e for e in store.events(start - 300, end + 300) if not (incident and e.id == incident.id)
               and e.kind not in ("stall", "mark", "sleep", "gap")]
    device = _device(platform)
    findings: list[Finding] = []

    feats: dict[str, Any] = {"samples": n}
    if n >= 3:
        feats.update(_features(samples))
        findings += _memory(feats, stats, device, platform)
        findings += _cpu(feats, stats, base_stats, device)
        findings += _disk(feats, stats, related, findings)
        findings += _thermal(feats, stats, device)

    system_busy = max((f.score for f in findings), default=0.0)
    findings += _os_events(related, incident, system_busy, device, platform)

    if incident is not None and incident.kind in ("stall", "mark") and n >= 3 and max((f.score for f in findings), default=0) < 0.3:
        findings.append(_quiet(incident, device, platform))

    findings.sort(key=lambda f: f.score, reverse=True)
    findings = [f for f in findings if f.score >= 0.2]

    if n < 3 and not findings:
        findings.append(Finding(
            "no-data", 0.0, "Freezeframe wasn't recording at that time", "no recording",
            "There are no measurements from the two minutes before this moment, so the cause can't be worked out. "
            "Keep Freezeframe running in the background (freezeframe install) so the next freeze is captured.",
        ))
    if not findings:
        findings.append(Finding(
            "none", 0.0, "Nothing unusual was recorded", "nothing unusual",
            "Memory, CPU, disk and temperature all looked normal around this time.",
        ))

    top = findings[0]
    confidence = "high" if top.score >= 0.7 else "medium" if top.score >= 0.4 else "low"
    culprits = _culprits(stats, top.cause)
    return {
        "start": start,
        "end": end,
        "headline": top.headline,
        "short": top.short,
        "confidence": confidence,
        "findings": [asdict(f) for f in findings],
        "culprits": culprits,
        "related": [{"id": e.id, "ts": e.ts, "kind": e.kind, "title": e.title, "severity": e.severity, "app": e.app} for e in related],
        "stats": feats,
    }


def incident_title(incident: Event, diagnosis: dict[str, Any]) -> str:
    short = diagnosis["short"]
    if incident.kind == "stall":
        dur = (incident.end_ts or incident.ts) - incident.ts
        return f"Froze for {fmt_duration(dur)}: {short}"
    if incident.kind == "mark":
        note = incident.detail.get("note")
        return f"You reported {('“' + note + '”') if note else 'a problem'}: {short}"
    return incident.title


# ------------------------------------------------------------ features

def _features(samples: list[Sample]) -> dict[str, Any]:
    n = len(samples)
    return {
        "cpu_avg": sum(s.cpu for s in samples) / n,
        "cpu_peak": max(s.cpu for s in samples),
        "cpu_high_frac": sum(s.cpu >= 90 for s in samples) / n,
        "mem_total": samples[-1].mem_total,
        "avail_min_pct": min(s.mem_available_pct for s in samples),
        "avail_min": min(s.mem_available for s in samples),
        "pressure_max": max((s.pressure or 0) for s in samples),
        "swap_peak": max(s.swap_in + s.swap_out for s in samples),
        "swap_growth": samples[-1].swap_used - samples[0].swap_used,
        "disk_peak": max(max(s.disk_read, s.disk_write) for s in samples),
        "disk_high_frac": sum(max(s.disk_read, s.disk_write) >= 150 * MB for s in samples) / n,
        "thermal_max": max((s.thermal or 0) for s in samples),
        "longest_gap": max(s.interval for s in samples),
    }


# ------------------------------------------------------------- causes

def _memory(f: dict[str, Any], stats: dict[str, AppStats], device: str, platform: str) -> list[Finding]:
    score = 0.0
    ev: list[str] = []
    if f["pressure_max"] >= 4:
        score += 0.6
        ev.append("macOS reported critical memory pressure.")
    elif f["pressure_max"] >= 2:
        score += 0.3
        ev.append("macOS reported elevated memory pressure.")
    if f["avail_min_pct"] < 5:
        score += 0.3
    elif f["avail_min_pct"] < 10:
        score += 0.15
    if f["avail_min_pct"] < 15:
        ev.append(f"Free memory fell to {f['avail_min_pct']:.0f}% ({human_bytes(f['avail_min'])} of {human_bytes(f['mem_total'])}).")
    if f["swap_peak"] > 50 * MB:
        score += 0.3
    elif f["swap_peak"] > 10 * MB:
        score += 0.15
    if f["swap_peak"] > 10 * MB:
        ev.append(f"The system was swapping memory to disk at up to {human_rate(f['swap_peak'])}.")
    if f["swap_growth"] > 256 * MB:
        ev.append(f"{human_bytes(f['swap_growth'])} more memory was pushed out to swap.")
    score = min(score, 1.0)
    if score < 0.2:
        return []
    by_mem = sorted(stats.values(), key=lambda a: a.rss_max, reverse=True)
    growers = sorted((a for a in stats.values() if a.growth >= 300 * MB), key=lambda a: a.growth, reverse=True)
    actions: list[str] = []
    for a in (growers[:1] + by_mem[:2])[:2]:
        grew = f", up {human_bytes(a.growth)} in the last 2 minutes" if a.growth >= 300 * MB else ""
        ev.append(f"{a.app} was using {human_bytes(a.rss_max)}{grew}.")
    culprit = (growers or by_mem or [None])[0]
    if culprit is not None:
        actions.append(f"Quit or restart {culprit.app} to give its memory back.")
        if culprit.app.lower() in BROWSERS:
            actions += _tip_actions(culprit.app)
    actions.append("Close apps you aren't using. If this keeps happening, your usual set of apps needs more memory than this computer has "
                   f"({human_bytes(f['mem_total'])}).")
    short = f"memory ran out ({culprit.app} was the biggest user)" if culprit else "memory ran out"
    return [Finding(
        "memory", score, f"{_cap(device)} ran out of memory", short,
        "When memory fills up, the system starts parking data on the disk (swapping) and fetching it back when it's needed. "
        "Disks are far slower than memory, so apps, and sometimes the whole computer, stall while they wait.",
        ev, actions,
    )]


def _cpu(f: dict[str, Any], stats: dict[str, AppStats], base: dict[str, AppStats], device: str) -> list[Finding]:
    score = min(1.0, f["cpu_high_frac"] * 0.9 + (0.15 if f["cpu_peak"] >= 99 else 0))
    if score < 0.2:
        return []
    top = max(stats.values(), key=lambda a: a.cpu_avg, default=None)
    ev = [f"CPU use averaged {f['cpu_avg']:.0f}% and hit {f['cpu_peak']:.0f}% in the two minutes before."]
    actions: list[str] = []
    headline, short = "The CPU was maxed out", "the CPU was maxed out"
    if top is not None and top.cpu_avg >= 50:
        usual = base.get(top.app)
        usual_txt = f" (it normally uses about {cores(usual.cpu_avg)})" if usual and usual.cpu_avg < top.cpu_avg / 2 else ""
        ev.append(f"{top.app} used {cores(top.cpu_avg)} on average, peaking at {cores(top.cpu_peak)}{usual_txt}.{_what(top.app, top.top_name)}")
        headline, short = f"{top.app} was using most of the CPU", f"{top.app} maxed out the CPU"
        actions += _tip_actions(top.app, top.top_name)
        actions.append(f"If {top.app} isn't doing something you asked for, quit and reopen it.")
    actions.append("Look for things running in the background: updates, backups, indexing, or a stuck browser tab.")
    return [Finding(
        "cpu", score, headline, short,
        "When every core is busy, everything else waits its turn, so clicks, typing and the screen itself fall behind.",
        ev, actions,
    )]


def _disk(f: dict[str, Any], stats: dict[str, AppStats], related: list[Event], so_far: list[Finding]) -> list[Finding]:
    score = min(1.0, f["disk_high_frac"] * 0.8 + (0.2 if f["disk_peak"] >= 400 * MB else 0))
    memory = next((x for x in so_far if x.cause == "memory"), None)
    if memory and f["swap_peak"] >= 0.5 * f["disk_peak"]:
        score *= 0.4  # the disk was busy because of swapping, which the memory finding already explains
    if score < 0.2:
        return []
    ev = [f"Disk traffic reached {human_rate(f['disk_peak'])}."]
    io = sorted((a for a in stats.values() if a.io_peak > 0), key=lambda a: a.io_peak, reverse=True)
    actions: list[str] = []
    short = "the disk was overloaded"
    if io:
        ev.append(f"{io[0].app} was reading and writing up to {human_rate(io[0].io_peak)}.{_what(io[0].app, io[0].top_name)}")
        actions += _tip_actions(io[0].app, io[0].top_name)
        short = f"the disk was overloaded by {io[0].app}"
    else:
        # macOS doesn't expose per-app disk traffic without admin rights; name known disk-heavy services that were busy.
        busy = sorted((a for a in stats.values() if a.cpu_avg >= 30 and lookup(a.app, a.top_name)), key=lambda a: a.cpu_avg, reverse=True)
        if busy:
            a = busy[0]
            ev.append(f"{a.app} was busy at the same time ({cores(a.cpu_avg)}).{_what(a.app, a.top_name)}")
            actions += _tip_actions(a.app, a.top_name)
            short = f"the disk was overloaded ({a.app} was busy)"
    for e in related:
        if e.kind == "resource" and "disk" in e.title:
            ev.append(e.title + ".")
            break
    actions.append("Big downloads, backups, updates, file syncing and search indexing are the usual causes; let them finish or pause them.")
    return [Finding(
        "disk", score, "The disk was overloaded", short,
        "Apps freeze while they wait for the disk to load or save something. When another program is keeping the disk busy, that wait gets long.",
        ev, actions,
    )]


def _thermal(f: dict[str, Any], stats: dict[str, AppStats], device: str) -> list[Finding]:
    level = f["thermal_max"]
    kt = stats.get("kernel_task")
    score = 0.75 if level >= 2 else 0.4 if level == 1 else 0.0
    if kt and kt.cpu_avg >= 100:
        score = min(1.0, score + 0.25)
    if score < 0.2:
        return []
    names = {1: "moderate", 2: "heavy", 3: "trapping", 4: "sleeping"}
    ev = []
    if level:
        ev.append(f"macOS reported {names.get(level, 'elevated')} thermal pressure.")
    if kt and kt.cpu_avg >= 100:
        ev.append(f"kernel_task was holding {cores(kt.cpu_avg)}, which is how macOS slows the CPU down to cool it.")
    return [Finding(
        "thermal", score, f"{_cap(device)} was overheating and slowed itself down", "it was overheating",
        "To protect itself from heat, the computer deliberately runs the processor slower, so everything takes longer.",
        ev, ["Keep the vents clear and use the computer on a hard, flat surface.", "Close heavy apps until it cools down.",
             "If it overheats doing light work, the fans or vents may be clogged with dust."],
    )]


def _os_events(related: list[Event], incident: Event | None, system_busy: float, device: str, platform: str) -> list[Finding]:
    out: list[Finding] = []
    events = list(related)
    if incident is not None and incident.kind in ("hang", "crash", "panic", "hardware", "shutdown"):
        events.insert(0, incident)
    seen: set[tuple[str, str]] = set()
    for e in events:
        key = (e.kind, e.app or e.title)
        if key in seen:
            continue
        seen.add(key)
        if e.kind == "hang":
            app = e.app or "An app"
            if system_busy < 0.35:
                out.append(Finding(
                    "hang", 0.8, f"{app} stopped responding on its own", f"{app} stopped responding",
                    f"Nothing else on {device} was struggling at the time, so the problem was inside {app} itself: often a bug, "
                    "a stuck network request, or a very large file or project it was working on.",
                    [e.title + "."] + ([f"Report: {e.detail['report']}"] if e.detail.get("report") else []),
                    [f"Update {app} to the latest version.", f"If it keeps happening, look up the issue on {app}'s support site or reinstall it."]
                    + _tip_actions(app),
                ))
            else:
                out.append(Finding("hang", 0.45, f"{app} stopped responding", f"{app} stopped responding",
                                   f"{app} hung while the system itself was under strain; the other findings explain why.", [e.title + "."], []))
        elif e.kind == "crash":
            out.append(Finding(
                "crash", 0.6, e.title, e.title[0].lower() + e.title[1:],
                "The operating system recorded a crash report for this app at about the same time.",
                [e.title + "."] + ([f"Report: {e.detail['report']}"] if e.detail.get("report") else []),
                [f"Update {e.app or 'the app'}; crashes like this are usually bugs fixed in newer versions."],
            ))
        elif e.kind in ("hardware", "panic"):
            out.append(Finding(
                "hardware", 0.9, e.title, "a hardware or driver error was logged",
                "The operating system logged an error from the hardware or a low-level driver at the same time. Problems like this "
                "aren't caused by an app, and they're worth taking seriously if they repeat.",
                [e.title + "."] + ([e.detail["message"][:240]] if e.detail.get("message") else []) + ([e.detail["panic"]] if e.detail.get("panic") else []),
                hardware_actions(platform),
            ))
        elif e.kind == "shutdown" and e.severity >= 2:
            out.append(Finding(
                "shutdown", 0.6, e.title, "the computer restarted unexpectedly",
                e.detail.get("explain", "The computer restarted without shutting down normally, which happens after a hard freeze, a crash, or a power loss."),
                [e.title + "."], hardware_actions(platform),
            ))
    return out


def _quiet(incident: Event, device: str, platform: str) -> Finding:
    if incident.kind == "mark":
        return Finding(
            "quiet", 0.3, "Nothing unusual in the 2 minutes before you reported it", "nothing unusual was recorded",
            "Memory, CPU, disk and temperature all looked normal right before you clicked. If the problem happened earlier, "
            "pick that moment on the timeline to see what was going on then.",
            [], [],
        )
    return Finding(
        "quiet", 0.45, f"Nothing on {device} was busy when it froze", "nothing was busy (points to a driver or hardware)",
        "CPU, memory and disk were all calm before and during the freeze. When the whole computer locks up without any app "
        "working hard, the cause is usually underneath the apps: the graphics driver, an external device, the operating system, "
        "or a hardware fault.",
        [f"The whole system was unresponsive for {fmt_duration((incident.end_ts or incident.ts) - incident.ts)}, with no heavy CPU, memory or disk use."],
        hardware_actions(platform) + ["If the freezes only happen with an external display or dock connected, that device or its driver is the likely culprit."],
    )


def _culprits(stats: dict[str, AppStats], cause: str) -> list[dict[str, Any]]:
    apps = list(stats.values())
    if cause == "memory":
        apps.sort(key=lambda a: (max(a.growth, 0), a.rss_max), reverse=True)
    elif cause == "disk":
        apps.sort(key=lambda a: a.io_peak, reverse=True)
    else:
        apps.sort(key=lambda a: a.cpu_avg + a.rss_max / GB * 10, reverse=True)
    out = []
    for a in apps[:6]:
        info = lookup(a.app, a.top_name)
        out.append({
            "app": a.app, "cpu_avg": round(a.cpu_avg, 1), "cpu_peak": round(a.cpu_peak, 1), "rss": a.rss_max,
            "growth": a.growth, "io_peak": a.io_peak, "nproc": a.nproc, "what": info.what if info else None,
        })
    return out


def diagnose_now(store: Store, minutes: float = 2.0, platform: str = sys.platform) -> dict[str, Any]:
    now = time.time()
    return diagnose(store, now - minutes * 60 + LOOKBACK, now, None, platform)
