/**
 * The Curate page's reads and writes under /api/web (DR-0709, DR-0786): this machine's round with its log and
 * stoplist, the open card's panel for the whole URL, each with the canonical URL for what it read (DR-0792); and the
 * four writes, a round, a decision, an undo and a lift. A value the URL or the body can't hold, or a write the store
 * refuses, throws an HttpError with the server's words; a decision on an entity file that changed since, with 409.
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";
import { encode, one, type Search } from "@/lib/search";

type Schemas = components["schemas"];
export type CurateRead = Schemas["CurateRead"];
export type CurateView = Schemas["CurateView"];
export type CardRow = Schemas["CardRow"];
export type LogRow = Schemas["LogRow"];
export type Lift = Schemas["Lift"];
export type CuratePanelRead = Schemas["CuratePanelRead"];
export type CardPanel = Schemas["CardPanel"];
export type EditForm = Schemas["EditForm"];
export type Decide = Schemas["CurateDecide"];
export type Edited = Schemas["Edited"];
export type Done = Schemas["Done"];

/** What a read answered, with the query string it was asked with. */
export type Read<T> = T & { asked: string };

export const PATH = "/curate";

/** The query string the round is read with: the open card left out, `done` only from a URL before the app. */
export function listQuery(search: Search): string {
  const done = one(search, "done");
  return done ? `?done=${encode(done)}` : "";
}

function nothing(what: string): never {
  throw new HttpError(500, `the ${what} answered nothing`);
}

export async function readCurate(query: string): Promise<Read<CurateRead>> {
  const { data } = await api.GET("/api/web/curate", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("round's read");
}

export async function readCard(query: string): Promise<Read<CuratePanelRead>> {
  const { data } = await api.GET("/api/web/curate/panel", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("card's read");
}

export async function startRound(): Promise<Done> {
  const { data } = await api.POST("/api/web/curate/round", { body: {} });
  return data ?? nothing("round");
}

export async function decide(body: Decide): Promise<Done> {
  const { data } = await api.POST("/api/web/curate/decide", { body });
  return data ?? nothing("decision");
}

export async function undo(op: string): Promise<Done> {
  const { data } = await api.POST("/api/web/curate/undo", { body: { op } });
  return data ?? nothing("undo");
}

export async function lift(key: string): Promise<Done> {
  const { data } = await api.POST("/api/web/curate/lift", { body: { key } });
  return data ?? nothing("lift");
}
