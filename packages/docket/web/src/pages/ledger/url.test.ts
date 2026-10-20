import { describe, expect, it } from "vitest";
import { parseSearch, stringifySearch } from "@/lib/search";
import canonical from "@/test/canonical.json";
import { listKey, ORDER, quiet, withSearch } from "./url";

describe("the Ledger's URL as the page changes it", () => {
  it("keeps the parameters in the order the server writes them", () => {
    expect([...ORDER]).toEqual(canonical.order["/ledger"]);
  });

  it("sets and takes out parameters, keeping the server's canonical form", () => {
    const url = canonical.urls.find((u) => u.startsWith("/ledger?")) ?? "";
    const search = parseSearch(url.split("?")[1] ?? "");
    expect(`/ledger${stringifySearch(withSearch(search, {}))}`).toBe(url);
    const opened = withSearch(search, { open: "A-0003", state: "all", q: undefined });
    expect(stringifySearch(opened)).toBe("?open=A-0003&who=Ada+Example&state=all");
    expect(stringifySearch(withSearch({ state: "all" }, { open: "A-0001", drop: "1" }))).toBe(
      "?open=A-0001&state=all&drop=1",
    );
  });

  it("keeps a parameter it doesn't know after its own", () => {
    expect(stringifySearch(withSearch({ later: "x", state: "all" }, { open: "A-1" }))).toBe(
      "?open=A-1&state=all&later=x",
    );
  });

  it("leaves out what a write said", () => {
    expect(quiet({ open: "A-1", done: "nothing", said: "undo", skipped: ["A-2"] })).toEqual({ open: "A-1" });
  });

  it("asks a new list only when what the list depends on changed", () => {
    const base = listKey({ q: "ada", state: "all" });
    expect(listKey({ state: "all", q: "ada", open: "A-1", text: "a.md", drop: "1" })).toBe(base);
    expect(listKey({ q: "ada", state: "all", k: "200" })).not.toBe(base);
  });
});
