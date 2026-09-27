import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { GraphPayload } from "@meshagent/graph";

import { FigmaEvidenceGraph, FigmaJourney } from "./FigmaWorkflowV3";

const graph: GraphPayload = {
  nodes: [
    { id: "session:1", kind: "session", label: "Cursor session" },
    { id: "package:requests", kind: "package", label: "requests" },
    { id: "version:2.32", kind: "version", label: "2.32.0" },
    { id: "cve:123", kind: "cve", label: "CVE-2026-123", severity: "high" },
  ],
  edges: [
    { id: "edge:1", source: "session:1", target: "package:requests", rel: "introduced" },
    { id: "edge:2", source: "package:requests", target: "version:2.32", rel: "has_version" },
  ],
  relations: [
    { id: "rel:1", kind: "advisory", label: "Affected package version", members: ["package:requests", "version:2.32", "cve:123"] },
  ],
};

describe("FigmaWorkflowV3", () => {
  it("renders the canonical seven-stage governed journey", () => {
    render(<FigmaJourney current="security_review" label="Governed journey" />);

    const journey = screen.getByRole("list", { name: "Governed journey" });
    expect(within(journey).getAllByRole("listitem")).toHaveLength(7);
    expect(screen.getByText("Security review").closest("li")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("CISO decision")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("uses returned graph facts and exposes relation members as synchronized text", () => {
    const onSelect = vi.fn();
    render(<FigmaEvidenceGraph graph={graph} label="Native evidence" onSelect={onSelect} />);

    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByText("Affected package version")).toBeInTheDocument();
    expect(screen.getByText("requests · 2.32.0 · CVE-2026-123")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "cve: CVE-2026-123" }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "cve:123" }));
  });
});
