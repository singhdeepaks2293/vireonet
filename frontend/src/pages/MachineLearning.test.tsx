import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import MachineLearning from "./MachineLearning";
import { AuthProvider } from "../lib/auth";

const m = vi.hoisted(() => ({
  anomalies: vi.fn(), clusters: vi.fn(), classification: vi.fn(), regression: vi.fn(), reinforcement: vi.fn(), risk: vi.fn(), logs: vi.fn(),
}));
vi.mock("../api/ml", () => ({ mlApi: m }));

function renderPage() {
  localStorage.setItem("vireonet-token", `h.${btoa(JSON.stringify({ username: "v", role: "viewer" }))}.s`);
  return render(<QueryClientProvider client={new QueryClient()}><AuthProvider><MemoryRouter><MachineLearning /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("MachineLearning", () => {
  it("shows anomalous interfaces with a chart", async () => {
    m.anomalies.mockResolvedValue({
      algorithm: "isolation_forest", interfaces: 26, anomalous_interfaces: 1, source: "zabbix", notes: [],
      results: [{ device: "fw-01", interface: "port2", points: 3, anomalies: [{ timestamp: "t2", in_util: 0.95, out_util: 0.4, score: 0.7 }],
        series: { timestamps: ["t1", "t2", "t3"], in_util: [0.2, 0.95, 0.2], out_util: [0.1, 0.4, 0.1] } }],
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Detect anomalies" }));
    await waitFor(() => expect(screen.getByText("1 of 26 interfaces have anomalous traffic.")).toBeInTheDocument());
    expect(screen.getByRole("img", { name: "Traffic for fw-01 port2" })).toBeInTheDocument();
    expect(m.anomalies).toHaveBeenCalledWith({ algorithm: "isolation_forest", hours: 24 });
  });

  it("flags synthetic training data and lists every regressor", async () => {
    m.regression.mockResolvedValue({
      task: "change_duration_minutes", source: "synthetic", notes: ["3 finished changes in the database; need 40"], rows: 600, best: "ridge",
      models: ["ridge", "svr"].map((a, i) => ({ algorithm: a, rmse: 6 + i, mae: 5, r2: 0.8, top_features: [], sample: [{ actual: 10, predicted: 11 }] })),
    });
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Regression" }));
    fireEvent.click(screen.getByRole("button", { name: "Train regressors" }));
    await waitFor(() => expect(screen.getByText(/synthetic training data/)).toBeInTheDocument());
    expect(screen.getByText("ridge (best)")).toBeInTheDocument();
    expect(m.regression.mock.calls[0][0].algorithms).toEqual(["linear", "ridge", "lasso", "random_forest", "gradient_boosting", "knn", "svr"]);
  });

  it("shows the learned wave-size policy", async () => {
    m.reinforcement.mockResolvedValue({
      episodes: 60000, blast_weight: 4, actions: [1, 2, 4],
      learning_curve: [{ episode: 1, mean_reward: -40 }, { episode: 2, mean_reward: -30 }],
      policy: [{ devices: "3-8", risk: "0%-2%", wave_size: 8, q_values: {} }, { devices: "3-8", risk: "15%-100%", wave_size: 2, q_values: {} }],
    });
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Reinforcement learning" }));
    fireEvent.click(screen.getByRole("button", { name: "Train agent" }));
    await waitFor(() => expect(screen.getByRole("table", { name: "Wave size policy" })).toBeInTheDocument());
    expect(screen.getByText("risk 15%-100%")).toBeInTheDocument();
  });

  it("shows log anomalies parsed from show logging", async () => {
    m.logs.mockResolvedValue({
      events: 772, windows: 48, window_minutes: 30, source: "fake syslog",
      devices: [{ device: "leaf-02", events: 168, by_severity: {}, top_events: [["LINK-UPDOWN", 60]], incidents: 2, status: "anomalous" },
                { device: "leaf-01", events: 47, by_severity: {}, top_events: [], incidents: 0, status: "normal" }],
      incidents: [{ device: "leaf-02", kind: "burst", event: "LINK-UPDOWN", count: 55, window_start: "2026-11-23T14:00:00+00:00", interfaces: ["Ethernet1/49"], explanation: "LINK-UPDOWN x55 in 30 min (normal 0/window)", score: 110, samples: ["... changed state to down"] }],
      timeline: { windows: ["a", "b"], devices: { "leaf-02": [1, 55], "leaf-01": [2, 1] } },
      collection: { "leaf-02": { platform: "nxos", lines: 168, parsed: 168, unparsed: 0, template: "cisco_show_logging" } },
      netbox: [],
    });
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Log anomalies (show logging)" }));
    fireEvent.click(screen.getByRole("button", { name: "Analyse logs" }));
    await waitFor(() => expect(screen.getByText(/LINK-UPDOWN x55 in 30 min/)).toBeInTheDocument());
    expect(screen.getByText("1 of 2 devices anomalous", { exact: false })).toBeInTheDocument();
    expect(m.logs).toHaveBeenCalledWith({ hours: 24, window_minutes: 30, push_to_netbox: false });
  });
});
