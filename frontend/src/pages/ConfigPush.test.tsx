import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ConfigPush from "./ConfigPush";
import { AuthProvider } from "../lib/auth";

const m = vi.hoisted(() => ({ preview: vi.fn(), submit: vi.fn(), fromTicket: vi.fn(), ticket: vi.fn(), health: vi.fn(), stateEvent: vi.fn() }));
vi.mock("../api/configPush", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, pushApi: m, fileToBase64: async () => "QkFTRTY0" };
});

function renderPage(role = "operator") {
  localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "op", role }))}.s`);
  return render(<QueryClientProvider client={new QueryClient()}><AuthProvider><MemoryRouter><ConfigPush /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("ConfigPush", () => {
  it("previews an uploaded workbook and creates the change", async () => {
    m.health.mockResolvedValue({ backend: "jira", status: "ok" });
    m.preview.mockResolvedValue({ valid: true, errors: [], warnings: ["leaf-03: no rollback config in the workbook; rollback uses Git / the AWX backup"],
      devices: [{ device: "leaf-01", config_lines: 5, rollback_lines: 3, config_preview: "vlan 300" }] });
    m.submit.mockResolvedValue({ change: { id: "c1", ticket_number: "RITM9", state: "validated", devices: ["leaf-01"], change_type: "config_push" }, created: true, workflow_started: true, workbook_id: "w", warnings: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText("jira: ok")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Workbook (.xlsx)"), { target: { files: [new File(["x"], "push.xlsx")] } });
    await waitFor(() => expect(screen.getByText("Workbook is valid.")).toBeInTheDocument());
    expect(screen.getByText(/no rollback config/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Ticket number"), { target: { value: "RITM9" } });
    fireEvent.click(screen.getByRole("button", { name: "Create change from workbook" }));
    await waitFor(() => expect(screen.getByText(/the workflow has started/)).toBeInTheDocument());
    expect(m.submit).toHaveBeenCalledWith({ filename: "push.xlsx", content_b64: "QkFTRTY0", ticket_number: "RITM9", site: "dc1", emergency: false, automation: "manual", planned_start: "", planned_end: "" });
  });

  it("blocks submission when the workbook has errors", async () => {
    m.health.mockResolvedValue({ backend: "fake", status: "ok" });
    m.preview.mockResolvedValue({ valid: false, errors: ["leaf-01 line 2: 'reload' is not allowed in a config push"], warnings: [], devices: [] });
    renderPage();
    fireEvent.change(screen.getByLabelText("Workbook (.xlsx)"), { target: { files: [new File(["x"], "bad.xlsx")] } });
    await waitFor(() => expect(screen.getByText(/'reload' is not allowed/)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Ticket number"), { target: { value: "RITM9" } });
    expect(screen.getByRole("button", { name: "Create change from workbook" })).toBeDisabled();
  });

  it("looks up an ITSM ticket and creates a change from its attachment", async () => {
    m.health.mockResolvedValue({ backend: "servicenow", status: "ok" });
    m.ticket.mockResolvedValue({ provider: "servicenow", ticket: { key: "RITM0005001", summary: "Push VLAN 300", status: "open", requester: "alice" }, attachments: [{ id: "a", filename: "vlan300.xlsx", size: 6636 }] });
    m.fromTicket.mockResolvedValue({ change: { id: "c2", ticket_number: "RITM0005001", state: "validated", devices: ["leaf-01"], change_type: "config_push" }, created: true, workflow_started: true, workbook_id: "w", warnings: [], skipped_attachments: [] });
    renderPage();
    fireEvent.change(screen.getByLabelText("Ticket"), { target: { value: "RITM0005001" } });
    fireEvent.click(screen.getByRole("button", { name: "Look up" }));
    await waitFor(() => expect(screen.getByText("vlan300.xlsx")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Create change from ticket" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "RITM0005001" })).toBeInTheDocument());
  });

  it("fully automatic needs the approver role and a planned window", async () => {
    m.health.mockResolvedValue({ backend: "fake", status: "ok" });
    m.preview.mockResolvedValue({ valid: true, errors: [], warnings: [], devices: [] });
    renderPage("operator");
    expect(screen.getByRole("radio", { name: /Fully automatic/ })).toBeDisabled();
  });
});
