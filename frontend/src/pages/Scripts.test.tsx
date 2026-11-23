import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Scripts from "./Scripts";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useScripts: () => ({
      data: [
        {
          name: "add_vlan_nornir",
          sha: "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
          allowed_change_types: ["vlan_add"],
          allowed_platforms: ["iosxe", "nxos"],
          writes: ["vlan"],
          last_run: "2026-11-23T11:00:00Z",
        },
        {
          name: "set_interface_description",
          sha: "9f2c1a4b7d8e3f0a1b2c3d4e5f6a7b8c9d0e1f2a",
          allowed_change_types: ["interface_set"],
          allowed_platforms: ["ios", "iosxe", "nxos"],
          writes: ["interface.description", "interface.enabled"],
          last_run: null,
        },
      ],
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
  };
});

function renderPage(role: string) {
  localStorage.setItem("vireonet-token", `header.${btoa(JSON.stringify({ username: "test", role }))}.signature`);
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/scripts"]}><Scripts /></MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("Scripts", () => {
  it("shows the read-only manifest table for admins, with short SHAs", async () => {
    renderPage("admin");
    await waitFor(() => expect(screen.getByText("add_vlan_nornir")).toBeInTheDocument());
    expect(screen.getByText("1a2b3c4d5e6f")).toBeInTheDocument();
    expect(screen.getByText("set_interface_description")).toBeInTheDocument();
    expect(screen.getByText("vlan_add")).toBeInTheDocument();
    expect(screen.getByText("iosxe, nxos")).toBeInTheDocument();
    expect(screen.getByText("never")).toBeInTheDocument();
    expect(screen.getByText(/edited by merge request/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
  });

  it("refuses non-admins", async () => {
    renderPage("viewer");
    await waitFor(() => expect(screen.getByText(/Admin role is required/)).toBeInTheDocument());
    expect(screen.queryByText("add_vlan_nornir")).not.toBeInTheDocument();
  });
});
