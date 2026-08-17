"""OPE-100 — the board as an open surface: claim + policy knob, the BoardDialect
seam (local and remote), token-bound identity on `/v1/board`, and the MCP/CLI
front doors."""

import json

import pytest

from coworker.teams import Actor, AuthorityError, BoardError, JournalStore, Role, TeamStore
from coworker.teams.dialect import LocalDialect, RemoteDialect, local_dialect
from coworker.teams.tokens import BoardTokens

USER = Actor(id="user", role=Role.USER)
LEAD = Actor(id="lead-1", role=Role.LEAD, persona="swe-lead")
NIA = Actor(id="nia", role=Role.WORKER, persona="swe-worker")
WEBB = Actor(id="webb", role=Role.WORKER, persona="swe-worker")


@pytest.fixture
def store(tmp_path):
    journal = JournalStore(tmp_path / "journal.db")
    store = TeamStore(tmp_path / "teams.db", journal=journal)
    yield store
    store.close()
    journal.close()


def seed(store, space="proj", case=None):
    return store.create_item(
        space, LEAD, title="Build the API", criteria="routes pass tests", case=case
    )


# ------------------------------------------------------------------ claim verb


def test_claim_self_assigns_an_open_item(store):
    item = seed(store)
    claimed = store.claim("proj", NIA, item["id"])
    assert claimed["assignee"] == "nia"
    assert claimed["state"] == "open"  # claiming is not starting


def test_second_claim_loses_cleanly(store):
    item = seed(store)
    store.claim("proj", NIA, item["id"])
    with pytest.raises(BoardError, match="already claimed by nia"):
        store.claim("proj", WEBB, item["id"])


def test_claim_requires_open_and_unassigned(store):
    item = seed(store)
    store.assign("proj", LEAD, item["id"], "nia")
    # an assigned item is not claimable, even while still open
    with pytest.raises(BoardError, match="already claimed by nia"):
        store.claim("proj", WEBB, item["id"])
    store.transition("proj", NIA, item["id"], "in_progress")
    # and a non-open item never is
    with pytest.raises(BoardError, match="only open items"):
        store.claim("proj", WEBB, item["id"])


def test_lead_only_policy_blocks_worker_claims(store):
    item = seed(store)
    store.set_policy("proj", LEAD, claims="lead-only")
    with pytest.raises(AuthorityError, match="lead-only"):
        store.claim("proj", NIA, item["id"])
    # flipping back re-opens the queue
    store.set_policy("proj", USER, claims="open")
    assert store.claim("proj", NIA, item["id"])["assignee"] == "nia"


def test_policy_defaults_open_and_validates(store):
    assert store.policy("proj") == {"claims": "open"}
    with pytest.raises(BoardError):
        store.set_policy("proj", LEAD, claims="anarchy")
    with pytest.raises(AuthorityError):
        store.set_policy("proj", NIA, claims="lead-only")


def test_claim_feeds_the_lead_subscription(store):
    item = seed(store)
    store.claim("proj", NIA, item["id"])
    subs = store.subscribed_events("proj", "lead-1")
    claims = [e for e in subs if e["kind"] == "item_assigned"]
    assert len(claims) == 1
    assert claims[0]["actor"] == "nia"
    assert claims[0]["payload"]["claimed"] is True


def test_lead_assigns_are_not_subscription_news(store):
    item = seed(store)
    store.assign("proj", USER, item["id"], "nia")
    subs = store.subscribed_events("proj", "lead-1")
    assert not [e for e in subs if e["kind"] == "item_assigned"]


def test_claim_feeds_journal_grants_like_assignment(store):
    item = seed(store, case="case-alpha")
    store.claim("proj", NIA, item["id"])
    # nia can now read the case it was granted through the claim
    assert "case-alpha" in store.journal.cases(NIA)


# ------------------------------------------------------------------ dialects


def test_local_dialect_binds_identity(tmp_path):
    dialect = local_dialect(tmp_path, actor="nia", role="worker")
    assert dialect.whoami() == {"actor": "nia", "role": "worker"}
    with pytest.raises(AuthorityError):
        dialect.assign("proj", 1, "webb")  # workers never assign


def test_local_dialect_full_worker_loop(tmp_path):
    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    item = lead.create_item(
        "proj", title="Build it", criteria="tests pass", case="case-b"
    )
    worker = LocalDialect(lead.store, lead.journal, NIA)
    claimed = worker.claim("proj", item["id"])
    assert claimed["assignee"] == "nia"
    worker.transition("proj", item["id"], "in_progress")
    worker.journal_append("case-b", "found the flaky fixture", kind="finding")
    worker.transition("proj", item["id"], "review", comment="branch ready")
    shown = worker.get_item("proj", item["id"])
    assert shown["state"] == "review"
    assert [e["body"] for e in worker.journal_read("case-b")] == [
        "found the flaky fixture"
    ]


# ------------------------------------------------------------- HTTP board API


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The real FastAPI app over a real manager state dir, driven in-process."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_API_TOKEN", "sidecar-secret")
    from coworker.permissions import Mode
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    manager = SessionManager(
        workspace=None,
        data_dir=tmp_path / "state",
        model="openai:gpt-test",
        mode=Mode("interactive"),
    )
    from fastapi.testclient import TestClient

    app = create_app(manager)
    client = TestClient(app, base_url="http://board.test")
    yield client, manager, app
    client.close()


def _tokens(manager) -> BoardTokens:
    return manager.board_tokens


def test_board_api_requires_a_token(api):
    client, _, _ = api
    response = client.get("/v1/board/items", params={"space": "proj"})
    assert response.status_code == 401
    assert "board token" in response.json()["error"]


def test_board_api_rejects_the_sidecar_token_as_a_board_token(api):
    client, _, _ = api
    response = client.get(
        "/v1/board/whoami",
        headers={"Authorization": "Bearer sidecar-secret"},
    )
    assert response.status_code == 401


def test_token_binds_identity_and_store_enforces_authority(api):
    client, manager, app = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    lead = {"Authorization": f"Bearer {lead_token}"}
    nia = {"Authorization": f"Bearer {nia_token}"}

    assert client.get("/v1/board/whoami", headers=nia).json() == {
        "actor": "nia",
        "role": "worker",
    }

    created = client.post(
        "/v1/board/items",
        headers=lead,
        json={"space": "proj", "title": "Build it", "criteria": "tests pass"},
    )
    assert created.status_code == 200
    item_id = created.json()["id"]

    # a worker token cannot assign — 403 from the store's authority check
    denied = client.post(
        "/v1/board/items/assign",
        headers=nia,
        json={"space": "proj", "id": item_id, "assignee": "nia"},
    )
    assert denied.status_code == 403

    # but it can claim, then work the item
    claimed = client.post(
        "/v1/board/items/claim", headers=nia, json={"space": "proj", "id": item_id}
    )
    assert claimed.status_code == 200
    assert claimed.json()["assignee"] == "nia"

    moved = client.post(
        "/v1/board/items/transition",
        headers=nia,
        json={"space": "proj", "id": item_id, "to": "in_progress"},
    )
    assert moved.status_code == 200

    # bad input is a 400 with the store's message, not a 500
    bad = client.post(
        "/v1/board/items/transition",
        headers=nia,
        json={"space": "proj", "id": item_id, "to": "done"},
    )
    assert bad.status_code == 403 or bad.status_code == 400


def test_remote_dialect_round_trip(api):
    client, manager, app = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    from fastapi.testclient import TestClient

    lead = RemoteDialect(
        "http://board.test",
        lead_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    nia = RemoteDialect(
        "http://board.test",
        nia_token,
        client=TestClient(app, base_url="http://board.test"),
    )

    item = lead.create_item(
        "proj", title="Remote item", criteria="works over the wire", case="case-r"
    )
    assert lead.policy("proj") == {"claims": "open"}
    claimed = nia.claim("proj", item["id"])
    assert claimed["assignee"] == "nia"
    nia.transition("proj", item["id"], "in_progress")
    nia.journal_append("case-r", "wire finding", kind="finding", item=item["id"])
    nia.transition("proj", item["id"], "review", comment="ready", refs=["branch:x"])

    # the lead sees the review, verifies, and closes
    shown = lead.get_item("proj", item["id"])
    assert shown["state"] == "review"
    assert "branch:x" in shown["refs"]
    entries = lead.journal_read("case-r")
    assert entries[0]["body"] == "wire finding"
    done = lead.transition("proj", item["id"], "done")
    assert done["state"] == "done"

    # errors surface as BoardError with the server's message
    with pytest.raises(BoardError, match="only open items"):
        nia.claim("proj", item["id"])

    # policy flip over the wire blocks the next worker claim
    second = lead.create_item("proj", title="Held back", criteria="c")
    lead.set_policy("proj", claims="lead-only")
    with pytest.raises(BoardError, match="lead-only"):
        nia.claim("proj", second["id"])


def test_pending_and_consume_over_the_wire(api):
    client, manager, app = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    from fastapi.testclient import TestClient

    lead = RemoteDialect(
        "http://board.test",
        lead_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    nia = RemoteDialect(
        "http://board.test",
        nia_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    item = lead.create_item("proj", title="Queued", criteria="c")
    lead.assign("proj", item["id"], "nia")
    events = nia.pending()
    assert events and events[-1]["kind"] == "item_assigned"
    nia.consume(events[-1]["seq"])
    assert nia.pending() == []


# ------------------------------------------------------------------ tokens


def test_tokens_are_hash_stored_and_revocable(tmp_path):
    tokens = BoardTokens(tmp_path / "board-tokens.json")
    token = tokens.mint("nia", "worker", label="laptop")
    # plaintext never touches disk
    assert token not in (tmp_path / "board-tokens.json").read_text()
    actor = tokens.resolve(token)
    assert (actor.id, actor.role) == ("nia", Role.WORKER)
    assert tokens.resolve("owb_forged") is None
    assert tokens.revoke(token[:12]) == 1
    assert tokens.resolve(token) is None


def test_token_mint_validates_role(tmp_path):
    tokens = BoardTokens(tmp_path / "board-tokens.json")
    with pytest.raises(ValueError):
        tokens.mint("nia", "admin")


# ------------------------------------------------------------------ MCP server


def test_mcp_tool_surface_is_role_scoped(tmp_path):
    import anyio

    from coworker.teams.mcp_server import build

    worker = build(
        local_dialect(tmp_path, actor="nia", role="worker"), space="proj"
    )
    lead = build(
        local_dialect(tmp_path, actor="lead-1", role="lead"), space="proj"
    )

    def names(server):
        return {tool.name for tool in anyio.run(server.list_tools)}

    worker_names = names(worker)
    lead_names = names(lead)
    assert "board_claim" in worker_names
    assert "board_assign" not in worker_names
    assert "board_policy" not in worker_names
    assert {"board_assign", "board_link", "board_policy"} <= lead_names
    assert "journal_append" in worker_names


def test_mcp_worker_loop_through_call_tool(tmp_path):
    import anyio

    from coworker.teams.mcp_server import build

    lead_dialect = local_dialect(tmp_path, actor="lead-1", role="lead")
    item = lead_dialect.create_item("proj", title="Via MCP", criteria="c")
    worker = build(
        LocalDialect(lead_dialect.store, lead_dialect.journal, NIA), space="proj"
    )

    def call(name, arguments):
        return anyio.run(lambda: worker.call_tool(name, arguments))

    call("board_claim", {"item": item["id"]})
    call("board_move", {"item": item["id"], "to": "in_progress"})
    shown = lead_dialect.get_item("proj", item["id"])
    assert (shown["assignee"], shown["state"]) == ("nia", "in_progress")


# ------------------------------------------------------------------ CLI


def test_cli_headless_flow(tmp_path, capsys):
    from coworker.teams.cli import main

    space_args = ["--db", str(tmp_path), "--space", "proj"]
    assert main(
        ["board", "create", "CLI item", "--criteria", "prints", *space_args,
         "--actor", "lead-1", "--role", "lead"]
    ) == 0
    capsys.readouterr()
    assert main(
        ["board", "claim", "1", *space_args, "--actor", "nia", "--role", "worker"]
    ) == 0
    assert "claimed #1" in capsys.readouterr().out
    assert main(["board", "list", *space_args, "--json"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert [(i["id"], i["assignee"]) for i in items] == [(1, "nia")]
    # a losing claim exits 1 with the store's message on stderr
    assert main(
        ["board", "claim", "1", *space_args, "--actor", "webb", "--role", "worker"]
    ) == 1
    assert "already claimed by nia" in capsys.readouterr().err
    # policy knob round-trips
    assert main(["board", "policy", "--claims", "lead-only", *space_args]) == 0
    assert "lead-only" in capsys.readouterr().out
    # journal append + read
    assert main(
        ["journal", "append", "case-cli", "found it", "--kind", "finding",
         *space_args]
    ) == 0
    capsys.readouterr()
    assert main(["journal", "read", "case-cli", *space_args]) == 0
    assert "found it" in capsys.readouterr().out


def test_cli_token_mint_and_list(tmp_path, capsys):
    from coworker.teams.cli import main

    assert main(
        ["board", "token", "mint", "--actor", "nia", "--role", "worker",
         "--label", "laptop", "--db", str(tmp_path)]
    ) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("owb_")
    assert main(["board", "token", "list", "--db", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "nia" in out and "laptop" in out and token not in out
