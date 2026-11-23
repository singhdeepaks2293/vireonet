import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import SystemHealth from "./SystemHealth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useSystemHealth: () => ({
      data: { status: "degraded", components: [
        { name: "api", status: "ok", detail: "FastAPI responding" },
        { name: "database", status: "ok", detail: "SELECT 1 succeeded" },
        { name: "temporal", status: "not_configured", detail: "temporal client not configured" },
      ]},
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    useAdaptersHealth: () => ({
      data: { adapters: [
        { name: "servicenow", status: "ok", latency_ms: 120, last_success: "2026-11-23T11:00:00Z", capabilities: ["read_ticket", "add_work_note"], detail: "ok" },
        { name: "awx", status: "down", latency_ms: null, last_success: null, capabilities: ["launch_job"], detail: "connection refused" },
      ]},
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
  };
});

function renderPage() {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/health"]}><SystemHealth /></MemoryRouter></QueryClientProvider>);
}

describe("SystemHealth", () => {
  it("shows components and adapter table with last success and capabilities", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("servicenow")).toBeInTheDocument());
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText("database")).toBeInTheDocument();
    expect(screen.getByText("temporal")).toBeInTheDocument();
    expect(screen.getByText("120 ms")).toBeInTheDocument();
    expect(screen.getByText("read_ticket, add_work_note")).toBeInTheDocument();
    expect(screen.getByText("never")).toBeInTheDocument();
    expect(screen.getByText("connection refused")).toBeInTheDocument();
    expect(screen.getByText("1 adapter down. Refreshed every 30 seconds.")).toBeInTheDocument();
  });
});
