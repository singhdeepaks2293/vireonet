import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LogSequencesTab, RiskTab, RootCauseTab, TicketTab } from "./NeuralTabs";

vi.mock("../lib/api", async (orig) => ({
  ...((await orig()) as Record<string, unknown>),
  request: vi.fn(async (path: string) => {
    if (path === "/ml/neural/log-sequences") return { model: "MLP next-event", top_k_accuracy: 0.79, devices: [
      { device: "spine-02", anomalous: true, rate: 0.36, scored: 14, baseline_rate: 0.11, reasons: ["new kind of event: PLATFORM-PS_FAIL"] },
      { device: "leaf-01", anomalous: false, rate: 0.07, scored: 15, baseline_rate: 0.1, reasons: [] }] };
    if (path === "/ml/neural/change-risk") return { model: "MLP", risk: 0.62, level: "high", auc: 0.71, base_rate: 0.21, trained_on: "3000 synthetic changes", importances: [{ feature: "prior_failure_rate" }, { feature: "devices" }] };
    if (path === "/ml/neural/root-cause") return { model: "SGC + MLP", auc: 0.98, trained_on: "1500 simulated failures", ranking: [{ device: "spine-01", probability: 0.48, alarm: 0 }] };
    return { model: "TF-IDF + MLP", accuracy: 1, change_type: "vlan_add", confidence: 0.97, devices: ["leaf-01", "leaf-03"], fields: { vlan_id: 300 } };
  }),
}));

describe("Neural tabs", () => {
  it("shows sequence anomalies with their reasons", async () => {
    render(<LogSequencesTab />);
    fireEvent.click(screen.getByRole("button", { name: "Train and score" }));
    expect(await screen.findByText("new kind of event: PLATFORM-PS_FAIL")).toBeInTheDocument();
    expect(screen.getByText("anomalous")).toBeInTheDocument();
  });
  it("scores change risk, ranks root cause, classifies a ticket", async () => {
    render(<><RiskTab /><RootCauseTab /><TicketTab /></>);
    fireEvent.click(screen.getByRole("button", { name: "Score risk" }));
    expect(await screen.findByText("62% (high)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Find root cause" }));
    expect(await screen.findByText("spine-01")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Classify" }));
    expect(await screen.findByText("leaf-01, leaf-03")).toBeInTheDocument();
  });
});
