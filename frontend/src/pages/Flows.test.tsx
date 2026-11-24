import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import catalog from "../test-fixtures/flowCatalog.json";
import osUpgrade from "../test-fixtures/osUpgradeFlow.json";
import Flows from "./Flows";
import FlowEditor from "./FlowEditor";
import FlowRun from "./FlowRun";
import { AuthProvider } from "../lib/auth";
import { flowsApi } from "../api/flows";

const m = vi.hoisted(() => ({
  catalog: vi.fn(), list: vi.fn(), get: vi.fn(), create: vi.fn(), update: vi.fn(), validate: vi.fn(), testTrigger: vi.fn(),
  simulate: vi.fn(), example: vi.fn(), runs: vi.fn(), getRun: vi.fn(), decide: vi.fn(), cancel: vi.fn(), publish: vi.fn(),
  enable: vi.fn(), run: vi.fn(), versions: vi.fn(), exportFlow: vi.fn(), importFlow: vi.fn(), remove: vi.fn(), rotateToken: vi.fn(),
}));

function page(path: string, role = "operator") {
  localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "op", role }))}.s`);
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/flows" element={<Flows />} />
            <Route path="/flows/new" element={<FlowEditor />} />
            <Route path="/flows/runs/:runId" element={<FlowRun />} />
            <Route path="/flows/:id" element={<FlowEditor />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  // The hooks call flowsApi.<method>, so stub the methods on the real object.
  Object.assign(flowsApi, m);
  m.catalog.mockResolvedValue(catalog);
  m.runs.mockResolvedValue({ items: [] });
  m.versions.mockResolvedValue({ items: [] });
});

describe("Flows list", () => {
  it("lists flows, shows templates and recent runs", async () => {
    m.list.mockResolvedValue({ items: [{ id: "f1", name: "OS upgrade", description: "d", enabled: true, trigger_type: "itsm", latest_version: 3, published_version: 2, trigger: { type: "itsm" }, steps: 9, runs: 4, last_run_at: null, updated_at: null }] });
    m.runs.mockResolvedValue({ items: [{ id: "r1234567890", flow_id: "f1", flow_name: "OS upgrade", version: 2, status: "succeeded", trigger_type: "itsm", started_by: "servicenow-webhook", created_at: "2026-11-24T01:00:00Z", finished_at: null, parent_run_id: null, error: "", waiting: {}, steps_logged: 3 }] });
    page("/flows");
    const table = await screen.findByRole("table", { name: "Flows" });
    expect(within(table).getByText("OS upgrade")).toBeInTheDocument();
    expect(within(table).getByText("v3 (live v2)")).toBeInTheDocument();
    expect(within(table).getByText("ITSM ticket event")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Use template" })).toHaveLength(catalog.examples.length);
    expect(await screen.findByText("r1234567")).toBeInTheDocument();
  });
});

describe("Flow editor", () => {
  it("builds a flow from the palette with a trigger condition and saves it", async () => {
    m.create.mockResolvedValue({ id: "new1", webhook_token: "tok-123", version: 1 });
    page("/flows/new");
    await screen.findByRole("region", { name: "Trigger" });
    fireEvent.change(screen.getByLabelText("Flow name"), { target: { value: "Link down triage" } });
    fireEvent.change(screen.getByLabelText("Trigger type"), { target: { value: "zabbix" } });
    fireEvent.click(screen.getAllByRole("button", { name: "+ Condition" })[0]);
    fireEvent.change(screen.getByLabelText("Condition 0.1 field"), { target: { value: "trigger" } });
    fireEvent.change(screen.getByLabelText("Condition 0.1 operator"), { target: { value: "matches" } });
    fireEvent.change(screen.getByLabelText("Condition 0.1 value"), { target: { value: "Link down on (?P<interface>\\S+)" } });
    // Add an If, then a log action inside its Then branch.
    fireEvent.click(screen.getByRole("button", { name: "+ Add action" }));
    fireEvent.change(screen.getByLabelText("Search actions"), { target: { value: "if" } });
    fireEvent.click(within(screen.getByRole("dialog", { name: "Add an action" })).getByRole("button", { name: /If \/ Else if/ }));
    const ifCard = screen.getByRole("article", { name: /If \/ Else if \/ Otherwise if1/ });
    fireEvent.click(within(ifCard).getAllByRole("button", { name: "+ Add action" })[0]);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Add an action" })).getByRole("button", { name: /Log message/ }));
    fireEvent.change(screen.getByLabelText(/^Message/), { target: { value: "down: {{ trigger.match.interface }}" } });
    // Dynamic content lists the regex capture.
    expect(screen.getByRole("button", { name: "trigger.match.interface" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save*" }));
    await waitFor(() => expect(m.create).toHaveBeenCalled());
    const body = m.create.mock.calls[0][0];
    expect(body.name).toBe("Link down triage");
    expect(body.trigger).toEqual({ type: "zabbix", conditions: { all: [{ field: "trigger", op: "matches", value: "Link down on (?P<interface>\\S+)" }] } });
    expect(body.definition.steps[0].type).toBe("if");
    expect(body.definition.steps[0].branches[0].steps[0]).toMatchObject({ type: "log", message: "down: {{ trigger.match.interface }}" });
  });

  it("opens an existing flow, shows validation problems on the step, tests the trigger", async () => {
    m.get.mockResolvedValue({ id: "f1", name: osUpgrade.name, description: "", enabled: false, trigger_type: "itsm", latest_version: 2, published_version: 1, version: 2, trigger: osUpgrade.trigger, definition: osUpgrade.definition, comment: "", webhook_url: "/webhooks/flows/f1" });
    m.validate.mockResolvedValue({ valid: false, errors: ["steps[4] (device:get_device): Device name is required"], warnings: [] });
    m.testTrigger.mockResolvedValue({ valid: true, errors: [], matched: true, captures: { device: "leaf-01", version: "17.9.4" }, dedup_key: "CHG0030005:Implement" });
    page("/flows/f1", "approver");
    expect(await screen.findByDisplayValue(osUpgrade.name)).toBeInTheDocument();
    expect(screen.getByText(/the live trigger runs v1/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish v2" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    const card = await screen.findByRole("article", { name: "Get device (NetBox) device" });
    expect(await within(card).findByText("Device name is required")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Test with sample data"));
    fireEvent.click(screen.getByRole("button", { name: "Test trigger" }));
    expect(await screen.findByText("Matches: the flow would start.")).toBeInTheDocument();
    expect(screen.getByText("CHG0030005:Implement")).toBeInTheDocument();
  });

  it("test-runs a flow in simulation", async () => {
    m.get.mockResolvedValue({ id: "f1", name: "x", description: "", enabled: false, trigger_type: "manual", latest_version: 1, published_version: null, version: 1, trigger: { type: "manual" }, definition: { inputs: [], variables: {}, steps: [{ id: "s1", type: "log", message: "hi" }], outputs: {} }, comment: "", webhook_url: "" });
    m.testTrigger.mockResolvedValue({ valid: true, errors: [], matched: true, captures: {} });
    m.simulate.mockResolvedValue({ valid: true, errors: [], result: { status: "succeeded", error: "", message: "", vars: {}, outputs: {} }, log: [{ id: "s1", type: "log", name: "Log message", path: "steps[0]", status: "succeeded", output: { message: "hi" } }] });
    page("/flows/f1");
    fireEvent.click(await screen.findByRole("tab", { name: "Test run" }));
    fireEvent.click(screen.getByRole("button", { name: "Test run" }));
    const log = await screen.findByRole("list", { name: "Step log" });
    expect(within(log).getByText("Log message")).toBeInTheDocument();
    expect(m.simulate).toHaveBeenCalled();
  });
});

describe("Flow editor: drag and drop, YAML", () => {
  const dt = () => ({ setData: () => {}, getData: () => "", effectAllowed: "", dropEffect: "" });

  it("drags an action from the side palette into the flow and moves cards", async () => {
    page("/flows/new");
    await screen.findByRole("region", { name: "Trigger" });
    const palette = screen.getByRole("complementary", { name: "Actions" });
    fireEvent.dragStart(within(palette).getByRole("button", { name: "Add Log message" }), { dataTransfer: dt() });
    fireEvent.drop(screen.getByRole("button", { name: "Drop here: position 1 at the top level" }), { dataTransfer: dt() });
    fireEvent.click(within(palette).getByRole("button", { name: "Add Repeat with each" }));
    expect(screen.getByRole("article", { name: "Log message log1" })).toBeInTheDocument();
    // Drag the log card into the loop's Do block.
    fireEvent.dragStart(screen.getByRole("button", { name: "Drag log1" }), { dataTransfer: dt() });
    fireEvent.drop(screen.getByRole("button", { name: "Drop here: position 1 in steps" }), { dataTransfer: dt() });
    const loop = screen.getByRole("article", { name: /Repeat with each foreach1/ });
    expect(within(loop).getByRole("article", { name: "Log message log1" })).toBeInTheDocument();
  });

  it("toggles to YAML and back, applying edits and refusing bad YAML", async () => {
    page("/flows/new");
    await screen.findByRole("region", { name: "Trigger" });
    fireEvent.click(screen.getByRole("button", { name: "YAML" }));
    const box = screen.getByLabelText("Flow YAML") as HTMLTextAreaElement;
    expect(box.value).toContain("trigger:");
    m.validate.mockResolvedValue({ valid: true, errors: [], warnings: [] });
    fireEvent.change(box, { target: { value: "trigger: {type: manual}\ndefinition:\n  steps:\n    - {id: hello, type: log, message: hi from yaml}\n" } });
    fireEvent.click(screen.getByRole("button", { name: "Visual (drag and drop)" }));
    expect(await screen.findByRole("article", { name: "Log message hello" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "YAML" }));
    fireEvent.change(screen.getByLabelText("Flow YAML"), { target: { value: "definition: [unclosed" } });
    fireEvent.click(screen.getByRole("button", { name: "Visual (drag and drop)" }));
    expect(await screen.findByText(/YAML not applied/)).toBeInTheDocument();
    expect(screen.getByLabelText("Flow YAML")).toBeInTheDocument();
  });
});

describe("Flow run", () => {
  it("shows the step log and approves a waiting step", async () => {
    m.getRun.mockResolvedValue({ id: "run-1", flow_id: "f1", flow_name: "OS upgrade", version: 2, parent_run_id: null, trigger_type: "itsm", status: "waiting", error: "", started_by: "servicenow-webhook",
      waiting: { approve: { step_id: "approve", message: "Upgrade leaf-01?", role: "approver", since: "2026-11-24T01:00:00Z" } },
      created_at: "2026-11-24T01:00:00Z", finished_at: null, steps_logged: 2,
      log: [{ id: "device", type: "get_device", name: "Get device (NetBox)", path: "steps[4]", status: "succeeded", output: { platform: "ios-xe" } }], variables: {}, outputs: {}, inputs: {}, trigger_event: {} });
    m.decide.mockResolvedValue({});
    page("/flows/runs/run-1", "approver");
    expect(await screen.findByText("Upgrade leaf-01?")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Comment"), { target: { value: "go" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(m.decide).toHaveBeenCalledWith("run-1", "approve", true, "go"));
    expect(screen.getByText("Get device (NetBox)")).toBeInTheDocument();
  });

  it("hides approval buttons from roles below the step's role", async () => {
    m.getRun.mockResolvedValue({ id: "run-2", flow_id: "f1", flow_name: "x", version: 1, parent_run_id: null, trigger_type: "manual", status: "waiting", error: "", started_by: "a", waiting: { a: { step_id: "a", message: "ok?", role: "approver", since: "2026-11-24T01:00:00Z" } }, created_at: "", finished_at: null, steps_logged: 0, log: [] });
    page("/flows/runs/run-2", "operator");
    expect(await screen.findByText("Your role cannot answer this approval.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});
