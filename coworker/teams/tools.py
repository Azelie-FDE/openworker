"""Board and journal verbs as agent tools.

The verbs are generic on purpose (the connector-dialect play): the local TeamStore is
the default backing, and a Jira/Linear-backed dialect can implement the same tool
surface later. Registration is gated by the persona's `team:` trait — a lead gets the
full set, a worker gets the worker set, solo personas get none of this.

The engine decides `taint` (whether this agent touched untrusted content this
session) and passes it at construction — the model never self-reports provenance.
"""

from __future__ import annotations

from typing import Callable, Optional

import aisuite as ai

from .journal import JournalStore
from .model import Actor, BoardError, Role
from .store import TeamStore

LEAD_VERBS = ("create_item", "list_items", "transition", "comment", "assign", "link")
# Workers file items too (a bug spotted in passing, a follow-up) — new items land
# in `proposed`, so the approval gate catches everything a worker proposes.
WORKER_VERBS = ("create_item", "list_items", "transition", "comment")
JOURNAL_VERBS = ("journal_append", "journal_read")

# Explicit schema: the auto-generator's normalizer strips every `title` key to drop
# pydantic metadata, which also deletes a PARAMETER named `title` from properties.
# Registered via `__coworker_schema__` (same escape hatch as todo_write).
_CREATE_ITEM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_item",
        "description": (
            "Create a work item in the proposed state. `criteria` is the acceptance"
            " criteria — what gets verified before the item can be done; required."
            " `parent` links it under another item; `case` names its journal case"
            " (children inherit the parent's case by default)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "criteria": {"type": "string"},
                "description": {"type": "string"},
                "parent": {"type": "integer"},
                "case": {"type": "string"},
            },
            "required": ["title", "criteria"],
        },
    },
}


def board_tools(
    store: TeamStore,
    *,
    space: str,
    actor: Actor,
    taint: Callable[[], bool] = lambda: False,
) -> list:
    """The board verbs for one agent, pre-bound to its space and identity.

    Authority is enforced twice on purpose: the returned set is role-filtered
    (a worker never even sees `assign`), and the store re-checks every call —
    the tool layer is convenience, the store is the gate.
    """

    def create_item(
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: str = "",
    ) -> dict:
        """Create a work item in the proposed state. `criteria` is the acceptance
        criteria — what gets verified before the item can be done; required.
        `parent` links it under another item; `case` names its journal case
        (children inherit the parent's case by default)."""
        return _call(
            store.create_item,
            space,
            actor,
            title=title,
            criteria=criteria,
            description=description,
            parent=parent,
            case=case or None,
        )

    def list_items(state: str = "", assignee: str = "") -> dict:
        """List work items on the board, optionally filtered by state
        (proposed/approved/in_progress/blocked/review/done/canceled) or assignee."""
        try:
            return {"items": store.list_items(space, actor, state=state or None, assignee=assignee or None)}
        except (BoardError, ValueError) as error:
            return {"error": str(error)}

    def transition(
        item: int, to: str, comment: str = "", refs: Optional[list] = None
    ) -> dict:
        """Move a work item to a new state. Workers move their own item to
        in_progress, blocked, or review (attach the blocker or a hand-off summary
        as `comment`, and artifact pointers — branch, report, session — as
        `refs`); done requires review verification first."""
        return _call(
            store.transition,
            space,
            actor,
            item,
            to,
            comment=comment,
            refs=[str(ref) for ref in refs or []],
            taint=taint(),
        )

    def comment(item: int, body: str, refs: Optional[list] = None) -> dict:
        """Add a comment to a work item. Comments are durable and attributed —
        answers that matter belong here, not in chat. `refs` attach artifact
        pointers (branch, PR, report, file:line) to the item."""
        return _call(
            store.comment,
            space,
            actor,
            item,
            body,
            refs=[str(ref) for ref in refs or []],
            taint=taint(),
        )

    def assign(item: int, assignee: str) -> dict:
        """Assign a work item to a worker coworker. The item itself becomes the
        worker's assignment — write the description and criteria accordingly."""
        return _call(store.assign, space, actor, item, assignee)

    def link(src: int, kind: str, dst: int) -> dict:
        """Link two work items: kind `parent` (dst becomes src's parent) or
        `blocks` (src blocks dst)."""
        return _call(store.link, space, actor, src, kind, dst)

    verbs = LEAD_VERBS if actor.role in (Role.USER, Role.LEAD) else WORKER_VERBS
    local = locals()
    out = []
    for name in verbs:
        wrapped = _wrap(local[name])
        if name == "create_item":
            wrapped.__coworker_schema__ = _CREATE_ITEM_SCHEMA
        out.append(wrapped)
    return out


def journal_tools(
    journal: "JournalStore",
    *,
    actor: Actor,
    space: str = "",
    taint: Callable[[], bool] = lambda: False,
) -> list:
    def journal_append(
        case: str,
        body: str,
        kind: str = "note",
        item: Optional[int] = None,
        entities: Optional[list] = None,
        refs: Optional[list] = None,
    ) -> dict:
        """Append an entry to a journal case as you work: kind is finding,
        evidence, decision, note (any observation), or raw (a capture like a log
        excerpt — for large captures, save the full output to a file and journal
        an excerpt that references it). `entities` are the concrete things it is
        about (file paths, resource names, CVE ids) — they power later recall;
        `refs` are pointers (file:line, commit, url)."""
        return _call(
            journal.append,
            actor,
            case,
            body,
            kind=kind,
            space=space or None,
            item=item,
            entities=[str(entity) for entity in entities or []],
            refs=[str(ref) for ref in refs or []],
            taint=taint(),
        )

    def journal_read(
        case: str,
        item: Optional[int] = None,
        author: str = "",
        kind: str = "",
        entity: str = "",
        include_raw: bool = False,
        limit: int = 50,
    ) -> dict:
        """Read a journal case, filtered: by item, author, entry kind, or entity.
        Prefer narrow filtered reads over pulling the whole case. Raw captures
        are skipped unless you pass include_raw or kind="raw"."""
        try:
            return {
                "entries": journal.read(
                    actor,
                    case,
                    item=item,
                    author=author or None,
                    kind=kind or None,
                    entity=entity or None,
                    include_raw=include_raw,
                    limit=limit,
                )
            }
        except (BoardError, ValueError) as error:
            return {"error": str(error)}

    local = locals()
    return [_wrap(local[name]) for name in JOURNAL_VERBS]


def _call(func, *args, **kwargs) -> dict:
    try:
        result = func(*args, **kwargs)
        return result if isinstance(result, dict) else {"ok": True}
    except (BoardError, ValueError) as error:
        return {"error": str(error)}


def _wrap(func):
    risk = "medium" if func.__name__ == "assign" else "low"
    return ai.tool(
        func,
        metadata=ai.ToolMetadata(
            category="team",
            risk_level=risk,
            capabilities=["team"],
        ),
    )
