import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ChangeDetail from "./ChangeDetail";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useChangeDetail: () => ({
      data: {
        change: { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: "ci_passed", requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
        intent: { change_type: "vlan_add", vlan_id: 100 },
        diff: [{ old_line: null, new_line: 1, type: "add", text: "vlan 100" }],
        checks: [{ name: "reachability", status: "pass", message: "all devices active" }],
        batfish: { passed: true, failures: [] },
        awx_jobs: [{ job_id: 42, status: "successful", to_state: "applied" }],
        transitions: [{ from_state: "received", to_state: "validated", actor: "vireonet", reason: "ok", evidence: {}, created_at: "2026-11-23T11:00:00Z" }],
      },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    api: { signalApproval: vi.fn(), rollback: vi.fn() },
  };
});

function renderDetail() {
  localStorage.setItem("vireonet-token", `header.${btoa(JSON.stringify({ username: "test", role: "approver" }))}.signature`);
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/changes/1"]}>
          <Routes><Route path="/changes/:id" element={<ChangeDetail />} /></Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("ChangeDetail", () => {
  it("shows header, timeline, approve action and checks tab", async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByText("RITM0001234")).toBeInTheDocument());
    expect(screen.getAllByText("ci_passed").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByText("reachability")).toBeInTheDocument();
    expect(screen.getByText("all devices active")).toBeInTheDocument();
  });

  it("shows diff with line numbers when Diff tab is selected", async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByText("RITM0001234")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Diff" }));
    await waitFor(() => expect(screen.getByText(/vlan 100/)).toBeInTheDocument());
  });

  it("shows audit trail", async () => {
    renderDetail();
    await waitFor(() => expect(screen.getByText("RITM0001234")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Audit trail" }));
    await waitFor(() => expect(screen.getAllByText("received").length).toBeGreaterThan(0));
    expect(screen.getAllByText("validated").length).toBeGreaterThan(0);
  });
});
