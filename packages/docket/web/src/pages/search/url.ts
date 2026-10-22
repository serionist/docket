/**
 * The Search page's URL (DR-0174, DR-0792): the search tool's parameters, then the panel's, in the order the server's
 * SearchState writes them; src/test/canonical.json holds this order to the server's. The results are read for the
 * search alone, so opening, moving or closing a hit asks no new search.
 */
import { type Search, stringifySearch } from "@/lib/search";

export const PATH = "/search";
/** The parameters in the order the server writes them (SearchState.pairs). */
export const ORDER = [
  "query",
  "sources",
  "people",
  "entities",
  "since",
  "until",
  "k",
  "hit",
  "lines",
  "around",
  "verify",
] as const;
/** The panel's own parameters: the results never depend on them. */
export const PANEL = ["hit", "lines", "around", "verify"];
/** What a new query carries over from the search it replaces: its filters and boosts, not its k or its panel. */
const CARRIED = ["sources", "people", "entities", "since", "until"];

/** The search with the panel's parameters left out, every other key as it came, in its order. */
export function searchOnly(search: Search): Search {
  return Object.fromEntries(Object.entries(search).filter(([key]) => !PANEL.includes(key)));
}

/** The query string the results are read with. */
export function resultsQuery(search: Search): string {
  return stringifySearch(searchOnly(search));
}

/** The search's parameters in the server's order, the ones it doesn't know after. */
export function ordered(search: Search): Search {
  const out: Search = {};
  for (const key of ORDER) {
    const value = search[key];
    if (value !== undefined && value !== "") out[key] = value;
  }
  for (const [key, value] of Object.entries(search)) if (!(key in out) && value !== "") out[key] = value;
  return out;
}

/** The URL a query sent from the box opens: the words, with the filters and boosts the page holds now. */
export function queryHref(search: Search, words: string, dates?: { since: string; until: string }): string {
  const next: Search = { query: words.trim() };
  for (const key of CARRIED) {
    const value = search[key];
    if (value !== undefined) next[key] = value;
  }
  if (dates) {
    next.since = dates.since.trim();
    next.until = dates.until.trim();
  }
  return `${PATH}${stringifySearch(ordered(next))}`;
}

/** Which hit a URL opens: its locator and its lines, the identity a row and the open panel share. */
export function hitOf(search: Search): string | undefined {
  const hit = search.hit;
  const first = Array.isArray(hit) ? hit[0] : hit;
  if (!first) return undefined;
  const lines = search.lines;
  const all = lines === undefined ? [] : Array.isArray(lines) ? lines : [lines];
  return [first, ...all].join("\n");
}
