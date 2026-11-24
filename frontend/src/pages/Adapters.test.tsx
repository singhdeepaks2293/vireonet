import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Adapters from "./Adapters";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useAdapterSettings: () => ({
      data: [
        { name: "servicenow", enabled: true, mode: "real", endpoint: "https://servicenow.example.com", timeout_seconds: 30, retry_max_attempts: 3, rate_limit_per_second: 10, capabilities: ["read_ticket", "add_work_note"] },
        { name: "netbox", enabled: true, mode: "fake", endpoint: "", timeout_seconds: 10, retry_max_attempts: 3, rate_limit_per_second: null, capabilities: ["get_device"] },
      ],
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
  };
});

function renderPage() {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={["/adapters"]}><Adapters /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("Adapters", () => {
  it("shows adapters read-only with mode, endpoint and capabilities, no secrets", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("servicenow")).toBeInTheDocument());
    expect(screen.getByText("real")).toBeInTheDocument();
    expect(screen.getByText("fake")).toBeInTheDocument();
    expect(screen.getByText("https://servicenow.example.com")).toBeInTheDocument();
    expect(screen.getByText("read_ticket, add_work_note")).toBeInTheDocument();
    expect(screen.getByText(/2 enabled of 2\./)).toBeInTheDocument();
    expect(screen.getByText(/Every adapter: core systems, ITSM tools/)).toBeInTheDocument();
    expect(screen.getByText(/No secrets are shown/)).toBeInTheDocument();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByText(/minioadmin|secret_key/i)).toBeNull();
  });
});
