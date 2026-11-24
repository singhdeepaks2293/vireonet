import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AdapterConfig } from "./AdapterConfig";

const calls: { path: string; init?: RequestInit }[] = [];
vi.mock("../lib/api", async (orig) => ({
  ...((await orig()) as Record<string, unknown>),
  request: vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path === "/adapters/config" && !init) {
      return { note: "Saved values override the environment when the API and the worker next start.", groups: [
        { id: "netbox", label: "NetBox", fields: [
          { name: "netbox_base_url", env: "VIREONET_NETBOX_BASE_URL", kind: "text", options: [], secret: false, value: "https://nb", is_set: null, source: "env", default: "" },
          { name: "netbox_token", env: "VIREONET_NETBOX_TOKEN", kind: "secret", options: [], secret: true, value: null, is_set: true, source: "ui", default: null },
        ] },
      ] };
    }
    if (path === "/adapters/config/test") return { ok: true, detail: { status: "ok" } };
    return { message: "Saved. Restart the API and the worker to apply.", saved: ["netbox_base_url"] };
  }),
}));

describe("AdapterConfig", () => {
  it("never shows secrets, tests and saves edits", async () => {
    render(<QueryClientProvider client={new QueryClient()}><AdapterConfig /></QueryClientProvider>);
    const token = (await screen.findByLabelText("netbox_token")) as HTMLInputElement;
    expect(token.type).toBe("password");
    expect(token.value).toBe("");
    expect(token.placeholder).toMatch(/set \(leave empty to keep\)/);
    fireEvent.change(screen.getByLabelText("netbox_base_url"), { target: { value: "https://netbox.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connection OK/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText(/Restart the API and the worker/)).toBeInTheDocument());
    const put = calls.find((c) => c.init?.method === "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ values: { netbox_base_url: "https://netbox.example" }, clear: [] });
  });
});
