import { expect, test } from "@playwright/test";

function tokenFor(role: string): string {
  const payload = btoa(JSON.stringify({ username: "alice", role }));
  return `header.${payload}.signature`;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.setItem("vireonet-token", token);
  }, tokenFor("admin"));
  await page.route("**/iam/users", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify([
          { id: "1", username: "alice", display_name: "Alice", role: "admin", is_active: true, auth_provider: "local", created_at: "2026-11-23T11:00:00Z" },
          { id: "2", username: "bob", display_name: "Bob", role: "viewer", is_active: true, auth_provider: "local", created_at: "2026-11-23T11:00:00Z" },
        ]),
      });
    }
  });
  await page.route("**/iam/roles", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ roles: { viewer: ["changes:read"], admin: ["changes:read", "iam:manage"] }, permissions: ["changes:read", "iam:manage"] }),
    });
  });
});

test("admin can see users, change a role, and view the roles matrix", async ({ page }) => {
  await page.goto("/iam");
  await expect(page.getByText("alice")).toBeVisible();
  await expect(page.getByText("bob")).toBeVisible();
  await page.route("**/iam/users/2", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ id: "2", username: "bob", display_name: "Bob", role: "operator", is_active: true, auth_provider: "local", created_at: "2026-11-23T11:00:00Z" }),
    });
  });
  await page.getByLabel("Role for bob").selectOption("operator");
  await expect(page.getByText("User updated.")).toBeVisible();
  await page.getByRole("tab", { name: "Roles" }).click();
  await expect(page.getByText("iam:manage")).toBeVisible();
});

test("login page is username and password, not token and role", async ({ page }) => {
  await page.addInitScript(() => localStorage.clear());
  await page.goto("/changes");
  await expect(page.getByLabel("Username")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
  await expect(page.getByLabel("Role")).toHaveCount(0);
});
