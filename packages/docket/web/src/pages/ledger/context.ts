/**
 * What the Ledger's parts call to write: the page owns the writes, so it can guard unsaved text, move the selection,
 * set the notice and refetch, and each part shows a refusal beside itself.
 */
import { createContext, useContext } from "react";
import type { Body, Row, Saved, Wrote } from "./api";

/** What a write came to: its answer, or its refusal's words and look-alikes; "" when the owner called it off. */
export type Outcome<T> = { ok: true; answer: T } | { ok: false; error: string; alikes: Row[] };

export interface How {
  /** The ticked rows, from the act bar: a dry run first, the rows the writer doesn't take skipped. */
  bulk?: boolean;
  /** Unsaved keys the write itself sends, which the leave guard doesn't ask about. */
  except?: string[];
}

export interface LedgerActions {
  /** One write, by user via page; a call moves the selection to the row after the acted ones. */
  write: (body: Body, how?: How) => Promise<Outcome<Wrote>>;
  /** A field saved on change: the row and the panel are put in place, nothing else refetched. */
  save: (body: Body) => Promise<Outcome<Saved>>;
  /** A material opened with its app, or shown in its folder. */
  open: (item: string, ref: string, reveal: boolean) => Promise<Outcome<null>>;
  /** Whether a write is in flight: every button that writes waits for it. */
  pending: boolean;
  streams: string[];
  kinds: string[];
}

export const Actions = createContext<LedgerActions | null>(null);

export function useActions(): LedgerActions {
  const found = useContext(Actions);
  if (!found) throw new Error("the Ledger's parts sit inside its page");
  return found;
}

/** A refused outcome's words, or "" for one that wrote or that the owner called off. */
export function refusal(outcome: Outcome<unknown>): string {
  return outcome.ok ? "" : outcome.error;
}
