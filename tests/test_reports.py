from pathlib import Path

from freezeframe.platforms.macos import parse_cputime, parse_ps
from freezeframe.reports import parse_journal, parse_mac_report, parse_windows_events, scan_macos

FIXTURES = Path(__file__).parent / "fixtures"


def report(name):
    return parse_mac_report(FIXTURES / name)


class TestMacReports:
    def test_cpu_resource_report(self):
        e = report("WindowServer_2026-10-29-084100_testmac.cpu_resource.diag")
        assert e.kind == "resource" and e.app == "WindowServer" and e.severity == 1
        assert "96% of a core" in e.title and "2 min" in e.title

    def test_disk_writes_report_names_the_app_from_its_path(self):
        e = report("Brave Browser Helper_2026-11-01-115139_testmac.diag")
        assert e.app == "Brave Browser" and "8589.96 MB" in e.title and e.severity == 0

    def test_daily_system_summary_is_skipped(self):
        assert report("disk writes_2026-10-26-213735_testmac.diag") is None

    def test_hang(self):
        e = report("Pages_2026-10-30-101500_testmac.hang")
        assert e.kind == "hang" and e.title == "Pages stopped responding for 20 s"
        assert e.end_ts > e.ts

    def test_crash_ips(self):
        e = report("Spotify-2026-10-28-220102.ips")
        assert e.kind == "crash" and e.app == "Spotify" and "EXC_BAD_ACCESS (SIGSEGV)" in e.title

    def test_kernel_panic(self):
        e = report("panic-full-2026-10-27-031244.0002.ips")
        assert e.kind == "panic" and e.severity == 3 and "watchdog timeout" in e.detail["panic"]

    def test_scan_dedupes_by_path(self):
        events = scan_macos(0, [FIXTURES])
        assert len(events) == 5
        assert len({e.source for e in events}) == 5


def test_windows_events():
    events = parse_windows_events((FIXTURES / "windows_events.json").read_text())
    kinds = [(e.kind, e.app) for e in events]
    assert ("hang", "Google Chrome") in kinds
    assert ("crash", "Discord") in kinds
    assert any(e.kind == "hardware" and "graphics driver" in e.title for e in events)
    assert any(e.kind == "shutdown" and e.severity == 3 for e in events)
    assert any(e.kind == "hardware" and "fatal" in e.title for e in events)
    assert any(e.kind == "pressure" for e in events)
    assert len(events) == 6  # the Kernel-Power power-source event is ignored
    assert parse_windows_events("") == []
    assert len(parse_windows_events(open(FIXTURES / "windows_events.json").read().split("\n")[0].strip("[,") + "")) == 1


def test_linux_journal():
    lines = [
        "1730000000.5 host kernel: Out of memory: Killed process 4242 (firefox) total-vm:123kB",
        "1730000100.0 host kernel: watchdog: BUG: soft lockup - CPU#3 stuck for 23s!",
        "1730000200.0 host kernel: mce: [Hardware Error]: Machine check events logged",
        "1730000300.0 host kernel: usb 1-1: new high-speed USB device",
    ]
    events = parse_journal(lines)
    assert [e.kind for e in events] == ["pressure", "stall", "hardware"]
    assert events[0].app == "firefox"


def test_ps_parsing():
    rows = parse_ps("  172 1 1183:23.37  97392 /System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer\n"
                    "  335 1 1-02:03:04 100 /usr/libexec/thing with spaces\n")
    assert rows[0].pid == 172 and abs(rows[0].cpu_seconds - (1183 * 60 + 23.37)) < 1e-6 and rows[0].rss == 97392 * 1024
    assert rows[1].path == "/usr/libexec/thing with spaces"
    assert parse_cputime("1-02:03:04") == 86400 + 2 * 3600 + 3 * 60 + 4
