import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "operator");
  });
  await page.route("**/ztp/devices?*", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        items: [
          { serial: "FOC1234A", hostname: "leaf-01", model: "C9300-48P", platform: "ios-xe", site: "dc1", stage: "active", ip_address: "10.0.1.101", version: "17.9.1", callback_at: "2026-11-23T11:00:00Z", ticket_number: "ZTP-FOC1234A", netbox_status: "active", dhcp_reservation: "host leaf-01 { fixed-address 10.0.1.101; }" },
          { serial: "FOC1234B", hostname: "leaf-02", model: "C9300-48P", platform: "ios-xe", site: "dc1", stage: "identity_failed", ip_address: "10.0.1.102", version: null, callback_at: "2026-11-23T11:05:00Z", ticket_number: "ZTP-FOC1234B", netbox_status: "staged", dhcp_reservation: "host leaf-02 { fixed-address 10.0.1.102; }" },
        ], total: 2, page: 1, page_size: 20, pages: 1,
      }),
    });
  });
});

test("ZTP devices list shows stages and reservation, retry asks for confirmation", async ({ page }) => {
  await page.goto("/ztp");
  await expect(page.getByText("FOC1234A")).toBeVisible();
  await expect(page.getByText("identity_failed")).toBeVisible();
  await expect(page.getByText("host leaf-01 { fixed-address 10.0.1.101; }")).toBeVisible();
  await page.route("**/ztp/devices/FOC1234B/retry", async (route) => {
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ serial: "FOC1234B", stage: "registered", token: "new-token-once" }) });
  });
  await page.getByRole("button", { name: "Retry" }).nth(1).click();
  await expect(page.getByText(/Retry FOC1234B. This issues a new one-time token/)).toBeVisible();
  await page.getByRole("button", { name: "Retry", exact: true }).last().click();
  await expect(page.getByText(/Retry started for FOC1234B/)).toBeVisible();
});
