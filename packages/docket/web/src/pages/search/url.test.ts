import { describe, expect, it } from "vitest";
import { parseSearch, stringifySearch } from "@/lib/search";
import canonical from "@/test/canonical.json";
import { hitOf, ORDER, ordered, queryHref, resultsQuery, searchOnly } from "./url";

describe("the Search page's URL as the page changes it", () => {
  it("keeps the parameters in the order the server writes them", () => {
    expect([...ORDER]).toEqual(canonical.search_order);
    const url = canonical.urls.find((u) => u.startsWith("/search?query=Northwind")) ?? "";
    const search = parseSearch(url.split("?")[1] ?? "");
    expect(`/search${stringifySearch(ordered(search))}`).toBe(url);
  });

  it("reads the results without the panel's parameters, any other key kept in its place", () => {
    const search = parseSearch("?later=x&query=a&hit=r:calls:rec-1&lines=4&lines=6-7&around=3&verify=1&k=40");
    expect(searchOnly(search)).toEqual({ later: "x", query: "a", k: "40" });
    expect(resultsQuery(search)).toBe("?later=x&query=a&k=40");
  });

  it("sends new words with the search's filters and boosts, never its k or its panel", () => {
    const search = parseSearch(
      "?query=old&sources=calls&sources=chat&people=ben-sample&since=2026-10-01&k=40&hit=r:calls:rec-1&around=2",
    );
    expect(queryHref(search, "  day rate ")).toBe(
      "/search?query=day+rate&sources=calls&sources=chat&people=ben-sample&since=2026-10-01",
    );
    expect(queryHref(search, "old", { since: " last week", until: "" })).toBe(
      "/search?query=old&sources=calls&sources=chat&people=ben-sample&since=last+week",
    );
    expect(queryHref({}, "")).toBe("/search");
  });

  it("names the open hit by its locator and its lines, as a row's URL names it", () => {
    expect(hitOf(parseSearch("?query=a&hit=minutes:x&lines=4&lines=6-7"))).toBe("minutes:x\n4\n6-7");
    expect(hitOf(parseSearch("?hit=r:calls:rec-1@00:10"))).toBe("r:calls:rec-1@00:10");
    expect(hitOf(parseSearch("?query=a"))).toBeUndefined();
  });
});
