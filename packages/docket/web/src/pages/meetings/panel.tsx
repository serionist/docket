/**
 * The open meeting's panel (DR-0667, DR-0769): when and where, who, each attendee one click from their brief and the
 * recording from the Recording page; the talking points; the items worth raising, once asked for, each added as a
 * point in one click; the outcome and notes saved on change; the items on it, each one click from its panel on the
 * Ledger; its materials and its history, the item panel's own parts. A meeting that is over with open points says
 * they roll on at the next sync (DR-0624).
 */
import { HistoryIcon, LightbulbIcon } from "lucide-react";
import * as React from "react";
import { AppLink } from "@/components/app-link";
import { KindTab } from "@/components/kind";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Attendee, Linked as LinkedRow, MeetingPanel } from "@/pages/day/api";
import { refusal, useActions } from "@/pages/day/context";
import { Field } from "@/pages/day/field";
import {
  type Ask,
  CopyButton,
  History,
  Materials,
  type PanelProps,
  Section,
  type ViewerState,
} from "@/pages/day/panel";
import { InLink } from "./links";
import { ResponseBadge } from "./list";
import { Points } from "./points";

/** The panel's top bar: its number and badges, the heading a screen reader names it by. */
export function MeetingHead({ meeting }: { meeting: MeetingPanel }) {
  return (
    <>
      <h2 id="panel-title" tabIndex={-1} className="flex min-w-0 items-center gap-2 text-sm outline-offset-2">
        <span className="font-mono text-xs text-ink-muted">{meeting.ref}</span>
        <span className="sr-only">{meeting.subject}</span>
      </h2>
      {meeting.response && <ResponseBadge response={meeting.response} />}
      {meeting.keyless && (
        <span
          className="inline-flex h-5 shrink-0 items-center rounded-sm border border-rule px-1 text-xs leading-none text-ink-muted"
          title="No calendar event carries it: Claude joins it to one when the calendar has it"
        >
          no calendar event
        </span>
      )}
      {meeting.archived && (
        <span className="inline-flex h-5 shrink-0 items-center rounded-sm border border-rule px-1 text-xs leading-none text-ink-muted">
          archived
        </span>
      )}
    </>
  );
}

function Person({ person }: { person: Attendee }) {
  return person.href ? (
    <a
      href={person.href}
      className="text-green underline-offset-4 hover:underline"
      dir="auto"
      title={`${person.name}'s brief`}
    >
      {person.name}
    </a>
  ) : (
    <span dir="auto">{person.name}</span>
  );
}

/** One fact of the meeting, a term and what it is, in the panel's two columns. */
function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-xs leading-5 font-semibold text-ink-muted">{term}</dt>
      <dd className="min-w-0 text-sm break-words">{children}</dd>
    </div>
  );
}

function Facts({ meeting }: { meeting: MeetingPanel }) {
  return (
    <dl className="grid grid-cols-[5.5rem_1fr] items-baseline gap-x-3 gap-y-1.5">
      {meeting.organiser && (
        <Fact term="Organiser">
          <Person person={meeting.organiser} />
        </Fact>
      )}
      {meeting.attendees.length > 0 && (
        <Fact term="Attendees">
          <ul className="flex flex-wrap gap-x-2 gap-y-0.5">
            {meeting.attendees.map((a, at) => (
              <li key={a.name}>
                <Person person={a} />
                {at < meeting.attendees.length - 1 && <span aria-hidden>,</span>}
              </li>
            ))}
          </ul>
        </Fact>
      )}
      {meeting.where && (
        <Fact term="Where">
          <span dir="auto">{meeting.where}</span>
        </Fact>
      )}
      <Fact term="Your answer">{meeting.response_words}</Fact>
      {meeting.stream && <Fact term="Stream">{meeting.stream}</Fact>}
      {meeting.recordings.map((r) => (
        <Fact key={r.ref} term="Recording">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2">
            <AppLink
              href={r.href}
              className="tap inline-flex h-6 items-center text-green underline-offset-4 hover:underline"
            >
              Open the recording
            </AppLink>
            <code className="min-w-0 font-mono text-xs break-all text-ink-muted">{r.ref}</code>
            <CopyButton value={r.ref} what="the recording's locator" />
          </span>
        </Fact>
      ))}
    </dl>
  );
}

/** An item as the panel lists it: its kind's tab, its number and title, one click from its panel on the Ledger. */
function ItemLine({ linked, action }: { linked: LinkedRow; action?: React.ReactNode }) {
  const row = linked.row;
  const closed = row.state === "done" || row.state === "dropped";
  return (
    <li className="grid gap-0.5 border-t border-rule py-2 first:border-t-0">
      <div className="flex min-w-0 items-center gap-2">
        <KindTab kind={row.kind} />
        <InLink href={linked.href} className="tap inline-flex h-6 shrink-0 items-center font-mono text-xs">
          {row.ref}
        </InLink>
        <span
          className={cn("min-w-0 flex-1 truncate text-sm", closed && "text-ink-muted")}
          dir="auto"
          title={row.title}
        >
          {row.title}
        </span>
        <span className="shrink-0 text-xs text-ink-muted tabular-nums" title={row.due.title}>
          {row.due.text}
        </span>
        {action}
      </div>
      {linked.why && <p className="pl-1 text-xs text-ink-muted">{linked.why}</p>}
    </li>
  );
}

function Suggestions({
  meeting,
  shown,
  onShow,
}: {
  meeting: MeetingPanel;
  shown: boolean;
  onShow: (shown: boolean) => void;
}) {
  const { write, pending } = useActions();
  const [problem, setProblem] = React.useState("");
  const id = `suggested-${meeting.ref}`;
  const add = async (ref: string) => {
    const outcome = await write({ op: "point", items: [meeting.ref], fields: { item: ref } });
    setProblem(refusal(outcome));
  };
  const found = meeting.suggestions;
  return (
    <section aria-labelledby={id} className="grid gap-2">
      <div className="flex min-h-6 items-center gap-2">
        <h3 id={id} className="text-xs font-semibold text-ink-muted">
          Worth raising
        </h3>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto h-6 px-1.5 text-xs text-ink-muted"
          aria-pressed={shown}
          onClick={() => onShow(!shown)}
        >
          <LightbulbIcon className="size-3.5" />
          {shown ? "Hide the suggestions" : "Suggest items"}
        </Button>
      </div>
      {shown &&
        (found === null || found === undefined ? (
          <p className="text-sm text-ink-muted">Looking for open items worth raising.</p>
        ) : found.length ? (
          <ul className="grid">
            {found.map((s) => (
              <ItemLine
                key={s.row.id}
                linked={s}
                action={
                  !meeting.archived && (
                    <Button
                      size="sm"
                      className="h-6 px-1.5 text-xs"
                      disabled={pending}
                      onClick={() => void add(s.row.ref)}
                    >
                      Add as a point<span className="sr-only">: {s.row.ref}</span>
                    </Button>
                  )
                }
              />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">
            Nothing to raise: no open item is on it, or on someone in it, that isn't a point already.
          </p>
        ))}
      {problem && <Refusal>{problem}</Refusal>}
    </section>
  );
}

export interface MeetingPanelProps {
  meeting: MeetingPanel;
  ask: Ask | null;
  /** The suggestions are asked for, as the URL's suggest=1 says. */
  suggest: boolean;
  viewer: ViewerState | null;
  problem: string;
  onSuggest: (shown: boolean) => void;
  onUndo: PanelProps["onUndo"];
  onView: PanelProps["onView"];
}

export function MeetingPanelBody({
  meeting,
  ask,
  suggest,
  viewer,
  problem,
  onSuggest,
  onUndo,
  onView,
}: MeetingPanelProps) {
  // A key's ask lands once the meeting it asked about is shown; each lands once.
  const handled = React.useRef(0);
  React.useEffect(() => {
    if (!ask || ask.ref !== meeting.ref || ask.n === handled.current) return;
    handled.current = ask.n;
    document.getElementById("panel-title")?.focus({ preventScroll: true });
  }, [ask, meeting.ref]);
  const items = `items-${meeting.ref}`;
  return (
    <div className="grid gap-6 px-4 py-4">
      <div className="grid gap-1">
        <p className="text-lg leading-snug font-semibold text-pretty" dir="auto">
          {meeting.subject}
        </p>
        <p className="text-sm tabular-nums">
          <time dateTime={meeting.start}>{meeting.when}</time>
          {meeting.over && <span className="text-ink-muted">, over</span>}
        </p>
      </div>
      <Facts meeting={meeting} />
      {meeting.rolls && (
        <p className="flex items-start gap-2 rounded-sm border border-rule bg-paper px-3 py-2 text-sm">
          <HistoryIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-muted" />
          {meeting.rolls}
        </p>
      )}
      <Points meeting={meeting} />
      <Suggestions meeting={meeting} shown={suggest} onShow={onSuggest} />
      <Section label="Outcome and notes" className="gap-3">
        <Field item={meeting.ref} name="outcome" label="Outcome" value={meeting.outcome} rows={3} meeting />
        <Field item={meeting.ref} name="notes" label="Notes" value={meeting.notes} rows={4} meeting />
      </Section>
      <section aria-labelledby={items} className="grid gap-2">
        <h3 id={items} className="text-xs font-semibold text-ink-muted">
          Items on it
        </h3>
        {meeting.items.length ? (
          <ul className="grid">
            {meeting.items.map((linked) => (
              <ItemLine key={linked.row.id} linked={linked} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">No item hangs on it. A point made an item comes here.</p>
        )}
      </section>
      <Materials owner={meeting.ref} materials={meeting.materials} viewer={viewer} onView={onView} />
      <p className="text-xs text-ink-muted">{meeting.facts}</p>
      {problem && <Refusal>{problem}</Refusal>}
      <History owner={meeting.ref} history={meeting.history} onUndo={onUndo} />
    </div>
  );
}
