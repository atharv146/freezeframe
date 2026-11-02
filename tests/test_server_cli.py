import http.client
import json
import shutil
import threading

import pytest

from freezeframe import cli
from freezeframe.paths import db_path
from freezeframe.server import start_server


@pytest.fixture
def server(demo_db, tmp_path):
    copy = tmp_path / "srv.db"
    shutil.copy(demo_db, copy)
    httpd, url = start_server(copy)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = conn.getresponse()
    data = r.read()
    return r.status, data


def test_api_round_trip(server):
    status, raw = request(server, "GET", "/api/overview")
    assert status == 200
    o = json.loads(raw)
    assert o["demo"] is True and len(o["incidents"]) == 5
    status, raw = request(server, "GET", f"/api/timeline?start={o['first']}&end={o['last']}&points=300")
    t = json.loads(raw)
    assert status == 200 and 200 <= len(t["series"]) <= 320 and t["apps"] and t["events"]
    inc = next(i for i in o["incidents"] if i["kind"] == "stall" and i["severity"] == 3)
    status, raw = request(server, "GET", f"/api/diagnose?id={inc['id']}")
    assert json.loads(raw)["diagnosis"]["headline"] == "Your Mac ran out of memory"
    status, raw = request(server, "GET", f"/api/diagnose?at={inc['ts']}")
    assert status == 200 and json.loads(raw)["event"] is None


def test_static_files(server):
    status, raw = request(server, "GET", "/")
    assert status == 200 and b"Freezeframe" in raw
    assert request(server, "GET", "/app.js")[0] == 200
    assert request(server, "GET", "/../pyproject.toml")[0] == 404


def test_other_websites_are_refused(server):
    assert request(server, "GET", "/api/overview", headers={"Host": "evil.example"})[0] == 403
    assert request(server, "POST", "/api/mark", {"note": "x"}, {"Content-Type": "application/json", "Origin": "https://evil.example"})[0] == 403
    assert request(server, "POST", "/api/mark", {"note": "x"}, {"Content-Type": "text/plain"})[0] == 403


def test_mark_via_api(server):
    status, raw = request(server, "POST", "/api/mark", {"note": "music stuttered"}, {"Content-Type": "application/json"})
    assert status == 200
    assert "music stuttered" in json.loads(raw)["event"]["title"]


def test_cli_why_list_and_show(demo_db, capsys):
    shutil.copy(demo_db, db_path())
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Froze for 9 s: memory ran out" in out
    incident_id = int(out.split("Froze for 9 s")[1].split("#")[1].split()[0])
    assert cli.main(["show", str(incident_id)]) == 0
    out = capsys.readouterr().out
    assert "Your Mac ran out of memory" in out and "What you can do" in out
    assert cli.main(["why", "--at", "2026-01-01T00:00:00"]) == 0
    assert "wasn't recording" in capsys.readouterr().out


def test_cli_without_recording(capsys):
    assert cli.main(["why"]) == 1
    assert "no recording yet" in capsys.readouterr().out.lower()


def test_parse_moment():
    import time

    assert abs(cli.parse_moment("10m") - (time.time() - 600)) < 2
    assert abs(cli.parse_moment("90 sec ago") - (time.time() - 90)) < 2
    assert cli.parse_moment("3:42pm") <= time.time()


def test_cli_uses_the_platform_the_recording_was_made_on(demo_db, capsys, monkeypatch):
    shutil.copy(demo_db, db_path())
    monkeypatch.setattr(cli.sys, "platform", "win32")
    cli.main(["list"])
    incident_id = int(capsys.readouterr().out.split("Froze for 9 s")[1].split("#")[1].split()[0])
    cli.main(["show", str(incident_id)])
    out = capsys.readouterr().out
    assert "Your Mac ran out of memory" in out and "Your PC" not in out
