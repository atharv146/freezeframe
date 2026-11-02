"""Where the disk space went, and what's safe to clear.

Measures the usual suspects (big apps and games, caches, downloads, developer
build files, phone backups) by adding up file sizes. It never opens, moves or
deletes anything; "Show" opens the folder so you can decide.

Downloads, Desktop, Documents and the Trash are only measured when you ask,
because macOS asks your permission before any app looks inside them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

BUDGET = 30.0  # seconds for a whole scan; whatever isn't finished is shown as "at least"
SKIP_SUFFIXES = (".photoslibrary", ".photolibrary")  # opening these would trigger a Photos permission prompt


@dataclass
class Place:
    key: str
    label: str
    path: str
    note: str
    how: str = ""
    private: bool = False  # inside a folder the OS protects; only scanned when asked
    list_children: bool = False
    size: int = 0
    files: int = 0
    complete: bool = True
    error: str | None = None
    children: list[dict[str, Any]] = field(default_factory=list)


def places(private: bool) -> list[Place]:
    home = Path.home()
    out: list[Place] = []
    if sys.platform == "darwin":
        lib = home / "Library"
        out += [
            Place("apps", "Apps and games", "/Applications",
                  "Everything installed in the Applications folder. Games are often the biggest.",
                  "Drag apps you no longer use to the Trash, or remove them in System Settings > General > Storage > Applications.",
                  list_children=True),
            Place("caches", "App caches", str(lib / "Caches"),
                  "Temporary files apps keep to load faster. Apps recreate what they need.",
                  "Usually not worth clearing by hand: space comes back only until apps rebuild it. If one app's cache is huge, clear it from that app's settings."),
            Place("movies", "Movies", str(home / "Movies"), "Videos, screen recordings and iMovie or Final Cut projects.",
                  "Move finished projects and old recordings to an external drive or the cloud."),
            Place("music", "Music", str(home / "Music"), "Your music library and GarageBand projects.",
                  "GarageBand sound libraries can be removed from GarageBand > Sound Library."),
            Place("xcode", "Xcode build files", str(lib / "Developer/Xcode/DerivedData"),
                  "Leftovers from building apps in Xcode. Completely safe to delete; Xcode rebuilds them when needed.",
                  "Delete the folder's contents, or in Xcode: Settings > Locations > Derived Data, then click the arrow and delete."),
            Place("simulators", "iPhone simulators", str(lib / "Developer/CoreSimulator/Devices"),
                  "Simulated iPhones and iPads used by Xcode.", "Run  xcrun simctl delete unavailable  to remove ones for old iOS versions."),
            Place("steam", "Steam games", str(lib / "Application Support/Steam/steamapps"),
                  "Games installed through Steam.", "Uninstall games you're not playing from Steam's library; they can be downloaded again any time."),
            Place("npm", "Node.js package cache", str(home / ".npm"), "Copies of downloaded JavaScript packages.", "Safe to clear: npm cache clean --force"),
            Place("devcache", "Developer tool caches", str(home / ".cache"), "Downloads cached by developer tools (pip, Hugging Face models and others).",
                  "Safe to clear the folders of tools you no longer use."),
            Place("gradle", "Gradle cache", str(home / ".gradle"), "Build files for Java and Android projects.", "Safe to delete ~/.gradle/caches."),
        ]
        if private:
            out += [
                Place("downloads", "Downloads", str(home / "Downloads"), "Everything you've downloaded.",
                      "Installers (.dmg, .pkg) and .zip files can usually go once you've installed or unpacked them.", private=True, list_children=True),
                Place("desktop", "Desktop", str(home / "Desktop"), "Files on your desktop.", "", private=True),
                Place("documents", "Documents", str(home / "Documents"), "Your documents.", "", private=True),
                Place("trash", "Trash", str(home / ".Trash"), "Files you've deleted but not emptied yet.",
                      "Empty the Trash (right-click its icon in the Dock) to get this space back.", private=True),
                Place("backups", "iPhone and iPad backups", str(lib / "Application Support/MobileSync/Backup"),
                      "Backups of iPhones and iPads made on this Mac. Old ones are often forgotten.",
                      "Remove old backups in Finder: select your phone in the sidebar, then Manage Backups.", private=True),
                Place("docker", "Docker", str(lib / "Containers/com.docker.docker"), "Docker's virtual disk with images and containers.",
                      "Run  docker system prune  to remove unused images and containers.", private=True),
            ]
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        out += [
            Place("downloads", "Downloads", str(home / "Downloads"), "Everything you've downloaded.",
                  "Installers (.exe, .msi) and .zip files can usually go once you've installed or unpacked them.", list_children=True),
            Place("desktop", "Desktop", str(home / "Desktop"), "Files on your desktop."),
            Place("documents", "Documents", str(home / "Documents"), "Your documents."),
            Place("videos", "Videos", str(home / "Videos"), "Videos and game clips (Xbox Game Bar saves clips here).",
                  "Move old recordings to an external drive or the cloud."),
            Place("temp", "Temporary files", str(local / "Temp"), "Files apps meant to delete later.",
                  "Settings > System > Storage > Temporary files clears these safely."),
            Place("steam", "Steam games", r"C:\Program Files (x86)\Steam\steamapps", "Games installed through Steam.",
                  "Uninstall games you're not playing from Steam's library; they can be downloaded again any time."),
            Place("windowsold", "Previous Windows version", r"C:\Windows.old", "A copy of your old Windows kept after a big update.",
                  "Settings > System > Storage > Temporary files > Previous Windows installation(s) removes it."),
            Place("npm", "Node.js package cache", str(local / "npm-cache"), "Copies of downloaded JavaScript packages.", "Safe to clear: npm cache clean --force"),
        ]
    else:
        share = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
        out += [
            Place("downloads", "Downloads", str(home / "Downloads"), "Everything you've downloaded.",
                  "Old installers and archives can usually go.", list_children=True),
            Place("documents", "Documents", str(home / "Documents"), "Your documents."),
            Place("videos", "Videos", str(home / "Videos"), "Videos and recordings."),
            Place("cache", "Caches", str(home / ".cache"), "Temporary files apps keep to load faster.", "Safe to clear the folders of apps you no longer use."),
            Place("trash", "Trash", str(share / "Trash"), "Files you've deleted but not emptied yet.", "Empty the Trash in your file manager."),
            Place("steam", "Steam games", str(share / "Steam/steamapps"), "Games installed through Steam.", "Uninstall games you're not playing from Steam."),
            Place("flatpak", "Flatpak apps", str(share / "flatpak"), "Apps installed with Flatpak and their runtimes.", "flatpak uninstall --unused  removes leftovers."),
            Place("npm", "Node.js package cache", str(home / ".npm"), "Copies of downloaded JavaScript packages.", "Safe to clear: npm cache clean --force"),
        ]
    return out


def _measure(path: str, deadline: float) -> tuple[int, int, bool, bool]:
    """(bytes on disk, files, finished, permission problems). Doesn't follow links or enter photo libraries."""
    total = files = 0
    denied = False
    stack = [path]
    use_blocks = sys.platform != "win32"
    while stack:
        if time.monotonic() > deadline:
            return total, files, False, denied
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if not e.name.endswith(SKIP_SUFFIXES):
                                stack.append(e.path)
                            continue
                        st = e.stat(follow_symlinks=False)
                        total += st.st_blocks * 512 if use_blocks and hasattr(st, "st_blocks") else st.st_size
                        files += 1
                    except OSError:
                        continue
        except PermissionError:
            denied = True
        except OSError:
            continue
    return total, files, True, denied


def volumes() -> list[dict[str, Any]]:
    out = []
    seen = set()
    for p in psutil.disk_partitions(all=False):
        mp = p.mountpoint
        if sys.platform == "darwin" and not (mp == "/" or mp.startswith("/Volumes/")):
            continue
        if sys.platform == "win32" and "fixed" not in p.opts and "removable" not in p.opts:
            continue
        if sys.platform.startswith("linux") and (not p.device.startswith("/dev/") or "/snap" in mp or p.fstype in ("squashfs", "tmpfs")):
            continue
        try:
            u = psutil.disk_usage(mp)
        except OSError:
            continue
        if (u.total, u.used) in seen or u.total < 1e9:
            continue
        seen.add((u.total, u.used))
        name = "This computer's drive" if mp in ("/", "C:\\") else (Path(mp).name or mp)
        # On a Mac, "/" is the small read-only system volume; what matters is the space shared by the whole drive.
        out.append({"name": name, "mount": mp, "total": u.total, "used": u.total - u.free, "free": u.free})
    return out


class StorageScan:
    """Runs one scan at a time in the background; the dashboard polls status()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"state": "idle", "items": [], "private": False}
        self._allowed: set[str] = set()

    def start(self, private: bool = False) -> None:
        with self._lock:
            if self._state["state"] == "scanning":
                return
            self._state = {"state": "scanning", "items": [], "private": private, "started": time.time()}
        threading.Thread(target=self._run, args=(private,), daemon=True).start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {**self._state, "volumes": volumes()}

    def _run(self, private: bool) -> None:
        deadline = time.monotonic() + BUDGET
        results: list[dict[str, Any]] = []
        allowed: set[str] = set()
        for p in places(private):
            if not os.path.isdir(p.path):
                continue
            if p.list_children:
                try:
                    kids = [e for e in os.scandir(p.path) if not e.name.startswith(".") and not e.is_symlink()]
                except OSError as e:
                    p.error = _why(e)
                    kids = []
                for k in kids:
                    if k.is_dir(follow_symlinks=False):
                        size, n, done, _ = _measure(k.path, deadline)
                    else:
                        try:
                            size, n, done = k.stat(follow_symlinks=False).st_size, 1, True
                        except OSError:
                            continue
                    p.size += size
                    p.files += n
                    p.complete &= done
                    p.children.append({"name": k.name, "path": k.path, "size": size})
                p.children = sorted(p.children, key=lambda c: c["size"], reverse=True)[:8]
                allowed.update(c["path"] for c in p.children)
            else:
                p.size, p.files, p.complete, denied = _measure(p.path, deadline)
                if denied and p.size == 0:
                    p.error = "Freezeframe wasn't allowed to look inside this folder."
            allowed.add(p.path)
            results.append(asdict(p))
            with self._lock:
                self._state["items"] = sorted(results, key=lambda r: r["size"], reverse=True)
        with self._lock:
            self._allowed = allowed
            self._state.update(state="done", finished=time.time(), budget_hit=time.monotonic() > deadline)

    def reveal(self, path: str) -> dict[str, Any]:
        """Opens a folder from the latest scan in Finder / Explorer / the file manager."""
        if path not in self._allowed:
            raise ValueError("Only folders from the storage scan can be opened.")
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", "/select,", path], check=False)
        else:
            subprocess.run(["xdg-open", str(Path(path).parent)], check=False)
        return {"ok": True}


def _why(e: OSError) -> str:
    if isinstance(e, PermissionError):
        return "Freezeframe wasn't allowed to look inside this folder."
    return str(e)
