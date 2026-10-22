/**
 * What waits at the gate (DR-0380, DR-0784): the filter bar of sources and states, the lead line, the ready bar with
 * a Promote per source and one for all, then a group per source under a sticky head, a ruled row per record. A row
 * reads at a glance: its kind, title, the record's time and how long it waited, its state as a tab in its ink, pending
 * in the owner's ochre since it waits for a call, ready in ledger green, on hold in red ink; below, why it waits. A
 * record a hold covers shows its rid and no title, and doesn't open.
 */
import {
  AudioLinesIcon,
  CalendarIcon,
  FileTextIcon,
  type LucideIcon,
  MessagesSquareIcon,
  ScrollTextIcon,
} from "lucide-react";
import type * as React from "react";
import { AppLink, plainClick } from "@/components/app-link";
import { Locator } from "@/components/doc";
import { Empty } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { StagedGroup, StagedRow, StagingList } from "./api";

const ICONS: Record<string, LucideIcon> = {
  recording: AudioLinesIcon,
  thread: MessagesSquareIcon,
  event: CalendarIcon,
  doc: FileTextIcon,
  record: ScrollTextIcon,
};

const TAB = "inline-flex h-5 shrink-0 items-center rounded-r-sm border-l-[3px] pr-1.5 pl-1 text-xs leading-none";

/** A record's state as a margin tab: what waits for the owner in ochre, what goes next in green, a hold in red. */
export function StateTab({ row }: { row: Pick<StagedRow, "state" | "on_hold" | "why"> }) {
  const held = row.on_hold || row.why === "held-back";
  const word = row.on_hold ? "on hold" : row.state;
  return (
    <span
      data-state={word}
      className={cn(
        TAB,
        held
          ? "border-red bg-red-wash text-red"
          : row.state === "ready"
            ? "border-green bg-selection text-green"
            : "border-me bg-me-wash text-me",
      )}
    >
      {word}
    </span>
  );
}

export interface RowsState {
  selected: string | undefined;
  open: string | undefined;
  onSelect: (key: string) => void;
  onOpen: (row: StagedRow) => void;
}

function Row({ row, rows, tabbable }: { row: StagedRow; rows: RowsState; tabbable: boolean }) {
  const Icon = ICONS[row.icon] ?? ScrollTextIcon;
  const selected = rows.selected === row.key;
  const head = (
    <>
      <span className="flex min-h-5 min-w-0 items-center gap-2">
        <Icon aria-hidden className="size-3.5 shrink-0 text-ink-muted" />
        <span className="shrink-0 text-xs text-ink-muted">{row.kind}</span>
        {row.on_hold ? (
          <Locator value={row.rid} className="text-ink" />
        ) : (
          <span dir="auto" className="min-w-0 truncate font-semibold">
            {row.title || "(no title)"}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          <span className="text-xs text-ink-muted tabular-nums max-sm:hidden">{row.when}</span>
          <StateTab row={row} />
        </span>
      </span>
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-3 pl-5.5 text-xs text-ink-muted">
        <span className={cn("min-w-0", (row.on_hold || row.why === "held-back") && "text-red")}>{row.line}</span>
        <span className="tabular-nums">waiting {row.waited}</span>
        <span className="tabular-nums sm:hidden">{row.when}</span>
        <code className="font-mono">{row.short}</code>
      </span>
    </>
  );
  const box = "block px-3 py-1.5 text-ink outline-none pointer-coarse:min-h-11 sm:px-4";
  return (
    <li
      data-row={row.key}
      data-why={row.why}
      tabIndex={tabbable ? 0 : -1}
      aria-current={selected ? "true" : undefined}
      onFocus={(event) => {
        if (event.target === event.currentTarget) rows.onSelect(row.key);
      }}
      className={cn(
        "relative border-b border-rule text-sm outline-offset-[-2px]",
        selected ? "bg-selection" : "bg-sheet hover:bg-hover",
      )}
    >
      {selected && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      {row.on_hold ? (
        // Nothing of a held record opens: its rid, kind and time are all the page shows (DR-0380).
        <div className={box}>{head}</div>
      ) : (
        <a
          href={row.url}
          tabIndex={-1}
          aria-current={rows.open === row.key ? "page" : undefined}
          onClick={(event) => {
            if (!plainClick(event)) return;
            event.preventDefault();
            rows.onOpen(row);
          }}
          className={cn(box, "cursor-default")}
        >
          {head}
        </a>
      )}
    </li>
  );
}

function Group({ group, rows, current }: { group: StagedGroup; rows: RowsState; current: string | undefined }) {
  const id = `source-${group.source}`;
  return (
    <section aria-labelledby={id}>
      <h3
        id={id}
        className="sticky top-0 z-10 flex h-8 items-center gap-2 border-b border-rule bg-paper px-3 text-xs font-semibold sm:px-4"
      >
        {group.source}
        <span className="font-normal text-ink-muted tabular-nums">{group.counts}</span>
      </h3>
      <ul>
        {group.rows.map((row) => (
          <Row key={row.key} row={row} rows={rows} tabbable={row.key === current} />
        ))}
      </ul>
    </section>
  );
}

/** The rows j and k move through, in the list's order: the ones that open. */
export function rowsOf(view: StagingList | undefined): StagedRow[] {
  return (view?.groups ?? []).flatMap((group) => group.rows.filter((row) => !row.on_hold));
}

/** The bar's chips: one source or all, one state or both; each a link, so each is a URL of its own. */
function Chips({ label, chips }: { label: string; chips: StagingList["source_chips"] }) {
  return (
    <nav aria-label={label} className="shrink-0">
      <ul className="flex gap-0.5">
        {chips.map((chip) => (
          <li key={chip.url}>
            <AppLink
              href={chip.url}
              aria-current={chip.on ? "page" : undefined}
              className={cn(
                "tap flex h-7 items-center rounded-sm px-2 text-sm whitespace-nowrap text-ink-muted hover:bg-hover hover:text-ink",
                chip.on && "bg-hover font-semibold text-ink",
              )}
            >
              {chip.label}
            </AppLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function FilterBar({ view }: { view: StagingList }) {
  return (
    <div className="flex shrink-0 items-center gap-x-4 overflow-x-auto border-b border-rule px-2 py-1.5 sm:px-3">
      <Chips label="Sources" chips={view.source_chips} />
      <span aria-hidden className="h-4 w-px shrink-0 bg-rule" />
      <Chips label="States" chips={view.state_chips} />
    </div>
  );
}

/** What goes at the next promotion, and the buttons that send it to raw: one per source, and one for all. */
function Ready({
  view,
  pending,
  onPromote,
}: {
  view: StagingList;
  pending: boolean;
  onPromote: (source: string | null) => void;
}) {
  if (!view.ready.length) return null;
  return (
    <section
      aria-labelledby="ready-title"
      className="grid gap-1.5 border-b border-rule border-l-2 border-l-green bg-selection/60 px-3 py-2 sm:px-4"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 id="ready-title" className="text-xs font-semibold text-green">
          Ready to promote
        </h2>
        <ul className="flex flex-wrap gap-x-3 text-sm">
          {view.ready.map(({ source, n }) => (
            <li key={source} className="tabular-nums">
              {source} <span className="font-semibold">{n}</span>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-1.5 sm:ml-auto">
          {view.ready.map(({ source }) => (
            <Button key={source} size="sm" disabled={pending} onClick={() => onPromote(source)}>
              Promote {source}
            </Button>
          ))}
          {view.ready.length > 1 && (
            <Button size="sm" variant="default" disabled={pending} onClick={() => onPromote(null)}>
              Promote all
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs text-ink-muted">
        Promotion cuts each record, redacts it with the current rules and commits it to the raw log.
      </p>
    </section>
  );
}

export function WaitingList({
  view,
  rows,
  pending,
  onPromote,
  onMore,
  children,
}: {
  view: StagingList;
  rows: RowsState;
  pending: boolean;
  onPromote: (source: string | null) => void;
  onMore: (href: string) => void;
  /** What follows the records: the held recordings, the files that don't read. */
  children?: React.ReactNode;
}) {
  const order = rowsOf(view);
  const current =
    rows.selected !== undefined && order.some((r) => r.key === rows.selected) ? rows.selected : order[0]?.key;
  return (
    <>
      <p className="border-b border-rule px-3 py-2 text-sm sm:px-4" data-lead>
        {view.lead}
      </p>
      <Ready view={view} pending={pending} onPromote={onPromote} />
      <section aria-labelledby="waiting-title">
        <h2 id="waiting-title" className="sr-only">
          Waiting
        </h2>
        {view.groups.length ? (
          view.groups.map((group) => <Group key={group.source} group={group} rows={rows} current={current} />)
        ) : (
          <Empty title={view.narrowed ? "Nothing here waits at the gate" : "Nothing waits at the gate"}>
            {!view.narrowed && <p>Records a sync or an import stages show here, for you to classify and promote.</p>}
          </Empty>
        )}
        {view.more && (
          <div className="px-3 py-3 sm:px-4">
            <Button size="sm" onClick={() => view.more && onMore(view.more)}>
              Show more
            </Button>
          </div>
        )}
      </section>
      {children}
    </>
  );
}
