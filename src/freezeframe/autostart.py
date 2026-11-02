"""Starting the recorder (and the menu-bar icon) automatically when you log in."""

from __future__ import annotations

import importlib.util
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import log_path

LABEL = "io.github.atharv146.freezeframe"
MENUBAR_LABEL = f"{LABEL}.menubar"


def menubar_available() -> bool:
    return importlib.util.find_spec("pystray") is not None and importlib.util.find_spec("PIL") is not None


def _python(windowless: bool = False) -> str:
    exe = sys.executable
    if windowless and sys.platform == "win32":
        candidate = Path(exe).with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return exe


def _mac_plist(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _systemd_unit() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user" / "freezeframe.service"


def _xdg_autostart() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "freezeframe-menubar.desktop"


def _mac_load(plist: Path, contents: dict) -> None:
    plist.parent.mkdir(parents=True, exist_ok=True)
    with open(plist, "wb") as f:
        plistlib.dump(contents, f)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True)
    res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True)
    if res.returncode != 0:
        subprocess.run(["launchctl", "load", "-w", str(plist)], check=True)


def install(menubar: bool = True) -> str:
    with_icon = menubar and menubar_available()
    if sys.platform == "darwin":
        _mac_load(_mac_plist(), {
            "Label": LABEL,
            "ProgramArguments": [_python(), "-m", "freezeframe", "record", "--quiet"],
            "RunAtLoad": True,
            "KeepAlive": True,
            # Not "Background": that would throttle the recorder's timer and fake freezes.
            "ProcessType": "Standard",
            "StandardOutPath": str(log_path()),
            "StandardErrorPath": str(log_path()),
        })
        if with_icon:
            _mac_load(_mac_plist(MENUBAR_LABEL), {
                "Label": MENUBAR_LABEL,
                "ProgramArguments": [_python(), "-m", "freezeframe", "menubar"],
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after you choose Quit
                "LimitLoadToSessionType": "Aqua",
                "ProcessType": "Interactive",
                "StandardOutPath": str(log_path()),
                "StandardErrorPath": str(log_path()),
            })
        icon = " The Freezeframe icon is in your menu bar." if with_icon else ""
        return f"Freezeframe is recording now and will start whenever you log in.{icon}"
    if sys.platform == "win32":
        cmd = f'"{_python(windowless=True)}" -m freezeframe record --quiet'
        subprocess.run(["schtasks", "/Create", "/TN", "Freezeframe", "/SC", "ONLOGON", "/TR", cmd, "/RL", "LIMITED", "/F"], check=True, capture_output=True)
        subprocess.run(["schtasks", "/Run", "/TN", "Freezeframe"], capture_output=True)
        if with_icon:
            icon_cmd = f'"{_python(windowless=True)}" -m freezeframe menubar'
            subprocess.run(["schtasks", "/Create", "/TN", "Freezeframe icon", "/SC", "ONLOGON", "/TR", icon_cmd, "/RL", "LIMITED", "/F"], check=True, capture_output=True)
            subprocess.run(["schtasks", "/Run", "/TN", "Freezeframe icon"], capture_output=True)
        icon = " The Freezeframe icon is in the system tray (next to the clock)." if with_icon else ""
        return f"Freezeframe is recording now and will start whenever you sign in.{icon}"
    unit = _systemd_unit()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\nDescription=Freezeframe recorder\n\n"
        f"[Service]\nExecStart={_python()} -m freezeframe record --quiet\nRestart=always\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "freezeframe.service"], check=True)
    if with_icon:
        desktop = _xdg_autostart()
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(f"[Desktop Entry]\nType=Application\nName=Freezeframe\nExec={_python()} -m freezeframe menubar\nX-GNOME-Autostart-enabled=true\n")
        subprocess.Popen([_python(), "-m", "freezeframe", "menubar"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "Freezeframe is recording now and will start whenever you log in." + (" Its tray icon starts with your desktop." if with_icon else "")


def uninstall() -> str:
    tail = " Your recording is still there; delete it with  freezeframe forget --yes  if you like."
    if sys.platform == "darwin":
        for label in (MENUBAR_LABEL, LABEL):
            plist = _mac_plist(label)
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
        return "Stopped recording and removed the menu-bar icon and login items." + tail
    if sys.platform == "win32":
        for task in ("Freezeframe icon", "Freezeframe"):
            subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True)
            subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"], capture_output=True)
        return "Stopped recording and removed the tray icon and Task Scheduler tasks." + tail
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", "freezeframe.service"], check=False)
    _systemd_unit().unlink(missing_ok=True)
    _xdg_autostart().unlink(missing_ok=True)
    return "Stopped recording and removed the systemd user service." + tail


def is_installed() -> bool:
    if sys.platform == "darwin":
        return _mac_plist().exists()
    if sys.platform == "win32":
        return subprocess.run(["schtasks", "/Query", "/TN", "Freezeframe"], capture_output=True).returncode == 0
    return _systemd_unit().exists()
