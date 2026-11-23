import { expect, test } from "@playwright/test";

test("style reference loads and theme toggles", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("VireoNet — Style Reference")).toBeVisible();
  await expect(page.getByText("1. Palette — tokens only")).toBeVisible();
  await expect(page.getByText("RITM0001234").first()).toBeVisible();
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("style reference has no hero or welcome screen", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/welcome/i)).toHaveCount(0);
});
