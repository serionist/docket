/**
 * The Ledger's filter bar (DR-0665, DR-0666): the states with their counts, then the words to find and the list
 * view's own filters. The words and who apply with Enter, a choice as it is made; the URL holds each.
 */
import { ListFilterIcon, SearchIcon } from "lucide-react";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { Ledger } from "./api";
import { leaveOnEscape } from "./field";
import type { Param } from "./url";

export const FIND = "ledger-find";
const ANY = "any";
const GROUPS = [
  ["due", "by due"],
  ["stream", "by stream"],
  ["kind", "by kind"],
  ["who", "by person"],
  ["meeting", "by meeting"],
] as const;

type Change = Partial<Record<Param, string | undefined>>;

/** A text filter: its draft until Enter applies it; a newer value from the URL replaces an untouched draft. */
function useDraft(value: string): [string, (next: string) => void] {
  const [draft, setDraft] = React.useState(value);
  const [seen, setSeen] = React.useState(value);
  if (value !== seen) {
    setSeen(value);
    setDraft(value);
  }
  return [draft, setDraft];
}

export function Filters({
  data,
  narrow,
  hrefOf,
  onChange,
}: {
  data: Ledger;
  /** At phone width the list view's filters fold behind a button, the words to find always shown. */
  narrow: boolean;
  hrefOf: (change: Change) => string;
  onChange: (change: Change) => void;
}) {
  const { filters, list } = data;
  const [unfolded, setUnfolded] = React.useState(false);
  const set = [filters.stream, filters.kind, filters.who, filters.group !== "due", filters.archived].filter(Boolean);
  const rest = !narrow || unfolded;
  const [q, setQ] = useDraft(filters.q);
  const [who, setWho] = useDraft(filters.who);
  const enter = (apply: () => void) => (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      apply();
    } else leaveOnEscape(event);
  };
  return (
    <div className="grid shrink-0 gap-2 border-b border-rule px-3 py-2 sm:px-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <nav aria-label="States" className="-ml-1 flex flex-wrap gap-0.5">
          {list.chips.map((chip) => {
            const change: Change = { state: chip.key === "open" ? undefined : chip.key, k: undefined };
            return (
              <a
                key={chip.key}
                href={hrefOf(change)}
                aria-current={chip.on ? "page" : undefined}
                onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
                  event.preventDefault();
                  onChange(change);
                }}
                className={cn(
                  "tap flex h-7 items-center gap-1.5 rounded-sm px-2 text-sm text-ink-muted hover:bg-hover hover:text-ink",
                  chip.on && "bg-hover font-semibold text-ink",
                )}
              >
                {chip.label}
                <span className="text-xs font-normal tabular-nums">{chip.count}</span>
              </a>
            );
          })}
        </nav>
        <p className="hidden text-xs text-ink-muted sm:block">{list.counts}</p>
      </div>
      <div role="search" className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1 basis-56">
          <SearchIcon
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-ink-muted"
          />
          <Input
            id={FIND}
            type="search"
            aria-label="Find"
            placeholder="Find in titles, people, notes, history"
            autoComplete="off"
            spellCheck={false}
            value={q}
            onChange={(event) => setQ(event.target.value)}
            onKeyDown={enter(() => onChange({ q: q.trim() || undefined, k: undefined }))}
            className="pl-8"
          />
        </div>
        {narrow && (
          <Button aria-expanded={unfolded} aria-controls="ledger-filters" onClick={() => setUnfolded(!unfolded)}>
            <ListFilterIcon />
            Filters
            {set.length > 0 && <span className="text-xs text-ink-muted tabular-nums">{set.length}</span>}
          </Button>
        )}
        {rest && (
          <div id="ledger-filters" className="contents">
            <Select
              value={filters.stream || ANY}
              onValueChange={(value) => onChange({ stream: value === ANY ? undefined : value, k: undefined })}
            >
              <SelectTrigger className="w-auto min-w-28" aria-label="Stream">
                <span className="text-ink-muted">Stream</span>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>any</SelectItem>
                {list.streams.map((stream) => (
                  <SelectItem key={stream} value={stream}>
                    {stream}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={filters.kind || ANY}
              onValueChange={(value) => onChange({ kind: value === ANY ? undefined : value, k: undefined })}
            >
              <SelectTrigger className="w-auto min-w-24" aria-label="Kind">
                <span className="text-ink-muted">Kind</span>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>any</SelectItem>
                {data.kinds.map((kind) => (
                  <SelectItem key={kind} value={kind}>
                    {kind}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              aria-label="Who"
              placeholder="Who"
              autoComplete="off"
              dir="auto"
              value={who}
              onChange={(event) => setWho(event.target.value)}
              onKeyDown={enter(() => onChange({ who: who.trim() || undefined, k: undefined }))}
              className="w-32"
            />
            {(filters.state === "open" || filters.state === "all") && (
              <Select
                value={filters.group}
                onValueChange={(value) => onChange({ group: value === "due" ? undefined : value })}
              >
                <SelectTrigger className="w-auto" aria-label="Group">
                  <span className="text-ink-muted">Group</span>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {GROUPS.map(([key, said]) => (
                    <SelectItem key={key} value={key}>
                      {said}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <label className="tap flex h-8 cursor-default items-center gap-1.5 rounded-sm pr-1 text-sm">
              <Checkbox
                checked={filters.archived}
                onCheckedChange={(on) => onChange({ archived: on === true ? "1" : undefined })}
              />
              archived too
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
