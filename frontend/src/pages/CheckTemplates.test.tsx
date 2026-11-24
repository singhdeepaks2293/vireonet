import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import CheckTemplates from "./CheckTemplates";
import { AuthProvider } from "../lib/auth";

const mocks = vi.hoisted(() => ({
  validate: vi.fn(),
  preview: vi.fn(),
}));

vi.mock("../api/checks", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    useCheckTemplates: () => ({
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
      data: {
        valid: true,
        errors: [],
        yaml: "version: 1\n",
        templates: {
          source: "policies/checks.yaml",
          allowed_command_prefixes: ["show ", "display "],
          change_types: {
            vlan_add: {
              pre: [{ kind: "builtin", name: "reachability", on_fail: "block" }],
              post: [
                {
                  kind: "command",
                  name: "vlan_active",
                  description: "The VLAN is active",
                  on_fail: "block",
                  command: { ios: "show vlan id {{ intent.vlan_id }}", junos: "show vlans {{ intent.vlan_name }}" },
                  expect: { default: { regex: "{{ intent.vlan_id }}" } },
                },
              ],
            },
          },
        },
      },
    }),
    useSampleIntents: () => ({ data: { vlan_add: { change_type: "vlan_add", vlan_id: 120 } } }),
    checksApi: { validate: mocks.validate, preview: mocks.preview },
  };
});

function renderPage(role = "operator") {
  localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "op", role }))}.s`);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <MemoryRouter><CheckTemplates /></MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("CheckTemplates", () => {
  it("lists pre and post checks with per-platform commands", () => {
    renderPage("viewer");
    expect(screen.getByText("reachability")).toBeInTheDocument();
    expect(screen.getByText("vlan_active")).toBeInTheDocument();
    expect(screen.getByText("show vlans {{ intent.vlan_name }}")).toBeInTheDocument();
    expect(screen.getByText("The VLAN is active")).toBeInTheDocument();
  });

  it("validates a draft and shows errors", async () => {
    mocks.validate.mockResolvedValue({ valid: false, errors: ["defaults.pre: unknown built-in 'nope'"], templates: null });
    renderPage("operator");
    fireEvent.click(screen.getByRole("tab", { name: "Edit and preview" }));
    fireEvent.change(screen.getByLabelText(/Template YAML/), { target: { value: "version: 1\ndefaults: {pre: [nope]}" } });
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => expect(screen.getByText("defaults.pre: unknown built-in 'nope'")).toBeInTheDocument());
    expect(mocks.validate).toHaveBeenCalledWith("version: 1\ndefaults: {pre: [nope]}");
  });

  it("previews rendered commands for a platform", async () => {
    mocks.preview.mockResolvedValue({
      valid: true, errors: [], platform: "junos",
      preview: { pre: [], post: [{ name: "vlan_active", kind: "command", on_fail: "block", command: "show vlans app-tier", expect: { regex: "120" } }] },
    });
    renderPage("viewer");
    fireEvent.click(screen.getByRole("tab", { name: "Edit and preview" }));
    fireEvent.change(screen.getByLabelText("Platform"), { target: { value: "junos" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview checks" }));
    await waitFor(() => expect(screen.getByText("show vlans app-tier")).toBeInTheDocument());
    expect(mocks.preview.mock.calls[0][0].platform).toBe("junos");
    // Viewers can preview but not validate.
    expect(screen.queryByRole("button", { name: "Validate" })).not.toBeInTheDocument();
  });
});
