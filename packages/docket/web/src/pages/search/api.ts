/**
 * The Search page's reads under /api/web (DR-0709, DR-0775): the results for the page's query string with the panel's
 * parameters left out, and the open hit's panel for the whole of it. Each answers the canonical URL for what it read,
 * which the page writes back (DR-0792). A value the URL can't hold throws an HttpError with the server's words.
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";

type Schemas = components["schemas"];
export type SearchRead = Schemas["SearchRead"];
export type Results = Schemas["Results"];
export type HitGroup = Schemas["HitGroup"];
export type HitLine = Schemas["HitLine"];
export type Piece = Schemas["Piece"];
export type Facet = Schemas["Facet"];
export type FacetItem = Schemas["FacetItem"];
export type Chosen = Schemas["Chosen"];
export type HitRead = Schemas["HitRead"];
export type HitPanel = Schemas["HitPanel"];
export type SpanView = Schemas["SpanView"];
export type SummaryDoc = Schemas["SummaryDoc"];
export type SummaryLine = Schemas["SummaryLine"];
export type VerifiedLine = Schemas["VerifiedLine"];

/** What a read answered, with the query string it was asked with, so the app writes back only a current canonical. */
export type Read<T> = T & { asked: string };

function nothing(what: string): never {
  throw new HttpError(500, `the ${what} answered nothing`);
}

export async function readResults(query: string): Promise<Read<SearchRead>> {
  const { data } = await api.GET("/api/web/search", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("search's read");
}

export async function readHit(query: string): Promise<Read<HitRead>> {
  const { data } = await api.GET("/api/web/search/panel", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("panel's read");
}
