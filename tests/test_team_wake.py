"""OPE-97 wake plumbing: the team trait, delivery cursors, lead subscriptions,
the registry + budget gate, pre-spawn at staffing, and digests."""

import pytest

from coworker.personas.loading import capability_set
from coworker.personas.manifest import ManifestError, parse_manifest
from coworker.server.manager import SessionManager
from coworker.teams import Actor, Role, TeamStore
from coworker.teams.registry import TeamRegistry, TeamWorker

USER = Actor(id="user", role=Role.USER)
LEAD = Actor(id="lead-1", role=Role.LEAD)
WORKER = Actor(id="swe-worker", role=Role.WORKER)
SPACE = "proj"


def manifest(team_line=""):
    return f"""---
id: t
name: T
family: code
tools: [search]
{team_line}
---
Prompt body.
"""


# ------------------------------------------------------------------- team trait

def test_team_trait_parses_and_gates_capabilities():
    lead = parse_manifest(manifest("team: lead"))
    worker = parse_manifest(manifest("team: worker"))
    solo = parse_manifest(manifest())
    assert lead.team == "lead" and worker.team == "worker" and solo.team is None
    assert "team:lead" in capability_set(lead)
    assert "team:worker" in capability_set(worker)
    assert not any(c.startswith("team:") for c in capability_set(solo))
    # the trait reaches the runtime Agent (it gates tool registration)
    assert lead.to_agent().team == "lead"


def test_invalid_team_trait_fails_loudly():
    with pytest.raises(ManifestError, match="team"):
        parse_manifest(manifest("team: manager"))


# ------------------------------------------------------- delivery cursors/queue

@pytest.fixture
def store(tmp_path):
    store = TeamStore(tmp_path / "teams.db")
    yield store
    store.close()


def assigned(store, assignee="swe-worker"):
    item = store.create_item(SPACE, LEAD, title="Task", criteria="tests pass")
    store.assign(SPACE, LEAD, item["id"], assignee)
    return item["id"]


def test_deliveries_are_durable_until_consumed(store):
    assigned(store)
    first = store.pending_for("swe-worker")
    assert len(first) == 1 and first[0]["kind"] == "item_assigned"
    # not consumed → still pending (crash-safe replay)
    assert store.pending_for("swe-worker") == first
    store.consume("swe-worker", first[-1]["seq"])
    assert store.pending_for("swe-worker") == []
    # a second assignment queues fresh
    assigned(store)
    assert len(store.pending_for("swe-worker")) == 1


def test_lead_subscriptions_are_an_allowlist(store):
    item_id = assigned(store)
    store.transition(SPACE, WORKER, item_id, "in_progress")  # not subscribed
    store.comment(SPACE, WORKER, item_id, "halfway")  # never wakes
    store.transition(SPACE, WORKER, item_id, "review", comment="done, please check")
    filed = store.create_item(SPACE, WORKER, title="Found a bug", criteria="fix")
    subs = store.subscribed_events(SPACE, "lead-1")
    # Exactly the worker's review transition + the worker's filing: the lead's own
    # verbs, the in_progress transition, and the comment never wake it.
    assert all(e["actor"] != "lead-1" for e in subs)
    assert {(e["kind"], e["payload"].get("to")) for e in subs} == {
        ("item_transitioned", "review"),
        ("item_created", None),
    }
    store.consume_subscription(SPACE, "lead-1", subs[-1]["seq"])
    assert store.subscribed_events(SPACE, "lead-1") == []
    _ = filed


# --------------------------------------------------------------- registry/budget

def test_registry_roundtrip_and_budget_cap(tmp_path):
    path = tmp_path / "teams.json"
    reg = TeamRegistry(path)
    team = reg.create(
        space=SPACE,
        lead_session="lead-sid",
        lead_actor="lead-1",
        workers=[TeamWorker(actor="swe-worker", persona="swe-worker", session_id="w1")],
    )
    again = TeamRegistry(path)
    loaded = again.get(team.team_id)
    assert loaded is not None and loaded.workers[0].session_id == "w1"
    assert again.for_lead_session("lead-sid").team_id == team.team_id
    assert again.for_worker_session("w1")[1].actor == "swe-worker"
    # budget gate: cap wakes, then refuse until the hour rolls
    assert all(again.count_wake(team.team_id, cap=3) for _ in range(3))
    assert again.count_wake(team.team_id, cap=3) is False


# -------------------------------------------------------- manager: spawn/digest

@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "repo"
    ws.mkdir()
    m = SessionManager(data_dir=tmp_path / "data", workspace=str(ws))
    yield m


def test_create_team_fails_closed_on_solo_personas(manager, tmp_path):
    from coworker.sessions import SessionRecord

    manager.session_store.save(
        SessionRecord(
            session_id="lead-sid",
            workspace=manager.default_workspace,
            model="m",
            mode="interactive",
            messages=[],
            agent="cowork",
        )
    )
    result = manager.create_team(
        "lead-sid", [{"persona": "cowork"}]
    )  # cowork is a solo builtin
    assert result["approved"] is False
    assert "team-capable" in result["error"] or "team: worker" in result["error"]
    assert manager.teams.all() == []  # nothing half-created


def test_create_team_prespawns_worker_sessions(manager, monkeypatch):
    from coworker.agents.base import Agent
    from coworker.sessions import SessionRecord

    worker_agent = Agent(
        name="swe-worker", title="SWE", system_prompt="p", team="worker"
    )
    monkeypatch.setattr(
        "coworker.server.manager.get_agent", lambda name: worker_agent
    )
    manager.session_store.save(
        SessionRecord(
            session_id="lead-sid",
            workspace=manager.default_workspace,
            model="m",
            mode="interactive",
            messages=[],
            agent="swe-lead",
        )
    )
    result = manager.create_team(
        "lead-sid",
        [{"persona": "swe-worker"}, {"persona": "swe-worker", "model": "other"}],
    )
    assert result["approved"] is True
    actors = [w["actor"] for w in result["workers"]]
    assert actors == ["swe-worker", "swe-worker-2"]  # unique actor ids
    # pre-spawn = state on disk, zero turns
    for w in result["workers"]:
        record = manager.session_store.load(w["session_id"])
        assert record is not None
        assert record.messages == []
        assert record.team["role"] == "worker"
        assert record.team["lead_session"] == "lead-sid"
    # the lead session is marked and the registry ties the roster
    lead = manager.session_store.load("lead-sid")
    assert lead.team["role"] == "lead"
    assert manager.teams.for_lead_session("lead-sid") is not None
    # second team on the same session refuses
    assert manager.create_team("lead-sid", [{"persona": "swe-worker"}])[
        "approved"
    ] is False


def test_staleness_digest_is_role_scoped(manager, monkeypatch):
    from coworker.agents.base import Agent
    from coworker.sessions import SessionRecord
    from coworker.teams.model import space_for_workspace

    # no team role → no digest (bare wake)
    assert manager.team_staleness_digest("nobody") == ""

    worker_agent = Agent(name="swe-worker", title="SWE", system_prompt="p", team="worker")
    monkeypatch.setattr("coworker.server.manager.get_agent", lambda name: worker_agent)
    manager.session_store.save(
        SessionRecord(
            session_id="lead-sid",
            workspace=manager.default_workspace,
            model="m",
            mode="interactive",
            messages=[],
            agent="swe-lead",
        )
    )
    manager.create_team("lead-sid", [{"persona": "swe-worker"}])
    space = space_for_workspace(manager.default_workspace)
    lead_actor = manager.teams.for_lead_session("lead-sid").lead_actor
    item = manager.team_store.create_item(
        space,
        Actor(id=lead_actor, role=Role.LEAD),
        title="Ship it",
        criteria="tests green",
    )
    digest = manager.team_staleness_digest("lead-sid")
    assert "1 open" in digest
    assert "no assignee" in digest
    _ = item


def test_team_options_lists_only_enabled_workers(manager):
    tool = manager._team_options_tool()
    workers = {w["persona"] for w in tool()["workers"]}
    assert {"swe-worker", "design-worker", "test-worker"} <= workers
    assert "swe-lead" not in workers  # leads staff, they aren't staffed
    assert "security" not in workers  # solo coworkers are not team-eligible


def test_turn_saves_never_detach_a_worker_from_its_team(manager, monkeypatch):
    from coworker.agents.base import Agent
    from coworker.sessions import SessionRecord

    worker_agent = Agent(name="swe-worker", title="SWE", system_prompt="p", team="worker")
    monkeypatch.setattr("coworker.server.manager.get_agent", lambda name: worker_agent)
    manager.session_store.save(
        SessionRecord(
            session_id="lead-sid",
            workspace=manager.default_workspace,
            model="m",
            mode="interactive",
            messages=[],
            agent="swe-lead",
        )
    )
    result = manager.create_team("lead-sid", [{"persona": "swe-worker"}])
    wid = result["workers"][0]["session_id"]
    # A per-turn save rebuilds the record WITHOUT the team field (the engine doesn't
    # carry it) — owner-hit 2026-08-16: this detached workers from the lead's entry.
    record = manager.session_store.load(wid)
    manager.session_store.save(
        SessionRecord(
            session_id=wid,
            workspace=record.workspace,
            model=record.model,
            mode=record.mode,
            messages=[{"role": "user", "content": "hi"}],
            agent=record.agent,
        )
    )
    assert manager.session_store.load(wid).team["lead_session"] == "lead-sid"
    assert manager.session_store.load("lead-sid").team["role"] == "lead"


# ------------------------------------------------------------------- chat (OPE-99)

def test_chat_groups_mentions_and_wake_reads(tmp_path):
    from coworker.teams.chat import ChatStore

    chat = ChatStore(tmp_path / "chat.db")
    group = chat.create_group(
        "team chat",
        [
            {"name": "nia", "persona": "swe-worker", "role": "worker"},
            {"name": "webb", "persona": "design-worker", "role": "worker"},
            {"name": "lead", "persona": "swe-lead", "role": "lead"},
        ],
    )
    gid = group["group_id"]
    # mention parsing against member handles; unknown handles ignored
    message = chat.post(gid, "lead", "does the api assume public logos? @nia @nobody")
    assert message["mentions"] == ["nia"]
    # mention-only wakes: nia woken, webb not; authors never wake themselves
    assert [m["seq"] for m in chat.unread_for(gid, "nia")] == [message["seq"]]
    assert chat.unread_for(gid, "webb") == []
    assert chat.unread_for(gid, "lead") == []
    chat.consume(gid, "nia", message["seq"])
    assert chat.unread_for(gid, "nia") == []
    # a USER post wakes every member
    chat.post(gid, "user", "ship it current-month only", author_role="user")
    assert len(chat.unread_for(gid, "nia")) == 1
    assert len(chat.unread_for(gid, "webb")) == 1
    assert len(chat.unread_for(gid, "lead")) == 1
    # badge count for the user (its own posts excluded)
    assert chat.unread_count(gid, "user") == 1


def test_create_team_uses_callnames_and_creates_the_chat_group(manager, monkeypatch):
    from coworker.agents.base import Agent
    from coworker.sessions import SessionRecord

    worker_agent = Agent(name="swe-worker", title="SWE", system_prompt="p", team="worker")
    monkeypatch.setattr("coworker.server.manager.get_agent", lambda name: worker_agent)
    manager.session_store.save(
        SessionRecord(
            session_id="lead-sid",
            workspace=manager.default_workspace,
            model="m",
            mode="interactive",
            messages=[],
            agent="swe-lead",
        )
    )
    bad = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "no spaces!"}])
    assert bad["approved"] is False and "callname" in bad["error"]
    result = manager.create_team(
        "lead-sid",
        [
            {"persona": "swe-worker", "name": "nia", "reason": "implementation"},
            {"persona": "swe-worker", "name": "nia"},  # dupe → suffixed
        ],
        enable_chat=True,
    )
    assert [w["actor"] for w in result["workers"]] == ["nia", "nia-2"]
    team = manager.teams.for_lead_session("lead-sid")
    assert team.chat_enabled and team.chat_group
    group = manager.chat_store.get_group(team.chat_group)
    assert {m["name"] for m in group["members"]} == {"nia", "nia-2", "lead"}
    # the worker digest carries the roster + how to reach teammates
    digest = manager._team_digest(team, [], [], is_lead=False)
    assert "Your team: nia (swe-worker — implementation)" in digest
    assert "@name in # team chat" in digest


def test_cancel_notice_is_addressed_to_the_assignee(store):
    item_id = assigned(store)
    store.consume("swe-worker", store.pending_for("swe-worker")[-1]["seq"])
    store.transition(SPACE, LEAD, item_id, "canceled", comment="scope cut")
    pending = store.pending_for("swe-worker")
    assert len(pending) == 1
    assert pending[0]["kind"] == "item_transitioned"
    assert pending[0]["payload"]["to"] == "canceled"
