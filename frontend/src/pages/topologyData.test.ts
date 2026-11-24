import { describe, expect, it } from "vitest";
import { formatBps, iconFor, toNextData, utilColor, type Topology } from "./topologyData";

const T: Topology = {
  site: "dc1", telemetry: "fake", errors: [],
  nodes: [
    { id: "spine-01", name: "spine-01", platform: "eos", role: "spine", site: "dc1" },
    { id: "fw-01", name: "fw-01", platform: "fortios", role: "", site: "dc1" },
  ],
  links: [
    { id: 0, source: "spine-01", target: "fw-01", source_interface: "Ethernet10", target_interface: "port1",
      a_to_b_bps: 3.2e9, b_to_a_bps: 950e6, speed_bps: 10e9, utilization: 0.32 },
    { id: 1, source: "spine-01", target: "fw-01", source_interface: "Ethernet11", target_interface: "port9",
      a_to_b_bps: null, b_to_a_bps: null, speed_bps: null, utilization: null },
  ],
};

describe("topologyData", () => {
  it("formats rates", () => {
    expect(formatBps(3.2e9)).toBe("3.2 Gbps");
    expect(formatBps(950e6)).toBe("950 Mbps");
    expect(formatBps(512)).toBe("512 bps");
    expect(formatBps(null)).toBe("—");
  });

  it("colours links by utilisation", () => {
    expect(utilColor(0.2)).toBe("#1e8e3e");
    expect(utilColor(0.6)).toBe("#f29900");
    expect(utilColor(0.85)).toBe("#d93025");
    expect(utilColor(null)).toBe("#9aa0a6");
  });

  it("picks icons by role, then platform", () => {
    expect(iconFor(T.nodes[0])).toBe("switch");
    expect(iconFor(T.nodes[1])).toBe("firewall");
  });

  it("maps to NeXt data with in/out labels and keeps positions", () => {
    const d = toNextData(T, { "spine-01": { x: 10, y: 20 } });
    expect(d.nodes[0]).toMatchObject({ id: "spine-01", iconType: "switch", x: 10, y: 20 });
    expect(d.nodes[1]).not.toHaveProperty("x");
    expect(d.links[0]).toMatchObject({ source: "spine-01", target: "fw-01", sourcelabel: "→ 3.2 Gbps", targetlabel: "950 Mbps ←" });
    expect(d.links[1]).toMatchObject({ sourcelabel: "", targetlabel: "", color: "#9aa0a6", width: 1 });
  });
});
