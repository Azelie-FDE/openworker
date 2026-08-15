// OPE-91: agent-authored HTML renders in the artifact viewer inside an AIRTIGHT sandbox.
// The app webview is privileged (Tauri IPC), so the report page must be null-origin
// (no parent access) and offline (no subresource exfiltration) — while inline scripts,
// the thing report interactivity needs, keep working. The fixture page actively probes
// all three properties and reports into #probe.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openReport(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask the coworker/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await page.locator(".artifact-row", { hasText: "security-review.html" }).click();
}

test("HTML artifact renders sandboxed: scripts run, parent and network stay sealed", async ({
  page,
}) => {
  await openReport(page);
  const frame = page.getByTestId("artifact-frame");
  await expect(frame).toBeVisible();
  // No allow-same-origin, ever: with srcDoc it would run the page same-origin with the
  // privileged app webview. This assertion is the regression lock for that exact flag.
  await expect(frame).toHaveAttribute("sandbox", "allow-scripts");

  const probe = page.frameLocator('[data-testid="artifact-frame"]').locator("#probe");
  await expect(probe).toContainText("script ran in sandbox"); // interactivity works
  await expect(probe).toContainText("parent blocked"); // null origin held
  await expect(probe).toContainText("network blocked"); // CSP stopped the exfil img
  await expect(page).not.toHaveTitle("ESCAPED");
});

test("HTML artifact offers Open in browser as the unsandboxed escape hatch", async ({
  page,
}) => {
  await openReport(page);
  await expect(page.getByTestId("artifact-open-browser")).toBeVisible();
});
