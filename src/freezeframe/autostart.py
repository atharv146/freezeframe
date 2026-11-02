"""Starting the recorder automatically when you log in."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import log_path

LABEL = "io.github.atharv146.freezeframe"


def _python(windowless: bool = False) -> str:
    exe = sys.executable
    if windowless and sys.platform == "win32":
        candidate = Path(exe).with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return exe


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _systemd_unit() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user" / "freezeframe.service"


def install() -> str:
    if sys.platform == "darwin":
        plist = _mac_plist()
        plist.parent.mkdir(parents=True, exist_ok=True)
        with open(plist, "wb") as f:
            plistlib.dump({
                "Label": LABEL,
                "ProgramArguments": [_python(), "-m", "freezeframe", "record", "--quiet"],
                "RunAtLoad": True,
                "KeepAlive": True,
                # Not "Background": that would throttle the recorder's timer and fake freezes.
                "ProcessType": "Standard",
                "StandardOutPath": str(log_path()),
                "StandardErrorPath": str(log_path()),
            }, f)
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True)
        res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True)
        if res.returncode != 0:
            subprocess.run(["launchctl", "load", "-w", str(plist)], check=True)
        return f"Installed a login item ({plist}). Freezeframe is recording now and will start whenever you log in."
    if sys.platform == "win32":
        cmd = f'"{_python(windowless=True)}" -m freezeframe record --quiet'
        subprocess.run(["schtasks", "/Create", "/TN", "Freezeframe", "/SC", "ONLOGON", "/TR", cmd, "/RL", "LIMITED", "/F"], check=True, capture_output=True)
        subprocess.run(["schtasks", "/Run", "/TN", "Freezeframe"], capture_output=True)
        return "Added a Task Scheduler task. Freezeframe is recording now and will start whenever you sign in."
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
    return f"Installed a systemd user service ({unit}). Freezeframe is recording now and will start whenever you log in."


def uninstall() -> str:
    if sys.platform == "darwin":
        plist = _mac_plist()
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        plist.unlink(missing_ok=True)
        return "Removed the login item. Your recording is still there; delete it with  freezeframe forget  if you like."
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/End", "/TN", "Freezeframe"], capture_output=True)
        subprocess.run(["schtasks", "/Delete", "/TN", "Freezeframe", "/F"], capture_output=True)
        return "Removed the Task Scheduler task. Your recording is still there; delete it with  freezeframe forget  if you like."
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", "freezeframe.service"], check=False)
    _systemd_unit().unlink(missing_ok=True)
    return "Removed the systemd user service."


def is_installed() -> bool:
    if sys.platform == "darwin":
        return _mac_plist().exists()
    if sys.platform == "win32":
        return subprocess.run(["schtasks", "/Query", "/TN", "Freezeframe"], capture_output=True).returncode == 0
    return _systemd_unit().exists()
