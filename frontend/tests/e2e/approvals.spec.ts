import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "approver");
  });
  await page.route("**/changes?state=ci_passed*", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        items: [
          { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: "ci_passed", requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
        ], total: 1, page: 1, page_size: 20, pages: 1,
      }),
    });
  });
  await page.route("**/changes/1/detail", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        change: { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: "ci_passed", requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
        intent: {}, diff: [{ old_line: null, new_line: 1, type: "add", text: "vlan 100" }], checks: [{ name: "reachability", status: "pass", message: "ok" }],
        batfish: { passed: true, failures: [] }, awx_jobs: [], transitions: [],
      }),
    });
  });
});

test("approvals queue shows evidence and approves behind confirm", async ({ page }) => {
  await page.goto("/approvals");
  await expect(page.getByText("RITM0001234")).toBeVisible();
  await expect(page.getByText("1 change waiting")).toBeVisible();
  await page.getByRole("button", { name: "Show" }).click();
  await expect(page.getByText("Batfish gate: passed")).toBeVisible();
  await expect(page.getByText("Diff: 1 added line(s), 0 removed line(s)")).toBeVisible();

  await page.route("**/changes/1/signals/approval", async (route) => {
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ ticket: "RITM0001234", signal: "approval", status: "accepted" }) });
  });
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/Approve RITM0001234. This moves the change to approved/)).toBeVisible();
  await page.getByRole("button", { name: "Approve", exact: true }).last().click();
  await expect(page.getByText("Approved RITM0001234.")).toBeVisible();
});

test("approvals queue empty state", async ({ page }) => {
  await page.route("**/changes?state=ci_passed*", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20, pages: 0 }) });
  });
  await page.goto("/approvals");
  await expect(page.getByText("No changes waiting for approval.")).toBeVisible();
});
