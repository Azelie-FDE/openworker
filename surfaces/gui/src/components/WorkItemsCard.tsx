// The decomposition gate (agent teams): a lead proposes work items; approval
// creates them on the board. The board-flavored sibling of PlanCard — items with
// acceptance criteria as the primary text, 3 visible + expander with the true
// count in the header, no in-card reply surface (editing happens by replying).
import { useState } from "react";
import type { Item } from "../types";
import { Icon } from "./Icon";

export function WorkItemsCard({
  item,
  onRespond,
}: {
  item: Extract<Item, { kind: "itemsreq" }>;
  onRespond: (approved: boolean, feedback?: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? item.items : item.items.slice(0, 3);
  const hidden = item.items.length - visible.length;
  return (
    <div className="dirreq-card itemsreq-card" data-testid="itemsreq-card">
      <div className="itemsreq-head">
        <Icon name="table" size={15} />
        <span className="itemsreq-title">
          Proposed work items — {item.items.length}
        </span>
      </div>
      {item.note && <div className="itemsreq-note">{item.note}</div>}
      {visible.map((entry, i) => (
        <div className="itemsreq-item" key={i}>
          <span className="itemsreq-num">{i + 1}.</span>
          <span className="itemsreq-body">
            <span className="itemsreq-item-title">{entry.title}</span>
            <span className="itemsreq-ac">
              <b>Done when:</b> {entry.criteria}
            </span>
          </span>
        </div>
      ))}
      {hidden > 0 && (
        <button className="itemsreq-more" onClick={() => setExpanded(true)}>
          ＋ {hidden} more item{hidden === 1 ? "" : "s"}
          <Icon name="chevronDown" size={12} />
        </button>
      )}
      <div className="dirreq-actions">
        <span className="itemsreq-grant">
          Reply to edit the split; approval creates these on the board.
        </span>
        <span className="spacer" />
        <button className="btn" onClick={() => onRespond(false)}>
          Not now
        </button>
        <button
          className="btn primary"
          data-testid="itemsreq-approve"
          onClick={() => onRespond(true)}
        >
          Approve items
        </button>
      </div>
    </div>
  );
}
