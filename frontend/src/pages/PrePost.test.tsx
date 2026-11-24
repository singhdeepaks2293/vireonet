import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PrePost from "./PrePost";
import { AuthProvider } from "../lib/auth";

const snaps = [
  { id: "aaaaaaaa-1", label: "pre", profile: "VLAN baseline", profile_version: 1, devices: ["leaf-01"], ticket: "CHG1", created_at: "2026-11-24T01:00:00Z", created_by: "op", results: [{ ok: true }], failed: 0 },
  { id: "bbbbbbbb-2", label: "post", profile: "VLAN baseline", profile_version: 1, devices: ["leaf-01"], ticket: "CHG1", created_at: "2026-11-24T02:00:00Z", created_by: "op", results: [{ ok: false }], failed: 1 },
];
vi.mock("../lib/api", async (orig) => ({
  ...((await orig()) as Record<string, unknown>),
  request: vi.fn(async (path: string) => {
    if (path === "/prepost/snapshots") return { items: snaps };
    if (path === "/prepost/profiles") return { items: [{ name: "VLAN baseline", version: 1, builtin: true, tests: 5 }] };
    if (path === "/prepost/compare") return { ok: false, summary: { tests: 2, failed: 1, changed: 1 },
      tests: [{ device: "leaf-01", name: "no-vlan-removed", type: "no_rows_removed", ok: false, message: "removed: ['100']" }, { device: "leaf-01", name: "vlan-1-present", type: "row_exists", ok: true, message: "1 matching row(s)" }],
      diffs: [{ device: "leaf-01", command: "vlans", changed: true,
        parsed: { available: true, key: ["vlan_id"], ignored: ["interfaces"], tolerances: {}, rows_added: [], rows_removed: [{ vlan_id: "100", vlan_name: "web" }], added: 0, removed: 1, changed: [{ key: "1", fields: { status: { pre: "active", post: "suspended" } } }] },
        raw: { available: true, changed: true, diff: "--- pre\n+++ post\n-100  web  active" } }] };
    return {};
  }),
}));

describe("Pre/Post checks", () => {
  it("compares two saved snapshots: tests, parsed diff and raw diff", async () => {
    localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "op", role: "operator" }))}.s`);
    render(<QueryClientProvider client={new QueryClient()}><AuthProvider><PrePost /></AuthProvider></QueryClientProvider>);
    const pre = await screen.findByLabelText("Pre snapshot");
    await screen.findAllByRole("option", { name: /PRE · VLAN baseline/ });
    fireEvent.change(pre, { target: { value: "aaaaaaaa-1" } });
    fireEvent.change(screen.getByLabelText("Post snapshot"), { target: { value: "bbbbbbbb-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    expect(await screen.findByText("1 of 2 tests failed")).toBeInTheDocument();
    expect(screen.getByText("no-vlan-removed")).toBeInTheDocument();
    const diff = screen.getByRole("article", { name: "Diff leaf-01 vlans" });
    expect(within(diff).getByText('"suspended"')).toBeInTheDocument();
    expect(within(diff).getByText(/− \{"vlan_id":"100"/)).toBeInTheDocument();
    fireEvent.click(within(diff).getByRole("button", { name: "Raw" }));
    expect(within(diff).getByText(/-100 web active/)).toBeInTheDocument();
  });
});
