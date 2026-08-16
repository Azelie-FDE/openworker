// Agent teams (OPE-96): the board in three shapes —
//  - BoardSection: the right-rail summary (grouped by state, blocked on top)
//  - BoardOverlay: the expanded, Linear-shaped view covering the chat column
//  - PlanGateCard: the decomposition gate (proposed items awaiting the user)
// All three render the same Board data App owns; mutations go through the
// /board endpoints and act as the USER — the human side of the gates.
import { useEffect, useMemo, useState } from "react";
import type { Board, BoardItem } from "../api";
import { Icon } from "./Icon";

// Display order: needs-attention first (mock UX-030: "grouped by state, blocked on top").
const GROUPS: { state: string; label: string }[] = [
  { state: "blocked", label: "Blocked" },
  { state: "review", label: "Review" },
  { state: "in_progress", label: "In progress" },
  { state: "approved", label: "Approved" },
  { state: "proposed", label: "Proposed" },
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
  if (counts.proposed) parts.push(`${counts.proposed} proposed`);
  return parts.join(" · ");
}

export function BoardSection({ board, onExpand }: { board: Board; onExpand: () => void }) {
  const groups = GROUPS.map((g) => ({
    ...g,
    items: board.items.filter((i) => i.state === g.state),
  })).filter((g) => g.items.length > 0);
  return (
    <div className="board-rail" data-testid="board-rail">
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
  })).filter((g) => g.items.length > 0 || ["in_progress", "approved", "review"].includes(g.state));

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
    item.state === "proposed"
      ? [{ to: "approved", label: "Approve" }, { to: "canceled", label: "Cancel" }]
      : item.state === "review"
        ? [{ to: "done", label: "Mark done" }, { to: "in_progress", label: "Send back" }]
        : item.state === "done" || item.state === "canceled"
          ? []
          : [{ to: "canceled", label: "Cancel" }];
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

// The decomposition gate: proposed items awaiting the user's approval, rendered in
// the composer head like the other request cards. Visible layer = the decisions
// (items + criteria); editing happens by replying — no in-card reply surface.
export function PlanGateCard({
  board,
  onApprove,
  busy,
}: {
  board: Board;
  onApprove: () => void;
  busy?: boolean;
}) {
  const proposed = useMemo(() => board.items.filter((i) => i.state === "proposed"), [board.items]);
  const [expanded, setExpanded] = useState(false);
  if (proposed.length === 0) return null;
  const visible = expanded ? proposed : proposed.slice(0, 3);
  const hidden = proposed.length - visible.length;
  return (
    <div className="dirreq-card plangate-card" data-testid="plangate-card">
      <div className="plangate-head">
        <Icon name="table" size={15} />
        <span className="plangate-title">
          Proposed plan — {proposed.length} work item{proposed.length === 1 ? "" : "s"}
        </span>
        <span className="plangate-board">board: {board.name}</span>
      </div>
      {visible.map((item) => (
        <div className="plangate-item" key={item.id}>
          <span className="plangate-num">#{item.id}</span>
          <span className="plangate-body">
            <span className="plangate-item-title">{item.title}</span>
            {item.criteria && (
              <span className="plangate-ac">
                <b>Done when:</b> {item.criteria}
              </span>
            )}
          </span>
        </div>
      ))}
      {hidden > 0 && (
        <button className="plangate-more" onClick={() => setExpanded(true)}>
          ＋ {hidden} more item{hidden === 1 ? "" : "s"}
          <Icon name="chevronDown" size={12} />
        </button>
      )}
      <div className="dirreq-actions">
        <span className="plangate-note">Reply to edit the plan; nothing runs until you approve.</span>
        <span className="spacer" />
        <button className="btn primary" data-testid="plangate-approve" disabled={busy} onClick={onApprove}>
          Approve plan
        </button>
      </div>
    </div>
  );
}
