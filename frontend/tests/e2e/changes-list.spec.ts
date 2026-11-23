import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "viewer");
  });
  await page.route("**/changes?*", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        items: [
          { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: "approved", requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
          { id: "2", ticket_number: "RITM0001235", change_type: "static_route_add", state: "pre_check_failed", requester: "bob", devices: ["leaf-02"], created_at: "2026-11-23T11:04:00Z" },
        ], total: 2, page: 1, page_size: 20, pages: 1,
      }),
    });
  });
});

test("changes list shows rows, filters sync to URL, row opens detail", async ({ page }) => {
  await page.goto("/changes");
  await expect(page.getByText("RITM0001234")).toBeVisible();
  await expect(page.getByText("2 changes")).toBeVisible();
  await page.getByLabel("State").selectOption("approved");
  await expect(page).toHaveURL(/state=approved/);
  await page.getByText("RITM0001234").click();
  await expect(page).toHaveURL(/\/changes\/1/);
});

test("changes list empty state", async ({ page }) => {
  await page.route("**/changes?*", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20, pages: 0 }) });
  });
  await page.goto("/changes");
  await expect(page.getByText("No changes match these filters.")).toBeVisible();
});
