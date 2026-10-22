/**
 * The Recording page's read under /api/web (DR-0709, DR-0776): the recording its URL names, its summary and
 * transcripts, and why no audio plays or the player's source, with the canonical URL for what it read (DR-0792). A
 * value the URL can't hold throws an HttpError with the server's words.
 */
import type { components } from "@/api/schema";
import { api } from "@/lib/api";
import { HttpError } from "@/lib/query";

type Schemas = components["schemas"];
export type RecordingRead = Schemas["RecordingRead"];
export type RecordingView = Schemas["RecordingView"];
export type TranscriptView = Schemas["TranscriptView"];
export type TranscriptLine = Schemas["TranscriptLine"];
export type TranscriptPair = Schemas["TranscriptPair"];
export type PairRow = Schemas["PairRow"];

/** What a read answered, with the query string it was asked with. */
export type Read<T> = T & { asked: string };

export async function readRecording(query: string): Promise<Read<RecordingRead>> {
  const { data } = await api.GET("/api/web/recording", { querySerializer: () => query });
  if (data === undefined) throw new HttpError(500, "the recording's read answered nothing");
  return { ...data, asked: query };
}
