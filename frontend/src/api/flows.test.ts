import { describe, expect, it } from "vitest";
import { getList, moveStep, setList, type Step } from "./flows";

const tree = (): Step[] => [
  { id: "a", type: "log" },
  { id: "if1", type: "if", branches: [{ condition: { expr: "true" }, steps: [{ id: "b", type: "log" }] }], else: [{ id: "c", type: "log" }] } as unknown as Step,
  { id: "d", type: "log" },
  { id: "loop", type: "for_each", steps: [] } as unknown as Step,
];
const ids = (l: Step[]) => l.map((s) => s.id);

describe("nested step lists", () => {
  it("reads and writes lists by path", () => {
    const t = tree();
    expect(ids(getList(t, [{ i: 1, key: "b0" }]))).toEqual(["b"]);
    expect(ids(getList(t, [{ i: 1, key: "else" }]))).toEqual(["c"]);
    const t2 = setList(t, [{ i: 3, key: "steps" }], [{ id: "x", type: "log" }]);
    expect(ids(getList(t2, [{ i: 3, key: "steps" }]))).toEqual(["x"]);
    expect(ids(getList(t, [{ i: 3, key: "steps" }]))).toEqual([]); // immutable
  });

  it("moves within a list, into a block, out of a block, and refuses moving into itself", () => {
    expect(ids(moveStep(tree(), [], 0, [], 3)!)).toEqual(["if1", "d", "a", "loop"]);
    const into = moveStep(tree(), [], 0, [{ i: 3, key: "steps" }], 0)!;
    expect(ids(into)).toEqual(["if1", "d", "loop"]);
    expect(ids(getList(into, [{ i: 2, key: "steps" }]))).toEqual(["a"]); // path index shifted after removal
    const out = moveStep(tree(), [{ i: 1, key: "b0" }], 0, [], 0)!;
    expect(ids(out)).toEqual(["b", "a", "if1", "d", "loop"]);
    expect(getList(out, [{ i: 2, key: "b0" }])).toEqual([]);
    expect(moveStep(tree(), [], 1, [{ i: 1, key: "else" }], 0)).toBeNull();
  });
});
