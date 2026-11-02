import json
import os

import pytest

from freezeframe import ai
from freezeframe.appinfo import _copyright_owner, _loose_binary, bundle_root, summary
from freezeframe.catalog import Catalog, kind_of, verdict
from freezeframe.detect import GB, MB
from freezeframe.storage import StorageScan, _measure

from test_server_cli import request, server  # noqa: F401

MACHINE = {"cores": 8, "memory": 16 * GB}


def test_bundle_root_is_the_outer_app():
    assert bundle_root("/Applications/Granola.app/Contents/Frameworks/Granola Helper.app/Contents/MacOS/Granola Helper") == "/Applications/Granola.app"
    assert bundle_root("/usr/libexec/opendirectoryd") is None


def test_copyright_owner():
    assert _copyright_owner("Copyright © 2026 Granola") == "Granola"
    assert _copyright_owner("© 2019-2024 Acme Inc. All rights reserved.") == "Acme Inc"
    assert _copyright_owner(None) is None


def test_summaries_from_app_details():
    menubar = {"bundle_id": "com.lwouis.alt-tab", "developer": "Louis Pontoise", "category": "utilities", "menubar": True}
    text = summary("AltTab", menubar, "darwin")
    assert text.startswith("A utilities app by Louis Pontoise.") and "menu bar" in text
    assert summary("NotchBox", {"bundle_id": "x", "category": "productivity", "app_store": True}, "darwin") == "A productivity app from the App Store."
    assert "Part of macOS" in summary("opendirectoryd", _loose_binary("/usr/libexec/opendirectoryd"), "darwin")
    assert "Electron" in summary("Thing", {"bundle_id": "x", "electron": True})
    assert summary("x", {}) is None


def test_kinds():
    assert kind_of("WindowServer", None, None, None) == "system"
    assert kind_of("Some App", None, {"kind": "background"}, None) == "background"
    assert kind_of("AltTab", None, None, {"bundle_id": "x", "menubar": True}) == "background"
    assert kind_of("cloudd", None, None, {}) == "background"
    assert kind_of("Granola", None, None, {}) == "app"


def test_verdicts():
    quiet_menubar = verdict("AltTab", "background", {"cpu_avg": 0.4}, 60 * MB, MACHINE, menubar=True)
    assert quiet_menubar["level"] == "fine" and quiet_menubar["label"] == "Made to stay open"
    assert verdict("Notes", "app", {"cpu_avg": 0.5}, 200 * MB, MACHINE)["label"] == "Fine to leave open"
    busy = verdict("Game", "app", {"cpu_avg": 160}, 2 * GB, MACHINE)
    assert busy["level"] == "heavy" and "20%" in busy["text"]  # 160% of one core on 8 cores
    assert verdict("Browser", "app", {"cpu_avg": 8}, 2.5 * GB, MACHINE)["level"] == "ok"
    assert verdict("Huge", "app", {"cpu_avg": 2}, 5 * GB, MACHINE)["level"] == "heavy"
    assert verdict("kernel_task", "system", {"cpu_avg": 300}, 0, MACHINE)["label"] == "Part of the system"


def test_catalog_prefers_builtin_then_ai_notes(demo_store):
    demo_store.set_app_notes({"Granola": {"known": True, "what": "a meeting notes app.", "kind": "app", "normal": "n", "stay_open": "s"}})
    d = Catalog("darwin", use_details=False).describe(demo_store, [("Slack", "Slack Helper"), ("Granola", None), ("Mystery", None)])
    assert d["Slack"]["source"] == "builtin"
    assert d["Granola"]["source"] == "ai" and d["Granola"]["what"] == "a meeting notes app."
    assert d["Mystery"]["source"] is None


def test_now_includes_verdicts(server):  # noqa: F811
    status, raw = request(server, "GET", "/api/now")
    n = json.loads(raw)
    assert status == 200 and n["ai"]["enabled"] is False
    assert all(a["verdict"]["label"] for a in n["apps"])


def test_ai_key_validation_and_storage(server):  # noqa: F811
    headers = {"Content-Type": "application/json"}
    status, raw = request(server, "POST", "/api/ai", {"key": "hello"}, headers)
    assert status == 400 and "sk-ant-" in json.loads(raw)["error"]
    status, raw = request(server, "POST", "/api/ai", {"key": "sk-ant-test-123"}, headers)
    assert status == 200 and json.loads(raw)["enabled"] is True
    assert ai.api_key() == "sk-ant-test-123"
    if os.name != "nt":
        assert oct(ai._key_file().stat().st_mode & 0o777) == "0o600"
    request(server, "POST", "/api/ai", {"forget": True}, headers)
    assert ai.api_key() is None or os.environ.get("ANTHROPIC_API_KEY")


def test_timeline_splits_cpu_by_kind(server):  # noqa: F811
    status, raw = request(server, "GET", "/api/overview")
    o = json.loads(raw)
    status, raw = request(server, "GET", f"/api/timeline?start={o['first']}&end={o['last']}&points=200")
    t = json.loads(raw)
    assert t["kinds"]["WindowServer"] == "system" and t["kinds"]["Brave Browser"] == "app"
    assert len(t["kind_series"]) == len(t["series"])
    k = t["kind_series"][10]
    assert 0 < k["apps"] < 100 and k["top"]


def test_demo_hardware_and_storage(server):  # noqa: F811
    status, raw = request(server, "GET", "/api/hardware")
    assert status == 200 and json.loads(raw)["model"] == "MacBook Air"
    status, raw = request(server, "GET", "/api/storage")
    st = json.loads(raw)
    assert st["state"] == "done" and st["items"][0]["size"] > st["items"][-1]["size"]


def test_storage_measures_and_only_reveals_scanned_folders(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.bin").write_bytes(b"x" * 100_000)
    size, files, done, denied = _measure(str(tmp_path), deadline=float("inf"))
    assert files == 1 and size >= 100_000 and done and not denied
    with pytest.raises(ValueError):
        StorageScan().reveal("/etc")
