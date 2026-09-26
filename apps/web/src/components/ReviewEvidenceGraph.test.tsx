import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { GraphPayload } from "@meshagent/graph";

import { ReviewEvidenceGraph } from "./ReviewEvidenceGraph";

const graph: GraphPayload = {
  nodes: [
    { id: "package:requests", kind: "package", label: "requests" },
    { id: "version:2.32", kind: "version", label: "2.32.0" },
    { id: "cve:123", kind: "cve", label: "CVE-2026-123", severity: "high" },
  ],
  edges: [
    { id: "edge:1", source: "package:requests", target: "version:2.32", rel: "has_version" },
  ],
  relations: [
    { id: "rel:1", kind: "advisory", label: "Affected package version", members: ["package:requests", "version:2.32", "cve:123"] },
  ],
};

describe("ReviewEvidenceGraph", () => {
  it("exposes native relation members as text and synchronizes relation selection", () => {
    const onSelect = vi.fn();
    render(<ReviewEvidenceGraph graph={graph} label="Review evidence" onSelect={onSelect} />);

    const relation = screen.getByRole("button", { name: /Affected package version.*requests.*2.32.0.*CVE-2026-123/i });
    expect(relation).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(relation);
    expect(relation).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: /^package\. requests$/i }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "package:requests" }));
  });

  it("keeps oversized relations complete in text instead of drawing a partial hull", () => {
    const nodes: GraphPayload["nodes"] = Array.from({ length: 30 }, (_, index) => ({
      id: `entity:${index + 1}`,
      kind: "package",
      label: `Entity ${index + 1}`,
    }));
    const dense: GraphPayload = {
      nodes,
      edges: [],
      relations: [{
        id: "rel:dense",
        kind: "native_relation",
        label: "Complete recorded relation",
        members: nodes.map((node) => node.id),
      }],
    };

    const { container } = render(<ReviewEvidenceGraph graph={dense} label="Dense evidence" />);

    expect(screen.getByText("0 complete relations mapped · full trail below")).toBeInTheDocument();
    expect(screen.getByText("Text only")).toBeInTheDocument();
    expect(screen.getByText(/Entity 30/)).toBeInTheDocument();
    expect(container.querySelectorAll(".evidence-relation-hull")).toHaveLength(0);
  });
});
