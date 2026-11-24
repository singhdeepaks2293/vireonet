import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Controllers from "./Controllers";
import { controllersApi } from "../api/controllers";

describe("Controllers page", () => {
  it("lists controllers and shows a controller's devices and versions", async () => {
    Object.assign(controllersApi, {
      list: async () => ({ items: [
        { name: "cloudvision", vendor: "Arista CloudVision", platforms: ["eos"], native_upgrade: true, fake: true, status: "ok", detail: "fake: 3 devices" },
        { name: "panorama", vendor: "Palo Alto Panorama", platforms: ["panos"], native_upgrade: false, fake: true, status: "ok", detail: "" },
      ] }),
      devices: async (n: string) => ({ items: n === "panorama" ? [{ id: "1", name: "fw-02", platform: "panos", version: "10.2.9", serial: "S", model: "PA", reachable: true }] : [{ id: "2", name: "spine-01", platform: "eos", version: "4.30.2F", serial: "S", model: "DCS", reachable: true }] }),
      images: async () => ({ items: [{ id: "i", name: "EOS-4.32.1F.swi", version: "4.32.1F", platform: "eos" }] }),
    });
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><Controllers /></MemoryRouter></QueryClientProvider>);
    const table = await screen.findByRole("table", { name: "Controllers" });
    expect(within(table).getByText("distribute, then activate")).toBeInTheDocument();
    expect(await screen.findByText("spine-01")).toBeInTheDocument();
    expect(await screen.findByText("4.32.1F")).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: "panorama" }));
    expect(await screen.findByText("fw-02")).toBeInTheDocument();
  });
});
