/**
 * The Sync page's reads under /api/web (DR-0788): the whole page for the URL's source, run and k, and its runs part,
 * which the page asks again every 5 seconds while a run is open. Each answers the canonical URL for what it read
 * (DR-0792). A value the URL can't hold throws an HttpError with the server's words.
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";
import { encode, one, type Search } from "@/lib/search";

type Schemas = components["schemas"];
export type SyncPage = Schemas["SyncPage"];
export type SyncRuns = Schemas["SyncRuns"];
export type RunsRead = Schemas["RunsRead"];
export type RunRead = Schemas["RunRead"];
export type NeedRead = Schemas["NeedRead"];
export type FailureRead = Schemas["FailureRead"];
export type CursorRead = Schemas["CursorRead"];
export type MachineRead = Schemas["MachineRead"];
export type EntryRead = Schemas["EntryRead"];

/** What a read answered, with the query string it was asked with. */
export type Read<T> = T & { asked: string };

export const PATH = "/sync";
// The page's parameters, in the order the server writes them (DR-0788).
const PARAMS = ["source", "run", "k"] as const;

/** The page's URL with `run` set, or left out: the source and k kept, as the server would write it. */
export function withRun(search: Search, run: string | undefined): string {
  const pairs = PARAMS.flatMap((key) => {
    const value = key === "run" ? run : one(search, key);
    return value ? [`${encode(key)}=${encode(value)}`] : [];
  });
  return pairs.length ? `${PATH}?${pairs.join("&")}` : PATH;
}

function nothing(what: string): never {
  throw new HttpError(500, `the ${what} answered nothing`);
}

export async function readSync(query: string): Promise<Read<SyncPage>> {
  const { data } = await api.GET("/api/web/sync", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("Sync page's read");
}

export async function readRuns(query: string): Promise<Read<SyncRuns>> {
  const { data } = await api.GET("/api/web/sync/runs", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("runs' read");
}
