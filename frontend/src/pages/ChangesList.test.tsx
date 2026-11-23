import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ChangesList from "./ChangesList";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useChanges: () => ({
      data: {
        items: [
          { id: "1", ticket_number: "RITM0001234", change_type: "vlan_add", state: "approved", requester: "alice", devices: ["leaf-01"], created_at: "2026-11-23T11:00:00Z" },
          { id: "2", ticket_number: "RITM0001235", change_type: "static_route_add", state: "pre_check_failed", requester: "bob", devices: ["leaf-02"], created_at: "2026-11-23T11:04:00Z" },
        ], total: 2, page: 1, page_size: 20, pages: 1,
      },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    api: { listChanges: vi.fn() },
  };
});

function LocationSpy() {
  const location = useLocation();
  return <span data-testid="location-search">{location.search}</span>;
}

function renderList() {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/changes"]}><LocationSpy /><ChangesList /></MemoryRouter></QueryClientProvider>);
}

describe("ChangesList", () => {
  it("renders rows with ticket, type, devices and state", async () => {
    renderList();
    expect(screen.getByText("RITM0001234")).toBeInTheDocument();
    expect(screen.getAllByText("vlan_add").length).toBeGreaterThan(0);
    expect(screen.getByText("leaf-01")).toBeInTheDocument();
    expect(screen.getAllByText("approved").length).toBeGreaterThan(0);
    expect(screen.getByText("2 changes")).toBeInTheDocument();
  });

  it("updates the URL when the state filter changes", async () => {
    renderList();
    expect(screen.getByText("RITM0001234")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("State"), { target: { value: "approved" } });
    expect(screen.getByTestId("location-search").textContent).toContain("state=approved");
  });

  it("shows density toggle", async () => {
    renderList();
    expect(screen.getByText("RITM0001234")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Density:/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
  });
});
