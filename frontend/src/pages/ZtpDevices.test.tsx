import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ZtpDevices from "./ZtpDevices";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useZtpDevices: () => ({
      data: {
        items: [
          { serial: "FOC1234A", hostname: "leaf-01", model: "C9300-48P", platform: "ios-xe", site: "dc1", stage: "active", ip_address: "10.0.1.101", version: "17.9.1", callback_at: "2026-11-23T11:00:00Z", ticket_number: "ZTP-FOC1234A", netbox_status: "active", dhcp_reservation: "host leaf-01 { fixed-address 10.0.1.101; }" },
          { serial: "FOC1234B", hostname: "leaf-02", model: "C9300-48P", platform: "ios-xe", site: "dc1", stage: "identity_failed", ip_address: "10.0.1.102", version: null, callback_at: "2026-11-23T11:05:00Z", ticket_number: "ZTP-FOC1234B", netbox_status: "staged", dhcp_reservation: "host leaf-02 { fixed-address 10.0.1.102; }" },
        ], total: 2, page: 1, page_size: 20, pages: 1,
      },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    api: { retryZtp: vi.fn(), resetZtp: vi.fn() },
  };
});

function renderPage() {
  localStorage.setItem("vireonet-token", `header.${btoa(JSON.stringify({ username: "test", role: "operator" }))}.signature`);
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={["/ztp"]}><ZtpDevices /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("ZtpDevices", () => {
  it("renders serial, model, NetBox status, stage, callback and reservation", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("FOC1234A")).toBeInTheDocument());
    expect(screen.getAllByText("C9300-48P")).toHaveLength(2);
    expect(screen.getAllByText("active").length).toBeGreaterThan(0);
    expect(screen.getAllByText("identity_failed").length).toBeGreaterThan(0);
    expect(screen.getByText("host leaf-01 { fixed-address 10.0.1.101; }")).toBeInTheDocument();
    expect(screen.getByText("2 devices")).toBeInTheDocument();
  });

  it("shows retry and reset actions", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("FOC1234A")).toBeInTheDocument());
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Reset" })).toHaveLength(2);
  });
});
