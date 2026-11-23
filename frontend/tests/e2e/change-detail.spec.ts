import { expect, test } from "@playwright/test";

const DETAIL = {
  change: { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: "ci_passed", requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
  intent: { change_type: "vlan_add", vlan_id: 100 },
  diff: [{ old_line: null, new_line: 1, type: "add", text: "vlan 100" }],
  checks: [{ name: "reachability", status: "pass", message: "all devices active" }],
  batfish: { passed: true, failures: [] },
  awx_jobs: [],
  transitions: [{ from_state: "received", to_state: "validated", actor: "vireonet", reason: "ok", evidence: {}, created_at: "2026-11-23T11:00:00Z" }],
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "approver");
  });
  await page.route("**/changes/1/detail", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(DETAIL) });
  });
});

test("view change detail, switch tabs, approve behind confirm", async ({ page }) => {
  await page.goto("/changes/1");
  await expect(page.getByText("RITM0001234")).toBeVisible();
  await expect(page.getByText("reachability")).toBeVisible();
  await page.getByRole("tab", { name: "Diff" }).click();
  await expect(page.getByText("vlan 100")).toBeVisible();
  await page.getByRole("tab", { name: "Audit trail" }).click();
  await expect(page.getByText("validated")).toBeVisible();

  await page.route("**/changes/1/signals/approval", async (route) => {
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ ticket: "RITM0001234", signal: "approval", status: "accepted" }) });
  });
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/Approve RITM0001234. This moves the change to approved/)).toBeVisible();
  await page.getByRole("button", { name: "Approve", exact: true }).last().click();
  await expect(page.getByText("Approval signal sent.")).toBeVisible();
});

test("roll back is not shown for ci_passed", async ({ page }) => {
  await page.goto("/changes/1");
  await expect(page.getByRole("button", { name: "Roll back" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Abort" })).toBeVisible();
});
