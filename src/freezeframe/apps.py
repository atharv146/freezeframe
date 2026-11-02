"""Turning process names into the app a person would recognise.

Browsers, editors and chat apps run dozens of helper processes. Adding them
up under one name ("Google Chrome", not "Google Chrome Helper (Renderer)")
is what makes the numbers meaningful.
"""

from __future__ import annotations

import ntpath
import posixpath
import re

WINDOWS_APPS = {
    "chrome": "Google Chrome",
    "msedge": "Microsoft Edge",
    "msedgewebview2": "Microsoft Edge WebView",
    "firefox": "Firefox",
    "brave": "Brave Browser",
    "opera": "Opera",
    "code": "Visual Studio Code",
    "discord": "Discord",
    "slack": "Slack",
    "teams": "Microsoft Teams",
    "ms-teams": "Microsoft Teams",
    "spotify": "Spotify",
    "zoom": "Zoom",
    "explorer": "File Explorer",
    "winword": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpnt": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook",
    "onedrive": "OneDrive",
    "steam": "Steam",
    "steamwebhelper": "Steam",
    "msmpeng": "Microsoft Defender",
    "mpdefendercoreservice": "Microsoft Defender",
    "searchindexer": "Windows Search",
    "searchprotocolhost": "Windows Search",
    "searchfilterhost": "Windows Search",
    "tiworker": "Windows Update",
    "trustedinstaller": "Windows Update",
    "wuauserv": "Windows Update",
    "dwm": "Desktop Window Manager",
    "python": "Python",
    "pythonw": "Python",
    "node": "Node.js",
    "java": "Java",
    "javaw": "Java",
}

_HELPER = re.compile(r"\s+(Helper|Networking|Web Content|GPU Process)\b.*$", re.IGNORECASE)


def app_name(path_or_name: str) -> str:
    """Best human name for a process given its executable path (or bare name)."""
    if not path_or_name:
        return "unknown"
    if ".app/" in path_or_name or path_or_name.endswith(".app"):
        # The outermost bundle is the app the person launched.
        head = path_or_name.split(".app", 1)[0]
        return posixpath.basename(head)
    if "\\" in path_or_name or re.match(r"^[A-Za-z]:", path_or_name):
        base = ntpath.basename(path_or_name)
    else:
        base = posixpath.basename(path_or_name)
    stem = base[:-4] if base.lower().endswith(".exe") else base
    mapped = WINDOWS_APPS.get(stem.lower())
    if mapped:
        return mapped
    return _HELPER.sub("", stem) or stem
