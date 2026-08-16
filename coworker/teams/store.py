"""The team event store — ONE append-only log; board, journal, and deliveries are
projections of it.

Doctrine (agent-teams design): item events, journal entries, and chat messages are one
attributed, timestamped, immutable record shape in a single log. One write path to
police and audit, one injection surface to defend, several read-side views. Nothing is
ever updated or deleted — a change of mind is a new event.

Mechanics, kept boring:
- Append and projection-fold happen in the same transaction via the same `_apply`
  used by `rebuild()` — the materialized board can always be reproduced by replay.
- Events hash-chain per space (entry carries the previous hash) → `verify_chain`
  detects out-of-band edits. Tamper-evidence, not tamper-proofing.
- `taint` marks records authored after touching untrusted content; readers render it
  as provenance ("treat as evidence, not instructions").
- `recipient` is how per-agent delivery works: a projection over the one log, not a
  second write path (consumption semantics arrive with the wake plumbing).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .model import (
    EDGES,
    JOURNAL_KINDS,
    LINK_KINDS,
    WORKER_TARGETS,
    Actor,
    AuthorityError,
    BoardError,
    ChainError,
    ItemState,
    Role,
)

GENESIS = "genesis"

# Event kinds. Chat lands later with the chat surface; the record shape already fits.
ITEM_CREATED = "item_created"
ITEM_TRANSITIONED = "item_transitioned"
ITEM_COMMENTED = "item_commented"
ITEM_ASSIGNED = "item_assigned"
ITEM_LINKED = "item_linked"
JOURNAL_APPENDED = "journal_appended"

_HASHED_FIELDS = (
    "ts",
    "space",
    "kind",
    "actor",
    "actor_role",
    "item_id",
    "case_id",
    "recipient",
    "payload",
    "taint",
    "prev_hash",
)


class TeamStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS team_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                space TEXT NOT NULL,
                kind TEXT NOT NULL,
                actor TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                persona TEXT DEFAULT '',
                model TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                item_id INTEGER,
                case_id TEXT,
                recipient TEXT,
                payload TEXT NOT NULL,
                taint INTEGER NOT NULL DEFAULT 0,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_team_events_space
                ON team_events (space, seq);
            CREATE INDEX IF NOT EXISTS idx_team_events_item
                ON team_events (space, item_id, seq);
            CREATE INDEX IF NOT EXISTS idx_team_events_case
                ON team_events (space, case_id, seq);
            CREATE INDEX IF NOT EXISTS idx_team_events_recipient
                ON team_events (recipient, seq);
            CREATE TABLE IF NOT EXISTS team_items (
                space TEXT NOT NULL,
                id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                criteria TEXT NOT NULL,
                state TEXT NOT NULL,
                assignee TEXT DEFAULT '',
                case_id TEXT DEFAULT '',
                created_ts TEXT NOT NULL,
                updated_seq INTEGER NOT NULL,
                PRIMARY KEY (space, id)
            );
            CREATE TABLE IF NOT EXISTS team_links (
                space TEXT NOT NULL,
                src INTEGER NOT NULL,
                kind TEXT NOT NULL,
                dst INTEGER NOT NULL,
                UNIQUE (space, src, kind, dst)
            );
            CREATE TABLE IF NOT EXISTS team_meta (
                space TEXT PRIMARY KEY,
                head_hash TEXT NOT NULL,
                watermark INTEGER NOT NULL
            );
            """)
        self._conn.commit()

    # ------------------------------------------------------------------ events core

    def append_event(
        self,
        space: str,
        kind: str,
        actor: Actor,
        *,
        item_id: Optional[int] = None,
        case_id: Optional[str] = None,
        recipient: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        taint: bool = False,
    ) -> dict[str, Any]:
        """Append one record and fold it into the projections, atomically."""
        if not space:
            raise BoardError("space is required")
        with self._lock:
            try:
                return self._append_locked(
                    space,
                    kind,
                    actor,
                    item_id=item_id,
                    case_id=case_id,
                    recipient=recipient,
                    payload=payload or {},
                    taint=taint,
                )
            except Exception:
                self._conn.rollback()
                raise

    def _append_locked(
        self,
        space: str,
        kind: str,
        actor: Actor,
        *,
        item_id: Optional[int],
        case_id: Optional[str],
        recipient: Optional[str],
        payload: dict[str, Any],
        taint: bool,
    ) -> dict[str, Any]:
        prev = self._head_hash(space)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "space": space,
            "kind": kind,
            "actor": actor.id,
            "actor_role": actor.role.value,
            "item_id": item_id,
            "case_id": case_id,
            "recipient": recipient,
            "payload": _canonical(payload),
            "taint": 1 if taint else 0,
            "prev_hash": prev,
        }
        record["hash"] = _hash(record)
        cursor = self._conn.execute(
            """
            INSERT INTO team_events
                (ts, space, kind, actor, actor_role, persona, model, session_id,
                 item_id, case_id, recipient, payload, taint, prev_hash, hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["ts"],
                space,
                kind,
                actor.id,
                actor.role.value,
                actor.persona,
                actor.model,
                actor.session_id,
                item_id,
                case_id,
                recipient,
                record["payload"],
                record["taint"],
                prev,
                record["hash"],
            ),
        )
        seq = cursor.lastrowid
        self._apply(space, seq, record["ts"], kind, item_id, payload)
        self._conn.execute(
            """
            INSERT INTO team_meta (space, head_hash, watermark) VALUES (?, ?, ?)
            ON CONFLICT(space) DO UPDATE SET head_hash = ?, watermark = ?
            """,
            (space, record["hash"], seq, record["hash"], seq),
        )
        self._conn.commit()
        return {**record, "seq": seq, "payload": payload}

    def events(
        self,
        space: str,
        *,
        kinds: Optional[list[str]] = None,
        item_id: Optional[int] = None,
        case_id: Optional[str] = None,
        since_seq: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        where = ["space = ?", "seq > ?"]
        params: list[Any] = [space, since_seq]
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if item_id is not None:
            where.append("item_id = ?")
            params.append(item_id)
        if case_id is not None:
            where.append("case_id = ?")
            params.append(case_id)
        sql = (
            "SELECT * FROM team_events WHERE "
            + " AND ".join(where)
            + " ORDER BY seq LIMIT ?"
        )
        params.append(max(1, min(int(limit or 500), 2000)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def for_recipient(
        self, recipient: str, *, since_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Everything addressed to one agent, in order — the delivery projection."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_events WHERE recipient = ? AND seq > ?"
                " ORDER BY seq LIMIT ?",
                (recipient, since_seq, max(1, min(int(limit or 200), 2000))),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def spaces(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT space FROM team_meta ORDER BY space"
            ).fetchall()
        return [row["space"] for row in rows]

    def verify_chain(self, space: str) -> int:
        """Recompute the chain; return the number of verified events.

        Raises ChainError at the first record whose hash or linkage does not match —
        the log was edited out of band.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_events WHERE space = ? ORDER BY seq", (space,)
            ).fetchall()
        prev = GENESIS
        for row in rows:
            record = {key: row[key] for key in _HASHED_FIELDS}
            if row["prev_hash"] != prev:
                raise ChainError(f"event {row['seq']}: chain linkage broken")
            if _hash(record) != row["hash"]:
                raise ChainError(f"event {row['seq']}: content does not match hash")
            prev = row["hash"]
        return len(rows)

    def rebuild(self, space: str) -> None:
        """Drop the space's projections and replay its log through `_apply`.

        The recovery path (projection bug fix, cache corruption) — never the hot
        path; live appends fold incrementally in `append_event`.
        """
        with self._lock:
            self._conn.execute("DELETE FROM team_items WHERE space = ?", (space,))
            self._conn.execute("DELETE FROM team_links WHERE space = ?", (space,))
            rows = self._conn.execute(
                "SELECT seq, ts, kind, item_id, payload FROM team_events"
                " WHERE space = ? ORDER BY seq",
                (space,),
            ).fetchall()
            for row in rows:
                self._apply(
                    space,
                    row["seq"],
                    row["ts"],
                    row["kind"],
                    row["item_id"],
                    json.loads(row["payload"]),
                )
            self._conn.commit()

    # ------------------------------------------------------------------ board verbs

    def create_item(
        self,
        space: str,
        actor: Actor,
        *,
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: Optional[str] = None,
    ) -> dict[str, Any]:
        """New item in `proposed`. Acceptance criteria are load-bearing — required."""
        self._require(actor, {Role.USER, Role.LEAD}, "create_item")
        if not (title or "").strip():
            raise BoardError("title is required")
        if not (criteria or "").strip():
            raise BoardError(
                "acceptance criteria are required — they are what gets verified at"
                " review"
            )
        with self._lock:
            if parent is not None:
                parent_item = self._item(space, parent)
                if case is None:
                    case = parent_item["case_id"] or None
            item_id = self._next_item_id(space)
            event = self.append_event(
                space,
                ITEM_CREATED,
                actor,
                item_id=item_id,
                case_id=case,
                payload={
                    "title": title.strip(),
                    "description": description,
                    "criteria": criteria.strip(),
                    "parent": parent,
                    "case": case,
                },
            )
        return self.get_item(space, item_id, seq=event["seq"])

    def list_items(
        self,
        space: str,
        actor: Actor,
        *,
        state: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Items in a space. Workers see only their slice: assigned items plus items
        directly linked to those."""
        where = ["space = ?"]
        params: list[Any] = [space]
        if state:
            where.append("state = ?")
            params.append(ItemState(state).value)
        if assignee:
            where.append("assignee = ?")
            params.append(assignee)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_items WHERE "
                + " AND ".join(where)
                + " ORDER BY id",
                params,
            ).fetchall()
            items = [dict(row) for row in rows]
            if actor.role == Role.WORKER:
                visible = self._worker_slice(space, actor.id)
                items = [item for item in items if item["id"] in visible]
            for item in items:
                item["links"] = self._links_of(space, item["id"])
        return items

    def get_item(
        self, space: str, item_id: int, *, seq: Optional[int] = None
    ) -> dict[str, Any]:
        with self._lock:
            item = self._item(space, item_id)
            item["links"] = self._links_of(space, item_id)
            item["comments"] = self.comments(space, item_id)
        if seq is not None:
            item["seq"] = seq
        return item

    def transition(
        self,
        space: str,
        actor: Actor,
        item_id: int,
        to: str,
        *,
        comment: str = "",
        taint: bool = False,
    ) -> dict[str, Any]:
        target = ItemState(to)
        with self._lock:
            item = self._item(space, item_id)
            current = ItemState(item["state"])
            if target not in EDGES[current]:
                raise BoardError(
                    f"illegal transition {current.value} → {target.value}"
                )
            self._check_transition_authority(actor, item, current, target)
            event = self.append_event(
                space,
                ITEM_TRANSITIONED,
                actor,
                item_id=item_id,
                case_id=item["case_id"] or None,
                payload={
                    "from": current.value,
                    "to": target.value,
                    "comment": comment,
                },
                taint=taint,
            )
        return self.get_item(space, item_id, seq=event["seq"])

    def comment(
        self,
        space: str,
        actor: Actor,
        item_id: int,
        body: str,
        *,
        taint: bool = False,
    ) -> dict[str, Any]:
        if not (body or "").strip():
            raise BoardError("comment body is required")
        with self._lock:
            item = self._item(space, item_id)
            if actor.role == Role.WORKER and item_id not in self._worker_slice(
                space, actor.id
            ):
                raise AuthorityError(
                    f"worker {actor.id} may only comment on its assigned items"
                    " and items linked to them"
                )
            return self.append_event(
                space,
                ITEM_COMMENTED,
                actor,
                item_id=item_id,
                case_id=item["case_id"] or None,
                payload={"body": body},
                taint=taint,
            )

    def assign(
        self, space: str, actor: Actor, item_id: int, assignee: str
    ) -> dict[str, Any]:
        """Set the assignee. Not a message: the event addresses the assignee
        (`recipient`), and the worker's prompt derives from the item itself."""
        self._require(actor, {Role.USER, Role.LEAD}, "assign")
        if not (assignee or "").strip():
            raise BoardError("assignee is required")
        with self._lock:
            item = self._item(space, item_id)
            state = ItemState(item["state"])
            if state in (ItemState.PROPOSED, ItemState.DONE, ItemState.CANCELED):
                raise BoardError(
                    f"cannot assign an item in state {state.value} — items are"
                    " assigned after approval"
                )
            event = self.append_event(
                space,
                ITEM_ASSIGNED,
                actor,
                item_id=item_id,
                case_id=item["case_id"] or None,
                recipient=assignee,
                payload={"assignee": assignee, "previous": item["assignee"] or ""},
            )
        return self.get_item(space, item_id, seq=event["seq"])

    def link(
        self, space: str, actor: Actor, src: int, kind: str, dst: int
    ) -> dict[str, Any]:
        self._require(actor, {Role.USER, Role.LEAD}, "link")
        if kind not in LINK_KINDS:
            raise BoardError(f"unknown link kind: {kind} (use one of {LINK_KINDS})")
        if src == dst:
            raise BoardError("an item cannot link to itself")
        with self._lock:
            self._item(space, src)
            self._item(space, dst)
            if kind == "parent" and self._would_cycle(space, src, dst):
                raise BoardError("parent link would create a cycle")
            return self.append_event(
                space,
                ITEM_LINKED,
                actor,
                item_id=src,
                payload={"src": src, "kind": kind, "dst": dst},
            )

    def comments(self, space: str, item_id: int) -> list[dict[str, Any]]:
        """Attributed comments on an item — standalone comments plus the notes
        carried on transitions (a `blocked` explanation lives with its event)."""
        out = []
        for event in self.events(
            space, kinds=[ITEM_COMMENTED, ITEM_TRANSITIONED], item_id=item_id
        ):
            body = (
                event["payload"].get("body")
                if event["kind"] == ITEM_COMMENTED
                else event["payload"].get("comment")
            )
            if body:
                out.append(
                    {
                        "seq": event["seq"],
                        "ts": event["ts"],
                        "author": event["actor"],
                        "role": event["actor_role"],
                        "body": body,
                        "taint": event["taint"],
                    }
                )
        return out

    # ---------------------------------------------------------------------- journal

    def journal_append(
        self,
        space: str,
        actor: Actor,
        case: str,
        body: str,
        *,
        kind: str = "note",
        item: Optional[int] = None,
        entities: Optional[list[str]] = None,
        refs: Optional[list[str]] = None,
        taint: bool = False,
    ) -> dict[str, Any]:
        """Append one journal entry to a case. Cases are created lazily on first
        append; sharing rides assignment — a worker may only touch cases referenced
        by its assigned items."""
        if not (case or "").strip():
            raise BoardError("case is required")
        if not (body or "").strip():
            raise BoardError("entry body is required")
        if kind not in JOURNAL_KINDS:
            raise BoardError(f"unknown entry kind: {kind} (use one of {JOURNAL_KINDS})")
        with self._lock:
            self._check_case_access(space, actor, case)
            if item is not None:
                self._item(space, item)
            return self.append_event(
                space,
                JOURNAL_APPENDED,
                actor,
                item_id=item,
                case_id=case,
                payload={
                    "kind": kind,
                    "body": body,
                    "entities": sorted(set(entities or [])),
                    "refs": list(refs or []),
                },
                taint=taint,
            )

    def journal_read(
        self,
        space: str,
        actor: Actor,
        case: str,
        *,
        item: Optional[int] = None,
        author: Optional[str] = None,
        kind: Optional[str] = None,
        entity: Optional[str] = None,
        since_seq: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Filtered read — the thing that makes shared journals viable. The entity
        filter scans extracted entities for now; a dedicated index (then vectors)
        drops in behind this same signature."""
        with self._lock:
            self._check_case_access(space, actor, case)
        events = self.events(
            space,
            kinds=[JOURNAL_APPENDED],
            case_id=case,
            item_id=item,
            since_seq=since_seq,
            limit=2000,
        )
        out = []
        for event in events:
            payload = event["payload"]
            if author and event["actor"] != author:
                continue
            if kind and payload.get("kind") != kind:
                continue
            if entity and entity not in (payload.get("entities") or []):
                continue
            out.append(
                {
                    "seq": event["seq"],
                    "ts": event["ts"],
                    "author": event["actor"],
                    "role": event["actor_role"],
                    "item": event["item_id"],
                    "kind": payload.get("kind"),
                    "body": payload.get("body"),
                    "entities": payload.get("entities") or [],
                    "refs": payload.get("refs") or [],
                    "taint": event["taint"],
                }
            )
            if len(out) >= max(1, min(int(limit or 100), 1000)):
                break
        return out

    def cases(self, space: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT case_id FROM team_events"
                " WHERE space = ? AND kind = ? AND case_id IS NOT NULL"
                " ORDER BY case_id",
                (space, JOURNAL_APPENDED),
            ).fetchall()
        return [row["case_id"] for row in rows]

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------- internals

    def _apply(
        self,
        space: str,
        seq: int,
        ts: str,
        kind: str,
        item_id: Optional[int],
        payload: dict[str, Any],
    ) -> None:
        """Fold one event into the projections. The ONLY writer of team_items and
        team_links — shared by live appends and rebuild(), so replay always
        reproduces the materialized state."""
        if kind == ITEM_CREATED:
            self._conn.execute(
                """
                INSERT INTO team_items
                    (space, id, title, description, criteria, state, assignee,
                     case_id, created_ts, updated_seq)
                VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    space,
                    item_id,
                    payload.get("title") or "",
                    payload.get("description") or "",
                    payload.get("criteria") or "",
                    ItemState.PROPOSED.value,
                    payload.get("case") or "",
                    ts,
                    seq,
                ),
            )
            if payload.get("parent") is not None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO team_links (space, src, kind, dst)"
                    " VALUES (?, ?, 'parent', ?)",
                    (space, item_id, payload["parent"]),
                )
        elif kind == ITEM_TRANSITIONED:
            self._conn.execute(
                "UPDATE team_items SET state = ?, updated_seq = ?"
                " WHERE space = ? AND id = ?",
                (payload.get("to"), seq, space, item_id),
            )
        elif kind == ITEM_ASSIGNED:
            self._conn.execute(
                "UPDATE team_items SET assignee = ?, updated_seq = ?"
                " WHERE space = ? AND id = ?",
                (payload.get("assignee") or "", seq, space, item_id),
            )
        elif kind == ITEM_LINKED:
            self._conn.execute(
                "INSERT OR IGNORE INTO team_links (space, src, kind, dst)"
                " VALUES (?, ?, ?, ?)",
                (space, payload.get("src"), payload.get("kind"), payload.get("dst")),
            )
        # Comments and journal entries have no materialized state: their
        # projections read straight off the (indexed) log.

    def _check_transition_authority(
        self, actor: Actor, item: dict[str, Any], current: ItemState, target: ItemState
    ) -> None:
        if actor.role == Role.SYSTEM:
            raise AuthorityError("system events cannot transition items")
        if current == ItemState.PROPOSED and target == ItemState.APPROVED:
            if actor.role != Role.USER:
                raise AuthorityError(
                    "only the user approves proposed items — that is the"
                    " decomposition gate"
                )
            return
        if target == ItemState.DONE and actor.role == Role.WORKER:
            raise AuthorityError(
                "workers finish by moving to review — done is the verdict after"
                " verification"
            )
        if actor.role == Role.WORKER:
            if item["assignee"] != actor.id:
                raise AuthorityError(
                    f"worker {actor.id} is not assigned item #{item['id']}"
                )
            if target not in WORKER_TARGETS:
                raise AuthorityError(
                    f"workers may move their item to"
                    f" {sorted(state.value for state in WORKER_TARGETS)} only"
                )

    def _check_case_access(self, space: str, actor: Actor, case: str) -> None:
        if actor.role != Role.WORKER:
            return
        rows = self._conn.execute(
            "SELECT DISTINCT case_id FROM team_items WHERE space = ? AND assignee = ?",
            (space, actor.id),
        ).fetchall()
        if case not in {row["case_id"] for row in rows if row["case_id"]}:
            raise AuthorityError(
                f"worker {actor.id} has no assigned item on case '{case}'"
            )

    def _worker_slice(self, space: str, worker_id: str) -> set[int]:
        rows = self._conn.execute(
            "SELECT id FROM team_items WHERE space = ? AND assignee = ?",
            (space, worker_id),
        ).fetchall()
        mine = {row["id"] for row in rows}
        if not mine:
            return set()
        linked = self._conn.execute(
            "SELECT src, dst FROM team_links WHERE space = ?", (space,)
        ).fetchall()
        out = set(mine)
        for row in linked:
            if row["src"] in mine:
                out.add(row["dst"])
            if row["dst"] in mine:
                out.add(row["src"])
        return out

    def _links_of(self, space: str, item_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT src, kind, dst FROM team_links WHERE space = ?"
            " AND (src = ? OR dst = ?)",
            (space, item_id, item_id),
        ).fetchall()
        out = []
        for row in rows:
            if row["src"] == item_id:
                out.append({"kind": row["kind"], "item": row["dst"]})
            else:
                inverse = "child" if row["kind"] == "parent" else "blocked_by"
                out.append({"kind": inverse, "item": row["src"]})
        return out

    def _would_cycle(self, space: str, src: int, dst: int) -> bool:
        # Walking up from dst: if we reach src, making dst the parent of src closes
        # a loop.
        current, hops = dst, 0
        while hops < 1000:
            row = self._conn.execute(
                "SELECT dst FROM team_links WHERE space = ? AND src = ?"
                " AND kind = 'parent'",
                (space, current),
            ).fetchone()
            if row is None:
                return False
            if row["dst"] == src:
                return True
            current, hops = row["dst"], hops + 1
        return True

    def _item(self, space: str, item_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM team_items WHERE space = ? AND id = ?", (space, item_id)
        ).fetchone()
        if row is None:
            raise BoardError(f"no item #{item_id} in space '{space}'")
        return dict(row)

    def _next_item_id(self, space: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(id) AS top FROM team_items WHERE space = ?", (space,)
        ).fetchone()
        return int(row["top"] or 0) + 1

    def _head_hash(self, space: str) -> str:
        row = self._conn.execute(
            "SELECT head_hash FROM team_meta WHERE space = ?", (space,)
        ).fetchone()
        return row["head_hash"] if row else GENESIS

    def _require(self, actor: Actor, roles: set[Role], verb: str) -> None:
        if actor.role not in roles:
            raise AuthorityError(
                f"{verb} requires one of"
                f" {sorted(role.value for role in roles)} (actor {actor.id} is"
                f" {actor.role.value})"
            )


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(record: dict[str, Any]) -> str:
    material = _canonical({key: record[key] for key in _HASHED_FIELDS})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    try:
        event["payload"] = json.loads(event.get("payload") or "{}")
    except json.JSONDecodeError:
        event["payload"] = {}
    return event
