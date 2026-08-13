"""Settings wiring for Auto-Approve (spec §1.5 / Part 6 step 3).

Covers the prefs-backed feature flag + shadow toggle: default off, REST round-trip,
persistence across a manager restart, config.toml fallback, and the build-engine override
so a flag flip takes effect on the next session build without a config edit.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.server.app import create_app
from coworker.server.manager import SessionManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate config.toml: no hand-set flag leaking in from the dev machine.
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))


def test_flags_default_off(client):
    s = client.get("/v1/settings").json()
    assert s["auto_approve"] is False
    assert s["auto_approve_shadow"] is False


def test_set_auto_approve_roundtrip(client):
    r = client.post("/v1/settings/auto-approve", json={"auto_approve": True}).json()
    assert r["ok"] and r["auto_approve"] is True
    assert client.get("/v1/settings").json()["auto_approve"] is True
    # Off again.
    client.post("/v1/settings/auto-approve", json={"auto_approve": False})
    assert client.get("/v1/settings").json()["auto_approve"] is False


def test_shadow_is_independent_of_the_live_flag(client):
    client.post("/v1/settings/auto-approve-shadow", json={"auto_approve_shadow": True})
    s = client.get("/v1/settings").json()
    assert s["auto_approve"] is False  # untouched
    assert s["auto_approve_shadow"] is True


def test_flags_persist_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    c1 = TestClient(create_app(SessionManager(data_dir=data_dir)))
    c1.post("/v1/settings/auto-approve", json={"auto_approve": True})

    reborn = SessionManager(data_dir=data_dir)
    assert reborn.auto_approve() is True
    assert TestClient(create_app(reborn)).get("/v1/settings").json()["auto_approve"] is True


def test_prefs_falls_back_to_config_when_unset(tmp_path, monkeypatch):
    # No prefs key set → the manager reads config.toml. Point config at a file that enables it.
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "config.toml").write_text("auto_approve = true\n")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(state))
    mgr = SessionManager(data_dir=tmp_path / "data")
    assert mgr.auto_approve() is True  # config value, no prefs entry
    # A prefs write then wins over config.
    mgr.set_auto_approve(False)
    assert mgr.auto_approve() is False


def test_build_engine_override_beats_config(tmp_path, monkeypatch):
    # build_engine's auto_approve arg (what the server passes) overrides the config value.
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.agent import build_engine
    from coworker.agents.chat import chat_agent

    # config has it off by default; override to on → a reviewer is attached.
    engine = build_engine(agent=chat_agent(), auto_approve=True, auto_approve_shadow=False)
    assert engine.reviewer is not None
    engine2 = build_engine(agent=chat_agent(), auto_approve=False, auto_approve_shadow=False)
    assert engine2.reviewer is None
