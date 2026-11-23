import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
  });
  await page.route("**/config/repos", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify([
        { hostname: "leaf-01", repo: "netops/config-leaf-01" },
        { hostname: "leaf-02", repo: "netops/config-leaf-02" },
      ]),
    });
  });
});

test("config repos list shows one repo per device", async ({ page }) => {
  await page.goto("/config");
  await expect(page.getByText("leaf-01")).toBeVisible();
  await expect(page.getByText("netops/config-leaf-01")).toBeVisible();
  await expect(page.getByText("netops/config-leaf-02")).toBeVisible();
  await page.getByLabel("Filter by hostname or repo").fill("leaf-02");
  await expect(page.getByText("netops/config-leaf-01")).toHaveCount(0);
  await expect(page.getByText("netops/config-leaf-02")).toBeVisible();
});
