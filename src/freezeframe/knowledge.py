"""What well-known apps and background processes are, in plain words.

Most "what is this and why is it using my CPU?" moments involve the same few
dozen processes. For each one: what it is, what's normal for it, and what to
do when it misbehaves.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Info:
    what: str
    tip: str = ""
    normal: str = ""
    kind: str = "app"  # app | system | background


def _s(what: str, normal: str = "", tip: str = "") -> Info:
    return Info(what, tip, normal, "system")


def _bg(what: str, normal: str = "", tip: str = "") -> Info:
    return Info(what, tip, normal, "background")


def _app(what: str, normal: str = "", tip: str = "") -> Info:
    return Info(what, tip, normal, "app")


BROWSER_TIP = ("Each tab and extension runs as its own helper process. Open the browser's own task manager "
               "(Shift+Esc in Chrome, Edge and Brave; about:processes in Firefox) to see which tab is responsible, and close it.")
BROWSER = _app("your web browser; every tab and extension is a separate process.",
               "1 to 4 GB of memory is common, and each open tab adds roughly 100 to 500 MB. CPU should be low unless a page is playing video or running heavy scripts.",
               BROWSER_TIP)
SPOTLIGHT = _bg("Spotlight, indexing your files so search works.",
                "Near zero when idle. It can use one or more cores for minutes or hours after a macOS update, after copying lots of files, or after plugging in a new drive.",
                "It stops on its own when indexing finishes. To skip folders, add them in System Settings > Siri & Spotlight > Spotlight Privacy.")
ICLOUD = _bg("iCloud Drive, syncing your files.", "Quiet most of the time; busy while large files upload or download.",
             "Large syncs keep it busy until they finish. Pausing or removing huge folders from iCloud Drive calms it down.")
WEBKIT = _app("part of Safari (or another app that shows web pages).", "Each Safari tab has its own process; heavy pages use more.",
              "Close the tab that's playing video or animation. Safari > Settings > Advanced > Show features for web developers, then Develop > Show Page Resource Usage, finds heavy tabs.")
DEFENDER = _bg("Windows Security's antivirus scanner (Antimalware Service Executable).",
               "Light most of the time; a full scan can use a lot of CPU and disk for 15 to 60 minutes.",
               "Scan history is in Windows Security > Virus & threat protection. Scheduled scans can be moved to times you're not using the PC.")
SEARCH_WIN = _bg("Windows Search, indexing your files.", "Quiet when idle; heavy after updates or big file copies.",
                 "You can limit which folders are indexed in Settings > Privacy & security > Searching Windows.")
UPDATE_WIN = _bg("Windows installing updates in the background.", "Can use lots of CPU and disk for a while after updates download.",
                 "Let it finish and restart when asked; it calms down afterwards.")

KNOWN: dict[str, Info] = {
    # ---------------------------------------------------------------- macOS
    "kernel_task": _s("macOS itself (the kernel).",
                      "A few percent of CPU. When it suddenly uses a lot, it is usually holding the CPU back on purpose to cool the Mac down.",
                      "Let the Mac cool off: keep vents clear, use it on a hard surface, and close heavy apps."),
    "windowserver": _s("the part of macOS that draws everything on screen.",
                       "Usually 5 to 20% of a core. More with external displays, many open windows, video, or transparency effects.",
                       "Close graphics-heavy apps, or turn on System Settings > Accessibility > Display > Reduce transparency."),
    "mds_stores": SPOTLIGHT, "mds": SPOTLIGHT, "mdworker": SPOTLIGHT, "mdworker_shared": SPOTLIGHT,
    "corespotlightd": _bg("Spotlight, indexing content inside apps (Mail, Messages, Notes).", "Brief bursts after lots of new content.", "It stops on its own."),
    "backupd": _bg("Time Machine, making a backup.", "Busy for a while each hour when a backup drive is connected; longest right after reconnecting it.",
                   "Backups finish on their own. If the first backup is huge, let it run overnight."),
    "photoanalysisd": _bg("Photos, analysing your library for faces, objects and memories.", "Mostly runs while the Mac is plugged in and idle.", "It slows down when you use the Mac and finishes eventually."),
    "mediaanalysisd": _bg("macOS analysing photos and videos for search and Live Text.", "Mostly runs while the Mac is plugged in and idle.", "It finishes on its own."),
    "photolibraryd": _bg("the Photos library service.", "Quiet unless Photos is importing or syncing with iCloud Photos."),
    "bird": ICLOUD, "cloudd": ICLOUD,
    "fileproviderd": _bg("the service behind cloud folders (iCloud Drive, Dropbox, OneDrive, Google Drive).", "Busy while cloud files sync."),
    "nsurlsessiond": _bg("macOS downloading things in the background for apps (updates, podcasts, iCloud).", "Busy during big downloads."),
    "softwareupdated": _bg("macOS checking for, downloading or preparing a software update.", "Busy for a while when an update is downloading.", "Install the update so it stops working in the background."),
    "xprotectservice": _bg("macOS checking apps and files for malware.", "Short bursts after updates and when new apps are opened."),
    "syspolicyd": _bg("macOS verifying that apps are safe to open.", "Short bursts when apps launch."),
    "trustd": _bg("macOS checking security certificates.", "Short bursts, often while browsing."),
    "coreaudiod": _s("the macOS audio system.", "A few percent while sound is playing, more with Bluetooth headphones or audio effects."),
    "bluetoothd": _s("Bluetooth.", "Low; spikes when connecting devices or with many Bluetooth accessories."),
    "airportd": _s("Wi-Fi.", "Low; spikes while scanning for networks."),
    "logd": _s("the system log.", "Low. When it's busy, some other app is writing an unusual amount of log messages."),
    "cfprefsd": _s("the service that stores app settings.", "Low; a busy cfprefsd usually means an app is saving settings in a loop."),
    "launchd": _s("the process that starts every other process on macOS.", "Low."),
    "loginwindow": _s("your login session.", "Low."),
    "finder": _app("the Finder, which shows your files and the desktop.", "Low; busy while copying files or calculating folder sizes."),
    "dock": _s("the Dock, Launchpad and Mission Control.", "Low."),
    "controlcenter": _s("Control Center and menu bar items.", "Low."),
    "notificationcenter": _s("Notification Center and widgets.", "Low; widgets can add a little."),
    "sharingd": _bg("AirDrop, Handoff and other sharing features.", "Low; busy during AirDrop transfers."),
    "rapportd": _bg("Continuity: talking to your other Apple devices.", "Low."),
    "locationd": _bg("Location Services.", "Low."),
    "powerd": _s("power management (sleep, battery).", "Low."),
    "runningboardd": _s("macOS deciding which apps get CPU and memory.", "Low."),
    "com.apple.webkit.webcontent": WEBKIT, "com.apple.webkit.gpu": WEBKIT, "com.apple.webkit.networking": WEBKIT,
    "safari": _app("Safari, your web browser.", "Usually lighter than Chrome; each tab is its own WebKit process.", WEBKIT.tip),
    "mail": _app("Apple Mail, the email app.", "Low; busy while downloading or indexing a large mailbox."),
    "messages": _app("Messages, for iMessage and texts.", "Low; long conversations with lots of photos use more memory."),
    "photos": _app("Photos, your photo library.", "Busy while importing, editing or syncing iCloud Photos."),
    "music": _app("Apple Music, the music app.", "Low while playing."),
    "xcode": _app("Xcode, Apple's app development tool.", "Indexing a project and building can use several cores and many GB of memory.",
                  "Building and indexing finish on their own. Deleting Derived Data fixes indexing that never ends."),
    "simulator": _app("the iOS Simulator.", "Each running simulated device uses 1 to 2 GB of memory."),
    # -------------------------------------------------------------- Windows
    "system": _s("the Windows kernel and its drivers.", "A few percent. Sustained high CPU here usually points to a misbehaving driver.",
                 "Update chipset, storage, network and graphics drivers from your PC maker's website."),
    "registry": _s("the Windows registry.", "Low."),
    "memory compression": _s("Windows compressing memory because RAM is nearly full.", "Grows when memory is tight; it's a symptom, not a cause.",
                             "Close memory-hungry apps."),
    "microsoft defender": DEFENDER, "msmpeng": DEFENDER, "antimalware service executable": DEFENDER,
    "windows search": SEARCH_WIN, "searchindexer": SEARCH_WIN,
    "windows update": UPDATE_WIN, "tiworker": UPDATE_WIN,
    "desktop window manager": _s("the part of Windows that draws the desktop and windows (dwm.exe).",
                                 "A few percent; more with multiple or high-refresh displays.",
                                 "High use usually means a graphics-heavy app or a graphics driver problem; updating the graphics driver often helps."),
    "dwm": _s("the part of Windows that draws the desktop and windows.", "A few percent.", "Updating the graphics driver often helps."),
    "file explorer": _s("File Explorer, which also runs the taskbar and desktop.", "Low; busy while copying files or generating thumbnails.",
                        "If it's stuck, restart it from Task Manager (right-click Windows Explorer > Restart)."),
    "svchost": _s("a container for Windows services.", "Many copies is normal; each should be low.",
                  "Task Manager > Details, then right-click svchost.exe > Go to service(s), shows which service is busy."),
    "runtimebroker": _s("Windows managing permissions for Store apps.", "Low; high use means a Store app is misbehaving."),
    "csrss": _s("a core Windows process (client/server runtime).", "Low. Never end it."),
    "lsass": _s("Windows sign-in and security.", "Low."),
    "audiodg": _s("Windows audio processing.", "Low; high CPU often comes from audio enhancements.", "Turn off audio enhancements in Sound settings > your device > Advanced."),
    "wmiprvse": _s("WMI, which apps use to ask Windows about the system.", "Low; spikes mean some app is querying system info repeatedly."),
    "system interrupts": _s("time spent handling hardware signals.", "Under a few percent.",
                            "If it stays higher, a driver or device (often USB, audio or network) is misbehaving; try unplugging devices one at a time."),
    "onedrive": _bg("OneDrive, syncing files.", "Quiet most of the time; busy while syncing.", "Large syncs keep it busy until they finish."),
    # ---------------------------------------------------------------- Linux
    "kswapd0": _s("the Linux kernel moving memory to swap.", "Near zero. When it's busy, the computer is running out of memory.", "Close memory-hungry apps."),
    "xorg": _s("the display server that draws the screen.", "Low to moderate."),
    "gnome-shell": _s("the GNOME desktop.", "Low to moderate; extensions can add more."),
    "tracker-miner-fs-3": _bg("GNOME's file indexer.", "Busy after lots of new files; quiet otherwise."),
    "baloo_file": _bg("KDE's file indexer.", "Busy after lots of new files; quiet otherwise."),
    "systemd-journald": _s("the system log.", "Low; busy when something logs excessively."),
    "packagekitd": _bg("the software updater.", "Busy while checking for or installing updates."),
    # ------------------------------------------------------- everyday apps
    "slack": _app("Slack, the team chat app.", "300 MB to 1 GB of memory is typical; more with many workspaces.", "Quitting and reopening frees memory it has accumulated."),
    "discord": _app("Discord, for chat and voice calls.", "300 MB to 1 GB; more in video calls or while streaming.", "Turning off hardware acceleration in Settings > Advanced can help on older computers."),
    "spotify": _app("Spotify, the music app.", "200 to 500 MB and little CPU while playing.", "It caches music on disk; you can limit the cache in Settings > Storage."),
    "zoom": _app("Zoom video calls.", "Heavy during calls, especially with virtual backgrounds or screen sharing; idle otherwise.",
                 "Turning off virtual backgrounds and HD video makes calls much lighter."),
    "zoom.us": _app("Zoom video calls.", "Heavy during calls, especially with virtual backgrounds or screen sharing; idle otherwise.",
                    "Turning off virtual backgrounds and HD video makes calls much lighter."),
    "microsoft teams": _app("Microsoft Teams chat and video calls.", "500 MB to 1.5 GB is typical.", "Restarting Teams frees memory it has accumulated."),
    "visual studio code": _app("a code editor; each extension runs in its own process.", "300 MB to 1.5 GB depending on extensions and open projects.",
                               "Help > Open Process Explorer shows which extension is busy."),
    "code": _app("Visual Studio Code, a code editor.", "300 MB to 1.5 GB.", "Help > Open Process Explorer shows which extension is busy."),
    "cursor": _app("Cursor, a code editor.", "300 MB to 2 GB depending on extensions and projects."),
    "docker": _app("Docker's virtual machine.", "Reserves a fixed amount of memory and some CPU even when no containers run.",
                   "Lower the limits in Docker Desktop > Settings > Resources, or quit Docker when you're not using it."),
    "com.docker.backend": _app("Docker's virtual machine.", "Reserves memory even when idle.", "Lower the limits in Docker Desktop > Settings > Resources."),
    "notion": _app("Notion, for notes and docs.", "300 MB to 1 GB; large pages and databases use more."),
    "figma": _app("Figma, the design tool.", "Large design files can use several GB."),
    "steam": _app("Steam, the game store and launcher.", "Low in the background; busy while downloading or updating games."),
    "dropbox": _bg("Dropbox, syncing files.", "Quiet most of the time; busy while syncing."),
    "google drive": _bg("Google Drive, syncing files.", "Quiet most of the time; busy while syncing."),
    "microsoft word": _app("Microsoft Word, the word processor.", "200 to 600 MB."),
    "microsoft excel": _app("Microsoft Excel, the spreadsheet app.", "200 MB to a few GB for big spreadsheets; long recalculations can freeze it briefly."),
    "microsoft powerpoint": _app("Microsoft PowerPoint, for slides.", "300 MB to 1 GB."),
    "microsoft outlook": _app("Microsoft Outlook, for email and calendar.", "300 MB to 1 GB; busy while syncing or searching mail."),
    "python": _app("a Python program.", "Depends entirely on the program."),
    "node.js": _app("a Node.js program (often a developer tool or server).", "Depends entirely on the program."),
    "node": _app("a Node.js program (often a developer tool or server).", "Depends entirely on the program."),
    "java": _app("a Java program (Minecraft, IDEs and many tools run on Java).", "Java reserves memory up front, set by the program (-Xmx)."),
    "minecraft": _app("Minecraft, the game.", "Uses the memory it is given; 2 to 4 GB is typical."),
    "claude": _app("Claude (the desktop app, or Claude Code in a terminal).", "300 MB to 1.5 GB."),
    "chatgpt": _app("the ChatGPT desktop app.", "200 MB to 1 GB."),
    "freezeframe": _app("Freezeframe, recording in the background.", "About 1% of one core and 50 to 100 MB of memory."),
}

BROWSERS = {"google chrome", "brave browser", "microsoft edge", "firefox", "opera", "arc", "vivaldi", "chromium", "safari", "zen browser"}


def lookup(app: str | None, process: str | None = None) -> Info | None:
    for name in (app, process):
        if not name:
            continue
        key = name.lower().removesuffix(".exe")
        if key in KNOWN:
            return KNOWN[key]
        if key in BROWSERS:
            return BROWSER
    return None
