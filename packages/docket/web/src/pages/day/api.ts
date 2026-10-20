/**
 * The day pages' calls to /api/web (DR-0709, DR-0710): each page's list and panel under its own path, and what they
 * share under /api/web/ledger: the viewer's text, the poll, the names the who fields suggest and the writes. A read
 * sends the page's query string as it is, so the server's own parser reads it, and answers the canonical URL with
 * what it read. A write answers its notice; a refusal throws an HttpError whose body is the Refused model.
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";

type Schemas = components["schemas"];
/** The day pages built so far, as a write's body and the routes name them. */
export type DayPageName = "ledger" | "queue";
export type Ledger = Schemas["Ledger"];
export type Queue = Schemas["Queue"];
/** A day page's list read: the Queue answers in the Ledger's shape. */
export type DayList = Ledger | Queue;
export type Row = Schemas["Row"];
export type Group = Schemas["Group"];
export type Button = Schemas["Button"];
export type Snooze = Schemas["Snooze"];
export type Notice = Schemas["Notice"];
export type PanelRead = Schemas["Panel"];
export type ItemPanel = Schemas["ItemPanel"];
export type Link = Schemas["Link"];
export type HistoryLine = Schemas["HistoryLine"];
export type Viewer = Schemas["Viewer"];
export type Person = Schemas["Person"];
/** A write's body: its op and the keys it needs; the server fills in the rest with their defaults. */
export type Body = Partial<Schemas["Body"]> & { op: string };
export type Wrote = Schemas["Wrote"];
export type Saved = Schemas["Saved"];
export type Refused = Schemas["Refused"];

/** What a read answered, with the query string it was asked with, so the app writes back only a current canonical. */
export type Read<T> = T & { asked: string };

function nothing(what: string): never {
  throw new HttpError(500, `the ${what} answered nothing`);
}

export async function readList(page: DayPageName, query: string): Promise<Read<DayList>> {
  const { data } =
    page === "queue"
      ? await api.GET("/api/web/queue", { querySerializer: () => query })
      : await api.GET("/api/web/ledger", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("list's read");
}

export async function readPanel(page: DayPageName, query: string): Promise<Read<PanelRead>> {
  const { data } =
    page === "queue"
      ? await api.GET("/api/web/queue/panel", { querySerializer: () => query })
      : await api.GET("/api/web/ledger/panel", { querySerializer: () => query });
  return data ? { ...data, asked: query } : nothing("panel's read");
}

export async function readText(query: string): Promise<Viewer> {
  const { data } = await api.GET("/api/web/ledger/text", { querySerializer: () => query });
  return data ?? nothing("viewer's read");
}

export async function readWho(): Promise<Person[]> {
  const { data } = await api.GET("/api/web/ledger/who");
  return data?.people ?? nothing("names' read");
}

export async function changed(version: string): Promise<{ changed: boolean; version: string }> {
  const { data } = await api.GET("/api/web/ledger/changed", { params: { query: { v: version } } });
  return data ?? nothing("poll");
}

export async function write(body: Body): Promise<Wrote> {
  const { data } = await api.POST("/api/web/ledger/write", { body: body as Schemas["Body"] });
  return data ?? nothing("write");
}

export async function save(body: Body): Promise<Saved> {
  const { data } = await api.POST("/api/web/ledger/save", { body: body as Schemas["Body"] });
  return data ?? nothing("save");
}

export async function openMaterial(page: DayPageName, item: string, ref: string, reveal: boolean): Promise<Notice> {
  const { data } = await api.POST("/api/web/ledger/open", { body: { page, item, ref, reveal } });
  return data?.notice ?? nothing("open");
}

/** The items a refused add looks like, from a refusal's body. */
export function alikesOf(error: unknown): Row[] {
  if (!(error instanceof HttpError)) return [];
  const body = error.body;
  if (body && typeof body === "object" && "alikes" in body && Array.isArray(body.alikes)) return body.alikes as Row[];
  return [];
}

/** A failure's words, as the page shows them beside what it concerns. */
export function wordsOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Something failed in Docket.";
}
