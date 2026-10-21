/**
 * The bar over the results (DR-0175, DR-0775): a menu per facet, sources and months that narrow, people and entities
 * that favour, each value a link to the search with it chosen or taken off, counted over the candidates search ranked;
 * the dates, typed as the search tool reads them; then each filter or boost the search holds, a chip that takes it
 * off; and under them how search read the query.
 */
import { CalendarRangeIcon, CheckIcon, ChevronDownIcon, XIcon } from "lucide-react";
import * as React from "react";
import { AppLink } from "@/components/app-link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { leaveOnEscape } from "@/pages/day/field";
import type { Chosen, Facet } from "./api";

const VERB: Record<string, string> = {
  sources: "Narrow to a source",
  people: "Favour a person",
  entities: "Favour a name or a term",
  time: "Narrow to a month",
};

function FacetMenu({ facet, candidates }: { facet: Facet; candidates: number }) {
  const chosen = facet.items.filter((item) => item.on).length;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" className={cn(chosen > 0 && "border-green")} data-facet={facet.key}>
          {facet.title}
          {chosen > 0 && <span className="text-xs font-semibold text-green tabular-nums">{chosen}</span>}
          <ChevronDownIcon aria-hidden className="text-ink-muted" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" aria-label={facet.title}>
        <p className="border-b border-rule px-3 py-2 text-xs font-semibold text-ink-muted">
          {VERB[facet.key] ?? facet.title}
          {facet.favours && <span className="font-normal">: ranked higher, nothing left out</span>}
        </p>
        <ul className="max-h-72 overflow-y-auto py-1">
          {facet.items.map((item) => (
            <li key={`${item.label}-${item.url}`}>
              <AppLink
                href={item.url}
                aria-current={item.on ? "true" : undefined}
                className={cn(
                  "flex min-h-8 items-center gap-2 px-3 text-sm hover:bg-hover pointer-coarse:min-h-11",
                  item.on && "font-semibold",
                )}
              >
                <CheckIcon aria-hidden className={cn("size-4 shrink-0 text-green", !item.on && "invisible")} />
                <span dir="auto" className="min-w-0 flex-1 truncate">
                  {item.label}
                </span>
                {item.note && <span className="shrink-0 text-xs font-normal text-ink-muted">{item.note}</span>}
                <span className="shrink-0 text-xs font-normal text-ink-muted tabular-nums">{item.count}</span>
              </AppLink>
            </li>
          ))}
        </ul>
        <p className="border-t border-rule px-3 py-2 text-xs text-ink-muted">
          Counts are over the {candidates} best {candidates === 1 ? "match" : "matches"}, not the whole vault.
        </p>
      </PopoverContent>
    </Popover>
  );
}

/** Since and until as the search tool reads them: a date, a time, or words such as last week. */
function DatesMenu({
  since,
  until,
  onApply,
}: {
  since: string;
  until: string;
  onApply: (dates: { since: string; until: string }) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [from, setFrom] = React.useState(since);
  const [to, setTo] = React.useState(until);
  const set = Boolean(since || until);
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setFrom(since);
          setTo(until);
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <Button size="sm" className={cn(set && "border-green")}>
          <CalendarRangeIcon aria-hidden className="text-ink-muted" />
          Dates
          <ChevronDownIcon aria-hidden className="text-ink-muted" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72" aria-label="Dates">
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            setOpen(false);
            onApply({ since: from, until: to });
          }}
        >
          <div className="grid gap-1.5">
            <Label htmlFor="since">Since</Label>
            <Input
              id="since"
              value={from}
              placeholder="2026-10-01, or last week"
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              onChange={(event) => setFrom(event.target.value)}
              onKeyDown={leaveOnEscape}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="until">Until</Label>
            <Input
              id="until"
              value={to}
              placeholder="2026-10-31"
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              onChange={(event) => setTo(event.target.value)}
              onKeyDown={leaveOnEscape}
            />
          </div>
          <p className="text-xs text-ink-muted">A day, a time such as 2026-10-07T12:30, or words such as yesterday.</p>
          <Button type="submit" variant="default" className="justify-self-start">
            Apply the dates
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}

/** A filter or a boost the search holds, a link to the search without it. */
function Chip({ chip }: { chip: Chosen }) {
  return (
    <AppLink
      href={chip.url}
      aria-label={`${chip.label}: take it off`}
      className="tap inline-flex h-7 shrink-0 items-center gap-1 rounded-sm border border-green/60 bg-selection pr-1 pl-2 text-sm whitespace-nowrap hover:border-green"
    >
      <span dir="auto">{chip.label}</span>
      <XIcon aria-hidden className="size-3.5 text-ink-muted" />
    </AppLink>
  );
}

export function FacetBar({
  facets,
  chips,
  candidates,
  since,
  until,
  reading,
  onDates,
}: {
  facets: Facet[];
  chips: Chosen[];
  candidates: number;
  since: string;
  until: string;
  reading: string[];
  onDates: (dates: { since: string; until: string }) => void;
}) {
  return (
    <div className="grid shrink-0 gap-1.5 border-b border-rule px-3 py-2 sm:px-4">
      {/* At phone width one row that scrolls on its own, so the results start high on the screen. */}
      <div
        className="-mx-3 flex items-center gap-1.5 overflow-x-auto px-3 max-sm:pb-1 sm:mx-0 sm:flex-wrap sm:px-0"
        role="group"
        aria-label="Narrow or favour"
      >
        {facets.map((facet) => (
          <FacetMenu key={facet.key} facet={facet} candidates={candidates} />
        ))}
        <DatesMenu since={since} until={until} onApply={onDates} />
        {chips.length > 0 && (
          <ul className="flex shrink-0 gap-1.5 sm:flex-wrap" aria-label="Filters and boosts">
            {chips.map((chip) => (
              <li key={chip.url} className="flex">
                <Chip chip={chip} />
              </li>
            ))}
          </ul>
        )}
      </div>
      {reading.length > 0 && (
        <ul aria-label="How search read the query" className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-ink-muted">
          {reading.map((fact) => (
            <li key={fact} dir="auto">
              {fact}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
