/**
 * The Staging page's reads and writes under /api/web (DR-0709, DR-0784, DR-0785): the list for the URL's source,
 * state and k, the open record's panel for the whole URL, each with the canonical URL for what it read (DR-0792); and
 * the two writes, a classification and a promotion. A value the URL or the body can't hold, or a write the gate
 * refuses, throws an HttpError with the server's words; a record that changed since its panel was read, with 409.
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";
import { encode, one, type Search } from "@/lib/search";

type Schemas = components["schemas"];
export type StagingRead = Schemas["StagingRead"];
export type StagingList = Schemas["StagingList"];
export type StagedRow = Schemas["StagedRow"];
export type StagedGroup = Schemas["StagedGroup"];
export type HeldSource = Schemas["HeldSource"];
export type StagingPanelRead = Schemas["StagingPanelRead"];
export type StagingPanel = Schemas["StagingPanel"];
export type Utterance = Schemas["Utterance"];
export type TextLine = Schemas["TextLine"];
export type Classify = Schemas["Classify"];
export type Classified = Schemas["Classified"];
export type Promoted = Schemas["Promoted"];

/** What a read answered, with the query string it was asked with. */
export type Read<T> = T & { asked: string };

export const PATH = "/staging";
// The list's own parameters, in the order the server writes them (DR-0784); `done` only from a URL before the app.
const LISTING = ["source", "state", "k", "done"] as const;

/** The query string the list is read with: the URL's source, state and k, the panel's parameters left out. */
export function listQuery(search: Search): string {
  const pairs = LISTING.flatMap((key) => {
    const value = one(search, key);
    return value ? [`${encode(key)}=${encode(value)}`] : [];
  });
  return pairs.length ? `?${pairs.join("&")}` : "";
}

/**
 * The URL that opens a record with what is checked on it and typed, as the page's own state (DR-0383, DR-0784): the
 * list's parameters kept, then open, u, line and cut in the server's order, and picked=1 when nothing is chosen, so
 * an empty selection reads as the owner's and not as the record's stored cuts. The server writes the canonical back.
 */
export function chosenHref(search: Search, key: string, u: number[], lines: number[], typed: string): string {
  const pairs: [string, string][] = [];
  for (const name of ["source", "state", "k"]) {
    const value = one(search, name);
    if (value) pairs.push([name, value]);
  }
  pairs.push(["open", key]);
  for (const n of [...u].sort((a, b) => a - b)) pairs.push(["u", String(n)]);
  for (const n of [...lines].sort((a, b) => a - b)) pairs.push(["line", String(n)]);
  const cut = typed.trim();
  if (cut) pairs.push(["cut", cut]);
  if (!u.length && !lines.length && !cut) pairs.push(["picked", "1"]);
  return `${PATH}?${pairs.map(([k, v]) => `${encode(k)}=${encode(v)}`).join("&")}`;
}

function nothing(what: string): never {
  throw new HttpError(500, `the ${what} answered nothing`);
}

export async function readStaging(query: string): Promise<Read<StagingRead>> {
  const { data } = await api.GET("/api/web/staging", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("staging list's read");
}

export async function readRecord(query: string): Promise<Read<StagingPanelRead>> {
  const { data } = await api.GET("/api/web/staging/panel", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("record's read");
}

export async function classify(body: Classify): Promise<Classified> {
  const { data } = await api.POST("/api/web/staging/classify", { body });
  return data ?? nothing("classification");
}

export async function promote(source: string | null): Promise<Promoted> {
  const { data } = await api.POST("/api/web/staging/promote", { body: { source } });
  return data ?? nothing("promotion");
}
