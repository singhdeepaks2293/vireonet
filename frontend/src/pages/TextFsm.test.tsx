import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import TextFsm from "./TextFsm";
import { AuthProvider } from "../lib/auth";

const m = vi.hoisted(() => ({ list: vi.fn(), text: vi.fn(), parse: vi.fn(), validate: vi.fn() }));
vi.mock("../api/textfsm", () => ({ textfsmApi: m }));

describe("TextFsm", () => {
  it("lists templates, parses output and tests a draft", async () => {
    m.list.mockResolvedValue({ total: 2, user_dir: "policies/textfsm", platform_map: {}, templates: [
      { template: "cisco_ios_show_vlan.textfsm", platform: "cisco_ios", command: "sh vlan", source: "ntc" },
      { template: "vireonet_cisco_show_logging.textfsm", platform: "cisco_ios", command: "sh log", source: "user" }] });
    m.text.mockResolvedValue({ text: "Value ID (\\d+)\n\nStart\n  ^${ID} -> Record\n" });
    m.parse.mockResolvedValue({ ok: true, template: "cisco_ios_show_vlan.textfsm", source: "ntc", platform: "cisco_ios", headers: ["VLAN_ID", "STATUS"], rows: [{ vlan_id: "300", status: "active" }] });
    m.validate.mockResolvedValue({ valid: true, errors: [], values: ["ID"] });
    localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "v", role: "viewer" }))}.s`);
    render(<QueryClientProvider client={new QueryClient()}><AuthProvider><MemoryRouter><TextFsm /></MemoryRouter></AuthProvider></QueryClientProvider>);
    await waitFor(() => expect(screen.getByText("cisco_ios_show_vlan.textfsm")).toBeInTheDocument());
    expect(screen.getByText("yours")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Device output"), { target: { value: "300 excel-demo active" } });
    fireEvent.click(screen.getByRole("button", { name: "Parse" }));
    await waitFor(() => expect(screen.getByText("1 rows", { exact: false })).toBeInTheDocument());
    expect(screen.getByRole("table", { name: "Parsed rows" })).toHaveTextContent("300");
    fireEvent.click(within(screen.getByRole("table", { name: "TextFSM templates" })).getByText("cisco_ios_show_vlan.textfsm"));
    await waitFor(() => expect((screen.getByLabelText(/Template \(click/) as HTMLTextAreaElement).value).toContain("Value ID"));
    fireEvent.click(screen.getByRole("button", { name: "Validate template" }));
    await waitFor(() => expect(screen.getByText(/Compiles/)).toBeInTheDocument());
  });
});
