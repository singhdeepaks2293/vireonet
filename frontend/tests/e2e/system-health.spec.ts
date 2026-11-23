import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "viewer");
  });
  await page.route("**/system/health", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        components: [
          { name: "api", status: "ok", detail: "FastAPI responding" },
          { name: "database", status: "ok", detail: "SELECT 1 succeeded" },
          { name: "temporal", status: "ok", detail: "namespace reachable" },
        ],
      }),
    });
  });
  await page.route("**/adapters/health", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        adapters: [
          { name: "servicenow", status: "ok", latency_ms: 120, last_success: "2026-11-23T11:00:00Z", capabilities: ["read_ticket", "add_work_note"], detail: "ok" },
          { name: "netbox", status: "ok", latency_ms: 45, last_success: "2026-11-23T11:01:00Z", capabilities: ["get_device", "update_device"], detail: "ok" },
        ],
      }),
    });
  });
});

test("system health shows components and adapters", async ({ page }) => {
  await page.goto("/health");
  await expect(page.getByText("api")).toBeVisible();
  await expect(page.getByText("database")).toBeVisible();
  await expect(page.getByText("temporal")).toBeVisible();
  await expect(page.getByText("servicenow")).toBeVisible();
  await expect(page.getByText("netbox")).toBeVisible();
  await expect(page.getByText("read_ticket, add_work_note")).toBeVisible();
  await expect(page.getByText("All adapters responding. Refreshed every 30 seconds.")).toBeVisible();
});
