import type { Item } from "../types";
import { Icon } from "./Icon";

type ToolReqItem = Extract<Item, { kind: "toolreq" }>;

// The agent asked (via request_tool) for a CLI it couldn't find — a scanner, usually.
// Declining is a normal outcome, not a failure: the agent falls back and says which checks
// were degraded, so the copy here shouldn't push the user toward Install.
export function ToolRequestCard({
  item,
  onRespond,
}: {
  item: ToolReqItem;
  onRespond: (approved: boolean) => void;
}) {
  return (
    <div className="dirreq-card">
      <div className="dirreq-head">
        <Icon name="wrench" size={16} className="ico" />
        <span>
          The coworker needs <code>{item.tool}</code>
        </span>
      </div>
      {item.reason && <div className="dirreq-reason">“{item.reason}”</div>}
      {item.installable ? (
        <div className="dirreq-reason">
          {item.summary ? `${item.summary}. ` : ""}
          Installs {item.tool}
          {item.version ? ` ${item.version}` : ""} — a pinned build, checksum-verified before
          it runs.
        </div>
      ) : (
        <div className="dirreq-reason">
          No verified build is available for this machine — install it yourself if you want
          this check, or skip and the coworker will note the gap.
        </div>
      )}
      <div className="dirreq-actions">
        <span className="spacer" />
        <button className="btn" data-testid="toolreq-skip" onClick={() => onRespond(false)}>
          Skip this check
        </button>
        <button
          className="btn primary"
          data-testid="toolreq-install"
          disabled={!item.installable}
          onClick={() => onRespond(true)}
        >
          Install
        </button>
      </div>
    </div>
  );
}
