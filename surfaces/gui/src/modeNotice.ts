// What the transcript records when the permission mode changes.
//
// Two different jobs, deliberately: the first time Auto-Approve is active in a session the
// user gets the full explanation, and every later switch gets a one-line marker. Without
// the marker a transcript is unreadable after the fact — nothing says which mode any given
// exchange ran under, and for a tool built on an auditable trail that matters more than the
// prose does.
import { modeLabel } from "./components/Composer";
import type { Item } from "./types";

// Spec §1.5. Says what the mode buys, what it can never override, and — deliberately
// unhedged — where it can be wrong. It reduces interruptions, not risk, and the closing
// line has to leave that clear.
export const AUTO_APPROVE_NOTICE = [
  "Your session model lets routine actions through without asking; anything it isn't sure " +
    "about still comes to you — whatever the rules settle outright, it never sees.",
  "It reduces interruptions from lower-risk actions, not the risk from what it can't tell: " +
    "what a script will do, or whether an instruction came from you or from a web page or " +
    "email. Shell commands aren't sandboxed — they reach anything you can. These are model " +
    "judgments, not guarantees — a false allow executes unchecked.",
].join("\n\n");

/** The mode the transcript was last showing, per session. */
export type ModeMark = { session: string; mode: string } | null;

/**
 * The item a mode change should append, or null when nothing should be said.
 *
 * Pure on purpose: the caller owns the refs, so this stays testable without rendering the
 * app. `bannerShownFor` is the session that has already seen the full explanation.
 */
export function modeNotice(
  mode: string,
  sessionId: string,
  previous: ModeMark,
  bannerShownFor: string,
): Item | null {
  // First time this session is in Auto-Approve, however it got there — picked in the
  // composer, applied by a plan approval, or restored with the session.
  if (mode === "auto-approve" && bannerShownFor !== sessionId) {
    return {
      kind: "notice",
      tone: "info",
      title: "Auto-Approve is on.",
      text: AUTO_APPROVE_NOTICE,
    };
  }
  // A later change WITHIN the same session: mark it, don't re-explain it. Switching
  // sessions is not a mode change, so it says nothing — otherwise every session you opened
  // would announce the mode it happened to be in.
  if (previous && previous.session === sessionId && previous.mode !== mode) {
    return { kind: "notice", tone: "info", text: `${modeLabel(mode)} is on.` };
  }
  return null;
}
