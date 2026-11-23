import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../App";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useAdaptersHealth: () => ({ data: { adapters: [] }, isLoading: false, isError: false }),
  };
});


function tokenFor(role: string): string {
  return `header.${btoa(JSON.stringify({ username: "test", role }))}.signature`;
}

function renderApp() {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/changes"]}><App /></MemoryRouter></QueryClientProvider>);
}

describe("Shell", () => {
  it("redirects unauthenticated users to login", () => {
    localStorage.clear();
    renderApp();
    expect(screen.getByText(/Sign in with your username and password/)).toBeInTheDocument();
  });

  it("shows nav and navigates to each section after login", async () => {
    localStorage.setItem("vireonet-token", tokenFor("approver"));
    renderApp();
    expect(screen.getByRole("link", { name: "ZTP devices" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "System health" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Approvals" }));
    expect(screen.getByText(/Changes that passed CI and are waiting for approval/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Style reference" }));
    expect(screen.getByText("1. Palette — tokens only")).toBeInTheDocument();
  });

  it("has a skip link and theme toggle", async () => {
    localStorage.setItem("vireonet-token", tokenFor("viewer"));
    renderApp();
    expect(screen.getByText("Skip to main content")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Toggle theme" });
    expect(toggle).toHaveTextContent("Theme: light");
    await userEvent.click(toggle);
    expect(toggle).toHaveTextContent("Theme: dark");
  });
});
