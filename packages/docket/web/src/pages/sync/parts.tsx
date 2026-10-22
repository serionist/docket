/**
 * The Sync page's parts (DR-0304, DR-0305, DR-0788), in the order the page reads: what needs the owner or a Claude
 * session, each with the words to paste; the failures still current, each with what to do; the runs open, then
 * ended, a ruled row each that opens its card in the panel; and each machine's cursor of each source. Every value is
 * the server's, shown as text: ids, counts, times in the owner's zone and Docket's own messages, nothing of a
 * capture.
 */
import { ChevronRightIcon } from "lucide-react";
import type * as React from "react";
import { AppLink, plainClick } from "@/components/app-link";
import { Locator, Note } from "@/components/doc";
import { Paste, Tab, type Tone } from "@/components/paste";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { CursorRead, EntryRead, FailureRead, MachineRead, NeedRead, RunRead, RunsRead, SyncPage } from "./api";

/** A part of the page under its sticky head: its name and how many it holds. */
export function Part({
  id,
  title,
  count,
  aside,
  children,
}: {
  id: string;
  title: string;
  count?: number;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id}>
      <h2
        id={id}
        className="sticky top-0 z-10 flex min-h-8 flex-wrap items-center gap-x-2 border-b border-rule bg-paper px-3 py-1 text-xs font-semibold sm:px-4"
      >
        {title}
        {count !== undefined && <span className="font-normal text-ink-muted tabular-nums">{count}</span>}
        {aside && <span className="ml-auto font-normal text-ink-muted">{aside}</span>}
      </h2>
      {children}
    </section>
  );
}

/** What a part says when it holds nothing. */
function Quiet({ children }: { children: React.ReactNode }) {
  return <p className="px-3 py-3 text-sm text-ink-muted sm:px-4">{children}</p>;
}

/** What a part narrowed to one source says when it holds nothing of it, with the way back to every source. */
function NoneOf({ narrowed, what }: { narrowed: string; what: string }) {
  return (
    <Quiet>
      {what} for {narrowed}.{" "}
      <AppLink
        href="/sync"
        className="inline-flex min-h-6 items-center text-ink underline underline-offset-2 pointer-coarse:min-h-11"
      >
        Every source
      </AppLink>
    </Quiet>
  );
}

const NEED_TONE: Record<NeedRead["kind"], Tone> = { you: "red", due: "me", final: "me" };

export function Needs({
  page,
  onCopy,
  narrowed,
}: {
  page: SyncPage;
  onCopy: (text: string) => void;
  narrowed?: string;
}) {
  return (
    <Part id="needs-title" title="Needs" count={page.needs.length}>
      {page.needs.length ? (
        <ul>
          {page.needs.map((need) => (
            <li
              key={`${need.source}-${need.kind}`}
              data-need={need.kind}
              className="grid gap-1.5 border-b border-rule px-3 py-2 sm:px-4"
            >
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                <Tab tone={NEED_TONE[need.kind]}>{need.word}</Tab>
                <span className="font-semibold">{need.source}</span>
                <span className={cn("min-w-0", need.kind === "you" && "text-red")}>{need.line}</span>
              </p>
              {need.paste && (
                <Paste
                  text={need.paste}
                  label={`the words to paste into Claude Code for ${need.source}`}
                  onCopy={onCopy}
                />
              )}
            </li>
          ))}
          {page.paste_all && (
            <li className="grid gap-1.5 border-b border-rule px-3 py-2 sm:px-4">
              <p className="text-sm">All of them in one Claude Code session:</p>
              <Paste text={page.paste_all} label="the words to paste for all of them" onCopy={onCopy} />
            </li>
          )}
        </ul>
      ) : narrowed ? (
        <NoneOf narrowed={narrowed} what="Nothing needs a session" />
      ) : page.live_sources ? (
        <Quiet>Nothing needs a session now.</Quiet>
      ) : (
        <Quiet>
          No source syncs live yet: <code className="font-mono text-xs">docket source new &lt;name&gt;</code>, then list
          its tools in vault.yaml and commit it.
        </Quiet>
      )}
    </Part>
  );
}

export function Failures({
  failures,
  onRun,
  narrowed,
}: {
  failures: FailureRead[];
  onRun: (href: string) => void;
  narrowed?: string;
}) {
  return (
    <Part id="failures-title" title="Failures" count={failures.length}>
      {failures.length ? (
        <ul>
          {failures.map((failure, at) => (
            <li
              // Two failures of one kind and source never share a run, nor the same words.
              key={`${failure.kind}-${failure.source}-${failure.run ?? at}`}
              data-failure={failure.kind}
              className="grid gap-1 border-b border-rule px-3 py-2 sm:px-4"
            >
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                <Tab tone={failure.severity === "error" ? "red" : "me"}>{failure.label}</Tab>
                {failure.source && <span className="font-semibold">{failure.source}</span>}
                {failure.when && <span className="text-xs text-ink-muted tabular-nums">{failure.when}</span>}
                {failure.run && failure.run_url && (
                  <a
                    href={failure.run_url}
                    onClick={(event) => {
                      if (!plainClick(event)) return;
                      event.preventDefault();
                      onRun(failure.run_url ?? "");
                    }}
                    className="inline-flex min-h-6 items-center underline decoration-rule underline-offset-2 hover:decoration-ink pointer-coarse:min-h-11"
                    aria-label={`The run ${failure.run}`}
                  >
                    <Locator value={failure.run} />
                  </a>
                )}
              </p>
              <p className="max-w-prose text-sm">{failure.what}</p>
              {failure.ids.length > 0 && (
                <p className="flex flex-wrap gap-x-2 gap-y-0.5">
                  {failure.ids.map((id) => (
                    <Locator key={id} value={id} />
                  ))}
                </p>
              )}
              <p className="max-w-prose text-sm">
                <span className="font-semibold">What to do:</span> {failure.todo}
              </p>
            </li>
          ))}
        </ul>
      ) : narrowed ? (
        <NoneOf narrowed={narrowed} what="Nothing failed since the last whole run" />
      ) : (
        <Quiet>Nothing failed since each source's last whole run.</Quiet>
      )}
    </Part>
  );
}

const RUN_TONE: Record<RunRead["state"], Tone> = { open: "green", closed: "muted", dropped: "red" };

export interface RunRows {
  selected: string | undefined;
  open: string | undefined;
  onSelect: (id: string) => void;
  onOpen: (run: RunRead) => void;
}

function RunRow({ run, rows, tabbable }: { run: RunRead; rows: RunRows; tabbable: boolean }) {
  const selected = rows.selected === run.id;
  return (
    <li
      data-row={run.id}
      data-state={run.state}
      tabIndex={tabbable ? 0 : -1}
      aria-current={selected ? "true" : undefined}
      onFocus={(event) => {
        if (event.target === event.currentTarget) rows.onSelect(run.id);
      }}
      className={cn(
        "relative border-b border-rule text-sm outline-offset-[-2px]",
        selected ? "bg-selection" : "bg-sheet hover:bg-hover",
      )}
    >
      {selected && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      <a
        href={run.url}
        tabIndex={-1}
        aria-current={rows.open === run.id ? "page" : undefined}
        onClick={(event) => {
          if (!plainClick(event)) return;
          event.preventDefault();
          rows.onOpen(run);
        }}
        className="block cursor-default px-3 py-1.5 text-ink outline-none pointer-coarse:min-h-11 sm:px-4"
      >
        <span className="flex min-h-5 min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <Tab tone={RUN_TONE[run.state]}>{run.word}</Tab>
          <span className="font-semibold">{run.source}</span>
          {run.badges.map((badge) => (
            <span key={badge} className="rounded-sm border border-rule px-1.5 text-xs text-ink-muted">
              {badge}
            </span>
          ))}
          <span className="ml-auto text-xs text-ink-muted tabular-nums">
            {run.opened}, {run.took}
          </span>
        </span>
        <span className="block truncate pt-0.5 text-xs text-ink-muted" title={run.facts.join("; ")}>
          {run.facts[0]}
          {run.why && <span className="text-red">; {run.why}</span>}
        </span>
      </a>
    </li>
  );
}

export function rowsOf(runs: RunsRead | undefined): RunRead[] {
  return runs ? [...runs.open, ...runs.recent] : [];
}

export function Runs({
  runs,
  rows,
  onMore,
  narrowed,
}: {
  runs: RunsRead;
  rows: RunRows;
  onMore: (href: string) => void;
  narrowed?: string;
}) {
  const all = rowsOf(runs);
  const first = all.find((run) => run.id === rows.selected)?.id ?? all[0]?.id;
  return (
    <Part
      id="runs-title"
      title="Runs"
      count={all.length}
      aside={
        <span data-as-of>
          as of {runs.as_of}
          {runs.live && "; refreshes while a run is open"}
        </span>
      }
    >
      {all.length ? (
        <ul aria-label="Open runs, then recent ones">
          {all.map((run) => (
            <RunRow key={run.id} run={run} rows={rows} tabbable={run.id === first} />
          ))}
        </ul>
      ) : narrowed ? (
        <NoneOf narrowed={narrowed} what="No run on this machine" />
      ) : (
        <Quiet>No run yet on this machine. Runs show here when Claude syncs a source.</Quiet>
      )}
      {runs.more && (
        <div className="px-3 py-2 sm:px-4">
          <Button variant="outline" size="sm" onClick={() => onMore(runs.more ?? "")}>
            Show more
          </Button>
        </div>
      )}
    </Part>
  );
}

/** A run's card, in the panel: its facts, why it ended so, and the ids it set aside, waits for or let go. */
export function RunCard({ run }: { run: RunRead }) {
  return (
    <div className="grid gap-3 p-4">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <Tab tone={RUN_TONE[run.state]}>{run.word}</Tab>
        <span className="font-semibold">{run.source}</span>
        {run.badges.map((badge) => (
          <span key={badge} className="rounded-sm border border-rule px-1.5 text-xs text-ink-muted">
            {badge}
          </span>
        ))}
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        <div className="grid gap-0.5">
          <dt className="text-xs text-ink-muted">Opened</dt>
          <dd className="text-sm tabular-nums">{run.opened}</dd>
        </div>
        <div className="grid gap-0.5">
          <dt className="text-xs text-ink-muted">{run.state === "open" ? "Open for" : "Took"}</dt>
          <dd className="text-sm tabular-nums">{run.took}</dd>
        </div>
      </dl>
      {run.why && <Refusal>{run.why}</Refusal>}
      <ul className="grid list-disc gap-1 pl-5 text-sm marker:text-ink-muted">
        {run.facts.map((fact) => (
          <li key={fact}>{fact}</li>
        ))}
      </ul>
      {run.lists.map((list) => (
        <section key={list.title} className="grid gap-1" aria-label={list.title}>
          <h3 className="text-xs font-semibold text-ink-muted">{list.title}</h3>
          <p className="flex flex-wrap gap-x-2 gap-y-0.5">
            {list.ids.map((id) => (
              <Locator key={id} value={id} className="text-ink" />
            ))}
            {list.more > 0 && <span className="text-xs text-ink-muted">and {list.more} more</span>}
          </p>
        </section>
      ))}
      <Locator value={run.id} />
    </div>
  );
}

function Entry({ entry }: { entry: EntryRead }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs">
      <Locator value={entry.id} className="text-ink" />
      <span>{entry.kind === "retry" ? `retry, ${entry.why}` : `waits for ${entry.why}`}</span>
      <span className="text-ink-muted tabular-nums">
        since {entry.since}, {entry.age}
        {entry.leaves && `; leaves ${entry.leaves}`}
      </span>
      {entry.final_due && <Tab tone="me">due a final run</Tab>}
    </li>
  );
}

function Machine({ row }: { row: MachineRead }) {
  const entries = [...row.retry, ...row.waiting];
  return (
    <li className="grid gap-1 border-t border-rule py-2 first:border-t-0" data-machine={row.machine}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-[minmax(8rem,1.2fr)_minmax(12rem,2fr)_repeat(3,minmax(4rem,1fr))]">
        <div className="grid gap-0.5">
          <dt className="text-xs text-ink-muted">Machine</dt>
          <dd className="flex flex-wrap items-center gap-1.5 text-sm">
            <span className="font-mono text-xs break-all">{row.machine}</span>
            {row.this && <Tab tone="green">this one</Tab>}
          </dd>
        </div>
        <div className="grid gap-0.5">
          <dt className="text-xs text-ink-muted">Mark</dt>
          <dd className="flex flex-wrap items-center gap-1.5 text-sm tabular-nums">
            {row.mark ? `${row.mark}, ${row.mark_age}` : "none yet"}
            {row.ahead && <Tab tone="red">ahead of this clock: left out</Tab>}
          </dd>
        </div>
        <div className="grid gap-0.5">
          <dt className="text-xs text-ink-muted">Lookback</dt>
          <dd className="text-sm tabular-nums">{row.lookback}</dd>
        </div>
        <div className="grid gap-0.5">
          <dt className="text-xs text-ink-muted">Retry</dt>
          <dd className="text-sm tabular-nums">{row.retry.length}</dd>
        </div>
        <div className="grid gap-0.5">
          <dt className="text-xs text-ink-muted">Waiting</dt>
          <dd className="text-sm tabular-nums">{row.waiting.length}</dd>
        </div>
      </dl>
      {row.handed && <p className="text-xs text-ink-muted">{row.handed}</p>}
      {entries.length > 0 && (
        <details className="group/ids">
          <summary className="flex min-h-6 cursor-default items-center gap-1 text-xs text-ink-muted hover:text-ink pointer-coarse:min-h-11">
            <ChevronRightIcon aria-hidden className="size-3.5 transition-transform group-open/ids:rotate-90" />
            Retry and waiting ids
          </summary>
          <ul className="grid gap-1 pt-1 pl-5">
            {entries.map((entry) => (
              <Entry key={`${entry.kind}-${entry.id}`} entry={entry} />
            ))}
          </ul>
          {row.more > 0 && <p className="pt-1 pl-5 text-xs text-ink-muted">and {row.more} more</p>}
        </details>
      )}
    </li>
  );
}

function Cursor({ cursor }: { cursor: CursorRead }) {
  const id = `cursor-${cursor.source}`;
  return (
    <article aria-labelledby={id} className="grid gap-1.5 border-b border-rule px-3 py-2 sm:px-4">
      <h3 id={id} className="flex flex-wrap items-baseline gap-x-2 text-sm">
        <span className="font-semibold">{cursor.source}</span>
        {cursor.window_since && (
          <span className="text-xs font-normal text-ink-muted">The next run lists from {cursor.window_since}.</span>
        )}
      </h3>
      {cursor.notes.map((note) => (
        <p key={note} className="text-xs text-ink-muted">
          {note}
        </p>
      ))}
      {cursor.problem && <Refusal>{cursor.problem}</Refusal>}
      {cursor.rows.length ? (
        <ul>
          {cursor.rows.map((row) => (
            <Machine key={row.machine} row={row} />
          ))}
        </ul>
      ) : (
        !cursor.problem && <p className="text-sm text-ink-muted">No machine has synced {cursor.source} yet.</p>
      )}
    </article>
  );
}

export function Cursors({ page }: { page: SyncPage }) {
  return (
    <Part id="cursors-title" title="Cursors" count={page.cursors.length}>
      {page.cursors.length ? (
        page.cursors.map((cursor) => <Cursor key={cursor.source} cursor={cursor} />)
      ) : (
        <Quiet>
          No cursor yet: the first whole run writes{" "}
          <code className="font-mono text-xs break-all">state/cursors-{page.machine}.yaml</code>.
        </Quiet>
      )}
    </Part>
  );
}

/** The notes the page says over everything: a folder source, a source it has nothing of, a run not found. */
export function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <div className="grid gap-2 px-3 pb-3 sm:px-4">
      {notes.map((note) => (
        <Note key={note} tone="warn">
          {note}
        </Note>
      ))}
    </div>
  );
}
