import json
from datetime import datetime

import pytest

from freezeframe import cli
from freezeframe.what import what_happened
from freezeframe.when import NotUnderstood, Window, parse

from test_server_cli import request, server  # noqa: F401

NOW = datetime(2026, 11, 2, 16, 30).timestamp()


def hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %H:%M")


@pytest.mark.parametrize("text, start, end", [
    ("3pm", "Mon 14:45", "Mon 15:15"),
    ("around 3", "Mon 14:45", "Mon 15:15"),
    ("3:42", "Mon 15:39", "Mon 15:45"),
    ("15:42", "Mon 15:39", "Mon 15:45"),
    ("20 minutes ago", "Mon 16:07", "Mon 16:13"),
    ("10m", "Mon 16:18", "Mon 16:22"),
    ("just now", "Mon 16:25", "Mon 16:30"),
    ("the last 2 hours", "Mon 14:30", "Mon 16:30"),
    ("this morning", "Mon 06:00", "Mon 12:00"),
    ("yesterday evening", "Sun 17:00", "Sun 22:00"),
    ("between 2 and 3pm", "Mon 14:00", "Mon 15:00"),
    ("from 2:30 to 3", "Mon 14:30", "Mon 15:00"),
    ("yesterday at 4", "Sun 15:45", "Sun 16:15"),
    ("what happened at 3:40pm?", "Mon 15:37", "Mon 15:43"),
    ("around 5pm", "Sun 16:45", "Sun 17:15"),  # 5pm today hasn't happened yet
])
def test_parse_times_people_say(text, start, end):
    w = parse(text, NOW)
    assert (hm(w.start), hm(w.end)) == (start, end)


def test_labels_read_naturally():
    assert parse("yesterday at 4", NOW).label == "around 4:00 PM yesterday"
    assert parse("this evening", NOW).label == "yesterday evening"
    assert parse("20 min ago", NOW).label.startswith("about 4:10 PM")


def test_nonsense_is_not_understood():
    with pytest.raises(NotUnderstood):
        parse("banana", NOW)


def window(store, m0, m1):
    first, _ = store.bounds()
    return Window(first + m0 * 60, first + m1 * 60, "then")


def test_quiet_stretch_says_nothing_happened(demo_store):
    r = what_happened(demo_store, window(demo_store, 2, 25), "darwin")
    assert r["verdict"] == "quiet" and r["focus"] is None
    assert r["headline"] == "Nothing unusual happened then"


def test_freeze_in_the_window_comes_first(demo_store):
    r = what_happened(demo_store, window(demo_store, 150, 205), "darwin")
    assert r["verdict"] == "problem"
    assert "memory ran out" in r["headline"]
    assert r["lines"][0]["level"] == "problem" and "incident" in r["focus"]
    assert any("Memory ran low" in line["text"] for line in r["lines"])


def test_before_the_recording_explains_why_there_is_nothing(demo_store):
    r = what_happened(demo_store, window(demo_store, -40, -20), "darwin")
    assert r["verdict"] == "no-data"
    assert "recording starts" in r["lines"][-1]["text"]


def test_what_endpoint(server):  # noqa: F811
    status, raw = request(server, "GET", "/api/what?q=banana")
    assert status == 400 and "didn't understand" in json.loads(raw)["error"]
    status, raw = request(server, "GET", "/api/overview")
    o = json.loads(raw)
    status, raw = request(server, "GET", f"/api/what?start={o['first'] + 9000}&end={o['first'] + 12300}&label=x")
    assert status == 200 and json.loads(raw)["verdict"] == "problem"
    status, raw = request(server, "GET", "/api/strip")
    strip = json.loads(raw)
    assert strip["first"] == o["first"] and len(strip["series"]) > 50


def test_cli_what(demo_db, capsys, monkeypatch):
    monkeypatch.setattr(cli, "db_path", lambda: demo_db)
    assert cli.main(["what", "banana"]) == 1
    assert cli.main(["what", "the", "last", "hour"]) in (0,)
    out = capsys.readouterr().out
    assert "didn't understand" in out
