// §8.4 breaker surfacing (owner ask 2026-08-24): when the Auto-Approve reviewer pauses
// itself after 5 straight denials, the transcript gets a notice AND the composer's mode
// chip says "· paused" — quietly, until the turn ends or an ask_user answer resets it.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("reviewer pause shows a transcript notice and marks the mode chip", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // Switch the session into Auto-approve (entry gated on the settings flag).
  await page.getByRole("button", { name: "Mode", exact: true }).click();
  await page.getByTestId("mode-menu").getByText("Auto-approve").click();

  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("trip the reviewer");
  await box.press("Enter");

  // The tripping deny carries the pause: notice inline, "· paused" on the chip.
  await expect(page.getByText(/Auto-approve is paused for the rest of this turn/)).toBeVisible();
  await expect(page.getByTestId("mode-paused")).toBeVisible();
  await expect(page.getByRole("button", { name: "Mode", exact: true })).toContainText("paused");
});

test("an unsure escalation shows the reviewer's hesitation on the card", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("run an unsure tool");
  await box.press("Enter");

  const note = page.getByTestId("approval-reviewer-unsure");
  await expect(note).toBeVisible();
  await expect(note).toContainText("reviewer wasn\u2019t sure: This runs a newly created script");
});
