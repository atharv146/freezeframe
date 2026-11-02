# Freezeframe

**A flight recorder for your computer.** It quietly keeps the last couple of days of what every app was doing, notices freezes on its own, and when something goes wrong it tells you why, in plain English.

[![CI](https://github.com/atharv146/freezeframe/actions/workflows/ci.yml/badge.svg)](https://github.com/atharv146/freezeframe/actions/workflows/ci.yml) ![macOS, Windows, Linux](https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux-13202e) ![License: MIT](https://img.shields.io/badge/license-MIT-13202e)

**[Try the live demo in your browser](https://atharv146.github.io/freezeframe/)**, no install needed, or [install it](#install) in one line.

![The Freezeframe dashboard explaining a 9-second freeze: a browser tab leaked memory for an hour until the Mac ran out and started swapping](docs/dashboard.png)

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
- a description of every running app: what it is, what's normal for it, and what to do if it's causing trouble ("mds_stores is Spotlight indexing your files; it's heavy after updates and stops on its own").

Above that, **Worth knowing** pulls out what matters from the last day: a disk that's nearly full, a battery that's worn out, memory that was tight for an hour, an app that seems to be leaking memory, or simply that nothing needs your attention.

![The Right now view: readouts for CPU, memory, disk, battery and uptime, memory and CPU by app, a list of things worth knowing, and plain-English descriptions of each running app](docs/right-now.png)

When it catches a freeze of three seconds or more, it shows a desktop notification with the cause, so you don't even have to go looking.

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

![Dark mode: a 4-second freeze explained as overheating, with kernel_task throttling the CPU during a Zoom call](docs/dashboard-dark.png)

## Install

**macOS and Linux**, in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/atharv146/freezeframe/main/install.sh | sh
```

**Windows**, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/atharv146/freezeframe/main/install.ps1 | iex
```

The installer needs Python 3.10 or newer, and no administrator rights. It installs Freezeframe into its own folder, adds the `freezeframe` command, and starts recording in the background whenever you log in. Read the scripts first if you like: [install.sh](install.sh), [install.ps1](install.ps1). If you already use pipx:

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
| `freezeframe why --at 3:42pm` | Explains a moment you choose (`10m`, `15:42` and ISO times work too) |
| `freezeframe mark "zoom froze"` | Notes that something just went wrong and explains the moments before it |
| `freezeframe list` / `show <n>` | Lists incidents / explains one |
| `freezeframe open` | Opens the timeline dashboard in your browser |
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

Everything stays on your computer. Nothing is uploaded. The dashboard listens only on `127.0.0.1` and rejects requests from other websites by checking the `Host` and `Origin` headers.

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

## License

MIT
