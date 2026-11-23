import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import GitSetup from "./GitSetup";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    api: { giteaManualSteps: vi.fn(), giteaInstall: vi.fn(), giteaInstallStatus: vi.fn() },
  };
});

function tokenFor(role: string): string {
  const payload = btoa(JSON.stringify({ username: "alice", role }));
  return `header.${payload}.signature`;
}

function renderPage() {
  localStorage.setItem("vireonet-token", tokenFor("admin"));
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={["/setup/gitea"]}><GitSetup /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("GitSetup", () => {
  it("offers manual and automatic, and shows manual steps", async () => {
    renderPage();
    expect(screen.getByText("Manual install")).toBeInTheDocument();
    expect(screen.getByText("Automatic install")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show manual steps" }));
    await waitFor(() => expect(screen.getByText("Back")).toBeInTheDocument());
  });

  it("shows automatic form", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Install automatically" }));
    await waitFor(() => expect(screen.getByLabelText("Admin username")).toBeInTheDocument());
    expect(screen.getByLabelText("Repo (owner/repo)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install Gitea automatically" })).toBeInTheDocument();
  });
});
