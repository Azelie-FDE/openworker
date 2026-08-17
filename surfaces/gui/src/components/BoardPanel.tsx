// Agent teams (OPE-96): the board in two shapes —
//  - BoardSection: the right-rail summary (grouped by state, blocked on top)
//  - BoardOverlay: the expanded, Linear-shaped view covering the chat column
// Both render the same Board data App owns; mutations go through the /board
// endpoints and act as the USER. There is NO proposed/draft state: a plan
// proposal lives in the conversation (plan-approval flow); the board only ever
// contains accepted work, and work starts at ASSIGNMENT.
import { useEffect, useState } from "react";
import type { Board, BoardItem } from "../api";
import { Icon } from "./Icon";

// Display order: needs-attention first (mock UX-030: "grouped by state, blocked on top").
const GROUPS: { state: string; label: string }[] = [
  { state: "blocked", label: "Blocked" },
  { state: "review", label: "Review" },
  { state: "in_progress", label: "In progress" },
  { state: "open", label: "Open" },
  { state: "done", label: "Done" },
  { state: "canceled", label: "Canceled" },
];

function dotClass(state: string): string {
  if (state === "blocked") return "board-dot blocked";
  if (state === "review") return "board-dot review";
  if (state === "in_progress") return "board-dot work";
  if (state === "done") return "board-dot done";
  return "board-dot idle";
}

export function boardSummary(board: Board): string {
  const counts: Record<string, number> = {};
  for (const item of board.items) counts[item.state] = (counts[item.state] || 0) + 1;
  const parts: string[] = [];
  if (counts.blocked) parts.push(`${counts.blocked} blocked`);
  if (counts.review) parts.push(`${counts.review} review`);
  if (counts.in_progress) parts.push(`${counts.in_progress} in progress`);
  if (counts.open) parts.push(`${counts.open} open`);
  return parts.join(" · ");
}

export function BoardSection({ board, onExpand }: { board: Board; onExpand: () => void }) {
  // The rail shows ACTIVE work only (owner ruling 2026-08-16): a project board
  // outlives its sessions, so finished history from a past effort would greet
  // every fresh session as a long stale list. Done/canceled sit behind a quiet
  // count; the expanded overlay keeps the full picture.
  const [showFinished, setShowFinished] = useState(false);
  const finished = board.items.filter(
    (i) => i.state === "done" || i.state === "canceled"
  ).length;
  const shown = showFinished
    ? GROUPS
    : GROUPS.filter((g) => g.state !== "done" && g.state !== "canceled");
  const groups = shown
    .map((g) => ({
      ...g,
      items: board.items.filter((i) => i.state === g.state),
    }))
    .filter((g) => g.items.length > 0);
  return (
    <div className="board-rail" data-testid="board-rail">
      {groups.length === 0 && (
        <div className="board-rail-quiet" data-testid="board-rail-quiet">
          No active work
        </div>
      )}
      {groups.map((group) => (
        <div key={group.state}>
          <div className="board-group">{group.label}</div>
          {group.items.map((item) => (
            <button className="board-row" key={item.id} onClick={onExpand} title="Open the board">
              <span className={dotClass(item.state)} />
              <span className="board-row-main">
                <span className="board-row-title">
                  <span className="board-row-id">#{item.id}</span> {item.title}
                </span>
                {item.assignee && <span className="board-row-who">{item.assignee}</span>}
              </span>
            </button>
          ))}
        </div>
      ))}
      {finished > 0 && (
        <button
          className="board-finished-toggle"
          data-testid="board-finished-toggle"
          onClick={() => setShowFinished((v) => !v)}
        >
          {showFinished ? "Hide finished" : `${finished} finished · show`}
        </button>
      )}
    </div>
  );
}

// The expanded board: state columns over the whole session area — the "clean and
// large board like Linear" (owner ask 2026-08-16). Esc, backdrop, or ✕ closes.
export function BoardOverlay({
  board,
  onClose,
  onTransition,
}: {
  board: Board;
  onClose: () => void;
  // (item, to) → performed as the user; App refetches on completion.
  onTransition?: (item: number, to: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const columns = GROUPS.map((g) => ({
    ...g,
    items: board.items.filter((i) => i.state === g.state),
  })).filter((g) => g.items.length > 0 || ["in_progress", "open", "review"].includes(g.state));

  return (
    <div className="board-overlay" data-testid="board-overlay" onClick={onClose}>
      <div className="board-overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="board-overlay-head">
          <div className="board-overlay-title">
            <Icon name="table" size={16} />
            <span>Board</span>
            <span className="board-overlay-space">{board.name}</span>
          </div>
          <button className="artifact-icon-btn" onClick={onClose} aria-label="Close board" title="Close">
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="board-columns">
          {columns.map((column) => (
            <div className="board-col" key={column.state} data-testid={`board-col-${column.state}`}>
              <div className="board-col-head">
                <span className={dotClass(column.state)} />
                {column.label}
                <span className="board-col-count">{column.items.length}</span>
              </div>
              <div className="board-col-body">
                {column.items.map((item) => (
                  <BoardCard key={item.id} item={item} onTransition={onTransition} />
                ))}
                {column.items.length === 0 && <div className="board-col-empty">—</div>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BoardCard({
  item,
  onTransition,
}: {
  item: BoardItem;
  onTransition?: (item: number, to: string) => void;
}) {
  // The user can always act; offer the obvious next moves for the state.
  const moves: { to: string; label: string }[] =
    item.state === "review"
      ? [{ to: "done", label: "Mark done" }, { to: "in_progress", label: "Send back" }]
      : item.state === "canceled"
        ? [{ to: "open", label: "Reopen" }]
        : item.state === "done"
          ? []
          : [{ to: "canceled", label: "Remove" }];
  return (
    <div className="board-card" data-testid={`board-item-${item.id}`}>
      <div className="board-card-title">
        <span className="board-row-id">#{item.id}</span> {item.title}
      </div>
      {item.criteria && (
        <div className="board-card-criteria">
          <span className="board-card-label">Done when:</span> {item.criteria}
        </div>
      )}
      <div className="board-card-foot">
        {item.assignee ? <span className="board-card-who">{item.assignee}</span> : <span />}
        {onTransition && moves.length > 0 && (
          <span className="board-card-actions">
            {moves.map((m) => (
              <button key={m.to} className="board-card-btn" onClick={() => onTransition(item.id, m.to)}>
                {m.label}
              </button>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}

