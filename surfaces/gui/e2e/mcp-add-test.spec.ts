// UX-033: the Add MCP server flow (Remote URL + JSON tabs) and the Test button.
// Remote URL adds an http entry and probes it immediately (testing… → connected);
// a guarded server (mock: locked-*) lands on "needs sign-in" with the OAuth switch;
// the JSON paste box remains for stdio/advanced, and every row can be re-tested.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openMcpTab(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await page.getByRole("button", { name: "MCP servers", exact: true }).click();
}

test("remote URL tab: add & test flips to connected with tool count", async ({ page }) => {
  await openMcpTab(page);
  await page.getByRole("button", { name: "Add a server" }).click();

  // URL tab is the default door; bad URL is caught before anything is added.
  await page.getByTestId("mcp-add-name").fill("notes");
  await page.getByTestId("mcp-add-url").fill("mcp.example.com/mcp");
  await page.getByRole("button", { name: "Add & test" }).click();
  await expect(page.getByText("Enter the server's full URL")).toBeVisible();

  await page.getByTestId("mcp-add-url").fill("https://mcp.example.com/mcp");
  await page.getByRole("button", { name: "Add & test" }).click();

  const row = page.locator(".space-y-2 > div").filter({ hasText: "notes" }).first();
  await expect(row).toContainText("testing…");
  await expect(row).toContainText("connected", { timeout: 10_000 });
  await expect(row).toContainText("6 tools");
});

test("guarded server: 401 → needs sign-in → OAuth switch connects", async ({ page }) => {
  await openMcpTab(page);
  await page.getByRole("button", { name: "Add a server" }).click();
  await page.getByTestId("mcp-add-name").fill("locked-crm");
  await page.getByTestId("mcp-add-url").fill("https://mcp.locked.example/mcp");
  await page.getByRole("button", { name: "Add & test" }).click();

  // The anonymous probe 401s: the row says needs sign-in and offers the fix.
  const row = page.locator(".space-y-2 > div").filter({ hasText: "locked-crm" }).first();
  await expect(row).toContainText("needs sign-in", { timeout: 10_000 });
  await expect(row).toContainText("authentication required");

  // Sign in switches the entry to oauth and starts the browser flow; the poll
  // flips it to connected.
  await row.getByTestId("mcp-authfix-locked-crm").click();
  await expect(row).toContainText("signing in…");
  await expect(row).toContainText("connected", { timeout: 10_000 });
  await expect(row).toContainText("oauth");
});

test("JSON tab still adds stdio servers; Test probes an existing row", async ({ page }) => {
  await openMcpTab(page);
  await page.getByRole("button", { name: "Add a server" }).click();
  await page.getByTestId("mcp-add-tab-json").click();
  await page
    .locator("textarea")
    .fill('{"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}}');
  await page.getByRole("button", { name: "Add", exact: true }).click();

  const row = page.locator(".space-y-2 > div").filter({ hasText: "files" }).first();
  await expect(row).toContainText("stdio · configured");

  // Test on the untouched row: testing… then the mock's connected · 6 tools.
  await row.getByTestId("mcp-test-files").click();
  await expect(row).toContainText("testing…");
  await expect(row).toContainText("connected", { timeout: 10_000 });
  await expect(row).toContainText("6 tools");
});
