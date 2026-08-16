// The staffing gate (agent teams, UX-030): a lead proposes its worker roster.
// Visible layer = the decisions (who, on what model, why); approving grants the lead
// create/assign/steer for this board — standing, revocable — and PRE-SPAWNS the
// worker sessions. No in-card reply surface: editing happens by replying.
import type { Item } from "../types";
import { Icon } from "./Icon";

export function TeamRequestCard({
  item,
  onRespond,
}: {
  item: Extract<Item, { kind: "teamreq" }>;
  onRespond: (approved: boolean, feedback?: string) => void;
}) {
  return (
    <div className="dirreq-card teamreq-card" data-testid="teamreq-card">
      <div className="teamreq-head">
        <Icon name="diamond" size={15} />
        <span className="teamreq-title">
          Proposed team — {item.members.length} worker{item.members.length === 1 ? "" : "s"}
        </span>
      </div>
      {item.note && <div className="teamreq-note">{item.note}</div>}
      {item.members.map((m, i) => (
        <div className="teamreq-row" key={i}>
          <span className="teamreq-diamond">◆</span>
          <span className="teamreq-body">
            <code>{m.persona}</code>
            {m.model && <span className="teamreq-model"> · {m.model}</span>}
            {m.reason && <span className="teamreq-reason"> — {m.reason}</span>}
          </span>
        </div>
      ))}
      <div className="dirreq-actions">
        <span className="teamreq-grant">
          Approving grants the lead create, assign &amp; steer — this team only, revocable.
        </span>
        <span className="spacer" />
        <button className="btn" onClick={() => onRespond(false)}>
          Not now
        </button>
        <button
          className="btn primary"
          data-testid="teamreq-approve"
          onClick={() => onRespond(true)}
        >
          Create team &amp; start
        </button>
      </div>
    </div>
  );
}
