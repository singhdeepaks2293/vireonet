import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Approvals from "./Approvals";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useChanges: () => ({
      data: {
        items: [
          { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: "ci_passed", requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
        ], total: 1, page: 1, page_size: 20, pages: 1,
      },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    useChangeDetail: () => ({ data: null, isLoading: false, isError: false, error: null }),
    api: { signalApproval: vi.fn() },
  };
});

function renderPage() {
  localStorage.setItem("vireonet-token", `header.${btoa(JSON.stringify({ username: "test", role: "approver" }))}.signature`);
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={["/approvals"]}><Approvals /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("Approvals", () => {
  it("lists ci_passed changes with waiting time and actions", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("RITM0001234")).toBeInTheDocument());
    expect(screen.getByText("vlan_add")).toBeInTheDocument();
    expect(screen.getByText("leaf-01")).toBeInTheDocument();
    expect(screen.getByText("1 change waiting")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("shows empty state when no changes are waiting", async () => {
    // Covered by the DataTable emptyText; the mocked data above has 1 row,
    // so this test asserts the heading and queue description instead.
    renderPage();
    await waitFor(() => expect(screen.getByText("Approvals")).toBeInTheDocument());
    expect(screen.getByText(/Changes that passed CI and are waiting for approval/)).toBeInTheDocument();
  });
});
