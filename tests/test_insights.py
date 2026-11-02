import json
import shutil
import threading

from freezeframe import cli, notify
from freezeframe.detect import GB
from freezeframe.insights import insights, machine, snapshot
from freezeframe.knowledge import KNOWN, lookup
from freezeframe.models import AppUsage, Sample
from freezeframe.paths import db_path
from freezeframe.server import start_server
from freezeframe.store import Store

DEMO_MACHINE = {"platform": "darwin", "cores": 8, "memory": 16 * GB, "disk_total": 494 * GB, "disk_free": 37 * GB,
                "uptime": 9.3 * 86400, "battery": {"percent": 64, "plugged": True, "cycles": 412, "health": 86}}


def titles(items):
    return [i.title for i in items]


def test_demo_insights_cover_the_day(demo_store):
    found = insights(demo_store, DEMO_MACHINE)
    text = " | ".join(titles(found))
    assert "disk is 93% full" in text
    assert "hasn't restarted in 9 days" in text
    assert "Memory was tight" in text
    assert "growing steadily" in text
    assert "Battery health is 86%" in text
    assert "3 freezes, 1 app hang" in text
    assert found[0].level == "warning"  # most important first
    assert [i.level for i in found] == sorted((i.level for i in found), key=["warning", "tip", "info", "good"].index)


def test_healthy_machine_says_so(tmp_path):
    s = Store(tmp_path / "r.db")
    for i in range(100):
        s.add_sample(Sample(1000 + i * 2, 2, 8, 16 * GB, 10 * GB, 0, 0, 0, 0, 0, 0, 0, pressure=1), [AppUsage("Notes", 1, GB // 4)])
    found = insights(s, {"platform": "darwin", "disk_total": 500 * GB, "disk_free": 300 * GB, "uptime": 3600})
    assert titles(found) == ["Nothing needs your attention"]


def test_low_battery_health_and_full_disk_warn(tmp_path):
    s = Store(tmp_path / "r.db")
    found = insights(s, {"platform": "win32", "disk_total": 256 * GB, "disk_free": 6 * GB, "battery": {"percent": 50, "health": 71, "cycles": 900}})
    assert [i.level for i in found][:2] == ["warning", "warning"]


def test_snapshot_explains_running_apps(demo_store):
    snap = snapshot(demo_store, DEMO_MACHINE)
    assert snap["sample"]["mem_total"] == 16 * GB
    brave = next(a for a in snap["apps"] if a["app"] == "Brave Browser")
    assert "browser" in brave["what"] and brave["normal"] and "Shift+Esc" in brave["tip"]


def test_live_machine_facts():
    m = machine()
    assert m["cores"] >= 1 and m["memory"] > 0 and 0 < m["disk_free"] <= m["disk_total"] and m["uptime"] > 0


def test_knowledge_base_is_complete_and_findable():
    for key, info in KNOWN.items():
        assert info.what and info.kind in ("app", "system", "background"), key
    assert lookup("Google Chrome").kind == "app"
    assert lookup("MsMpEng.exe") is lookup("microsoft defender")
    assert lookup(None, "mds_stores").kind == "background"
    assert lookup("Some Unknown App") is None


def test_now_endpoint(demo_db, tmp_path):
    copy = tmp_path / "srv.db"
    shutil.copy(demo_db, copy)
    httpd, _ = start_server(copy)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    import http.client

    port = httpd.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/now", headers={"Host": f"127.0.0.1:{port}"})
    data = json.loads(conn.getresponse().read())
    httpd.shutdown()
    httpd.server_close()
    assert data["machine"]["battery"]["health"] == 86  # the demo's own machine, not this one
    assert data["apps"] and data["insights"]


def test_cli_now(demo_db, capsys):
    shutil.copy(demo_db, db_path())
    assert cli.main(["now"]) == 0
    out = capsys.readouterr().out
    assert "right now" in out and "Worth knowing" in out and "What's running" in out and "Brave Browser" in out


def test_notifications_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("FREEZEFRAME_NO_NOTIFY", "1")
    assert notify.notify("t", "b") is False
