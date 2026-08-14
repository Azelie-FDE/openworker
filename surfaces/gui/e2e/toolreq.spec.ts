// OPE-85: a missing CLI becomes a visible decision, never a silently dropped check.
// The bug this guards (owner-hit 2026-08-13): with gitleaks absent, a security review
// quietly omitted its git-history secret scan — "we couldn't look" rendered as "clean".
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function ask(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("scan for secrets");
  await page.getByRole("button", { name: "Send" }).click();
}

test("request_tool surfaces a card naming the tool, the reason and the pinned version", async ({
  page,
}) => {
  await ask(page);
  const card = page.locator(".dirreq-card");
  await expect(card).toContainText("gitleaks");
  await expect(card).toContainText("scan the git history for committed secrets");
  await expect(card).toContainText("8.30.1");
  await expect(card).toContainText(/checksum-verified/i);
  // Declining must read as a normal choice, not a failure.
  await expect(card.getByTestId("toolreq-skip")).toBeVisible();
});

test("installing runs the check; skipping still reports coverage", async ({ page }) => {
  await ask(page);
  await page.getByTestId("toolreq-install").click();
  await expect(page.locator(".main-scroll")).toContainText("Installed gitleaks");

  await page.getByPlaceholder(/Ask the coworker/).fill("scan for secrets");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("toolreq-skip").click();
  // The whole point: the skipped check is disclosed, not invisible.
  await expect(page.locator(".main-scroll")).toContainText(/Coverage:/);
});
