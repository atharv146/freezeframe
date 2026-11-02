# Freezeframe

**A flight recorder for your computer.** It quietly keeps the last couple of days of what every app was doing, notices freezes on its own, and when something goes wrong it tells you why, in plain English.

[![CI](https://github.com/atharv146/freezeframe/actions/workflows/ci.yml/badge.svg)](https://github.com/atharv146/freezeframe/actions/workflows/ci.yml) ![macOS, Windows, Linux](https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux-13202e) ![License: MIT](https://img.shields.io/badge/license-MIT-13202e)

**[Try the live demo in your browser](https://atharv146.github.io/freezeframe/)**, no install needed, or [install it](#install) in one line.

![The Freezeframe timeline over four hours: plain numbers for the stretch on top, processor use split into your apps and background, with spikes labeled (zoom.us, mds_stores, kernel_task), memory climbing as a browser tab leaks, and three freezes marked](docs/dashboard.png)

## Why

Your laptop locks up for ten seconds. By the time you open Activity Monitor or Task Manager, everything looks normal, because those tools only show *right now*. The evidence is gone. The tools that do keep history are built for experts: Console and Event Viewer are walls of log lines, Reliability Monitor lists crashes with no context, and Performance Monitor needs you to set up a trace *before* the problem happens.

Freezeframe is always recording, so after a freeze you can rewind and ask:

```
$ freezeframe why

Froze for 9 s: memory ran out (Brave Browser was the biggest user)
Today 11:14:11 PM, lasting 9 s

  Your Mac ran out of memory  high confidence
  When memory fills up, the system starts parking data on the disk (swapping) and fetching
  it back when it's needed. Disks are far slower than memory, so apps, and sometimes the
  whole computer, stall while they wait.

  What the recording shows
  - macOS reported critical memory pressure.
  - Free memory fell to 2% (256 MB of 16.0 GB).
  - The system was swapping memory to disk at up to 325 MB/s.
  - Brave Browser was using 9.4 GB.

  What you can do
  1. Quit or restart Brave Browser to give its memory back.
  2. Each tab and extension runs as its own helper process. Open the browser's own task
     manager (Shift+Esc in Chrome, Edge and Brave; about:processes in Firefox) to see which
     tab is responsible, and close it.
  3. Close apps you aren't using. If this keeps happening, your usual set of apps needs
     more memory than this computer has (16.0 GB).
```

In that recording it had also flagged *"Brave Browser has been growing steadily (+5.3 GB per hour); it may be leaking memory"* almost an hour before the freeze.

## A task manager that explains itself

Freezeframe also answers the everyday question: *what is my computer doing right now, and is that normal?* The **Right now** view (or `freezeframe now` in a terminal) shows:
- CPU and memory broken down by app, with each app's helper processes added up;
- disk space, battery health and uptime;
- a description of every running app: what it is, what's normal for it, and what to do if it's causing trouble ("mds_stores is Spotlight indexing your files; it's heavy after updates and stops on its own");
- for each app, whether it's fine to leave open, based on what it actually used over the last hour: *Made to stay open*, *Fine to leave open*, *OK to leave open*, or *Close when not using*. Point at an app to see why.

Apps that aren't in Freezeframe's built-in list are identified from their own details: who signed them, their App Store category, whether they live in the menu bar, whether they're built on Electron ("AltTab: a utilities app by Louis Pontoise. It lives in the menu bar instead of the Dock, so it keeps running with no window open"). If you add your own Anthropic API key, Claude writes a short plain-English description for the rest, once per app. Only app names, developers and categories are sent; it's off until you turn it on (`freezeframe ai --on`, or the button in the dashboard).

Above that, **Worth knowing** pulls out what matters from the last day: a disk that's nearly full, a battery that's worn out, memory that was tight for an hour, an app that seems to be leaking memory, or simply that nothing needs your attention.

![What's running: each app with a plain-English description, memory, its share of the processor, and whether it's fine to leave open; pointing at Slack shows why it's fine to leave open](docs/right-now.png)

## "What happened at 3pm?"

Type a time the way you'd say it, in the dashboard's **What happened at** box or in a terminal, and Freezeframe looks over that stretch: `3:40pm`, `around 3`, `20 minutes ago`, `the last hour`, `yesterday evening`, `between 2 and 3pm`. It answers with the freezes, hangs and crashes in that window first, then whether the processor, memory, disk or heat were under strain and which app was responsible, then the most likely cause. When nothing stood out it says so, and says what it can't see (a slow website, the network).

![Asking "around 9:40am": the Mac was under strain, memory ran low because Brave Browser was using 9.2 GB, it was swapping, and the most likely cause is running out of memory](docs/what-happened.png)

```
$ freezeframe what around 9:20am
Your Mac was under strain around 9:20 AM
  * Memory ran low for 18 min, down to 256 MB free around 9:16 AM; Brave Browser was using 8.7 GB.
  * Your Mac was moving memory to and from the disk (swapping) for 18 min.
  + The processor was calm: 16% on average, never above 19%.
```

## A timeline anyone can read

The timeline opens with plain numbers for the stretch on screen (average and peak processor use, the busiest app, peak memory, how many problems), then splits processor use into **Your apps** and **Background and system**, so it's obvious whether a spike came from something you opened or from the OS. The biggest spikes are labeled with their size and the app behind them. A strip above the charts shows the whole recording; drag its window or click anywhere to move there. Arrow keys, a sideways trackpad swipe, + and −, and **Previous / Next problem** get you around.

## This computer

The **This computer** tab (or `freezeframe computer --space`) explains the hardware in practical terms ("16 GB: plenty for everyday use and a few heavy apps at once"), shows the drive's self-test result and battery health, and finds what's using disk space: apps and games, caches, videos, Xcode build files, simulators, phone backups, Steam games, developer caches, each with how to free it safely. It only measures; it never deletes anything. Downloads, Desktop, Documents and the Trash are only measured when you ask, since your OS asks permission first.

![This computer: the chip, memory, graphics, drive and battery, each explained, and a disk space breakdown with Steam games, movies, apps, Xcode build files and how to free each](docs/this-computer.png)

## Lives in your menu bar

After installing, a Freezeframe icon sits in the macOS menu bar (or the Windows system tray), so you never need a terminal. Click it to see live CPU and memory, the apps using the most memory, the latest incident with its cause, and anything that needs your attention. From there you can open the dashboard, ask **What just happened?**, or press **Something went wrong…** the moment you notice a problem. A small badge appears on the icon after a freeze or when memory runs critically low, and freezes of three seconds or more also pop up as a desktop notification with the cause.

## What it catches

| Problem | How it's detected |
|---|---|
| The whole computer freezing | A watchdog: the recorder expects to wake every 2 s, so a late wake-up is a freeze. Sleep is told apart by comparing two clocks (see below). |
| An app hanging or crashing | Imports the hang and crash reports macOS and Windows already write but hide away |
| Running out of memory, swapping | OS memory pressure, free memory and swap traffic |
| One app hogging the CPU | Per-app CPU, with helper processes added up under the app's real name |
| Slow memory leaks | A steady climb over the last hour (linear fit with R² ≥ 0.85) |
| An overloaded disk | Read and write throughput; per app on Windows and Linux |
| Overheating and throttling | macOS thermal pressure, and `kernel_task` holding the CPU back |
| Hardware and driver errors | Kernel panics, blue screens, WHEA hardware errors, graphics driver resets, disk errors, unexpected restarts |

Each verdict comes with the numbers behind it, a confidence level, and what to do about it, tailored to Mac, Windows or Linux. When nothing was busy during a freeze, it says so. That's a finding too: a calm machine that still froze points at drivers or hardware, not your apps.

![Dark mode: the 16 minutes around a 4-second freeze during a Zoom call, with zoom.us in the Your apps lane and kernel_task in the Background lane as the Mac overheats](docs/dashboard-dark.png)

## Install

**macOS and Linux**, in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/atharv146/freezeframe/main/install.sh | sh
```

**Windows**, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/atharv146/freezeframe/main/install.ps1 | iex
```

The installer needs Python 3.10 or newer, and no administrator rights. It installs Freezeframe into its own folder, adds the `freezeframe` command, starts recording in the background, and puts the Freezeframe icon in your menu bar or tray; both start again whenever you log in. Read the scripts first if you like: [install.sh](install.sh), [install.ps1](install.ps1). If you already use pipx:

```bash
pipx install git+https://github.com/atharv146/freezeframe && freezeframe install
```

To see what it does before anything has gone wrong, open the demo recording, with five kinds of incidents:

```bash
freezeframe open --demo
```

To remove it: `freezeframe uninstall` stops the background recording and `freezeframe forget --yes` deletes the recording.

## Use

| Command | What it does |
|---|---|
| `freezeframe now` | What's running right now, explained, plus things worth knowing |
| `freezeframe why` | Explains the latest freeze, or the last two minutes if nothing froze |
| `freezeframe what 20 minutes ago` | What happened at a time you describe, in your own words |
| `freezeframe why --at 3:42pm` | Explains one moment you choose |
| `freezeframe computer --space` | What's inside this computer, and where the disk space went |
| `freezeframe ai --on` | Describe unfamiliar apps with Claude, using your own API key |
| `freezeframe mark "zoom froze"` | Notes that something just went wrong and explains the moments before it |
| `freezeframe list` / `show <n>` | Lists incidents / explains one |
| `freezeframe open` | Opens the timeline dashboard in your browser |
| `freezeframe menubar` | Shows the menu-bar / tray icon (`install` starts it at login) |
| `freezeframe status` | Is it recording, how far back, and how much space it uses |
| `freezeframe uninstall` / `forget --yes` | Stops background recording / deletes the recording |

In the dashboard, **Right now** shows the live breakdown. On the **Timeline**, click any incident or moment to get an explanation, and drag across the chart to zoom. Use **Something went wrong** whenever you notice a problem. To turn off desktop notifications, set `FREEZEFRAME_NO_NOTIFY=1` or record with `--no-notify`.

## How it works

```
 sampler (every 2 s) ──► detectors ──► SQLite recording ◄── OS report importer (every 60 s)
   CPU, memory, swap,      freezes,       samples, per-app      macOS DiagnosticReports,
   disk, heat, per-app     spikes,        usage, events         Windows event logs,
   usage                   leaks                                 Linux kernel journal
                                               │
                                     diagnosis engine ──► CLI and local dashboard
```

- **Freeze detection with two clocks.** The recorder sleeps until its next 2-second tick. If the machine locks up, the recorder can't run either, so it wakes late, and the delay is the freeze length. Sleep also makes it wake late, but the *wall clock* keeps counting during sleep while the *monotonic clock* pauses. During a freeze both keep counting, so comparing them separates "froze for 6 s" from "the lid was closed for an hour".
- **Seeing system processes without admin rights.** On macOS, Python can't read the CPU time of root processes like WindowServer or Spotlight's `mds_stores`, and those matter most. So Freezeframe reads them through `ps`, which can, and computes CPU% from the change in each process's CPU time between samples.
- **Apps, not processes.** Browsers and editors run dozens of helpers. Freezeframe adds them up under the app you recognize, using the outermost `.app` bundle on macOS and a name map on Windows.
- **Diagnosis.** For an incident, it looks at the two minutes before and scores each cause from the evidence: memory, CPU, disk, heat, an app hanging on its own, or a hardware or driver error the OS logged. It also compares against the preceding half hour to call out what's unusual. A knowledge base explains the usual suspects (Spotlight, Time Machine, Windows Defender, Windows Update, `kernel_task`, WindowServer, `dwm.exe` and more) in plain words.
- **Storage.** A local SQLite file of about 40 MB per day. Detailed history is trimmed after 2 days, but the minutes around each incident are kept for 30 days.
- **Overhead.** About 1% of one CPU core on an M-series MacBook (measured: the recorder itself 0.4%, plus the `ps` snapshot).

## Privacy

Everything stays on your computer. Nothing is uploaded, with one opt-in exception: if you add an Anthropic API key, the names, developers and categories of apps Freezeframe doesn't recognize are sent to Claude to be described (requests use `claude-opus-5-5` with server-side fallbacks enabled). No file paths, window titles or usage data are sent. The dashboard listens only on `127.0.0.1` and rejects requests from other websites by checking the `Host` and `Origin` headers.

## Platform support

| | macOS | Windows | Linux |
|---|---|---|---|
| Freeze detection, CPU, memory, swap, disk | ✓ | ✓ | ✓ |
| Per-app disk traffic | needs admin, not used | ✓ | ✓ |
| Memory pressure from the OS | ✓ | low-memory events | out-of-memory kills |
| Heat and throttling | ✓ | not yet | not yet |
| App hangs and crashes | DiagnosticReports | Event Log | – |
| Kernel panics and hardware errors | panic reports | blue screens, WHEA, GPU resets, disk errors | machine checks, GPU hangs, I/O errors |
| Starts at login | launchd agent | Task Scheduler | systemd user service |
| Menu-bar / tray icon | ✓ | ✓ | desktops with tray support (`pip install 'freezeframe[menubar]'`) |

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The tests cover:
- the freeze/sleep classifier and every detector;
- the report parsers, against sample macOS (`.ips`, `.hang`, `.diag`, panic), Windows event-log and Linux journal data;
- storage and pruning;
- a recorder run with a simulated freeze and a simulated sleep;
- each diagnosis scenario in the demo recording;
- the HTTP API, including its cross-site protections;
- the CLI.

CI runs the tests on macOS, Windows and Linux.

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). The easiest way to help is adding apps to the knowledge base in `src/freezeframe/knowledge.py`.

## License

MIT
