/**
 * The results (DR-0775): a group per recording, thread, record or doc under a sticky head with its kind, title, source
 * and time, its summary first when a doc covers it, then its hits as ruled rows. A row reads at a glance: where in the
 * group the hit is, who and when, the snippet with the words search found under the highlighter, and below it the
 * locator, why it was found, what it mentions and, for a transcript, its place on the Recording page. The selected
 * row holds the focus and the green bar; a plain click opens it beside the results.
 */
import {
  AudioLinesIcon,
  ChevronRightIcon,
  FileTextIcon,
  type LucideIcon,
  MessagesSquareIcon,
  ScrollTextIcon,
} from "lucide-react";
import type * as React from "react";
import { AppLink, plainClick } from "@/components/app-link";
import { Locator, Snippet } from "@/components/doc";
import { cn } from "@/lib/utils";
import { parseSearch } from "@/lib/search";
import type { HitGroup, HitLine, Results } from "./api";
import { hitOf } from "./url";

export const ICONS: Record<HitLine["icon"], LucideIcon> = {
  recording: AudioLinesIcon,
  thread: MessagesSquareIcon,
  record: ScrollTextIcon,
  doc: FileTextIcon,
};

/** A row of the list: a group's summary or one of its hits, with the URL that opens it beside the results. */
export interface ResultRow {
  /** The hit and lines its URL opens, which the open panel's URL shares. */
  key: string;
  url: string;
  locator: string;
  hit?: HitLine;
}

/** The row a URL from the server opens: its hit and lines. */
export function keyOf(url: string): string {
  return hitOf(parseSearch(url.split("?")[1]?.split("#")[0] ?? "")) ?? url;
}

/** Every row, in the list's order: j and k move through them. */
export function rowsOf(results: Results | null | undefined): ResultRow[] {
  const out: ResultRow[] = [];
  for (const group of results?.groups ?? []) {
    if (group.summary) out.push({ key: keyOf(group.summary.url), url: group.summary.url, locator: group.summary.id });
    for (const hit of group.hits) out.push({ key: keyOf(hit.url), url: hit.url, locator: hit.locator, hit });
  }
  return out;
}

interface RowState {
  selected: string | undefined;
  open: string | undefined;
  onSelect: (key: string) => void;
  onOpen: (row: ResultRow) => void;
}

function Row({
  row,
  state,
  tabbable,
  children,
  below,
}: {
  row: ResultRow;
  state: RowState;
  tabbable: boolean;
  children: React.ReactNode;
  below?: React.ReactNode;
}) {
  const selected = state.selected === row.key;
  const opened = state.open === row.key;
  return (
    <li
      data-row={row.key}
      data-locator={row.locator}
      tabIndex={tabbable ? 0 : -1}
      aria-current={selected ? "true" : undefined}
      onFocus={(event) => {
        if (event.target === event.currentTarget) state.onSelect(row.key);
      }}
      className={cn(
        "group/row relative border-b border-rule text-sm outline-offset-[-2px]",
        selected ? "bg-selection" : "bg-sheet hover:bg-hover",
      )}
    >
      {selected && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      <a
        href={row.url}
        tabIndex={-1}
        aria-current={opened ? "page" : undefined}
        onClick={(event) => {
          if (!plainClick(event)) return;
          event.preventDefault();
          state.onOpen(row);
        }}
        className="block cursor-default px-3 pt-2 pb-1 text-ink outline-none sm:px-4"
      >
        {children}
      </a>
      {below && <div className="px-3 pb-2 sm:px-4">{below}</div>}
    </li>
  );
}

function HitBody({ hit }: { hit: HitLine }) {
  return (
    <>
      <span className="flex min-w-0 items-baseline gap-x-3 gap-y-0.5 text-xs text-ink-muted">
        {hit.stretch && <span className="shrink-0 font-mono font-semibold text-ink tabular-nums">{hit.stretch}</span>}
        {hit.who && (
          <span dir="auto" className="min-w-0 truncate">
            {hit.who}
          </span>
        )}
        {hit.when && <span className="ml-auto shrink-0 tabular-nums">{hit.when}</span>}
      </span>
      <Snippet pieces={hit.snippet} className="mt-0.5 line-clamp-3 block text-sm break-words" />
    </>
  );
}

function HitBelow({ hit }: { hit: HitLine }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
      <Locator value={hit.locator} />
      {hit.found && <span className="rounded-sm border border-rule px-1.5 text-ink-muted">{hit.found}</span>}
      {hit.entities.length > 0 && <span dir="auto">mentions {hit.entities.join(", ")}</span>}
      {hit.recording && (
        <AppLink
          href={hit.recording}
          className="tap inline-flex min-h-6 items-center gap-1 text-green underline-offset-4 hover:underline"
        >
          <AudioLinesIcon aria-hidden className="size-3.5" />
          On the Recording page
        </AppLink>
      )}
      {hit.also.length > 0 && (
        <details className="group/also basis-full">
          <summary className="tap inline-flex min-h-6 cursor-default items-center gap-1 rounded-sm hover:text-ink">
            <ChevronRightIcon aria-hidden className="size-3.5 transition-transform group-open/also:rotate-90" />
            and {hit.also.length} more on the same stretch
          </summary>
          <ul className="grid gap-0.5 pt-1 pl-5">
            {hit.also.map((also) => (
              <li key={also}>
                <Locator value={also} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Group({ group, at, rows, state }: { group: HitGroup; at: number; rows: RowState; state: TabState }) {
  const Icon = ICONS[group.icon];
  const id = `group-${at}`;
  return (
    <section aria-labelledby={id}>
      <h2
        id={id}
        className="sticky top-0 z-10 flex min-h-8 flex-wrap items-baseline gap-x-2 border-b border-rule bg-paper px-3 py-1.5 sm:px-4"
      >
        <span className="inline-flex shrink-0 items-center gap-1 self-center text-xs text-ink-muted">
          <Icon aria-hidden className="size-3.5" />
          {group.kind}
        </span>
        <span dir="auto" className="min-w-0 text-sm font-semibold break-words">
          {group.title}
        </span>
        <span className="ml-auto shrink-0 text-xs font-normal text-ink-muted tabular-nums">
          {group.source}
          {group.when && `, ${group.when}`}
        </span>
      </h2>
      <ul>
        {group.summary && (
          <Row
            row={{ key: keyOf(group.summary.url), url: group.summary.url, locator: group.summary.id }}
            state={rows}
            tabbable={state.tabbable(keyOf(group.summary.url))}
          >
            <span className="flex min-w-0 items-baseline gap-2">
              <FileTextIcon aria-hidden className="size-3.5 shrink-0 self-center text-ink-muted" />
              <span className="shrink-0 text-xs text-ink-muted">summary</span>
              <span dir="auto" className="min-w-0 truncate font-semibold">
                {group.summary.title}
              </span>
              <span className="ml-auto shrink-0 text-xs text-ink-muted">
                {group.summary.type}, rev {group.summary.rev}
              </span>
            </span>
          </Row>
        )}
        {group.hits.map((hit) => {
          const row: ResultRow = { key: keyOf(hit.url), url: hit.url, locator: hit.locator, hit };
          return (
            <Row key={row.key} row={row} state={rows} tabbable={state.tabbable(row.key)} below={<HitBelow hit={hit} />}>
              <HitBody hit={hit} />
            </Row>
          );
        })}
      </ul>
    </section>
  );
}

interface TabState {
  tabbable: (key: string) => boolean;
}

/** The groups and their rows; Tab reaches the selected row, else the first. */
export function ResultGroups({
  results,
  selected,
  open,
  onSelect,
  onOpen,
}: {
  results: Results;
  selected: string | undefined;
  open: string | undefined;
  onSelect: (key: string) => void;
  onOpen: (row: ResultRow) => void;
}) {
  const order = rowsOf(results);
  const current = selected !== undefined && order.some((r) => r.key === selected) ? selected : order[0]?.key;
  const rows: RowState = { selected, open, onSelect, onOpen };
  const tabs: TabState = { tabbable: (key) => key === current };
  return (
    <div>
      {results.groups.map((group, at) => (
        // A group's place in the answer is what tells it apart: two groups may share a title.
        <Group key={`${at}-${group.title}`} group={group} at={at} rows={rows} state={tabs} />
      ))}
    </div>
  );
}
