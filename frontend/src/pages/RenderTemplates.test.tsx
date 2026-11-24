import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import RenderTemplates from "./RenderTemplates";
import { AuthProvider } from "../lib/auth";

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  templates: {
    isLoading: false, isError: false,
    data: { valid: true, errors: [], platforms: ["ios", "fortios"], aliases: {}, yaml: "version: 1\n", source: "policies/render_templates.yaml" },
  },
  samples: { data: { static_route_add: { change_type: "static_route_add", prefix: "10.9.0.0/24", next_hop: "10.0.0.1" } } },
}));

vi.mock("../api/checks", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    // Stable objects, like React Query returns between renders.
    useRenderTemplates: () => mocks.templates,
    useSampleIntents: () => mocks.samples,
    renderApi: { preview: mocks.preview },
  };
});

function renderPage() {
  localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "v", role: "viewer" }))}.s`);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider><MemoryRouter><RenderTemplates /></MemoryRouter></AuthProvider>
    </QueryClientProvider>,
  );
}

describe("RenderTemplates", () => {
  it("renders config for the chosen vendor", async () => {
    mocks.preview.mockResolvedValue({
      valid: true, errors: [],
      rendered: { device: "edge-01", platform: "fortios", format: "cli", source: "template", lines: ["config router static", "    edit 0"] },
    });
    renderPage();
    fireEvent.change(screen.getByLabelText("Change type"), { target: { value: "static_route_add" } });
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "fortinet" } });
    fireEvent.click(screen.getByRole("button", { name: "Render" }));
    await waitFor(() => expect(screen.getByLabelText("Rendered config")).toHaveTextContent("config router static"));
    expect(mocks.preview.mock.calls[0][0].platform).toBe("fortinet");
    expect(screen.getByText(/from template/)).toBeInTheDocument();
  });

  it("shows why a vendor cannot render a change", async () => {
    mocks.preview.mockResolvedValue({ valid: true, errors: ["edge-01: no render template for vlan_add on 'sros'"], rendered: null });
    renderPage();
    fireEvent.change(screen.getByLabelText("Change type"), { target: { value: "static_route_add" } });
    fireEvent.click(screen.getByRole("button", { name: "Render" }));
    await waitFor(() => expect(screen.getByText(/no render template for vlan_add/)).toBeInTheDocument());
  });
});
