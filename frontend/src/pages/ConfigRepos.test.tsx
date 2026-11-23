import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ConfigRepos from "./ConfigRepos";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig() as Record<string, unknown>;
  return {
    ...actual,
    useConfigRepos: () => ({
      data: [
        { hostname: "leaf-01", repo: "netops/config-leaf-01" },
        { hostname: "leaf-02", repo: "netops/config-leaf-02" },
      ],
      isLoading: false, isError: false, error: null, refetch: vi.fn(),
    }),
  };
});

describe("ConfigRepos", () => {
  it("shows per-device repos and the separation note", async () => {
    const qc = new QueryClient();
    render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/config"]}><ConfigRepos /></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.getByText("leaf-01")).toBeInTheDocument());
    expect(screen.getByText("netops/config-leaf-01")).toBeInTheDocument();
    expect(screen.getByText("netops/config-leaf-02")).toBeInTheDocument();
    expect(screen.getByText(/Three stores, kept separate/)).toBeInTheDocument();
    expect(screen.getByText(/NetBox Jinja lives in NetBox only/)).toBeInTheDocument();
  });
});
