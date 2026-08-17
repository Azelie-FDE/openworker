// Agent teams (OPE-97): the staffing gate + the sidebar's expandable team entry.
// The fake lead proposes a roster on "staff the team" and suspends; approval
// "pre-spawns" workers (the fixture mirrors create_team by adding worker sessions),
// which then nest under the lead's ONE expandable RECENT entry.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function proposeTeam(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("staff the team");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("teamreq-card")).toBeVisible();
}

test("the decomposition gate shows items with criteria; approval lands them on the board", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("propose the split");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("itemsreq-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Proposed work items — 4");
  await expect(card).toContainText("Done when:");
  // 3 visible + expander with the true remainder
  await expect(card.getByText("Verification pass")).toHaveCount(0);
  await card.getByRole("button", { name: /1 more item/ }).click();
  await expect(card.getByText("Verification pass")).toBeVisible();

  await page.getByTestId("itemsreq-approve").click();
  await expect(page.getByText(/Items created on the board/)).toBeVisible();
  await expect(page.getByTestId("board-rail")).toBeVisible();
});

test("declining the split returns feedback to the lead", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("propose the split");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("itemsreq-card").waitFor();
  await page.getByRole("button", { name: "Not now" }).click();
  await expect(page.getByText(/reworking the split/)).toBeVisible();
});

test("the staffing gate shows named workers, the chat toggle, and the grant sentence", async ({
  page,
}) => {
  await proposeTeam(page);
  const card = page.getByTestId("teamreq-card");
  await expect(card).toContainText("Proposed team — 3 workers");
  // callnames lead the rows; persona + reason follow
  await expect(card).toContainText("nia");
  await expect(card).toContainText("swe-worker");
  await expect(card).toContainText("implementation");
  await expect(card).toContainText("checks");
  // the chat checkbox defaults OFF — the user's call, not the lead's
  await expect(card.getByTestId("teamreq-chat-toggle")).not.toBeChecked();
  await expect(card).toContainText(
    "Approving grants the lead create, assign & steer — this team only, revocable.",
  );
});

test("enabling chat at the gate adds the # team chat row; posting works with mentions", async ({
  page,
}) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-chat-toggle").check();
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();

  await page.getByTestId("team-toggle-sess-lead").click();
  const chatRow = page.getByTestId("team-chat-row-sess-lead");
  await expect(chatRow).toBeVisible();
  await expect(chatRow).toContainText("1"); // unread badge

  await chatRow.click();
  const view = page.getByTestId("teamchat-view");
  await expect(view).toBeVisible();
  await expect(view).toContainText("assets bucket is public");
  await expect(view.locator(".chat-mention").first()).toHaveText("@nia");

  await page.getByTestId("chat-input").fill("ship it current-month only @lead");
  await page.getByTestId("chat-send").click();
  await expect(view).toContainText("ship it current-month only");

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("teamchat-view")).toHaveCount(0);
});

test("a sleeping lead shows the strip; Ask for a status wakes it", async ({ page }) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();
  // open the lead's session — it set a check-in timer, so it's sleeping
  await page.getByText("Build the statements page").click();
  const strip = page.getByTestId("sleep-strip");
  await expect(strip).toBeVisible({ timeout: 12_000 });
  await expect(strip).toContainText("Sleeping until");
  await expect(strip).toContainText("while the team works");
  await page.getByTestId("sleep-status-btn").click();
  await expect(page.getByText(/Echo: Quick status check/)).toBeVisible();
});

test("with chat declined at the gate, no chat row renders", async ({ page }) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();
  await page.getByTestId("team-toggle-sess-lead").click();
  await expect(page.getByTestId("team-children-sess-lead")).toBeVisible();
  await expect(page.getByTestId("team-chat-row-sess-lead")).toHaveCount(0);
});

test("declining the roster returns the turn to the lead", async ({ page }) => {
  await proposeTeam(page);
  await page.getByRole("button", { name: "Not now" }).click();
  await expect(page.getByText(/tell me how to change the roster/)).toBeVisible();
  await expect(page.getByTestId("teamreq-card")).toHaveCount(0);
});

test("approval creates the team; workers nest under the lead's expandable entry", async ({
  page,
}) => {
  await proposeTeam(page);
  await page.getByTestId("teamreq-approve").click();
  await expect(page.getByText(/Team created/)).toBeVisible();

  // The workers exist as sessions now — but never as top-level RECENT rows.
  // (The sidebar refreshes on its 5s poll, so allow one full cycle.)
  await expect(page.getByTestId("team-toggle-sess-lead")).toBeVisible({ timeout: 12_000 });
  await expect(page.getByText("Build the statements page")).toBeVisible();
  await expect(page.getByTestId("team-children-sess-lead")).toHaveCount(0);

  await page.getByTestId("team-toggle-sess-lead").click();
  const children = page.getByTestId("team-children-sess-lead");
  await expect(children).toBeVisible();
  await expect(children).toContainText("nia · #1 in progress");
  await expect(children).toContainText("webb · idle");
  await expect(children).toContainText("checks · #4 blocked");

  // Collapse hides them again — the team is one entry, not a panel.
  await page.getByTestId("team-toggle-sess-lead").click();
  await expect(page.getByTestId("team-children-sess-lead")).toHaveCount(0);
});
