import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Gitea from "./Gitea";

let info: Record<string, unknown> = {};
vi.mock("../lib/api", async (orig) => ({ ...((await orig()) as Record<string, unknown>), request: vi.fn(async () => info) }));

const view = () => render(<QueryClientProvider client={new QueryClient()}><Gitea /></QueryClientProvider>);

describe("Gitea tab", () => {
  it("embeds Gitea when it is installed", async () => {
    info = { configured: true, installed: true, version: "1.22.6", ui_url: "http://h:4173/gitea", embed_url: "/gitea/", repos: [{ name: "netops/config-leaf-01", url: "u", updated: "" }], detail: "" };
    view();
    expect(await screen.findByTitle("Gitea")).toHaveAttribute("src", "/gitea/");
    expect(screen.getByText(/version 1.22.6 · 1 repositories/)).toBeInTheDocument();
  });
  it("explains when Gitea is not installed", async () => {
    info = { configured: false, installed: false, version: "", ui_url: "", embed_url: "", repos: [], detail: "Gitea is not configured" };
    view();
    expect(await screen.findByText(/Gitea is not configured/)).toBeInTheDocument();
  });
});
