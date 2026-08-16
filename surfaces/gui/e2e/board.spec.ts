// Agent teams (OPE-96): the board in the session UI — rail section (grouped by
// state, blocked on top), the plan gate (decomposition approval), and the expanded
// Linear-shaped overlay. The fake agent files items on "plan the work"; approve and
// transition round-trip through the mocked /board endpoints.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function planTheWork(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan the work");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/approve the plan and I'll get started/)).toBeVisible();
}

test("plain sessions carry zero board chrome", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello")).toBeVisible();
  await expect(page.getByTestId("board-rail")).toHaveCount(0);
  await expect(page.getByTestId("plangate-card")).toHaveCount(0);
});

test("a decomposition turn raises the plan gate; approving moves items to Approved", async ({
  page,
}) => {
  await planTheWork(page);
  const gate = page.getByTestId("plangate-card");
  await expect(gate).toBeVisible();
  // 3 visible + expander with the true remainder (mock UX-030: expander, true count in header)
  await expect(gate).toContainText("Proposed plan — 4 work items");
  await expect(gate).toContainText("Done when:");
  await expect(gate.getByText("Code security review — api")).toBeVisible();
  await expect(gate.getByText("Rate-limit audit — public endpoints")).toHaveCount(0);
  await gate.getByRole("button", { name: /1 more item/ }).click();
  await expect(gate.getByText("Rate-limit audit — public endpoints")).toBeVisible();

  // Blocked renders on top in the rail; proposed items collapse to ONE line —
  // the gate card is the only place the plan renders in full (no double listing).
  const rail = page.getByTestId("board-rail");
  await expect(rail).toBeVisible();
  const groups = rail.locator(".board-group");
  await expect(groups.first()).toHaveText("Blocked");
  await expect(page.getByTestId("board-proposed-note")).toHaveText("4 items awaiting your approval.");
  await expect(rail.getByText("Dependency audit — lockfiles")).toHaveCount(0);

  await page.getByTestId("plangate-approve").click();
  await expect(page.getByTestId("plangate-card")).toHaveCount(0);
  await expect(page.getByTestId("board-proposed-note")).toHaveCount(0);
  await expect(rail).toContainText("Approved");
  await expect(rail.getByText("Dependency audit — lockfiles")).toBeVisible();
});

test("expand opens the overlay board; Esc closes; the user can act on a review item", async ({
  page,
}) => {
  await planTheWork(page);
  await page.getByTestId("board-expand").click();
  const overlay = page.getByTestId("board-overlay");
  await expect(overlay).toBeVisible();
  // Columns render need-attention first; the review item offers the user verbs.
  await expect(page.getByTestId("board-col-blocked")).toBeVisible();
  const reviewCol = page.getByTestId("board-col-review");
  await expect(reviewCol).toContainText("Report rollup");
  await reviewCol.getByRole("button", { name: "Mark done" }).click();
  await expect(page.getByTestId("board-col-done")).toContainText("Report rollup");

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("board-overlay")).toHaveCount(0);
});

test("journal section lists cases once a board exists", async ({ page }) => {
  await planTheWork(page);
  await page.getByRole("button", { name: /Journal/ }).click();
  const journal = page.getByTestId("journal-list");
  await expect(journal).toBeVisible();
  await expect(journal).toContainText("findings");
  await expect(journal).toContainText("12 entries");
});
