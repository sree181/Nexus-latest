import { describe, expect, it } from "vitest";
import { relationRows, toCytoscape, type GraphPayload } from "@meshagent/graph";

const payload: GraphPayload = {
  nodes: [
    { id: "class:loader", kind: "class", label: "Loader" },
    { id: "sink:eval", kind: "sink", label: "eval" },
    { id: "cwe:95", kind: "cwe", label: "CWE-95" },
  ],
  edges: [{ id: "direct", source: "class:loader", target: "sink:eval", rel: "calls" }],
  relations: [{
    id: "01FACT",
    kind: "finding",
    label: "Dynamic execution finding",
    members: ["class:loader", "sink:eval", "cwe:95"],
  }],
};

describe("graph relation transform", () => {
  it("represents an n-ary recorded fact with one relation node and membership links", () => {
    const elements = toCytoscape(payload);

    expect(elements).toEqual(expect.arrayContaining([
      expect.objectContaining({
        data: expect.objectContaining({ id: "relation:01FACT", memberCount: 3 }),
        classes: "kind-relation",
      }),
      expect.objectContaining({
        data: expect.objectContaining({ source: "relation:01FACT", target: "class:loader", rel: "member" }),
        classes: "relation-member",
      }),
      expect.objectContaining({
        data: expect.objectContaining({ id: "direct", rel: "calls" }),
        classes: "graph-link rel-calls",
      }),
    ]));
    expect(elements.filter((element) => element.classes === "relation-member")).toHaveLength(3);
  });

  it("creates a readable relation row with all member labels", () => {
    expect(relationRows(payload)).toEqual([{
      id: "01FACT",
      kind: "finding",
      label: "Dynamic execution finding",
      members: ["Loader", "eval", "CWE-95"],
      tombstoned: false,
    }]);
  });
});
