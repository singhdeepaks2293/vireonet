import { expect, test } from "@playwright/test";

// NOTE: this spec is NOT run in the sandbox — there is no system Chromium
// there. Run it with `npm run test:e2e` on a machine with Playwright
// browsers installed. All API calls are route-mocked, so no backend is
// needed; the flows assert the UI behaviour (list, filters, confirm
// dialogs, action results) against the mocked API.

const LIST = {
  items: [
    {
      id: "rec-1", device: "leaf-01", kind: "unmanaged", severity: "high", status: "detected",
      first_seen: "2026-11-23T10:00:00Z", last_seen: "2026-11-23T11:00:00Z",
      baseline_ref: null, baseline_hash: "aaa", running_hash: "bbb", diff: "- description uplink\n+ description CHANGED",
      attributed_actor: "alice", attributed_at: null, linked_ticket: null,
    },
  ],
  total: 1, limit: 50, offset: 0,
};

const DETAIL = {
  record: LIST.items[0],
  decisions: [],
  audit: [
    { id: "a1", from_state: "detected", to_state: "under_review", actor: "approver1", reason: "triaged", created_at: "2026-11-23T10:05:00Z" },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("vireonet-token", "e2e-token");
    localStorage.setItem("vireonet-role", "approver");
  });
  await page.route("**/drift?status=exception_active*", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        items: [{
          ...LIST.items[0],
          id: "rec-exp", status: "exception_active",
        }],
        total: 1, limit: 100, offset: 0,
      }),
    });
  });
  await page.route("**/drift/rec-exp", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        record: { ...LIST.items[0], id: "rec-exp", status: "exception_active" },
        decisions: [{
          id: "d1", action: "exception", actor: "approver1", reason: "known good",
          ticket_ref: "RITM1", expiry: "2026-11-20T00:00:00Z", created_at: "2026-11-23T10:00:00Z",
        }],
        audit: [],
      }),
    });
  });
  await page.route("**/drift/rec-1/rollback", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ status: "resolved", record_id: "rec-1", change_ticket: "DRIFT-RB-abc" }),
    });
  });
  await page.route("**/drift/rec-1/merge", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ status: "merge_pending", record_id: "rec-1", branch: "drift/leaf-01/rec-1", mr_id: 1, mr_url: "https://git.example/mr/1" }),
    });
  });
  await page.route("**/drift/rec-1/exception", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ status: "exception_active", record_id: "rec-1", expiry: "2026-12-01T00:00:00Z", owner: "netops" }),
    });
  });
  await page.route("**/drift/rec-1", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(DETAIL) });
  });
  await page.route("**/drift?*", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(LIST) });
  });
});

test("view drift list and open a record", async ({ page }) => {
  await page.goto("/drift");
  await expect(page.getByText("leaf-01")).toBeVisible();
  await expect(page.getByText("high")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByText("1 record")).toBeVisible();
});

test("rollback from the detail page", async ({ page }) => {
  await page.goto("/drift/rec-1");
  await expect(page.getByText("Drift leaf-01")).toBeVisible();
  await page.getByLabel("Reason (required)").fill("restore baseline");
  await page.getByLabel("ServiceNow reference (required)").fill("RITM0001234");
  await page.getByRole("button", { name: "Roll back" }).first().click();
  await expect(page.getByText(/restores the stored baseline config/)).toBeVisible();
  await page.getByRole("button", { name: "Roll back" }).last().click();
  await expect(page.getByText(/rollback accepted: resolved/)).toBeVisible();
});

test("merge from the detail page", async ({ page }) => {
  await page.goto("/drift/rec-1");
  await page.getByLabel("Reason (required)").fill("keep the new description");
  await page.getByLabel("ServiceNow reference (required)").fill("RITM0001234");
  await page.getByRole("button", { name: "Merge" }).first().click();
  await expect(page.getByText(/per-device vars override/)).toBeVisible();
  await page.getByRole("button", { name: "Merge" }).last().click();
  await expect(page.getByText(/merge accepted: merge_pending/)).toBeVisible();
});

test("grant exception from the detail page", async ({ page }) => {
  await page.goto("/drift/rec-1");
  await page.getByLabel("Reason (required)").fill("known good");
  await page.getByLabel("ServiceNow reference (required)").fill("RITM0001234");
  await page.getByLabel("Exception owner").fill("netops");
  await page.getByLabel("Exception expiry").fill("2026-12-01T00:00");
  await page.getByRole("button", { name: "Grant exception" }).first().click();
  await expect(page.getByText(/ignores this exact diff hash/)).toBeVisible();
  await page.getByRole("button", { name: "Grant exception" }).last().click();
  await expect(page.getByText(/exception accepted: exception_active/)).toBeVisible();
});

test("expired exception shows as expired", async ({ page }) => {
  await page.goto("/exceptions");
  await expect(page.getByText("leaf-01")).toBeVisible();
  await expect(page.getByText("expired")).toBeVisible();
});
