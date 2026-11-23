import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "operator");
  });
  await page.route("**/locks", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify([
        { device: "leaf-01", ticket_number: "RITM0001234", holder: "vireonet-worker", acquired_at: "2026-11-23T11:00:00Z", expires_at: "2026-11-23T11:30:00Z" },
      ]),
    });
  });
  await page.route("**/change-windows", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify([
        { days: ["Mon", "Tue", "Wed", "Thu", "Fri"], start: "01:00", end: "05:00", timezone: "UTC", emergency_bypasses_window: true, currently_in_window: false, next_window_start: "2026-11-24T01:00:00Z" },
      ]),
    });
  });
});

test("locks list shows TTL and change windows, release asks for confirmation", async ({ page }) => {
  await page.goto("/locks");
  await expect(page.getByText("leaf-01")).toBeVisible();
  await expect(page.getByText("RITM0001234")).toBeVisible();
  await expect(page.getByText("01:00")).toBeVisible();
  await expect(page.getByText("05:00")).toBeVisible();

  await page.route("**/locks/leaf-01/release", async (route) => {
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ device: "leaf-01", status: "released" }) });
  });
  await page.getByRole("button", { name: "Release" }).click();
  await expect(page.getByText(/Release the lock on leaf-01 held by RITM0001234/)).toBeVisible();
  await page.getByRole("button", { name: "Release", exact: true }).last().click();
  await expect(page.getByText("Released lock on leaf-01.")).toBeVisible();
});
