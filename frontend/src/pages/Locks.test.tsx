import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Locks from "./Locks";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useLocks: () => ({
      data: [
        { device: "leaf-01", ticket_number: "RITM0001234", holder: "vireonet-worker", acquired_at: "2026-11-23T11:00:00Z", expires_at: "2026-11-23T11:30:00Z" },
      ],
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    useChangeWindows: () => ({
      data: [{ days: ["Mon", "Tue", "Wed", "Thu", "Fri"], start: "01:00", end: "05:00", timezone: "UTC", emergency_bypasses_window: true, currently_in_window: false, next_window_start: "2026-11-24T01:00:00Z" }],
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    api: { releaseLock: vi.fn() },
  };
});

function renderPage() {
  localStorage.setItem("vireonet-token", `header.${btoa(JSON.stringify({ username: "test", role: "operator" }))}.signature`);
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={["/locks"]}><Locks /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("Locks", () => {
  it("shows locks and change windows", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("leaf-01")).toBeInTheDocument());
    expect(screen.getByText("RITM0001234")).toBeInTheDocument();
    expect(screen.getByText("vireonet-worker")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument();
    expect(screen.getByText("Mon, Tue, Wed, Thu, Fri")).toBeInTheDocument();
    expect(screen.getByText("01:00")).toBeInTheDocument();
    expect(screen.getByText("05:00")).toBeInTheDocument();
    expect(screen.getByText("1 lock held. Refreshed every 30 seconds.")).toBeInTheDocument();
  });
});
