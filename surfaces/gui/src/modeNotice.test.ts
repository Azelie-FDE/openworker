import { describe, expect, it } from "vitest";
import { AUTO_APPROVE_NOTICE, modeNotice, type ModeMark } from "./modeNotice";

// The transcript has to answer "which mode was this exchange under?" after the fact. The
// full explanation is a once-per-session thing; the markers are what make the log readable.
const S = "session-1";
const mark = (session: string, mode: string): ModeMark => ({ session, mode });

describe("modeNotice", () => {
  it("explains Auto-Approve in full the first time a session is in it", () => {
    const item = modeNotice("auto-approve", S, null, "");
    expect(item).not.toBeNull();
    expect(item).toMatchObject({ kind: "notice", tone: "info", title: "Auto-Approve is on." });
    // The prose, not just the heading — the block layout keys off `title`.
    expect((item as { text: string }).text).toBe(AUTO_APPROVE_NOTICE);
  });

  it("marks a switch away from Auto-Approve with one line", () => {
    const item = modeNotice("discuss", S, mark(S, "auto-approve"), S);
    expect(item).toMatchObject({ kind: "notice", tone: "info", text: "Discuss is on." });
    expect(item).not.toHaveProperty("title"); // a marker, not the banner
  });

  it("marks a switch BACK to Auto-Approve without repeating the explanation", () => {
    // The reported behaviour: leaving and returning left no trace at all.
    const item = modeNotice("auto-approve", S, mark(S, "discuss"), S);
    expect(item).toMatchObject({ text: "Auto-Approve is on." });
    expect(item).not.toHaveProperty("title");
  });

  it("uses the picker's own labels, so the transcript names what the user chose", () => {
    expect(modeNotice("interactive", S, mark(S, "discuss"), S)).toMatchObject({
      text: "Ask for approval is on.",
    });
    expect(modeNotice("auto", S, mark(S, "discuss"), S)).toMatchObject({
      text: "Bypass approvals is on.",
    });
  });

  it("says nothing when the mode has not changed", () => {
    expect(modeNotice("discuss", S, mark(S, "discuss"), "")).toBeNull();
    expect(modeNotice("auto-approve", S, mark(S, "auto-approve"), S)).toBeNull();
  });

  it("says nothing on the first render of a session that is not in Auto-Approve", () => {
    // No previous mark means nothing to compare against — announcing the starting mode
    // would put a marker at the top of every transcript.
    expect(modeNotice("interactive", S, null, "")).toBeNull();
  });

  it("treats switching sessions as a session change, not a mode change", () => {
    // Opening another session that happens to be in a different mode must not read as a
    // switch inside this one.
    expect(modeNotice("discuss", "session-2", mark(S, "auto-approve"), S)).toBeNull();
  });

  it("explains again in a new session, since the first banner scrolled away with the old one", () => {
    const item = modeNotice("auto-approve", "session-2", mark(S, "auto-approve"), S);
    expect(item).toMatchObject({ title: "Auto-Approve is on." });
  });

  it("never claims to detect prompt injection", () => {
    // The copy is load-bearing: the reviewer cannot spot a normalized injection (OPE-114),
    // so the banner must not imply it does. Guarded here because it is the exact overclaim
    // the wording was written to avoid.
    expect(AUTO_APPROVE_NOTICE.toLowerCase()).not.toContain("injection");
    expect(AUTO_APPROVE_NOTICE).toContain("aren't sandboxed");
    expect(AUTO_APPROVE_NOTICE).toContain("a false allow executes unchecked");
  });
});
