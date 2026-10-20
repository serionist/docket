import { describe, expect, it } from "vitest";
import { after, pressed, standIn, step, writes } from "./day";

describe("the old tasks app's bulk rule", () => {
  it("sets a verdict on every row unless every row holds it, and then clears it", () => {
    expect(writes("chase", ["chase", null])).toBe("chase");
    expect(writes("chase", ["chase", "chase"])).toBeNull();
    expect(writes("me", ["chase"])).toBe("me");
    expect(pressed("chase", ["chase", "chase"])).toBe(true);
    expect(pressed("chase", ["chase", "me"])).toBe(false);
  });

  it("presses nothing over no rows, so a call there sets", () => {
    expect(pressed("discuss", [])).toBe(false);
    expect(writes("discuss", [])).toBe("discuss");
  });
});

describe("where the selection goes after a call", () => {
  const order = ["A-1", "A-2", "A-3", "A-4"];

  it("goes to the next row the call didn't act on", () => {
    expect(after(order, "A-2", ["A-2"])).toBe("A-3");
    expect(after(order, "A-2", ["A-2", "A-3"])).toBe("A-4");
  });

  it("goes to the one before when the acted rows run to the end", () => {
    expect(after(order, "A-4", ["A-4"])).toBe("A-3");
    expect(after(order, "A-3", ["A-3", "A-4"])).toBe("A-2");
  });

  it("stays when the selection wasn't acted on, or nothing is left", () => {
    expect(after(order, "A-1", ["A-3"])).toBeUndefined();
    expect(after(["A-1"], "A-1", ["A-1"])).toBeUndefined();
    expect(after(order, "A-9", ["A-9"])).toBeUndefined();
  });

  it("takes the one acted row as the selection when none is selected", () => {
    expect(after(order, undefined, ["A-2"])).toBe("A-3");
    expect(after(order, undefined, ["A-2", "A-3"])).toBeUndefined();
  });
});

describe("j and k", () => {
  const order = ["A-1", "A-2", "A-3"];

  it("move one row, and stop at either end", () => {
    expect(step(order, "A-2", 1)).toBe("A-3");
    expect(step(order, "A-3", 1)).toBe("A-3");
    expect(step(order, "A-1", -1)).toBe("A-1");
  });

  it("start at the first or the last row when none is selected", () => {
    expect(step(order, undefined, 1)).toBe("A-1");
    expect(step(order, "gone", -1)).toBe("A-3");
    expect(step([], undefined, 1)).toBeUndefined();
  });

  it("hand a row a refetch dropped to the one now in its place", () => {
    expect(standIn(["A-1", "A-3"], 1)).toBe("A-3");
    expect(standIn(["A-1"], 4)).toBe("A-1");
    expect(standIn([], 0)).toBeUndefined();
  });
});
