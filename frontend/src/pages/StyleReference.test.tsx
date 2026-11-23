import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StyleReference from "./StyleReference";
import { statusToken } from "../lib/tokens";

describe("StyleReference", () => {
  it("renders all sections", () => {
    render(<StyleReference />);
    expect(screen.getByText("1. Palette — tokens only")).toBeInTheDocument();
    expect(screen.getByText("5. Table — full width, sticky header, 1px borders")).toBeInTheDocument();
    expect(screen.getByText("RITM0001234")).toBeInTheDocument();
    expect(screen.getByText("Pre-check failed: neighbour 10.1.1.2 down")).toBeInTheDocument();
  });

  it("renders every change state as a badge", () => {
    render(<StyleReference />);
    for (const state of ["received", "closed", "failed", "rolled_back"]) {
      expect(screen.getByText(state)).toBeInTheDocument();
    }
  });

  it("contains no emoji or lorem ipsum", () => {
    const { container } = render(<StyleReference />);
    expect(screen.getByText(/Forbidden — none of these appear on this page/)).toBeInTheDocument();
    expect(container.textContent).toMatch(/8\. Forbidden/);
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}]/u);
  });
});

describe("statusToken", () => {
  it("maps closed to success and failed to danger", () => {
    expect(statusToken("closed")).toBe("success");
    expect(statusToken("failed")).toBe("danger");
    expect(statusToken("rolled_back")).toBe("warning");
    expect(statusToken("received")).toBe("info");
  });
});
