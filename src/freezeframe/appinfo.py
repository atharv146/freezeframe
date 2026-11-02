"""Identifying apps Freezeframe has no write-up for, from the app's own details.

Every installed app carries facts about itself: who signed it, which App
Store category it's in, whether it lives in the menu bar instead of the Dock,
whether it was built with Electron. Put together, those answer most of
"what is NotchBox and why is it running?" without guessing.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CATEGORIES = {
    "business": "business", "developer-tools": "developer tools", "education": "education",
    "entertainment": "entertainment", "finance": "finance", "games": "games", "graphics-design": "graphics and design",
    "healthcare-fitness": "health and fitness", "lifestyle": "lifestyle", "medical": "medical", "music": "music",
    "news": "news", "photography": "photography", "productivity": "productivity", "reference": "reference",
    "social-networking": "social networking", "sports": "sports", "travel": "travel", "utilities": "utilities",
    "video": "video", "weather": "weather", "books": "books", "navigation": "navigation",
}


def bundle_root(path: str) -> str | None:
    """The outermost .app bundle a macOS executable belongs to."""
    i = path.find(".app/")
    if i != -1:
        return path[: i + 4]
    return path if path.endswith(".app") else None


def _category(uti: str | None) -> str | None:
    if not uti:
        return None
    key = uti.rsplit(".", 1)[-1]
    if key.endswith("-games"):
        return "games"
    return CATEGORIES.get(key)


def _copyright_owner(text: str | None) -> str | None:
    if not text:
        return None
    t = re.sub(r"(?i)copyright|\(c\)|©|all rights reserved\.?|\b(19|20)\d\d\b(\s*[-–]\s*(19|20)?\d\d)?", " ", text)
    t = re.sub(r"[\s,.]+", " ", t).strip(" .,-")
    return t if 1 < len(t) <= 60 else None


def _codesign(bundle: str) -> dict[str, Any]:
    try:
        out = subprocess.run(["codesign", "-dv", "--verbose=2", bundle], capture_output=True, text=True, timeout=5).stderr
    except Exception:
        return {}
    info: dict[str, Any] = {}
    for line in out.splitlines():
        if line.startswith("Authority=") and "signer" not in info:
            who = line.split("=", 1)[1]
            info["signer"] = who
            m = re.match(r"Developer ID Application: (.+?)(?: \([A-Z0-9]+\))?$", who)
            if m:
                info["developer"] = m.group(1)
            elif who == "Software Signing":
                info["developer"] = "Apple"
            elif who == "Apple Mac OS Application Signing":
                info["app_store"] = True
    return info


def _mac_running_types() -> dict[str, str]:
    """bundle path -> Foreground | UIElement | BackgroundOnly, for running apps."""
    try:
        out = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {}
    types: dict[str, str] = {}
    path = None
    for line in out.splitlines():
        m = re.search(r'bundle path="([^"]+)"', line)
        if m:
            path = m.group(1)
            continue
        m = re.search(r'type="(\w+)"', line)
        if m and path:
            types.setdefault(path, m.group(1))
            path = None
    return types


def _mac_find_bundle(app: str) -> str | None:
    for base in ("/Applications", str(Path.home() / "Applications"), "/System/Applications",
                 "/System/Applications/Utilities", "/Applications/Utilities"):
        p = Path(base) / f"{app}.app"
        if p.exists():
            return str(p)
    return None


def _mac(app: str, path: str | None, running: dict[str, str]) -> dict[str, Any]:
    bundle = bundle_root(path) if path else None
    bundle = bundle or (None if path else _mac_find_bundle(app))
    if bundle is None:
        return _loose_binary(path) if path else {}
    info: dict[str, Any] = {"path": bundle}
    try:
        with open(Path(bundle) / "Contents" / "Info.plist", "rb") as f:
            plist = plistlib.load(f)
    except Exception:
        plist = {}
    bid = plist.get("CFBundleIdentifier")
    info["bundle_id"] = bid
    info["version"] = plist.get("CFBundleShortVersionString")
    info["category"] = _category(plist.get("LSApplicationCategoryType"))
    if plist.get("LSUIElement") in (True, "1", 1) or running.get(bundle) == "UIElement":
        info["menubar"] = True
    if plist.get("LSBackgroundOnly") in (True, "1", 1) or running.get(bundle) == "BackgroundOnly":
        info["background_only"] = True
    if (Path(bundle) / "Contents" / "Frameworks" / "Electron Framework.framework").exists():
        info["electron"] = True
    if (Path(bundle) / "Contents" / "_MASReceipt").exists():
        info["app_store"] = True
    info.update({k: v for k, v in _codesign(bundle).items() if k not in info or not info[k]})
    if not info.get("developer"):
        owner = _copyright_owner(plist.get("NSHumanReadableCopyright"))
        if owner:
            info["developer"] = owner
    if bundle.startswith("/System/") or (bid or "").startswith("com.apple."):
        info["developer"] = "Apple"
        info["origin"] = "system" if bundle.startswith("/System/") else "apple"
    return info


def _loose_binary(path: str) -> dict[str, Any]:
    """A process that isn't inside an app bundle: where it lives says a lot."""
    p = path.replace("\\", "/")
    if re.match(r"^/(System|usr/(libexec|sbin|bin|lib))/", p) or p.startswith("/sbin/") or p.startswith("/bin/"):
        return {"path": path, "origin": "system", "developer": "Apple" if sys.platform == "darwin" else None}
    if p.startswith("/opt/homebrew/") or p.startswith("/usr/local/Cellar/") or p.startswith("/home/linuxbrew/"):
        return {"path": path, "origin": "homebrew"}
    if p.startswith("/Library/") or "/PrivilegedHelperTools/" in p:
        return {"path": path, "origin": "helper"}
    if "/node_modules/" in p or "/.nvm/" in p or "/.pyenv/" in p or "/venv/" in p or "/.venv/" in p:
        return {"path": path, "origin": "developer"}
    return {"path": path}


def _windows_versions(paths: list[str]) -> dict[str, dict[str, Any]]:
    """FileDescription and CompanyName for each exe, in one PowerShell call."""
    paths = [p for p in paths if p and p.lower().endswith(".exe")][:40]
    if not paths:
        return {}
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$paths = '{json.dumps(paths)}' | ConvertFrom-Json;"
        "$paths | ForEach-Object { $v = (Get-Item -LiteralPath $_).VersionInfo;"
        "[pscustomobject]@{ path=$_; description=$v.FileDescription; company=$v.CompanyName; product=$v.ProductName; version=$v.ProductVersion } }"
        " | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=20).stdout
        rows = json.loads(out or "[]")
    except Exception:
        return {}
    rows = rows if isinstance(rows, list) else [rows]
    return {r["path"]: r for r in rows if isinstance(r, dict) and r.get("path")}


def _windows(path: str | None, version: dict[str, Any] | None) -> dict[str, Any]:
    if not path:
        return {}
    info = _loose_binary(path)
    low = path.lower().replace("/", "\\")
    if low.startswith("c:\\windows\\"):
        info.update(origin="system", developer="Microsoft")
    elif "\\windowsapps\\" in low:
        info["app_store"] = True
    if version:
        info["description"] = (version.get("description") or "").strip() or None
        info["developer"] = info.get("developer") or (version.get("company") or "").strip() or None
        info["product"] = (version.get("product") or "").strip() or None
        info["version"] = (version.get("version") or "").strip() or None
    if os.path.exists(os.path.join(os.path.dirname(path), "resources", "app.asar")):
        info["electron"] = True
    return info


def _desktop_entries() -> dict[str, dict[str, str]]:
    """Linux .desktop files: executable name -> Name / Comment / Categories."""
    out: dict[str, dict[str, str]] = {}
    dirs = [Path.home() / ".local/share/applications", Path("/usr/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"), Path("/var/lib/snapd/desktop/applications")]
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.glob("*.desktop"):
            fields: dict[str, str] = {}
            try:
                for line in f.read_text(errors="replace").splitlines():
                    if line.startswith("[") and fields:
                        break  # only the main [Desktop Entry] section
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        fields.setdefault(k.strip(), v.strip())
            except OSError:
                continue
            exe = (fields.get("Exec") or "").split(" ")[0].strip('"')
            if exe:
                out.setdefault(os.path.basename(exe).lower(), fields)
    return out


def _linux(app: str, path: str | None, entries: dict[str, dict[str, str]]) -> dict[str, Any]:
    info = _loose_binary(path) if path else {}
    names = [os.path.basename(path).lower()] if path else []
    names.append(app.lower())
    for n in names:
        e = entries.get(n)
        if e:
            info["description"] = e.get("Comment") or e.get("GenericName")
            cats = [c for c in (e.get("Categories") or "").split(";") if c]
            if cats:
                info["category"] = cats[0].lower()
            break
    return info


# ------------------------------------------------------------- summaries

def summary(app: str, info: dict[str, Any], platform: str = sys.platform) -> str | None:
    """One plain sentence or two from what's known, or None when nothing is."""
    if not info:
        return None
    device = {"darwin": "macOS", "win32": "Windows"}.get(platform, "the operating system")
    origin = info.get("origin")
    if origin == "system":
        return f"Part of {device}: it's in a system folder, so it came with the computer."
    if origin == "homebrew":
        return "A command-line tool installed with Homebrew (a developer package manager)."
    if origin == "developer":
        return "A developer tool, run from a programming project or toolchain."
    parts = []
    lead = info.get("description") or info.get("product")
    dev = info.get("developer")
    cat = info.get("category")
    if lead and lead.lower() != app.lower():
        parts.append(lead.rstrip(".") + ".")
        if dev:
            parts.append(f"Made by {dev}.")
    else:
        kind = f"{'an' if cat and cat[0] in 'aeio' else 'a'} {cat} app" if cat else "an app"
        if origin == "apple":
            parts.append(f"{kind[0].upper()}{kind[1:]} made by Apple.")
        else:
            by = f" by {dev}" if dev else ""
            store = " from the App Store" if info.get("app_store") else ""
            parts.append(f"{kind[0].upper()}{kind[1:]}{by}{store}.")
    if origin == "helper":
        parts.append("It's a helper another app installed for the whole computer.")
    if info.get("menubar"):
        parts.append("It lives in the menu bar instead of the Dock, so it keeps running with no window open.")
    elif info.get("background_only"):
        parts.append("It runs in the background with no window.")
    if info.get("electron"):
        parts.append("It's built with Electron (a web page in an app wrapper), so it uses more memory than a typical app.")
    return " ".join(parts) if parts else None


def guess_kind(info: dict[str, Any]) -> str | None:
    """app | background | system, from the details alone."""
    origin = info.get("origin")
    if origin == "system":
        return "system"
    if origin in ("helper", "homebrew") or info.get("background_only"):
        return "background"
    if info.get("menubar"):
        return "background"
    if info.get("bundle_id") or info.get("description") or info.get("product"):
        return "app"
    return None


# --------------------------------------------------------------- lookups

class AppInfo:
    """Looks up and remembers app details. Reads are cheap; codesign is not, so results are cached."""

    def __init__(self, platform: str = sys.platform):
        self.platform = platform
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._running: tuple[float, dict[str, str]] = (0.0, {})
        self._desktop: dict[str, dict[str, str]] | None = None

    def lookup(self, apps: list[tuple[str, str | None]]) -> dict[str, dict[str, Any]]:
        """{app: info} for (app name, executable path) pairs. Only works on the computer that recorded them."""
        if self.platform != sys.platform:
            return {}
        now = time.time()
        todo = [(a, p) for a, p in apps if a not in self._cache or now - self._cache[a][0] > 3600]
        if todo:
            if sys.platform == "darwin":
                if now - self._running[0] > 30:
                    self._running = (now, _mac_running_types())
                for a, p in todo:
                    self._cache[a] = (now, _mac(a, p, self._running[1]))
            elif sys.platform == "win32":
                versions = _windows_versions([p for _, p in todo if p])
                for a, p in todo:
                    self._cache[a] = (now, _windows(p, versions.get(p or "")))
            else:
                if self._desktop is None:
                    self._desktop = _desktop_entries()
                for a, p in todo:
                    self._cache[a] = (now, _linux(a, p, self._desktop))
        return {a: self._cache[a][1] for a, _ in apps if a in self._cache}
