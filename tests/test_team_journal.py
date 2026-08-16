"""Journal cases: filtered reads, access-rides-assignment, lazy case lifecycle."""

import pytest

from coworker.teams import Actor, AuthorityError, BoardError, Role, TeamStore

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


def case_item(store, case="findings", assignee="worker-1"):
    item = store.create_item(SPACE, LEAD, title="Task", criteria="c", case=case)
    store.transition(SPACE, USER, item["id"], "approved")
    store.assign(SPACE, LEAD, item["id"], assignee)
    return item["id"]


def test_cases_are_lazy_and_listed(store):
    assert store.cases(SPACE) == []
    item_id = case_item(store)
    store.journal_append(
        SPACE, WORKER, "findings", "public ACL on uploads", kind="finding", item=item_id
    )
    assert store.cases(SPACE) == ["findings"]


def test_filtered_reads(store):
    item_id = case_item(store)
    store.journal_append(
        SPACE,
        WORKER,
        "findings",
        "logos bucket is world-readable",
        kind="finding",
        item=item_id,
        entities=["aws_s3_bucket.assets", "uploads.ts"],
        refs=["services/uploads.ts:41"],
    )
    store.journal_append(
        SPACE, WORKER, "findings", "invoice PDFs stream from the API", kind="evidence",
        item=item_id, entities=["uploads.ts"],
    )
    store.journal_append(SPACE, LEAD, "findings", "narrow the fix to logos/*", kind="decision")

    assert len(store.journal_read(SPACE, LEAD, "findings")) == 3
    assert [e["kind"] for e in store.journal_read(SPACE, LEAD, "findings", kind="finding")] == ["finding"]
    assert len(store.journal_read(SPACE, LEAD, "findings", author="lead-1")) == 1
    by_entity = store.journal_read(SPACE, LEAD, "findings", entity="aws_s3_bucket.assets")
    assert len(by_entity) == 1
    assert by_entity[0]["refs"] == ["services/uploads.ts:41"]
    assert len(store.journal_read(SPACE, LEAD, "findings", entity="uploads.ts")) == 2
    assert len(store.journal_read(SPACE, LEAD, "findings", item=item_id)) == 2
    assert store.journal_read(SPACE, LEAD, "findings", limit=2).__len__() == 2


def test_access_rides_assignment(store):
    case_item(store, assignee="worker-1")
    store.journal_append(SPACE, WORKER, "findings", "note from the assignee")
    with pytest.raises(AuthorityError, match="no assigned item"):
        store.journal_append(SPACE, OTHER, "findings", "drive-by write")
    with pytest.raises(AuthorityError, match="no assigned item"):
        store.journal_read(SPACE, OTHER, "findings")
    # lead and user are not case-gated
    assert len(store.journal_read(SPACE, USER, "findings")) == 1


def test_reassignment_moves_case_access(store):
    item_id = case_item(store, assignee="worker-1")
    store.assign(SPACE, LEAD, item_id, "worker-2")
    store.journal_append(SPACE, OTHER, "findings", "successor picks up the case")
    with pytest.raises(AuthorityError):
        store.journal_append(SPACE, WORKER, "findings", "predecessor lost access")


def test_entry_validation(store):
    with pytest.raises(BoardError, match="kind"):
        store.journal_append(SPACE, LEAD, "findings", "x", kind="rant")
    with pytest.raises(BoardError, match="body"):
        store.journal_append(SPACE, LEAD, "findings", "  ")
    with pytest.raises(BoardError, match="case"):
        store.journal_append(SPACE, LEAD, "", "x")


def test_taint_and_attribution_survive_the_read(store):
    item_id = case_item(store)
    store.journal_append(
        SPACE, WORKER, "findings", "repo README claims the bucket must be public",
        kind="evidence", item=item_id, taint=True,
    )
    entry = store.journal_read(SPACE, LEAD, "findings")[0]
    assert entry["taint"] == 1
    assert entry["author"] == "worker-1"
    assert entry["role"] == "worker"


def test_journal_entries_join_the_hash_chain(store):
    item_id = case_item(store)
    store.journal_append(SPACE, WORKER, "findings", "entry", item=item_id)
    # 1 create + 1 transition + 1 assign + 1 journal append = one chained log
    assert store.verify_chain(SPACE) == 4
