/**
 * The Settings page's read and its three writes under /api/web (DR-0789, DR-0790). The read answers the canonical URL
 * and a token for vault.yaml as it read it, which each write carries back: a page older than the file writes nothing
 * and answers 409. A never-store value goes only in a write's body, and nothing the service answers holds it; the
 * line an add Docket can't place shows is the page's own, from the value it holds (`neverStoreLine`).
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";

type Schemas = components["schemas"];
export type SettingsPage = Schemas["SettingsPage"];
export type SourceRead = Schemas["SourceRead"];
export type NeverListRead = Schemas["NeverListRead"];
export type RuleRead = Schemas["RuleRead"];
export type ModelsRead = Schemas["ModelsRead"];
export type AddQuestion = Schemas["AddQuestion"];
export type PrivateQuestion = Schemas["PrivateQuestion"];
export type NeverStoreAnswer = Schemas["NeverStoreAnswer"];
export type RuleAnswer = Schemas["RuleAnswer"];
export type SourceAnswer = Schemas["SourceAnswer"];

/** What a read answered, with the query string it was asked with. */
export type Read<T> = T & { asked: string };

export const PATH = "/settings";

/** A value's line under its list in vault.yaml, to add by hand: single-quoted, its own quotes doubled, so YAML reads
 * it as the one value whatever it holds (src/test/never-store-lines.json, which the Python tests read too). */
export function neverStoreLine(value: string): string {
  return `- '${value.trim().replaceAll("'", "''")}'`;
}

function nothing(what: string): never {
  throw new HttpError(500, `the ${what} answered nothing`);
}

export async function readSettings(query: string): Promise<Read<SettingsPage>> {
  const { data } = await api.GET("/api/web/settings", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("Settings page's read");
}

export async function neverStore(body: Schemas["NeverStoreWrite"]): Promise<NeverStoreAnswer> {
  const { data } = await api.POST("/api/web/settings/never-store", { body });
  return data ?? nothing("never-store write");
}

export async function redaction(body: Schemas["RuleWrite"]): Promise<RuleAnswer> {
  const { data } = await api.POST("/api/web/settings/redaction", { body });
  return data ?? nothing("redaction write");
}

export async function source(body: Schemas["SourceWrite"]): Promise<SourceAnswer> {
  const { data } = await api.POST("/api/web/settings/source", { body });
  return data ?? nothing("source's write");
}
