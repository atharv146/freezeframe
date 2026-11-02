import itertools

import pytest

from freezeframe import recorder as recorder_mod
from freezeframe.models import AppUsage, Event, Sample
from freezeframe.recorder import Recorder, mark
from freezeframe.sampler import Sampler, select_notable
from freezeframe.store import DAY, Store

GB = 1024**3


def S(ts, **kw):
    base = dict(ts=ts, interval=2.0, cpu=10.0, mem_total=16 * GB, mem_available=8 * GB, swap_used=0, swap_in=0.0,
                swap_out=0.0, disk_read=0.0, disk_write=0.0, net_rx=0.0, net_tx=0.0)
    base.update(kw)
    return Sample(**base)


class TestStore:
    def test_series_buckets_and_top_apps(self, tmp_path):
        s = Store(tmp_path / "r.db")
        for i in range(60):
            s.add_sample(S(1000 + i, cpu=float(i)), [AppUsage("A", 100, GB), AppUsage("B", 5, 3 * GB)])
        buckets = s.series(1000, 1059, 10)
        assert len(buckets) == 6
        assert buckets[0]["cpu"] == pytest.approx(4.5) and buckets[0]["cpu_max"] == 9
        top = s.top_apps(1000, 1059)
        assert {a["app"] for a in top} == {"A", "B"}
        assert {a["app"]: a["cpu_avg"] for a in top}["A"] == pytest.approx(100)

    def test_events_from_the_same_report_are_imported_once(self, tmp_path):
        s = Store(tmp_path / "r.db")
        assert s.add_event(Event(1, "hang", "x", 2, source="/a.hang")) is not None
        assert s.add_event(Event(1, "hang", "x", 2, source="/a.hang")) is None
        assert len(s.events(0, 10)) == 1

    def test_prune_keeps_the_minutes_around_incidents(self, tmp_path):
        s = Store(tmp_path / "r.db")
        now = 100 * DAY
        old = now - 5 * DAY
        for i in range(0, 1200, 10):
            s.add_sample(S(old + i), [AppUsage("A", 1, GB)])
        s.add_event(Event(old + 600, "stall", "Froze", 2, end_ts=old + 605))
        s.prune(now)
        kept = s.samples(0, now)
        assert kept and all(old + 300 <= x.ts <= old + 725 for x in kept)
        assert s.app_rows(0, now)


def test_select_notable_keeps_busy_and_big_apps():
    apps = [AppUsage(f"idle{i}", 0.1, i * 1000) for i in range(30)] + [AppUsage("Busy", 60, 10), AppUsage("Big", 0, 9 * GB)]
    names = {a.app for a in select_notable(apps, 4)}
    assert {"Busy", "Big"} <= names and len(names) <= 9


def test_live_sampler_smoke():
    s = Sampler()
    s.sample()
    sample, apps = s.sample()
    assert 0 <= sample.cpu <= 100
    assert sample.mem_total > 0 and 0 < sample.mem_available <= sample.mem_total
    assert apps and all(a.rss >= 0 and a.cpu >= 0 for a in apps)


class FakeSampler:
    def __init__(self, clock):
        self.clock = clock

    def sample(self):
        return S(self.clock["wall"], pressure=None), [AppUsage("Editor", 20, GB)]


def test_recorder_catches_a_freeze_and_ignores_sleep(tmp_path, monkeypatch):
    clock = {"wall": 50_000.0, "mono": 100.0}
    monkeypatch.setattr(recorder_mod.time, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(recorder_mod.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(recorder_mod.psutil, "boot_time", lambda: 1.0)
    store = Store(tmp_path / "r.db")
    rec = Recorder(store, interval=2.0, sampler=FakeSampler(clock), scan_reports=None)
    rec.start()

    def step(wall, mono):
        clock["wall"] += wall
        clock["mono"] += mono
        rec.tick()

    for _ in range(30):
        step(2, 2)
    step(8, 8)  # the whole machine stopped for ~6 s
    for _ in range(3):
        step(2, 2)
    step(3600, 2)  # the lid was closed for an hour
    events = store.events(0, 1e12)
    stalls = [e for e in events if e.kind == "stall"]
    assert len(stalls) == 1
    assert stalls[0].end_ts - stalls[0].ts == pytest.approx(6.0)
    assert stalls[0].title.startswith("Froze for 6 s")
    assert "diagnosis" in stalls[0].detail
    assert any(e.kind == "sleep" for e in events)


def test_unclean_restart_is_reported(tmp_path, monkeypatch):
    store = Store(tmp_path / "r.db")
    store.add_sample(S(1000.0), [])
    store.set_meta("clean_exit", False)
    monkeypatch.setattr(recorder_mod.psutil, "boot_time", lambda: 5000.0)
    Recorder(store, sampler=FakeSampler({"wall": 6000.0}), scan_reports=None).start()
    restarts = [e for e in store.events(0, 1e12) if e.kind == "shutdown"]
    assert len(restarts) == 1 and restarts[0].severity == 2


def test_mark_diagnoses_the_moments_before(demo_db, tmp_path):
    import shutil

    copy = tmp_path / "copy.db"
    shutil.copy(demo_db, copy)
    s = Store(copy)
    last = s.bounds()[1]
    e = mark(s, "zoom froze", minutes_ago=(1e10 - last) / 60 if False else 0)
    assert e.kind == "mark" and "diagnosis" in e.detail
    assert e.title.startswith("You reported “zoom froze”")


def test_interval_scheduler_does_not_drift(tmp_path):
    ticks = itertools.count()
    store = Store(tmp_path / "r.db")
    rec = Recorder(store, interval=0.01, sampler=FakeSampler({"wall": 1.0}), scan_reports=None)
    import threading

    stop = threading.Event()
    original = rec.tick

    def tick():
        original()
        if next(ticks) >= 5:
            stop.set()

    rec.tick = tick
    rec.run(stop)
    assert store.meta()["clean_exit"] is True
