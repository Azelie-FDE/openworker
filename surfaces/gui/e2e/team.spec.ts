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

test("the staffing gate shows the roster and the grant sentence", async ({ page }) => {
  await proposeTeam(page);
  const card = page.getByTestId("teamreq-card");
  await expect(card).toContainText("Proposed team — 3 workers");
  await expect(card).toContainText("swe-worker");
  await expect(card).toContainText("implementation");
  await expect(card).toContainText("test-worker");
  await expect(card).toContainText(
    "Approving grants the lead create, assign & steer — this team only, revocable.",
  );
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
  await expect(children).toContainText("swe-worker · #1 in progress");
  await expect(children).toContainText("design-worker · idle");
  await expect(children).toContainText("test-worker · #4 blocked");

  // Collapse hides them again — the team is one entry, not a panel.
  await page.getByTestId("team-toggle-sess-lead").click();
  await expect(page.getByTestId("team-children-sess-lead")).toHaveCount(0);
});
