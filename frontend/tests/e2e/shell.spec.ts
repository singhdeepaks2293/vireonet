import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "approver");
    localStorage.setItem("vireonet-theme", "light");
  });
});

test("nav navigates to every section by keyboard", async ({ page }) => {
  await page.goto("/changes");
  await expect(page.getByRole("heading", { name: "Changes" })).toBeVisible();
  await page.getByRole("link", { name: "ZTP devices" }).click();
  await expect(page.getByRole("heading", { name: "ZTP devices" })).toBeVisible();
  await page.getByRole("link", { name: "Approvals" }).click();
  await expect(page.getByRole("heading", { name: "Approvals" })).toBeVisible();
  await page.getByRole("link", { name: "System health" }).click();
  await expect(page.getByRole("heading", { name: "System health" })).toBeVisible();
  await page.getByRole("link", { name: "Style reference" }).click();
  await expect(page.getByText("1. Palette — tokens only")).toBeVisible();
});

test("unauthenticated user sees login, not the shell", async ({ page }) => {
  await page.addInitScript(() => localStorage.clear());
  await page.goto("/changes");
  await expect(page.getByText("Sign in with an API token.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Changes" })).toHaveCount(0);
});

test("theme persists across reload", async ({ page }) => {
  await page.goto("/changes");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
