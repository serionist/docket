/**
 * The Ledger's list (DR-0666, DR-0710): its groups under sticky heads with their counts and Select all, and its
 * rows, ruled, never carded. A row reads at a glance: its number and kind, its title, the call it holds as an ink
 * stamp, who, the stream, and how near its due is, late in red ink. The selected row holds the focus and shows its
 * quick buttons, the four calls by their keys in their own inks, done and drop; a row shows them on hover too.
 */
import { CheckIcon, CircleSlashIcon, XIcon } from "lucide-react";
import { Empty } from "@/components/shell/page";
import { isVerdict, Stamp, STAMP_KEY } from "@/components/stamp";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { type Search, stringifySearch } from "@/lib/search";
import { cn } from "@/lib/utils";
import type { Body, Group, Ledger, Row } from "./api";
import { quiet, withSearch } from "./url";

const TONE: Record<string, string> = {
  late: "font-semibold text-red",
  today: "font-semibold text-ink",
  soon: "text-ink",
  "": "text-ink-muted",
};

export interface ListProps {
  data: Ledger;
  search: Search;
  selected: string | undefined;
  open: string | undefined;
  ticked: ReadonlySet<string>;
  pending: boolean;
  /** One column, a finger's targets: the quick buttons on a line of the selected row's own. */
  narrow: boolean;
  onSelect: (ref: string) => void;
  onOpen: (ref: string) => void;
  onTick: (refs: string[], on: boolean) => void;
  onCall: (row: Row, body: Partial<Body>) => void;
  onDrop: (row: Row) => void;
  onMore: (k: number) => void;
  onNew: () => void;
}

/** A row's own URL: the Ledger with its panel open, for a link to open in a tab of its own. */
function rowHref(search: Search, ref: string): string {
  return `/ledger${stringifySearch(withSearch(quiet(search), { open: ref, at: undefined, drop: undefined, text: undefined }))}`;
}

function Quick({
  row,
  pending,
  onCall,
  onDrop,
  className,
}: {
  row: Row;
  pending: boolean;
  onCall: ListProps["onCall"];
  onDrop: ListProps["onDrop"];
  className?: string;
}) {
  const small = "tap grid size-6 shrink-0 cursor-default place-items-center rounded-sm border text-xs";
  return (
    <div role="group" aria-label={`Calls on ${row.ref}`} className={cn("items-center gap-1", className)}>
      {row.quick.map((button) => (
        <button
          key={button.verdict}
          type="button"
          disabled={pending}
          aria-pressed={button.pressed}
          title={button.title}
          onClick={() => onCall(row, { op: "triage", verdict: button.writes })}
          className={cn(
            small,
            "bg-sheet font-semibold disabled:opacity-50",
            isVerdict(button.verdict) && STAMP_KEY[button.verdict],
          )}
        >
          {button.key}
          <span className="sr-only">
            {" "}
            {button.label} {row.ref}
          </span>
        </button>
      ))}
      {row.triage && (
        <button
          type="button"
          disabled={pending}
          title="Clear the call (0)"
          onClick={() => onCall(row, { op: "triage", verdict: null })}
          className={cn(small, "border-control bg-sheet text-ink-muted hover:bg-hover disabled:opacity-50")}
        >
          <CircleSlashIcon className="size-3.5" aria-hidden />
          <span className="sr-only">No call for {row.ref}</span>
        </button>
      )}
      <button
        type="button"
        disabled={pending}
        title="Done (e)"
        onClick={() => onCall(row, { op: "done" })}
        className={cn(small, "border-control bg-sheet text-ink hover:bg-hover disabled:opacity-50")}
      >
        <CheckIcon className="size-3.5" aria-hidden />
        <span className="sr-only">Done {row.ref}</span>
      </button>
      <button
        type="button"
        disabled={pending}
        title="Drop: say why (Delete)"
        onClick={() => onDrop(row)}
        className={cn(small, "border-red/60 bg-sheet text-red hover:bg-red-wash disabled:opacity-50")}
      >
        <XIcon className="size-3.5" aria-hidden />
        <span className="sr-only">Drop {row.ref}</span>
      </button>
    </div>
  );
}

function RowLine({
  row,
  props,
  tabbable,
}: {
  row: Row;
  props: ListProps;
  /** The row Tab reaches: the selected one, or the first while none is. */
  tabbable: boolean;
}) {
  const selected = row.ref === props.selected;
  const ticked = props.ticked.has(row.ref);
  const opened = row.ref === props.open;
  const meta = [row.ref, row.kind, row.who].filter(Boolean).join(", ");
  const quickShown = selected && row.quick.length > 0;
  return (
    <li
      data-row={row.ref}
      data-triage={row.triage ?? ""}
      tabIndex={tabbable ? 0 : -1}
      aria-current={selected ? "true" : undefined}
      onFocus={() => props.onSelect(row.ref)}
      className={cn(
        "group/row relative border-b border-rule text-sm outline-offset-[-2px]",
        selected ? "bg-selection" : ticked ? "bg-hover" : "bg-sheet hover:bg-hover",
      )}
    >
      {selected && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      <div className="flex min-h-8 items-center gap-2 pr-3 pl-1.5 pointer-coarse:min-h-11 sm:pr-4 sm:pl-2.5">
        {row.callable ? (
          <Checkbox
            checked={ticked}
            tabIndex={tabbable ? 0 : -1}
            onCheckedChange={(on) => props.onTick([row.ref], on === true)}
            aria-label={`Tick ${row.ref}`}
          />
        ) : (
          <span aria-hidden className="size-6 shrink-0 pointer-coarse:size-11" />
        )}
        <a
          href={rowHref(props.search, row.ref)}
          tabIndex={-1}
          onClick={(event) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            props.onOpen(row.ref);
          }}
          aria-current={opened ? "page" : undefined}
          className="flex min-w-0 flex-1 cursor-default items-center gap-3 self-stretch py-1.5 text-ink outline-none"
        >
          <span className="hidden w-14 shrink-0 font-mono text-xs text-ink-muted @lg:block">{row.ref}</span>
          <span className="hidden w-16 shrink-0 text-xs text-ink-muted @lg:block">{row.kind}</span>
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-center gap-2">
              <span className={cn("truncate", row.state !== "open" && !row.woke && "text-ink-muted")} dir="auto">
                {row.title}
              </span>
              {row.meeting && (
                <span className="hidden shrink-0 rounded-sm border border-rule px-1 font-mono text-xs text-ink-muted @md:inline">
                  {row.meeting}
                </span>
              )}
              {row.archived && (
                <span className="shrink-0 rounded-sm border border-rule px-1 text-xs text-ink-muted">archived</span>
              )}
            </span>
            <span className="block truncate text-xs text-ink-muted @lg:hidden" dir="auto">
              {meta}
            </span>
          </span>
          {/* The calls stand in a column of their own, so they read down the list. */}
          <span className="flex shrink-0 @lg:w-16">{isVerdict(row.triage) && <Stamp verdict={row.triage} />}</span>
          <span className="hidden w-28 shrink-0 truncate text-xs text-ink-muted @2xl:block" dir="auto">
            {row.who}
          </span>
          <span className="hidden w-20 shrink-0 truncate text-xs text-ink-muted @4xl:block">{row.stream}</span>
          <span
            title={row.due.title}
            className={cn("w-[4.5rem] shrink-0 text-right text-xs tabular-nums", TONE[row.due.tone] ?? "")}
          >
            {row.due.text}
          </span>
          <span className="hidden w-8 shrink-0 text-right text-xs text-ink-muted tabular-nums @4xl:block">
            <span className="sr-only">open </span>
            {row.age}d
          </span>
        </a>
        {row.quick.length > 0 && !props.narrow && (
          <Quick
            row={row}
            pending={props.pending}
            onCall={props.onCall}
            onDrop={props.onDrop}
            className={cn(
              // Over who and the stream, never over the due: the due and its age keep their place at the right.
              "absolute inset-y-0 right-24 pr-2 pl-3 sm:right-[100px] @4xl:right-36",
              quickShown ? "flex bg-selection" : "hidden bg-hover group-hover/row:flex",
            )}
          />
        )}
      </div>
      {quickShown && props.narrow && (
        <Quick
          row={row}
          pending={props.pending}
          onCall={props.onCall}
          onDrop={props.onDrop}
          className="flex flex-wrap pb-2 pl-12"
        />
      )}
    </li>
  );
}

function GroupBlock({ group, props, tab }: { group: Group; props: ListProps; tab: string | undefined }) {
  const id = `group-${group.key}`;
  const callable = group.rows.filter((r) => r.callable).map((r) => r.ref);
  return (
    <section aria-labelledby={id}>
      <h2
        id={id}
        className="sticky top-0 z-10 flex min-h-8 items-center gap-2 border-b border-rule bg-paper px-3 text-xs font-semibold text-ink-muted sm:px-4"
      >
        <span className={cn(group.tone === "late" && "text-red")}>{group.label}</span>
        <span className="font-normal tabular-nums">{group.total}</span>
        {group.selectable && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-6 px-1.5 text-xs font-normal text-ink-muted"
            onClick={() => props.onTick(callable, true)}
          >
            Select all<span className="sr-only"> in {group.label}</span>
          </Button>
        )}
      </h2>
      <ul>
        {group.rows.map((row) => (
          <RowLine key={row.id} row={row} props={props} tabbable={row.ref === tab} />
        ))}
      </ul>
    </section>
  );
}

export function LedgerRows(props: ListProps) {
  const { list } = props.data;
  const listed = list.groups.flatMap((g) => g.rows.map((r) => r.ref));
  const tab = props.selected !== undefined && listed.includes(props.selected) ? props.selected : listed[0];
  if (!list.groups.length) {
    const blank = list.empty.startsWith("The ledger is empty");
    return (
      <Empty title={blank ? "Nothing in the ledger yet" : list.empty}>
        {blank ? (
          <div className="grid justify-items-start gap-3">
            <p>Claude adds what you owe and await as it syncs. You can add an item here too.</p>
            <Button variant="default" onClick={props.onNew}>
              New item
            </Button>
          </div>
        ) : null}
      </Empty>
    );
  }
  return (
    <>
      {list.groups.map((group) => (
        <GroupBlock key={group.key} group={group} props={props} tab={tab} />
      ))}
      {list.more !== null && (
        <div className="flex items-center gap-3 px-3 py-3 text-xs text-ink-muted sm:px-4">
          <span className="tabular-nums">
            {list.shown} of {list.total}
          </span>
          <Button size="sm" onClick={() => props.onMore(list.more ?? list.shown)}>
            Show more
          </Button>
        </div>
      )}
    </>
  );
}
