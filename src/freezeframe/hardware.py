"""What's inside this computer, in words a non-expert can use.

Each fact comes with a short note on what it means in practice ("16 GB:
plenty for everyday use and a few heavy apps at once"), and the health
checks the hardware reports about itself (drive self-test, battery health).
Serial numbers and other identifiers are left out on purpose.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from .detect import GB, human_bytes


def _run(cmd: list[str], timeout: float = 20) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def memory_note(total: int) -> str:
    gb = total / GB
    if gb < 7:
        return "Tight for today's apps: a browser with a few tabs and a video call can fill it, and then everything slows down."
    if gb < 12:
        return "Enough for everyday use. Lots of browser tabs plus a video call or a game can fill it."
    if gb < 24:
        return "Plenty for everyday use and a few heavy apps at once."
    if gb < 48:
        return "Lots: comfortable for video editing, big coding projects and games."
    return "A huge amount: room for virtual machines, large models and pro video work."


def _gb(n: float) -> str:
    """Drive sizes the way the OS and the box say them (1 GB = 1,000,000,000 bytes)."""
    return f"{n / 1e12:.1f} TB" if n >= 1e12 else f"{n / 1e9:.0f} GB"


def _mem(n: int) -> str:
    gb = n / GB
    return f"{gb:.0f} GB" if abs(gb - round(gb)) < 0.05 else human_bytes(n)


def _item(label: str, value: str, note: str = "", level: str | None = None) -> dict[str, Any]:
    return {"label": label, "value": value, "note": note, "level": level}


def _battery_items(health: int | None, cycles: int | None, condition: str | None, design_cycles: int = 1000) -> list[dict[str, Any]]:
    items = []
    if health is not None:
        if health >= 90:
            note, level = "Holds nearly as much charge as when it was new.", "good"
        elif health >= 80:
            note, level = "Normal wear. It holds a bit less charge than new.", "good"
        else:
            note, level = "Worn: it holds noticeably less charge. A battery replacement brings back the battery life.", "warning"
        items.append(_item("Battery health", f"{health}%", note, level))
    if cycles is not None:
        items.append(_item("Charge cycles", str(cycles), f"One cycle is 100% worth of charging, spread over any number of charges. "
                                                         f"Batteries like this one are rated for about {design_cycles:,}."))
    if condition and condition.lower() not in ("normal", "good"):
        items.append(_item("Battery condition", condition, "The computer itself recommends getting the battery checked.", "warning"))
    return items


# ------------------------------------------------------------------ macOS

def _mac() -> dict[str, Any]:
    out = _run(["system_profiler", "-json", "-detailLevel", "mini", "SPHardwareDataType", "SPDisplaysDataType",
                "SPStorageDataType", "SPPowerDataType", "SPNVMeDataType"])
    try:
        sp = json.loads(out) if out else {}
    except ValueError:
        sp = {}
    hw = (sp.get("SPHardwareDataType") or [{}])[0]
    sections: list[dict[str, Any]] = []

    chip = hw.get("chip_type") or hw.get("cpu_type") or platform.processor()
    procs = re.match(r"proc (\d+):(\d+):(\d+)", hw.get("number_processors", ""))
    cpu_items = [_item("Chip", chip, "Apple's own chip: the processor, graphics and memory in one package." if chip.startswith("Apple") else "")]
    if procs:
        total, perf, eff = (int(x) for x in procs.groups())
        cpu_items.append(_item("Cores", f"{total} ({perf} performance, {eff} efficiency)",
                               "Performance cores take on heavy work; efficiency cores run background tasks using less power. "
                               "That's why 100% in Freezeframe means every core is busy."))
    else:
        cpu_items.append(_item("Cores", str(psutil.cpu_count(logical=True))))
    sections.append({"title": "Processor", "items": cpu_items})

    mem = psutil.virtual_memory().total
    mem_items = [_item("Memory", _mem(mem), memory_note(mem))]
    if chip.startswith("Apple"):
        mem_items.append(_item("Type", "Unified memory", "Shared by the processor and graphics, and built into the chip, so it can't be upgraded later."))
    sections.append({"title": "Memory", "items": mem_items})

    gfx, screens = [], []
    for g in sp.get("SPDisplaysDataType") or []:
        cores = g.get("sppci_cores")
        name = g.get("sppci_model") or g.get("_name", "Graphics")
        gfx.append(_item("Graphics", f"{name}{f', {cores} cores' if cores else ''}",
                         "Draws everything on screen and speeds up video, games and photo editing."))
        for d in g.get("spdisplays_ndrvs") or []:
            internal = d.get("spdisplays_connection_type") == "spdisplays_internal"
            res = d.get("_spdisplays_resolution") or d.get("spdisplays_resolution") or ""
            looks = re.sub(r"\s*@.*", "", res)
            pixels = d.get("_spdisplays_pixels", "")
            screens.append(_item("Built-in display" if internal else (d.get("_name") or "External display"),
                                 f"Looks like {looks}" + (f" ({pixels} pixels)" if pixels else ""),
                                 "" if internal else "External displays add work for the graphics chip and for WindowServer."))
    if gfx:
        sections.append({"title": "Graphics and displays", "items": gfx + screens})

    drives = []
    seen = set()
    for v in sp.get("SPStorageDataType") or []:
        phys = v.get("physical_drive") or {}
        dev = phys.get("device_name")
        internal_extra = phys.get("is_internal_disk") == "yes" and v.get("mount_point") != "/"
        if not dev or dev in seen or internal_extra:  # one entry per physical drive
            continue
        seen.add(dev)
        kind = "SSD" if phys.get("medium_type") == "ssd" else "Hard drive" if phys.get("medium_type") == "rotational" else "Drive"
        smart = phys.get("smart_status")
        size = v.get("size_in_bytes")
        internal = phys.get("is_internal_disk") == "yes"
        drives.append(_item("Internal drive" if internal else f"External drive ({v.get('_name')})",
                            f"{_gb(size) if size else ''} {kind}".strip(),
                            "A solid-state drive: fast, silent, no moving parts." if kind == "SSD" else
                            "A spinning hard drive: much slower than an SSD, which makes everything feel slower." if kind == "Hard drive" else ""))
        if smart:
            ok = smart.lower() == "verified"
            drives.append(_item("Drive self-test (SMART)", "Passed" if ok else smart,
                                "The drive checks itself for wear and errors and reports no problems." if ok else
                                "The drive is reporting problems. Back up anything important now.", "good" if ok else "warning"))
    if drives:
        sections.append({"title": "Storage", "items": drives})

    batt = []
    for p in sp.get("SPPowerDataType") or []:
        h = p.get("sppower_battery_health_info")
        if h:
            cap = str(h.get("sppower_battery_health_maximum_capacity", "")).rstrip("%")
            batt = _battery_items(int(cap) if cap.isdigit() else None, h.get("sppower_battery_cycle_count"),
                                  h.get("sppower_battery_health"))
    if batt:
        sections.append({"title": "Battery", "items": batt})

    model = hw.get("machine_name") or "Mac"
    ident = hw.get("machine_model")
    return {"model": model, "detail": ident, "os": f"macOS {platform.mac_ver()[0]}", "sections": sections}


# ---------------------------------------------------------------- Windows

_WIN_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
  maker = $cs.Manufacturer; model = $cs.Model; memory = $cs.TotalPhysicalMemory
  os = $os.Caption; build = $os.BuildNumber
  cpu = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors)
  gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name, CurrentHorizontalResolution, CurrentVerticalResolution)
  disks = @(Get-PhysicalDisk | Select-Object FriendlyName, MediaType, Size, HealthStatus, BusType)
  battery = @(Get-CimInstance Win32_Battery | Select-Object EstimatedChargeRemaining)
  design = (Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData | Select-Object -First 1).DesignedCapacity
  full = (Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity | Select-Object -First 1).FullChargedCapacity
  cycles = (Get-CimInstance -Namespace root\wmi -ClassName BatteryCycleCount | Select-Object -First 1).CycleCount
} | ConvertTo-Json -Depth 4 -Compress
"""


def _windows() -> dict[str, Any]:
    try:
        d = json.loads(_run(["powershell", "-NoProfile", "-Command", _WIN_SCRIPT], timeout=40) or "{}")
    except ValueError:
        d = {}
    sections = []
    cpus = d.get("cpu") or []
    if cpus:
        c = cpus[0]
        sections.append({"title": "Processor", "items": [
            _item("Processor", (c.get("Name") or "").strip()),
            _item("Cores", f"{c.get('NumberOfCores')} cores, {c.get('NumberOfLogicalProcessors')} threads",
                  "Threads are what Windows schedules work on; 100% in Freezeframe means all of them are busy."),
        ]})
    mem = d.get("memory") or psutil.virtual_memory().total
    sections.append({"title": "Memory", "items": [_item("Memory", _mem(int(mem)), memory_note(int(mem)))]})
    gfx = [_item("Graphics", g.get("Name") or "", f"Screen: {g['CurrentHorizontalResolution']} x {g['CurrentVerticalResolution']}" if g.get("CurrentHorizontalResolution") else "")
           for g in d.get("gpu") or [] if g.get("Name")]
    if gfx:
        sections.append({"title": "Graphics and displays", "items": gfx})
    drives = []
    for k in d.get("disks") or []:
        media = {"SSD": "SSD", "HDD": "Hard drive"}.get(str(k.get("MediaType")), "Drive")
        drives.append(_item(k.get("FriendlyName") or "Drive", f"{_gb(int(k.get('Size') or 0))} {media}",
                            "A spinning hard drive: much slower than an SSD." if media == "Hard drive" else ""))
        health = k.get("HealthStatus")
        if health:
            ok = str(health).lower() == "healthy"
            drives.append(_item("Drive health", "Healthy" if ok else str(health),
                                "Windows reports no problems with this drive." if ok else "Windows reports a problem. Back up anything important now.",
                                "good" if ok else "warning"))
    if drives:
        sections.append({"title": "Storage", "items": drives})
    if d.get("battery"):
        health = round(100 * d["full"] / d["design"]) if d.get("full") and d.get("design") else None
        items = _battery_items(health, d.get("cycles"), None)
        if items:
            sections.append({"title": "Battery", "items": items})
    model = " ".join(x for x in (d.get("maker"), d.get("model")) if x and "To Be Filled" not in x) or "PC"
    return {"model": model, "detail": None, "os": d.get("os") or platform.platform(terse=True), "sections": sections}


# ------------------------------------------------------------------ Linux

def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _linux() -> dict[str, Any]:
    cpuinfo = _read("/proc/cpuinfo")
    m = re.search(r"model name\s*:\s*(.+)", cpuinfo)
    sections = [{"title": "Processor", "items": [
        _item("Processor", m.group(1) if m else platform.processor() or platform.machine()),
        _item("Cores", f"{psutil.cpu_count(logical=False)} cores, {psutil.cpu_count(logical=True)} threads"),
    ]}]
    mem = psutil.virtual_memory().total
    sections.append({"title": "Memory", "items": [_item("Memory", _mem(mem), memory_note(mem))]})
    gpus = [line.split(":", 2)[-1].strip() for line in _run(["lspci"], 5).splitlines() if re.search(r"VGA|3D controller", line)]
    if gpus:
        sections.append({"title": "Graphics and displays", "items": [_item("Graphics", g) for g in gpus]})
    drives = []
    for dev in sorted(Path("/sys/block").glob("*")) if Path("/sys/block").exists() else []:
        if re.match(r"(loop|ram|zram|dm-|sr)", dev.name):
            continue
        size = int(_read(f"{dev}/size") or 0) * 512
        if size < 1 * GB:
            continue
        rotational = _read(f"{dev}/queue/rotational") == "1"
        name = _read(f"{dev}/device/model") or dev.name
        drives.append(_item(name, f"{_gb(size)} {'hard drive' if rotational else 'SSD'}",
                            "A spinning hard drive: much slower than an SSD." if rotational else ""))
    if drives:
        sections.append({"title": "Storage", "items": drives})
    for bat in Path("/sys/class/power_supply").glob("BAT*") if Path("/sys/class/power_supply").exists() else []:
        full = _read(f"{bat}/energy_full") or _read(f"{bat}/charge_full")
        design = _read(f"{bat}/energy_full_design") or _read(f"{bat}/charge_full_design")
        cycles = _read(f"{bat}/cycle_count")
        health = round(100 * int(full) / int(design)) if full.isdigit() and design.isdigit() and int(design) else None
        items = _battery_items(health, int(cycles) if cycles.isdigit() and int(cycles) > 0 else None, None)
        if items:
            sections.append({"title": "Battery", "items": items})
        break
    vendor, product = _read("/sys/class/dmi/id/sys_vendor"), _read("/sys/class/dmi/id/product_name")
    model = " ".join(x for x in (vendor, product) if x and "To Be Filled" not in x) or "Linux computer"
    distro = ""
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            distro = line.split("=", 1)[1].strip('"')
    return {"model": model, "detail": None, "os": distro or platform.platform(terse=True), "sections": sections}


def hardware() -> dict[str, Any]:
    try:
        if sys.platform == "darwin":
            info = _mac()
        elif sys.platform == "win32":
            info = _windows()
        else:
            info = _linux()
    except Exception as e:  # a missing tool shouldn't break the page
        info = {"model": "This computer", "detail": None, "os": platform.platform(terse=True), "sections": [],
                "error": f"Couldn't read the hardware details: {e}"}
    info["uptime"] = max(0.0, time.time() - psutil.boot_time())
    return info
