import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "viewer");
  });
  await page.route("**/adapters/settings", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify([
        { name: "servicenow", enabled: true, mode: "real", endpoint: "https://servicenow.example.com", timeout_seconds: 30, retry_max_attempts: 3, rate_limit_per_second: 10, capabilities: ["read_ticket", "add_work_note"] },
        { name: "awx", enabled: true, mode: "real", endpoint: "https://awx.example.com", timeout_seconds: 30, retry_max_attempts: 3, rate_limit_per_second: 5, capabilities: ["launch_job"] },
      ]),
    });
  });
});

test("adapter settings are read-only and show no secrets", async ({ page }) => {
  await page.goto("/adapters");
  await expect(page.getByText("servicenow")).toBeVisible();
  await expect(page.getByText("awx")).toBeVisible();
  await expect(page.getByText("https://servicenow.example.com")).toBeVisible();
  await expect(page.getByText("read_ticket, add_work_note")).toBeVisible();
  await expect(page.getByText(/No secrets are shown on this page/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Save|Edit|Update/ })).toHaveCount(0);
});
