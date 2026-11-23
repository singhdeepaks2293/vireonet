import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import DriftDetail from "./DriftDetail";

const DETAIL = {
  record: {
    id: "1", device: "leaf-01", kind: "unmanaged", severity: "high", status: "under_review",
    first_seen: "2026-11-23T10:00:00Z", last_seen: "2026-11-23T11:00:00Z",
    baseline_ref: "sha1", baseline_hash: "aaa", running_hash: "bbb",
    diff: "- description uplink\n+ description CHANGED",
    attributed_actor: "alice", attributed_at: "2026-11-23T10:30:00Z", linked_ticket: null,
  },
  decisions: [
    { id: "d1", action: "rollback", actor: "approver1", reason: "restore", ticket_ref: "RITM1", expiry: null, created_at: "2026-11-23T11:00:00Z" },
  ],
  audit: [
    { id: "a1", from_state: "detected", to_state: "under_review", actor: "approver1", reason: "triaged", created_at: "2026-11-23T10:05:00Z" },
  ],
};

const { driftAction } = vi.hoisted(() => ({ driftAction: vi.fn(async () => ({ status: "resolved" })) }));

vi.mock("../api/drift", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useDriftDetail: () => ({
      data: DETAIL, isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    driftApi: { driftAction },
  };
});

function renderPage() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/drift/1"]}>
        <Routes><Route path="/drift/:id" element={<DriftDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DriftDetail", () => {
  it("renders the diff tab, timeline and attribution", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Drift leaf-01")).toBeInTheDocument());
    expect(screen.getByText(/description CHANGED/)).toBeInTheDocument();
    expect(screen.getByText(/detected → under_review/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Attribution" }));
    await waitFor(() => expect(screen.getByText(/Attributed actor: alice/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Decisions / Audit" }));
    await waitFor(() => expect(screen.getByText("rollback")).toBeInTheDocument());
    expect(screen.getByText("RITM1")).toBeInTheDocument();
  });

  it("keeps action buttons disabled until reason and ticket are filled", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Drift leaf-01")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Roll back" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Merge" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Grant exception" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reason (required)"), { target: { value: "restore baseline" } });
    fireEvent.change(screen.getByLabelText("ServiceNow reference (required)"), { target: { value: "RITM0001234" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Roll back" })).not.toBeDisabled());
    expect(screen.getByRole("button", { name: "Merge" })).not.toBeDisabled();
    // Exception still needs owner + expiry.
    expect(screen.getByRole("button", { name: "Grant exception" })).toBeDisabled();
  });

  it("opens a confirm dialog stating what rollback will do", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Drift leaf-01")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Reason (required)"), { target: { value: "restore baseline" } });
    fireEvent.change(screen.getByLabelText("ServiceNow reference (required)"), { target: { value: "RITM0001234" } });
    fireEvent.click(screen.getByRole("button", { name: "Roll back" }));
    await waitFor(() => expect(screen.getByText(/restores the stored baseline config/)).toBeInTheDocument());
    // The dialog's confirm button is the second "Roll back" button.
    const buttons = screen.getAllByRole("button", { name: "Roll back" });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(driftAction).toHaveBeenCalledWith(
      "1", "rollback", { reason: "restore baseline", ticket_ref: "RITM0001234" },
    ));
  });
});
