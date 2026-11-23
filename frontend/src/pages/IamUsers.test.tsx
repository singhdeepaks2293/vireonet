import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import IamUsers from "./IamUsers";
import { AuthProvider } from "../lib/auth";
import { setAccessToken } from "../lib/token-store";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useIamUsers: () => ({
      data: [
        { id: "1", username: "alice", display_name: "Alice", role: "admin", is_active: true, auth_provider: "local", created_at: "2026-11-23T11:00:00Z" },
        { id: "2", username: "bob", display_name: "Bob", role: "viewer", is_active: true, auth_provider: "local", created_at: "2026-11-23T11:00:00Z" },
      ],
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    useIamRoles: () => ({
      data: { roles: { viewer: ["changes:read"], admin: ["changes:read", "iam:manage"] }, permissions: ["changes:read", "iam:manage"] },
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
    api: { createIamUser: vi.fn(), updateIamUser: vi.fn() },
  };
});

function tokenFor(role: string): string {
  const payload = btoa(JSON.stringify({ username: "alice", role }));
  return `header.${payload}.signature`;
}

function renderPage() {
  setAccessToken(tokenFor("admin"));
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={["/iam"]}><IamUsers /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("IamUsers", () => {
  it("lists users with role selectors and activate actions", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice (you)")).toBeInTheDocument());
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByLabelText("Role for bob")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Deactivate" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Create user" })).toBeInTheDocument();
  });

  it("shows roles matrix", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice (you)")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Roles" }));
    await waitFor(() => expect(screen.getByText("iam:manage")).toBeInTheDocument());
  });
});
