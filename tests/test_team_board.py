"""Board verbs: state-machine legality and role authority (the capability firebreak)."""

import pytest

from coworker.teams import Actor, AuthorityError, BoardError, Role, TeamStore
from coworker.teams.tools import board_tools

USER = Actor(id="user", role=Role.USER)
LEAD = Actor(id="lead-1", role=Role.LEAD)
WORKER = Actor(id="worker-1", role=Role.WORKER)
OTHER = Actor(id="worker-2", role=Role.WORKER)
SPACE = "proj"


@pytest.fixture
def store(tmp_path):
    store = TeamStore(tmp_path / "teams.db")
    yield store
    store.close()


def assigned_item(store, assignee="worker-1"):
    item = store.create_item(SPACE, LEAD, title="Task", criteria="tests pass")
    store.transition(SPACE, USER, item["id"], "approved")
    store.assign(SPACE, LEAD, item["id"], assignee)
    return item["id"]


# ------------------------------------------------------------------ create_item

def test_acceptance_criteria_are_required(store):
    with pytest.raises(BoardError, match="criteria"):
        store.create_item(SPACE, LEAD, title="Vague hope", criteria="  ")


def test_workers_cannot_create_items(store):
    with pytest.raises(AuthorityError):
        store.create_item(SPACE, WORKER, title="Scope creep", criteria="c")


def test_child_inherits_parent_case(store):
    parent = store.create_item(
        SPACE, LEAD, title="Root", criteria="c", case="findings"
    )
    child = store.create_item(
        SPACE, LEAD, title="Child", criteria="c", parent=parent["id"]
    )
    assert child["case_id"] == "findings"
    assert {"kind": "parent", "item": parent["id"]} in child["links"]


# ------------------------------------------------------------------- transitions

def test_full_happy_path(store):
    item_id = assigned_item(store)
    store.transition(SPACE, WORKER, item_id, "in_progress")
    store.transition(SPACE, WORKER, item_id, "review", comment="branch green")
    done = store.transition(SPACE, LEAD, item_id, "done")
    assert done["state"] == "done"


def test_illegal_edges_rejected(store):
    item = store.create_item(SPACE, LEAD, title="T", criteria="c")
    with pytest.raises(BoardError, match="illegal transition"):
        store.transition(SPACE, USER, item["id"], "done")
    with pytest.raises(BoardError, match="illegal transition"):
        store.transition(SPACE, USER, item["id"], "in_progress")


def test_only_the_user_approves(store):
    item = store.create_item(SPACE, LEAD, title="T", criteria="c")
    with pytest.raises(AuthorityError, match="decomposition gate"):
        store.transition(SPACE, LEAD, item["id"], "approved")
    approved = store.transition(SPACE, USER, item["id"], "approved")
    assert approved["state"] == "approved"


def test_workers_never_mark_done(store):
    item_id = assigned_item(store)
    store.transition(SPACE, WORKER, item_id, "in_progress")
    store.transition(SPACE, WORKER, item_id, "review")
    with pytest.raises(AuthorityError, match="review"):
        store.transition(SPACE, WORKER, item_id, "done")


def test_worker_cannot_touch_someone_elses_item(store):
    item_id = assigned_item(store, assignee="worker-1")
    with pytest.raises(AuthorityError, match="not assigned"):
        store.transition(SPACE, OTHER, item_id, "in_progress")


def test_worker_cannot_cancel(store):
    item_id = assigned_item(store)
    with pytest.raises(AuthorityError):
        store.transition(SPACE, WORKER, item_id, "canceled")


def test_cancel_is_a_board_verb_and_reopen_works(store):
    item_id = assigned_item(store)
    store.transition(SPACE, LEAD, item_id, "canceled")
    reopened = store.transition(SPACE, LEAD, item_id, "approved")
    assert reopened["state"] == "approved"
    assert reopened["assignee"] == "worker-1"  # reassignable — assignment survives


def test_done_is_terminal(store):
    item_id = assigned_item(store)
    store.transition(SPACE, WORKER, item_id, "in_progress")
    store.transition(SPACE, WORKER, item_id, "review")
    store.transition(SPACE, LEAD, item_id, "done")
    with pytest.raises(BoardError, match="illegal transition"):
        store.transition(SPACE, USER, item_id, "in_progress")


def test_rework_loop(store):
    item_id = assigned_item(store)
    store.transition(SPACE, WORKER, item_id, "in_progress")
    store.transition(SPACE, WORKER, item_id, "review")
    store.transition(SPACE, LEAD, item_id, "in_progress", comment="criteria 2 unmet")
    item = store.get_item(SPACE, item_id)
    assert item["state"] == "in_progress"
    assert item["comments"][-1]["body"] == "criteria 2 unmet"


# ---------------------------------------------------------------- assign / link

def test_cannot_assign_before_approval(store):
    item = store.create_item(SPACE, LEAD, title="T", criteria="c")
    with pytest.raises(BoardError, match="after approval"):
        store.assign(SPACE, LEAD, item["id"], "worker-1")


def test_workers_cannot_assign_or_link(store):
    item_id = assigned_item(store)
    with pytest.raises(AuthorityError):
        store.assign(SPACE, WORKER, item_id, "worker-2")
    other = store.create_item(SPACE, LEAD, title="B", criteria="c")
    with pytest.raises(AuthorityError):
        store.link(SPACE, WORKER, item_id, "blocks", other["id"])


def test_parent_cycles_rejected(store):
    a = store.create_item(SPACE, LEAD, title="A", criteria="c")
    b = store.create_item(SPACE, LEAD, title="B", criteria="c", parent=a["id"])
    with pytest.raises(BoardError, match="cycle"):
        store.link(SPACE, LEAD, a["id"], "parent", b["id"])


def test_blocked_by_shows_on_the_other_side(store):
    a = store.create_item(SPACE, LEAD, title="A", criteria="c")
    b = store.create_item(SPACE, LEAD, title="B", criteria="c")
    store.link(SPACE, LEAD, a["id"], "blocks", b["id"])
    assert {"kind": "blocked_by", "item": a["id"]} in store.get_item(SPACE, b["id"])[
        "links"
    ]


# ------------------------------------------------------------- worker visibility

def test_worker_sees_only_its_slice(store):
    mine = assigned_item(store, assignee="worker-1")
    theirs = assigned_item(store, assignee="worker-2")
    linked = store.create_item(SPACE, LEAD, title="Dep", criteria="c")
    store.link(SPACE, LEAD, linked["id"], "blocks", mine)
    visible = {item["id"] for item in store.list_items(SPACE, WORKER)}
    assert visible == {mine, linked["id"]}
    assert theirs not in visible
    # the user sees everything
    assert len(store.list_items(SPACE, USER)) == 3


def test_worker_comments_only_on_its_slice(store):
    theirs = assigned_item(store, assignee="worker-2")
    with pytest.raises(AuthorityError, match="assigned"):
        store.comment(SPACE, WORKER, theirs, "drive-by")


# ------------------------------------------------------------------- tool layer

def test_tool_sets_are_role_filtered(store):
    lead_names = {tool.__name__ for tool in board_tools(store, space=SPACE, actor=LEAD)}
    worker_names = {
        tool.__name__ for tool in board_tools(store, space=SPACE, actor=WORKER)
    }
    assert lead_names == {"create_item", "list_items", "transition", "comment", "assign", "link"}
    assert worker_names == {"list_items", "transition", "comment"}


def test_tools_return_errors_instead_of_raising(store):
    tools = {t.__name__: t for t in board_tools(store, space=SPACE, actor=WORKER)}
    result = tools["transition"](item=99, to="in_progress")
    assert "error" in result


def test_create_item_tool_schema_keeps_the_title_parameter(store):
    from coworker.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register_all(board_tools(store, space=SPACE, actor=LEAD))
    schema = registry.get("create_item").schema
    properties = schema["function"]["parameters"]["properties"]
    # The auto-generator strips dict keys named "title" (pydantic metadata cleanup),
    # which would delete this parameter; the explicit schema must keep it.
    assert "title" in properties
