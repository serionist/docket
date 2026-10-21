/**
 * The Briefs page's read and write under /api/web (DR-0709, DR-0780, DR-0781): the page its URL names, with the
 * canonical URL for what it read (DR-0704), and the owner's pick on a waiting decision at the rev the page showed. A
 * value the URL or the body can't hold, a pick refused and a decision that moved since all throw an HttpError with the
 * server's words, a moved one with 409.
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";

type Schemas = components["schemas"];
export type BriefsRead = Schemas["BriefsRead"];
export type BriefsView = Schemas["BriefsView"];
export type BriefRow = Schemas["BriefRow"];
export type BriefGroup = Schemas["BriefGroup"];
export type BriefLine = Schemas["BriefLine"];
export type BriefLink = Schemas["BriefLink"];
export type RawView = Schemas["RawView"];
export type DecisionsPart = Schemas["DecisionsPart"];
export type Card = Schemas["Card"];
export type Pick = Schemas["Pick"];
export type Decide = Schemas["Decide"];
export type Decided = Schemas["Decided"];

/** What a read answered, with the query string it was asked with. */
export type Read<T> = T & { asked: string };

export async function readBriefs(query: string): Promise<Read<BriefsRead>> {
  const { data } = await api.GET("/api/web/briefs", { querySerializer: () => query });
  if (data === undefined) throw new HttpError(500, "the briefs' read answered nothing");
  return { ...data, asked: query };
}

export async function decide(body: Decide): Promise<Decided> {
  const { data } = await api.POST("/api/web/briefs/decision", { body });
  if (data === undefined) throw new HttpError(500, "the decision's write answered nothing");
  return data;
}
