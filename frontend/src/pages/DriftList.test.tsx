import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import DriftList, { driftToCsv } from "./DriftList";

const ROWS = [
  {
    id: "1", device: "leaf-01", kind: "unmanaged", severity: "high", status: "detected",
    first_seen: "2026-11-23T10:00:00Z", last_seen: "2026-11-23T11:00:00Z",
    baseline_ref: null, baseline_hash: "aaa", running_hash: "bbb", diff: "diff",
    attributed_actor: "alice", attributed_at: null, linked_ticket: null,
  },
  {
    id: "2", device: "leaf-02", kind: "state", severity: "low", status: "resolved",
    first_seen: "2026-11-22T10:00:00Z", last_seen: "2026-11-22T11:00:00Z",
    baseline_ref: null, baseline_hash: "aaa", running_hash: "aaa", diff: "",
    attributed_actor: null, attributed_at: null, linked_ticket: null,
  },
];

vi.mock("../api/drift", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useDriftList: () => ({
      data: { items: ROWS, total: 2, limit: 50, offset: 0 },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    driftApi: {
      listDrift: vi.fn(async () => ({ items: ROWS, total: 2, limit: 50, offset: 0 })),
      startScan: vi.fn(async () => ({ workflow_id: "drift-scan-dc1-2026-11-23" })),
    },
  };
});

function renderPage() {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/drift"]}><DriftList /></MemoryRouter></QueryClientProvider>);
}

describe("DriftList", () => {
  it("renders rows with severity badge, owner and age", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("leaf-01")).toBeInTheDocument());
    expect(screen.getByText("leaf-02")).toBeInTheDocument();
    expect(screen.getByText("high", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("low", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("2 records")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start scan" })).toBeInTheDocument();
  });

  it("syncs the severity filter to the URL", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("leaf-01")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Severity"), { target: { value: "critical" } });
    await waitFor(() => expect(screen.getByLabelText("Severity")).toHaveValue("critical"));
  });

  it("builds a CSV with one row per record", () => {
    const csv = driftToCsv(ROWS);
    const lines = csv.trim().split("\n");
    expect(lines[0]).toBe("id,device,kind,severity,status,first_seen,last_seen,attributed_actor,linked_ticket");
    expect(lines).toHaveLength(3);
    expect(lines[1]).toContain("leaf-01");
    expect(lines[2]).toContain("leaf-02");
  });
});
