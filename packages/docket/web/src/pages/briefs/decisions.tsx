/**
 * An entity's decisions (DR-0781): Waiting for you, each proposed decision with its picks, those that contradict a
 * decision first, each with what it contradicts or would replace under it; Decided, each current one with what it
 * superseded and a rejected candidate that contradicted it folded under it; Rejected, folded. A proposed decision is
 * only ever under Waiting, never among the decided (DR-0565), and only the owner's pick here settles one: Confirm or
 * Reject, or one of three for a candidate that contradicts a decision. A pick carries the rev the page shows.
 */
import { ChevronRightIcon } from "lucide-react";
import type * as React from "react";
import { AppLink } from "@/components/app-link";
import { Note } from "@/components/doc";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Card, DecisionsPart, Pick } from "./api";
import { Cites, StandingBadge } from "./parts";

export const DECISIONS_TITLE = "decisions-title";

export interface Picking {
  /** The decision whose pick is on its way, whose buttons wait for its answer. */
  pending: string | null;
  onPick: (card: Card, pick: Pick) => void;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function Folded({ summary, children }: { summary: string; children: React.ReactNode }) {
  return (
    <details className="group/fold">
      <summary className="inline-flex min-h-6 cursor-default items-center gap-1 rounded-sm text-xs text-ink-muted hover:text-ink pointer-coarse:min-h-11">
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform group-open/fold:rotate-90" />
        {summary}
      </summary>
      <div className="mt-1 ml-1.5 grid gap-1 border-l border-rule pl-3">{children}</div>
    </details>
  );
}

function Lines({ card }: { card: Card }) {
  if (!card.lines.length) return null;
  return (
    <ul className="grid gap-1">
      {card.lines.map((line) => (
        <li key={line.n} className="grid gap-0.5">
          <span
            dir="auto"
            className={cn(
              "text-sm break-words",
              line.style === "bullet" && "before:mr-1.5 before:text-ink-muted before:content-['•']",
              line.style === "heading" && "font-semibold",
              line.hidden && "text-ink-muted italic",
            )}
          >
            {line.body}
          </span>
          <Cites anchors={line.anchors} refs={line.refs} className={line.style === "bullet" ? "pl-3.5" : undefined} />
        </li>
      ))}
    </ul>
  );
}

function Picks({ card, picking }: { card: Card; picking: Picking }) {
  if (!card.picks.length) return null;
  const busy = picking.pending === card.id;
  return (
    <div role="group" aria-label={`Your call on: ${card.title}`} className="flex flex-wrap gap-1.5 pt-1">
      {card.picks.map((pick) => (
        <Button
          key={pick.value}
          variant={pick.primary ? "default" : "outline"}
          size="sm"
          disabled={picking.pending !== null}
          aria-busy={busy || undefined}
          onClick={() => picking.onPick(card, pick)}
        >
          {pick.label}
        </Button>
      ))}
    </div>
  );
}

function Decision({
  card,
  picking,
  depth = 0,
}: {
  card: Card;
  picking: Picking;
  /** How far it is folded under another, which sets its heading's level. */
  depth?: number;
}) {
  const Title = depth === 0 ? "h4" : "h5";
  const waiting = card.picks.length > 0;
  return (
    <article
      data-decision={card.id}
      data-standing={card.standing}
      className={cn("relative grid gap-1.5", depth === 0 && "px-3 py-3 sm:px-4", waiting && "bg-sheet")}
    >
      {depth === 0 && waiting && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-me" />}
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <StandingBadge standing={card.standing} />
        <Title dir="auto" className="min-w-0 text-sm font-semibold break-words">
          {card.title}
        </Title>
      </header>
      <p className="flex flex-wrap items-center gap-x-3 text-xs text-ink-muted">
        <span className="tabular-nums">said {card.said}</span>
        <span className="tabular-nums">rev {card.rev}</span>
        <span dir="auto">{card.by}</span>
        <AppLink
          href={card.verify}
          className="inline-flex min-h-6 items-center text-green underline-offset-4 hover:underline pointer-coarse:min-h-11"
        >
          Verify on the Search page
        </AppLink>
      </p>
      {card.note && <Note tone="warn">{card.note}</Note>}
      <Lines card={card} />
      <Picks card={card} picking={picking} />
      {card.against.length > 0 && (
        <div className="mt-1 grid gap-1 rounded-sm border border-rule bg-paper px-3 py-2">
          <p className="text-xs font-semibold text-ink-muted">
            {card.picks.length === 3 ? "It contradicts" : "It would replace"}
          </p>
          {card.against.map((other) => (
            <Decision key={other.id} card={other} picking={picking} depth={depth + 1} />
          ))}
        </div>
      )}
      {card.folded.length > 0 && (
        <Folded summary={`It replaced ${plural(card.folded.length, "earlier decision", "earlier decisions")}`}>
          {card.folded.map((other) => (
            <Decision key={other.id} card={other} picking={picking} depth={depth + 1} />
          ))}
        </Folded>
      )}
      {card.contradicted.length > 0 && (
        <Folded summary={`Contradicted ${plural(card.contradicted.length, "time", "times")}; it stands`}>
          {card.contradicted.map((other) => (
            <Decision key={other.id} card={other} picking={picking} depth={depth + 1} />
          ))}
        </Folded>
      )}
    </article>
  );
}

function Group({
  id,
  title,
  count,
  children,
  className,
}: {
  id: string;
  title: string;
  count: number;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section aria-labelledby={id} data-count={count} className={className}>
      <h3
        id={id}
        className="flex h-8 items-center gap-2 border-y border-rule bg-paper px-3 text-xs font-semibold sm:px-4"
      >
        {title}
        <span className="font-normal text-ink-muted tabular-nums">{count}</span>
      </h3>
      {children}
    </section>
  );
}

export function Decisions({
  part,
  refused,
  picking,
}: {
  part: DecisionsPart;
  /** Why the last pick wrote nothing, in the writer's words. */
  refused: string;
  picking: Picking;
}) {
  return (
    <section aria-labelledby={DECISIONS_TITLE} className="grid gap-2 border-t border-rule pt-3 pb-6">
      {/* The pick's answer gives the heading the focus, as the decision it was on may have moved. */}
      <h2 id={DECISIONS_TITLE} tabIndex={-1} className="px-3 text-xs font-semibold text-ink-muted outline-none sm:px-4">
        Decisions
      </h2>
      {refused && <Refusal className="mx-3 sm:mx-4">{refused}</Refusal>}
      {part.hidden_note && <Note className="mx-3 sm:mx-4">{part.hidden_note}</Note>}
      {part.empty && <p className="px-3 text-sm text-ink-muted sm:px-4">{part.empty}</p>}
      {part.waiting.length > 0 && (
        <Group id="decisions-waiting" title="Waiting for you" count={part.waiting.length} className="bg-me-wash/40">
          <p className="px-3 pt-2 text-xs text-ink-muted sm:px-4">
            Only your pick here confirms or rejects one. A proposed decision isn't decided.
          </p>
          <ul>
            {part.waiting.map((card) => (
              <li key={card.id} className="border-b border-rule last:border-b-0">
                <Decision card={card} picking={picking} />
              </li>
            ))}
          </ul>
        </Group>
      )}
      {part.current.length > 0 && (
        <Group id="decisions-current" title="Decided" count={part.current.length}>
          <ul>
            {part.current.map((card) => (
              <li key={card.id} className="border-b border-rule last:border-b-0">
                <Decision card={card} picking={picking} />
              </li>
            ))}
          </ul>
        </Group>
      )}
      {part.rejected.length > 0 && (
        <details className="group/fold">
          <summary className="flex min-h-8 cursor-default items-center gap-2 border-y border-rule bg-paper px-3 hover:bg-hover pointer-coarse:min-h-11 sm:px-4">
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 text-ink-muted transition-transform group-open/fold:rotate-90"
            />
            <h3 className="text-xs font-semibold">Rejected</h3>
            <span className="text-xs text-ink-muted tabular-nums">{part.rejected.length}</span>
          </summary>
          <ul>
            {part.rejected.map((card) => (
              <li key={card.id} className="border-b border-rule last:border-b-0">
                <Decision card={card} picking={picking} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
