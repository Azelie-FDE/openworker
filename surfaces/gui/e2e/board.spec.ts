// Agent teams (OPE-96): the board in the session UI — rail section (grouped by
// state, blocked on top) and the expanded Linear-shaped overlay. There is no
// draft/proposed state: plan approval is a conversation-layer moment (the existing
// plan-approval flow); the board only ever holds accepted work. The fake agent
// files items on "plan the work"; transitions round-trip through the mocked
// /board endpoints as the user.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function planTheWork(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("plan the work");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/filed 5 work items/)).toBeVisible();
}

test("plain sessions carry zero board chrome", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello")).toBeVisible();
  await expect(page.getByTestId("board-rail")).toHaveCount(0);
});

test("filed items appear grouped in the rail, blocked on top, open items listed", async ({
  page,
}) => {
  await planTheWork(page);
  const rail = page.getByTestId("board-rail");
  await expect(rail).toBeVisible();
  const groups = rail.locator(".board-group");
  await expect(groups.first()).toHaveText("Blocked");
  // No gate, no draft: open items are real items, listed like any other state.
  await expect(rail).toContainText("Open");
  await expect(rail.getByText("Secrets — git history, both repos")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Board · 1 blocked · 1 review · 1 in progress · 2 open/ }),
  ).toBeVisible();
});

test("expand opens the overlay board; the user verifies review items and removes open ones", async ({
  page,
}) => {
  await planTheWork(page);
  await page.getByTestId("board-expand").click();
  const overlay = page.getByTestId("board-overlay");
  await expect(overlay).toBeVisible();
  await expect(page.getByTestId("board-col-blocked")).toBeVisible();

  // review → done (the verification gate stays a human/lead call)
  const reviewCol = page.getByTestId("board-col-review");
  await reviewCol.getByRole("button", { name: "Mark done" }).click();
  await expect(page.getByTestId("board-col-done")).toContainText("Report rollup");

  // open → removed (lead/user triage of filed items; maps to canceled underneath)
  const openCol = page.getByTestId("board-col-open");
  await openCol.getByRole("button", { name: "Remove" }).first().click();
  await expect(page.getByTestId("board-col-canceled")).toBeVisible();

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
