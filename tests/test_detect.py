from freezeframe.apps import app_name
from freezeframe.detect import GB, MB, Detectors, classify_gap, leak_rate
from freezeframe.models import AppUsage, Sample


def sample(ts, **kw):
    base = dict(ts=ts, interval=2.0, cpu=10.0, mem_total=16 * GB, mem_available=8 * GB, swap_used=0, swap_in=0.0,
                swap_out=0.0, disk_read=0.0, disk_write=0.0, net_rx=0.0, net_tx=0.0)
    base.update(kw)
    return Sample(**base)


class TestAppNames:
    def test_mac_helpers_roll_up_to_the_app(self):
        path = "/Applications/Brave Browser.app/Contents/Frameworks/Brave Browser Framework.framework/Versions/1/Helpers/Brave Browser Helper (Renderer).app/Contents/MacOS/Brave Browser Helper (Renderer)"
        assert app_name(path) == "Brave Browser"
        assert app_name("/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder") == "Finder"

    def test_system_binaries_keep_their_name(self):
        assert app_name("/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/Versions/A/Support/mds_stores") == "mds_stores"

    def test_windows_executables_get_friendly_names(self):
        assert app_name(r"C:\Program Files\Google\Chrome\Application\chrome.exe") == "Google Chrome"
        assert app_name(r"C:\ProgramData\Microsoft\Windows Defender\Platform\4.18\MsMpEng.exe") == "Microsoft Defender"
        assert app_name(r"C:\Tools\mytool.exe") == "mytool"

    def test_helper_suffixes_are_dropped(self):
        assert app_name("Slack Helper (GPU)") == "Slack"
        assert app_name("") == "unknown"


class TestClassifyGap:
    def test_on_time(self):
        assert classify_gap(0, 0, 2.05, 2.05, 2.0) == ("ok", 0.0)

    def test_freeze_moves_both_clocks(self):
        kind, seconds = classify_gap(0, 100, 9.0, 109.0, 2.0)
        assert kind == "stall" and abs(seconds - 7.0) < 1e-9

    def test_sleep_only_moves_the_wall_clock(self):
        assert classify_gap(0, 100, 3600, 102, 2.0)[0] == "sleep"

    def test_small_jitter_is_not_a_freeze(self):
        assert classify_gap(0, 0, 4.0, 4.0, 2.0)[0] == "ok"

    def test_very_long_gap_counts_as_sleep(self):
        assert classify_gap(0, 0, 900, 900, 2.0)[0] == "sleep"


class TestDetectors:
    def test_cpu_hog_reported_when_burst_ends(self):
        d = Detectors()
        events = []
        for i in range(20):
            events += d.observe(sample(i * 2.0), [AppUsage("Miner", 380, GB)])
        events += d.observe(sample(40.0), [AppUsage("Miner", 5, GB)])
        hog = [e for e in events if e.kind == "cpu" and e.app == "Miner"]
        assert len(hog) == 1 and "3.8 cores" in hog[0].title

    def test_memory_jump(self):
        d = Detectors()
        events = []
        for i in range(30):
            events += d.observe(sample(i * 2.0), [AppUsage("Chrome", 10, int(2 * GB + i * 0.1 * GB))])
        assert any(e.kind == "memory" and e.app == "Chrome" for e in events)

    def test_critical_pressure_fires_once_per_cooldown(self):
        d = Detectors()
        events = []
        for i in range(10):
            events += d.observe(sample(i * 2.0, pressure=4), [AppUsage("Chrome", 10, 9 * GB)])
        assert sum(e.kind == "pressure" for e in events) == 1

    def test_thermal_heavy(self):
        d = Detectors()
        events = d.observe(sample(0, thermal=2), [])
        assert events and events[0].kind == "thermal" and events[0].severity == 2

    def test_swap_storm(self):
        d = Detectors()
        events = []
        for i in range(6):
            events += d.observe(sample(i * 2.0, swap_out=80 * MB), [])
        assert any(e.kind == "swap" for e in events)


def test_leak_rate_finds_steady_growth_only():
    steady = [(i * 60.0, int(1.2 * GB + i * 15 * MB)) for i in range(61)]
    assert leak_rate(steady) is not None
    flat = [(i * 60.0, int(3 * GB + (i % 3) * MB)) for i in range(61)]
    assert leak_rate(flat) is None
    short = steady[:20]
    assert leak_rate(short) is None
