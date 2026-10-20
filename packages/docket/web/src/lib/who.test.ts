import { describe, expect, it } from "vitest";
import { folded, named, suggest } from "./who";

// As the server orders them: the most used first, then the most recent; a vault's person with no item last.
const PEOPLE = [
  { name: "Ben Sample", uses: 7, also: [] },
  { name: "Ada Example", uses: 5, also: ["Ada E.", "Example Ada"] },
  { name: "Cleo Example", uses: 2, also: [] },
  { name: "Dóra Minta", uses: 0, also: ["Dora"] },
];

describe("what a who field suggests", () => {
  it("gives every name in the server's order while nothing is typed", () => {
    expect(suggest(PEOPLE, "").map((s) => s.name)).toEqual(["Ben Sample", "Ada Example", "Cleo Example", "Dóra Minta"]);
    expect(suggest(PEOPLE, "  ", 2).map((s) => s.name)).toEqual(["Ben Sample", "Ada Example"]);
  });

  it("puts a name or a word of it that starts with the text first, then an other name, then any that holds it", () => {
    // "ex": Ada's and Cleo's surnames start with it, in the server's order; none holds it only inside.
    expect(suggest(PEOPLE, "ex").map((s) => s.name)).toEqual(["Ada Example", "Cleo Example"]);
    // "mple": inside Ben's and the others' surnames, so in the server's order.
    expect(suggest(PEOPLE, "mple").map((s) => s.name)).toEqual(["Ben Sample", "Ada Example", "Cleo Example"]);
    // "c": Cleo's own name first; then none.
    expect(suggest(PEOPLE, "c")[0]?.name).toBe("Cleo Example");
  });

  it("finds a name by its aliases and says which one, and leaves case and accents aside", () => {
    expect(suggest(PEOPLE, "ada e.")).toEqual([{ name: "Ada Example", uses: 5, via: "Ada E." }]);
    expect(suggest(PEOPLE, "DORA")).toEqual([{ name: "Dóra Minta", uses: 0 }]);
    expect(suggest(PEOPLE, "example a")).toEqual([{ name: "Ada Example", uses: 5, via: "Example Ada" }]);
    expect(suggest(PEOPLE, "zeppelin")).toEqual([]);
  });

  it("knows a typed name already suggested, so it needs no choice as typed", () => {
    expect(named(suggest(PEOPLE, "ada example"), "ada example ")).toBe(true);
    expect(named(suggest(PEOPLE, "Ada"), "Ada")).toBe(false);
    expect(folded("  Dóra   MINTA ")).toBe("dora minta");
  });
});
