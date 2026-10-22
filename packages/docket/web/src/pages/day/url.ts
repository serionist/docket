/**
 * A day page's URL (DR-0792, DR-0669): the server's day_state.py reads it and answers its canonical form, which the
 * page writes back; the Queue's leaves out the Ledger's own state, group and archived, and Meetings' the items' kind,
 * who and k, with suggest its own (DR-0768). A change the page makes itself
 * keeps the keys in the canonical order, so the address bar holds the server's URL without waiting for a read;
 * src/test/canonical.json holds this order to the server's.
 */
import { one, type Search, stringifySearch } from "@/lib/search";

/** The parameters in the order the server writes them (DayState.pairs). */
export const ORDER = [
  "open",
  "at",
  "q",
  "stream",
  "kind",
  "who",
  "k",
  "state",
  "group",
  "archived",
  "drop",
  "text",
  "suggest",
] as const;
export type Param = (typeof ORDER)[number];

/** What a write said, which a URL may carry from before and the canonical leaves out. */
const SAID = ["done", "said", "skipped"];
/** What the list's read depends on: a change to anything else asks no new list. */
const LISTED: Param[] = ["q", "stream", "kind", "who", "k", "state", "group", "archived"];

/** The search with these parameters set, or taken out when undefined, in the canonical order, others after. */
export function withSearch(search: Search, changes: Partial<Record<Param, string | undefined>>): Search {
  const set = new Map<string, string | string[]>(Object.entries(search));
  for (const [key, value] of Object.entries(changes)) set.set(key, value ?? "");
  const out: Search = {};
  const keep = (key: string) => {
    const value = set.get(key);
    if (value !== undefined && value !== "" && !(key in out)) out[key] = value;
  };
  for (const key of ORDER) keep(key);
  for (const key of set.keys()) keep(key);
  return out;
}

/** The search without what a write said: a URL still carrying it shows its notice once. */
export function quiet(search: Search): Search {
  return Object.fromEntries(Object.entries(search).filter(([key]) => !SAID.includes(key)));
}

/** The list's key: the parameters its read depends on, as given. */
export function listKey(search: Search): string {
  return LISTED.map((key) => `${key}=${one(search, key) ?? ""}`).join("&");
}

/** The query string a read sends: the page's own, as the address bar holds it. */
export function queryOf(search: Search): string {
  return stringifySearch(search);
}
