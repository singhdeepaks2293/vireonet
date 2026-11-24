import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChangeDetail from "./ChangeDetail";
import { AuthProvider } from "../lib/auth";

const mocks = vi.hoisted(() => ({
  state: "approved",
  abort: vi.fn(),
  rollback: vi.fn(),
  signalApproval: vi.fn(),
}));

vi.mock("../lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    useChangeDetail: () => ({
      data: {
        change: { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: mocks.state, requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
        intent: {}, diff: [], checks: [], batfish: {}, awx_jobs: [], declared_changes: [], transitions: [],
      },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    api: { abort: mocks.abort, rollback: mocks.rollback, signalApproval: mocks.signalApproval },
  };
});

function renderDetail(role = "operator") {
  localStorage.setItem("vireonet-token", `header.${btoa(JSON.stringify({ username: "op", role }))}.signature`);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/changes/1"]}>
          <Routes><Route path="/changes/:id" element={<ChangeDetail />} /></Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("ChangeDetail actions", () => {
  beforeEach(() => {
    mocks.abort.mockReset();
    mocks.rollback.mockReset();
    mocks.signalApproval.mockReset();
  });

  it("abort calls the abort endpoint, not the approver-only reject signal", async () => {
    mocks.state = "pre_checked";
    renderDetail("operator");
    fireEvent.click(await screen.findByRole("button", { name: "Abort" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Abort" }));
    await waitFor(() => expect(mocks.abort).toHaveBeenCalledWith("1"));
    expect(mocks.signalApproval).not.toHaveBeenCalled();
  });

  it("warns loudly when the rollback failed", async () => {
    mocks.state = "rollback_failed";
    renderDetail("operator");
    expect(await screen.findByText(/Rollback failed\. Devices may be partially changed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Roll back" })).not.toBeInTheDocument();
  });
});
