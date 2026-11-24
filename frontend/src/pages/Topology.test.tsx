import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Topology from "./Topology";
import { AuthProvider } from "../lib/auth";

vi.mock("../lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    request: vi.fn(async () => ({
      site: null, telemetry: "zabbix", errors: ["telemetry (zabbix): timeout on fw-02"],
      nodes: [{ id: "a", name: "a", platform: "eos", role: "spine", site: "dc1" }, { id: "b", name: "b", platform: "nxos", role: "leaf", site: "dc1" }],
      links: [{ id: 0, source: "a", target: "b", source_interface: "Ethernet1", target_interface: "Ethernet1/49", a_to_b_bps: 8.5e9, b_to_a_bps: 1.2e9, speed_bps: 10e9, utilization: 0.85 }],
    })),
  };
});

describe("Topology", () => {
  it("lists links with in/out traffic and shows telemetry errors", async () => {
    localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "v", role: "viewer" }))}.s`);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider><MemoryRouter><Topology /></MemoryRouter></AuthProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("8.5 Gbps")).toBeInTheDocument());
    expect(screen.getByText("1.2 Gbps")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();
    expect(screen.getByText("zabbix")).toBeInTheDocument();
    expect(screen.getByText(/timeout on fw-02/)).toBeInTheDocument();
    expect(screen.getByLabelText("Topology map")).toBeInTheDocument();
  });
});
