import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Help, { splitPages } from "./Help";
import { Markdown, slugify } from "../components/Markdown";

const MANUAL = [
  "# VireoNet User Manual",
  "",
  "Intro text. Jump to [Webhooks](#3-webhooks).",
  "",
  "# Part I — Basics",
  "",
  "## 1. Login",
  "",
  "Sign in with **your** account.",
  "",
  "# Part II — Integrations",
  "",
  "## 3. Webhooks",
  "",
  "Send **signed** calls with `X-VireoNet-Signature`.",
  "",
  "### 3.1 HMAC",
  "",
  "| Setting | Default |",
  "|---|---|",
  "| `wave_health_checks` | true |",
  "",
  "```",
  "<script>alert(1)</script>",
  "```",
  "",
  "<img src=x onerror=alert(1)> and [evil](javascript:alert(1))",
  "",
  "![Run page](images/flows-run.png) ![bad](https://evil.example/x.png)",
].join("\n");

vi.mock("../lib/api", async (orig) => ({
  ...((await orig()) as Record<string, unknown>),
  request: vi.fn(async (path: string) => (path === "/help/docs"
    ? { items: [{ name: "USER_MANUAL", title: "User manual" }, { name: "ADAPTERS", title: "Adapter integration guide" }] }
    : { name: "USER_MANUAL", title: "User manual", markdown: MANUAL })),
}));

function page(url = "/help") {
  return render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={[url]}><Help /></MemoryRouter></QueryClientProvider>);
}

describe("Documentation space", () => {
  it("splits the manual into pages grouped by part", () => {
    const pages = splitPages(MANUAL, "User manual");
    expect(pages.map((p) => [p.title, p.part])).toEqual([["User manual", ""], ["1. Login", "Part I — Basics"], ["3. Webhooks", "Part II — Integrations"]]);
    expect(pages[2].anchors).toContain("31-hmac");
  });

  it("shows the page tree, breadcrumbs, prev/next, and follows in-page links to their page", async () => {
    page();
    expect(await screen.findByText(/Intro text/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Webhooks" }));
    const h = await screen.findByRole("heading", { name: "3. Webhooks" });
    expect(h.id).toBe("3-webhooks");
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText("3. Webhooks")).toBeInTheDocument();
    expect(screen.getByText(/Part II — Integrations ›/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "← 1. Login" })).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: "On this page" })).getByText("3.1 HMAC")).toBeInTheDocument();
  });

  it("renders safely: no raw HTML, only the manual's own images", async () => {
    page("/help?page=3-webhooks");
    await screen.findByRole("heading", { name: "3. Webhooks" });
    expect(screen.getByText("signed").tagName).toBe("STRONG");
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "evil" })).toBeNull();
    const imgs = Array.from(document.querySelectorAll("img"));
    expect(imgs.map((i) => i.getAttribute("alt"))).toEqual(["Run page"]);
    expect(imgs[0].getAttribute("src")).toMatch(/\/help\/images\/flows-run\.png$/);
  });

  it("searches every page with snippets", async () => {
    page();
    await screen.findByText(/Intro text/);
    fireEvent.change(screen.getByLabelText("Search this space"), { target: { value: "signature" } });
    const results = screen.getByRole("list", { name: "Search results" });
    fireEvent.click(within(results).getByText("3. Webhooks"));
    expect(await screen.findByRole("heading", { name: "3. Webhooks" })).toBeInTheDocument();
  });

  it("slugifies like GitHub", () => {
    expect(slugify("43. Adapter integration — ServiceNow")).toBe("43-adapter-integration--servicenow");
    render(<Markdown source={"- one\n- two"} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});
