import { expect, test } from "@playwright/test";

function tokenFor(role: string): string {
  const payload = btoa(JSON.stringify({ username: "alice", role }));
  return `header.${payload}.signature`;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.setItem("vireonet-token", token);
  }, tokenFor("admin"));
  await page.route("**/setup/gitea/manual", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ steps: ["1. Install Docker.", "2. Run install_gitea.sh."] }) });
  });
});

test("Gitea setup offers manual and automatic", async ({ page }) => {
  await page.goto("/setup/gitea");
  await expect(page.getByText("Manual install")).toBeVisible();
  await expect(page.getByText("Automatic install")).toBeVisible();
  await page.getByRole("button", { name: "Show manual steps" }).click();
  await expect(page.getByText("1. Install Docker.")).toBeVisible();
  await page.getByRole("button", { name: "Back" }).click();
  await page.getByRole("button", { name: "Install automatically" }).click();
  await expect(page.getByLabel("Admin username")).toBeVisible();
  await page.route("**/setup/gitea/install", async (route) => {
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ id: "job-1", status: "running", steps: [{ step: "install", status: "running", detail: "docker compose up gitea" }] }) });
  });
  await page.route("**/setup/gitea/status/job-1", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "job-1", status: "done", steps: [{ step: "install", status: "done", detail: "docker compose up gitea" }, { step: "setup", status: "done", detail: "create admin, repo, token, Vault" }] }) });
  });
  await page.getByRole("button", { name: "Install Gitea automatically" }).click();
  await expect(page.getByText(/Install Gitea with Docker Compose at/)).toBeVisible();
  await page.getByRole("button", { name: "Install Gitea", exact: true }).last().click();
  await expect(page.getByText("Status: done")).toBeVisible();
});
