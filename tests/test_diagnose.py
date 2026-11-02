"""The demo recording contains one of each kind of incident; each must get the right verdict."""

import pytest

from freezeframe.diagnose import diagnose
from freezeframe.models import AppUsage, Event, Sample
from freezeframe.store import Store

GB = 1024**3


def incidents(store):
    return {e.kind + (":" + str(int((e.end_ts or e.ts) - e.ts)) if e.kind == "stall" else ""): e for e in store.incidents()}


@pytest.mark.parametrize(
    "key, cause, culprit",
    [
        ("stall:9", "memory", "Brave Browser"),
        ("stall:4", "thermal", "kernel_task"),
        ("stall:5", "quiet", None),
        ("hang", "hang", None),
        ("mark", "disk", "mds_stores"),
    ],
)
def test_demo_incidents_get_the_right_cause(demo_store, key, cause, culprit):
    e = incidents(demo_store)[key]
    d = diagnose(demo_store, e.ts, e.end_ts, e, "darwin")
    assert d["findings"][0]["cause"] == cause, d["findings"][0]["headline"]
    if culprit:
        text = " ".join(d["findings"][0]["evidence"]) + d["short"]
        assert culprit in text


def test_memory_verdict_has_evidence_and_actions(demo_store):
    e = incidents(demo_store)["stall:9"]
    d = diagnose(demo_store, e.ts, e.end_ts, e, "darwin")
    assert d["confidence"] == "high"
    top = d["findings"][0]
    assert top["headline"] == "Your Mac ran out of memory"
    assert any("critical memory pressure" in x for x in top["evidence"])
    assert any("Shift+Esc" in a for a in top["actions"])
    assert d["culprits"][0]["app"] == "Brave Browser"


def test_app_hang_on_a_calm_system_blames_the_app(demo_store):
    e = incidents(demo_store)["hang"]
    d = diagnose(demo_store, e.ts, e.end_ts, e, "darwin")
    assert d["headline"] == "Microsoft Excel stopped responding on its own"


def test_platform_specific_hardware_advice(demo_store):
    e = incidents(demo_store)["stall:5"]
    mac = diagnose(demo_store, e.ts, e.end_ts, e, "darwin")["findings"][0]["actions"]
    win = diagnose(demo_store, e.ts, e.end_ts, e, "win32")["findings"][0]["actions"]
    assert any("Apple Diagnostics" in a for a in mac)
    assert any("Windows Memory Diagnostic" in a for a in win)


def test_no_recording(tmp_path):
    s = Store(tmp_path / "empty.db")
    d = diagnose(s, 1_000_000.0, 1_000_000.0)
    assert d["findings"][0]["cause"] == "no-data"


def test_os_hardware_error_dominates(tmp_path):
    s = Store(tmp_path / "hw.db")
    t0 = 2_000_000.0
    for i in range(80):
        ts = t0 + i * 2
        s.add_sample(Sample(ts, 2.0, 12.0, 16 * GB, 9 * GB, 0, 0, 0, 0, 0, 0, 0), [AppUsage("Notes", 3, GB // 4)])
    s.add_event(Event(t0 + 150, "hardware", "The graphics driver stopped responding and was restarted", 2, source="x"))
    stall = Event(t0 + 155, "stall", "Froze", 2, end_ts=t0 + 160)
    stall.id = s.add_event(stall)
    d = diagnose(s, stall.ts, stall.end_ts, stall, "win32")
    assert d["findings"][0]["cause"] == "hardware"
    assert any("graphics" in a.lower() for a in d["findings"][0]["actions"])
