/**
 * The round and what stands around it (DR-0468, DR-0474, DR-0786): the lead line with the review minutes, when the
 * next round is due and its button; the round's cards, a ruled row each with its number and kind, the name or
 * spelling it proposes, what search misses, and its state as a tab, open in the owner's ochre since it waits for a
 * call; then what tier 1 applied by itself, the question set before and after, the forms matching leaves out, the log
 * with Undo, and the stoplist with Lift. A value a never-store list holds shows as hidden, with no Undo or Lift.
 */
import { plainClick } from "@/components/app-link";
import { Note } from "@/components/doc";
import { Empty } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { CardRow, CurateView, Lift, LogRow } from "./api";

const TAB = "inline-flex h-5 shrink-0 items-center rounded-r-sm border-l-[3px] pr-1.5 pl-1 text-xs leading-none";
const INK: Record<CardRow["state"], string> = {
  open: "border-me bg-me-wash text-me",
  accepted: "border-green bg-selection text-green",
  done: "border-green bg-selection text-green",
  rejected: "border-control bg-hover text-ink-muted",
};

/** A card's state as a margin tab: open waits for the owner's call, in ochre. */
export function CardState({ state }: { state: CardRow["state"] }) {
  return (
    <span data-state={state} className={cn(TAB, INK[state])}>
      {state}
    </span>
  );
}

function SectionHead({ id, title, count }: { id: string; title: string; count?: number }) {
  return (
    <h2
      id={id}
      className="sticky top-0 z-10 flex h-8 items-center gap-2 border-y border-rule bg-paper px-3 text-xs font-semibold sm:px-4"
    >
      {title}
      {count !== undefined && <span className="font-normal text-ink-muted tabular-nums">{count}</span>}
    </h2>
  );
}

export interface CardsState {
  selected: string | undefined;
  open: string | undefined;
  onSelect: (id: string) => void;
  onOpen: (card: CardRow) => void;
}

function Card({ card, rows, tabbable }: { card: CardRow; rows: CardsState; tabbable: boolean }) {
  const selected = rows.selected === card.id;
  return (
    <li
      data-row={card.id}
      data-state={card.state}
      tabIndex={tabbable ? 0 : -1}
      aria-current={selected ? "true" : undefined}
      onFocus={(event) => {
        if (event.target === event.currentTarget) rows.onSelect(card.id);
      }}
      className={cn(
        "relative border-b border-rule text-sm outline-offset-[-2px]",
        selected ? "bg-selection" : "bg-sheet hover:bg-hover",
      )}
    >
      {selected && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      <a
        href={card.url}
        tabIndex={-1}
        aria-current={rows.open === card.id ? "page" : undefined}
        onClick={(event) => {
          if (!plainClick(event)) return;
          event.preventDefault();
          rows.onOpen(card);
        }}
        className="grid cursor-default gap-0.5 px-3 py-1.5 text-ink outline-none pointer-coarse:min-h-11 sm:px-4"
      >
        <span className="flex min-h-5 min-w-0 items-center gap-2">
          <span className="w-5 shrink-0 text-right text-xs text-ink-muted tabular-nums">{card.n}</span>
          <span className="shrink-0 text-xs text-ink-muted">{card.kind}</span>
          <span dir="auto" className="min-w-0 truncate font-semibold">
            {card.title}
          </span>
          <span className="ml-auto shrink-0">
            <CardState state={card.state} />
          </span>
        </span>
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-3 pl-7 text-xs text-ink-muted">
          <span>{card.missed}</span>
          <span className="flex min-w-0 flex-wrap gap-x-2">
            {card.spellings.map((s, at) => (
              // A spelling may repeat, written the same way by two engines; its place doesn't.
              <span key={at} dir="auto" className="text-ink">
                {s.text} <span className="text-ink-muted">({s.trust})</span>
              </span>
            ))}
          </span>
          <code className="font-mono">{card.id}</code>
        </span>
      </a>
    </li>
  );
}

/** The cards j and k move through, in the round's order. */
export function cardsOf(view: CurateView | undefined): CardRow[] {
  return view?.cards ?? [];
}

export function RoundHead({ view, pending, onStart }: { view: CurateView; pending: boolean; onStart: () => void }) {
  return (
    <div className="grid gap-1.5 border-b border-rule px-3 py-2 sm:px-4">
      <p className="text-sm" data-lead>
        {view.lead}
      </p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <p className="min-w-0 flex-1 text-xs text-ink-muted">
          {/* The header holds the minutes from 640 px; below, they lead this line. */}
          <span className="sm:hidden">{view.minutes.charAt(0).toUpperCase() + view.minutes.slice(1)}. </span>
          {view.due}
          {view.round && " A new round replaces this one; every decision stays in the log."}
        </p>
        <Button size="sm" variant={view.round ? "outline" : "default"} disabled={pending} onClick={onStart}>
          {view.round ? "Start a new round" : "Start a round"}
        </Button>
      </div>
      {view.problems.map((problem) => (
        <Note key={problem} tone="warn">
          {problem}
        </Note>
      ))}
    </div>
  );
}

export function Cards({ view, rows }: { view: CurateView; rows: CardsState }) {
  const order = cardsOf(view);
  const current =
    rows.selected !== undefined && order.some((c) => c.id === rows.selected) ? rows.selected : order[0]?.id;
  return (
    <section aria-labelledby="round-title">
      <SectionHead id="round-title" title="This round" count={view.cards.length} />
      {view.cards.length ? (
        <ul>
          {view.cards.map((card) => (
            <Card key={card.id} card={card} rows={rows} tabbable={card.id === current} />
          ))}
        </ul>
      ) : view.round ? (
        <Empty title="This round has no cards">
          <p>Every name the index holds is known, or rejected.</p>
        </Empty>
      ) : (
        <Empty title="No round yet">
          <p>A round is up to 15 cards Docket mines from what it holds, ranked by what search misses.</p>
        </Empty>
      )}
      {view.left_out && <p className="px-3 py-2 text-xs text-ink-muted sm:px-4">Left out: {view.left_out}.</p>}
    </section>
  );
}

function LogLine({ line, pending, onUndo }: { line: LogRow; pending: boolean; onUndo: (line: LogRow) => void }) {
  return (
    <li
      data-op={line.id}
      className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-rule px-3 py-1.5 text-sm sm:px-4"
    >
      <span className="text-xs text-ink-muted tabular-nums">{line.at}</span>
      <span className="text-xs font-semibold">{line.op}</span>
      <code className="font-mono text-xs break-all">{line.entity}</code>
      <span dir="auto" className="min-w-0 break-words">
        {line.part}
      </span>
      <span className="text-xs text-ink-muted">
        {line.by}, {line.via}
        {line.undone_by && `; undone by ${line.undone_by}`}
      </span>
      {line.unfinished && (
        <span className="inline-flex h-5 items-center rounded-sm border border-me px-1.5 text-xs text-me">
          not finished: the next write commits it
        </span>
      )}
      {line.undo && (
        <Button
          size="sm"
          className="ml-auto"
          disabled={pending}
          aria-label={`Undo ${line.op} ${line.id}`}
          onClick={() => onUndo(line)}
        >
          Undo
        </Button>
      )}
    </li>
  );
}

export function Applied({
  view,
  pending,
  onUndo,
}: {
  view: CurateView;
  pending: boolean;
  onUndo: (line: LogRow) => void;
}) {
  return (
    <section aria-labelledby="applied-title">
      <SectionHead id="applied-title" title="Applied by themselves" count={view.applied.length} />
      {view.applied.length ? (
        <ol>
          {view.applied.map((line) => (
            <LogLine key={line.id} line={line} pending={pending} onUndo={onUndo} />
          ))}
        </ol>
      ) : (
        <p className="px-3 py-2 text-sm text-ink-muted sm:px-4">
          Nothing was applied by itself{view.round ? " since this round started" : ""}.
        </p>
      )}
      {view.skipped.length > 0 && (
        <div className="grid gap-1 px-3 py-2 text-sm sm:px-4">
          <p className="text-xs text-ink-muted">Left out, since Docket won't write them:</p>
          <ul className="grid list-disc gap-0.5 pl-5">
            {view.skipped.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      {view.tier1 && <p className="px-3 py-2 text-xs text-ink-muted sm:px-4">{view.tier1}</p>}
    </section>
  );
}

export function Questions({ view }: { view: CurateView }) {
  if (!view.measures) return null;
  return (
    <section aria-labelledby="questions-title">
      <SectionHead id="questions-title" title="Your questions" />
      <div className="overflow-x-auto px-3 py-2 sm:px-4">
        <table className="text-sm tabular-nums" data-measures>
          <caption className="sr-only">Recall at 10 and MRR before this round and after its last card</caption>
          <thead>
            <tr className="text-left text-xs text-ink-muted">
              <th scope="col" className="py-1 pr-6 font-normal">
                Kind
              </th>
              <th scope="col" className="py-1 pr-6 font-normal">
                Before
              </th>
              <th scope="col" className="py-1 font-normal">
                After
              </th>
            </tr>
          </thead>
          <tbody>
            {view.measures.rows.map(({ kind, before, after }) => (
              <tr key={kind} className="border-t border-rule">
                <th scope="row" className="py-1 pr-6 text-left font-semibold">
                  {kind}
                </th>
                <td className="py-1 pr-6">{before}</td>
                <td className={cn("py-1", !after && "text-ink-muted")}>{after || "after the last card"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Dropped({ view }: { view: CurateView }) {
  if (!view.dropped.length) return null;
  return (
    <section aria-labelledby="dropped-title">
      <SectionHead id="dropped-title" title="Forms not matched" count={view.dropped.length} />
      <div className="grid gap-1 px-3 py-2 text-sm sm:px-4">
        <p className="text-xs text-ink-muted">
          Stem and case forms matching leaves out, so ordinary words don't count as these names.
        </p>
        <ul className="grid gap-0.5">
          {view.dropped.map((d) => (
            <li key={d} dir="auto" data-dropped>
              {d}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export function Log({ view, pending, onUndo }: { view: CurateView; pending: boolean; onUndo: (line: LogRow) => void }) {
  return (
    <section aria-labelledby="log-title">
      <SectionHead id="log-title" title="Log" count={view.log.length} />
      {view.log.length ? (
        <ol>
          {view.log.map((line) => (
            <LogLine key={line.id} line={line} pending={pending} onUndo={onUndo} />
          ))}
        </ol>
      ) : (
        <p className="px-3 py-2 text-sm text-ink-muted sm:px-4">No change to an entity is logged yet.</p>
      )}
    </section>
  );
}

export function Stoplist({
  lifts,
  pending,
  onLift,
}: {
  lifts: Lift[];
  pending: boolean;
  onLift: (lift: Lift) => void;
}) {
  return (
    <section aria-labelledby="stoplist-title" className="pb-6">
      <SectionHead id="stoplist-title" title="Stoplist" count={lifts.length} />
      {lifts.length ? (
        <>
          <p className="px-3 pt-2 text-xs text-ink-muted sm:px-4">
            What curation won't offer again in that role. Lift a key to have it offered again.
          </p>
          <ul>
            {lifts.map((key) => (
              <li
                key={key.id}
                data-stop={key.id}
                className="flex min-h-9 items-center gap-3 border-b border-rule px-3 py-1 text-sm sm:px-4"
              >
                <code dir="auto" className="min-w-0 font-mono text-xs break-all">
                  {key.shown}
                </code>
                {key.liftable && (
                  <Button
                    size="sm"
                    className="ml-auto"
                    disabled={pending}
                    aria-label={`Lift ${key.shown}`}
                    onClick={() => onLift(key)}
                  >
                    Lift
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="px-3 py-2 text-sm text-ink-muted sm:px-4">Nothing is on the stoplist.</p>
      )}
    </section>
  );
}
