"""Desktop notifications ("Froze for 6 s: memory ran out"), best effort on each OS."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

WINDOWS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:FF_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:FF_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def enabled() -> bool:
    return os.environ.get("FREEZEFRAME_NO_NOTIFY") != "1"


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, body: str) -> bool:
    """Shows a notification. Returns False if it couldn't (never raises)."""
    if not enabled():
        return False
    try:
        if sys.platform == "darwin":
            script = f"display notification {_applescript_string(body)} with title {_applescript_string(title)}"
            return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10).returncode == 0
        if sys.platform == "win32":
            env = {**os.environ, "FF_TITLE": title, "FF_BODY": body}
            return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_TOAST],
                                  capture_output=True, timeout=20, env=env).returncode == 0
        if shutil.which("notify-send"):
            return subprocess.run(["notify-send", "--app-name=Freezeframe", title, body], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False
