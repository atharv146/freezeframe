"""What well-known background processes are, in plain words.

Most "what is this and why is it using my CPU?" moments involve the same
couple of dozen system processes. Explaining them is half of a diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Info:
    what: str
    tip: str = ""


BROWSER_TIP = "Each tab and extension runs as its own helper process. Open the browser's own task manager (Shift+Esc in Chrome, Edge and Brave; about:processes in Firefox) to see which tab is responsible, and close it."

KNOWN: dict[str, Info] = {
    # macOS
    "kernel_task": Info("macOS itself. When kernel_task uses a lot of CPU, it is usually reserving the CPU on purpose to cool the Mac down.",
                        "Let the Mac cool off: keep vents clear, use it on a hard surface, and close heavy apps."),
    "windowserver": Info("the part of macOS that draws everything on screen.",
                         "High use comes from graphics-heavy apps, many open windows, external displays or transparency effects (System Settings > Accessibility > Display > Reduce transparency)."),
    "mds_stores": Info("Spotlight, indexing your files so search works.", "Indexing is heavy after updates or after copying many files, and stops on its own when it finishes. You can exclude folders in System Settings > Siri & Spotlight > Spotlight Privacy."),
    "mds": Info("Spotlight, indexing your files so search works.", "It stops on its own once indexing is finished."),
    "mdworker_shared": Info("Spotlight, reading files to index them.", "It stops on its own once indexing is finished."),
    "mdworker": Info("Spotlight, reading files to index them.", "It stops on its own once indexing is finished."),
    "backupd": Info("Time Machine, making a backup.", "Backups are busiest right after you reconnect the backup drive or after big file changes."),
    "photoanalysisd": Info("Photos, analysing your library for faces and objects.", "It mostly runs while the Mac is plugged in and idle."),
    "mediaanalysisd": Info("macOS analysing photos and videos for search and Live Text.", "It mostly runs while the Mac is plugged in and idle."),
    "photolibraryd": Info("the Photos library service.", ""),
    "bird": Info("iCloud Drive, syncing files.", "Large uploads or downloads to iCloud Drive keep it busy until they finish."),
    "cloudd": Info("iCloud, syncing data.", "Large iCloud syncs keep it busy until they finish."),
    "fileproviderd": Info("the service that syncs cloud folders (iCloud Drive, Dropbox, OneDrive, Google Drive).", ""),
    "softwareupdated": Info("macOS downloading or preparing a software update.", "Let it finish, or install the update so it stops."),
    "xprotectservice": Info("macOS checking apps and files for malware.", "Scans are brief; they happen after updates and when new apps are opened."),
    "syspolicyd": Info("macOS verifying that apps are safe to open.", ""),
    "coreaudiod": Info("the macOS audio system.", ""),
    "launchd": Info("the process that starts every other process on macOS.", ""),
    "trustd": Info("macOS checking security certificates.", ""),
    "com.apple.webkit.gpu": Info("Safari's (or another WebKit app's) graphics process.", "A page with heavy video or animation is usually the cause; close it."),
    "com.apple.webkit.webcontent": Info("a Safari tab.", "Safari > Settings > Advanced > Show features for web developers, then Develop > Show Page Resource Usage helps find heavy tabs."),
    # Windows
    "microsoft defender": Info("Windows Security's antivirus scanner.", "Full scans are heavy. You can see scan history in Windows Security > Virus & threat protection."),
    "windows search": Info("Windows Search, indexing your files.", "Indexing is heavy after updates or big file copies. You can limit indexed folders in Settings > Privacy & security > Searching Windows."),
    "windows update": Info("Windows installing updates in the background.", "Let it finish and restart when asked; it calms down afterwards."),
    "desktop window manager": Info("the part of Windows that draws the desktop and windows.", "High use usually means a graphics-heavy app or a graphics driver problem. Updating the graphics driver often helps."),
    "system": Info("the Windows kernel and its drivers.", "Sustained high CPU here usually points to a misbehaving driver; updating chipset, storage and graphics drivers is the usual fix."),
    "memory compression": Info("Windows compressing memory because RAM is nearly full.", "This is a symptom of running low on memory, not a cause."),
    "svchost": Info("a container for Windows services.", "Task Manager > Details, then right-click svchost.exe > Go to service(s) shows which service is busy."),
    "system interrupts": Info("time spent handling hardware interrupts.", "If this stays above a few percent, a driver or device (often USB, audio or network) is misbehaving."),
    "onedrive": Info("OneDrive, syncing files.", "Large syncs keep it busy until they finish."),
    # Cross-platform
    "docker": Info("Docker's virtual machine.", "Docker reserves memory and CPU for its VM even when idle; lower the limits in Docker Desktop > Settings > Resources."),
    "com.docker.backend": Info("Docker's virtual machine.", "Lower the limits in Docker Desktop > Settings > Resources."),
    "zoom": Info("video calls.", "Video calls are heavy, especially with virtual backgrounds or screen sharing."),
    "microsoft teams": Info("chat and video calls.", "Teams is memory-hungry; restarting it frees memory it has accumulated."),
    "visual studio code": Info("a code editor; each extension runs in its own process.", "Extensions run in their own processes; Help > Open Process Explorer shows which one is busy."),
    "code": Info("Visual Studio Code, a code editor.", "Help > Open Process Explorer shows which extension is busy."),
}

BROWSERS = {"google chrome", "brave browser", "microsoft edge", "firefox", "opera", "arc", "vivaldi", "chromium", "safari"}


def lookup(app: str | None, process: str | None = None) -> Info | None:
    for name in (app, process):
        if not name:
            continue
        key = name.lower().removesuffix(".exe")
        if key in KNOWN:
            return KNOWN[key]
        if key in BROWSERS:
            return Info("your web browser; every tab and extension is a separate process.", BROWSER_TIP)
    return None
