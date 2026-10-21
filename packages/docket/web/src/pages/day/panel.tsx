/**
 * The open item's panel (DR-0664): its call, its fields saved on change, the meeting it hangs on with its points,
 * its free text, its materials with the text viewer, where it came from, and its history with Undo and Redo per
 * line. Every value comes ready to show from the server's panel read. Its sections, the materials and the history
 * are the meeting panel's too (pages/meetings), each taking the ref of what it belongs to.
 */
import { CheckIcon, CopyIcon, ExternalLinkIcon, FileTextIcon, FolderOpenIcon, XIcon } from "lucide-react";
import * as React from "react";
import { AppLink } from "@/components/app-link";
import { DatePicker } from "@/components/date-picker";
import { KindTab } from "@/components/kind";
import { Refusal } from "@/components/shell/page";
import { isVerdict, STAMP_INK, STAMP_PRESSED } from "@/components/stamp";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useUnsaved } from "@/lib/drafts";
import { cn } from "@/lib/utils";
import type { Body, HistoryLine, ItemPanel, Link, Viewer } from "./api";
import { refusal, useActions } from "./context";
import { draftKey, Field, leaveOnEscape } from "./field";

/** What a key asked the panel to focus once it shows its item: the drop's why, the first snooze, or its heading. */
export interface Ask {
  what: "why" | "snooze" | "heading";
  ref: string;
  n: number;
}

export interface ViewerState {
  ref: string;
  data: Viewer | undefined;
  problem: string;
  loading: boolean;
}

/** A part of the panel: under a visible heading, or named for a screen reader alone. */
export function Section({
  title,
  label,
  id,
  children,
  className,
}: {
  title?: string;
  label?: string;
  id?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      aria-labelledby={title ? id : undefined}
      aria-label={title ? undefined : label}
      className={cn("grid gap-2", className)}
    >
      {title && (
        <h3 id={id} className="text-xs font-semibold text-ink-muted">
          {title}
        </h3>
      )}
      {children}
    </section>
  );
}

/** A locator or path to copy, with a word that says it was copied. */
export function CopyButton({ value, what }: { value: string; what: string }) {
  const [copied, setCopied] = React.useState(false);
  React.useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="size-6"
      aria-label={`Copy ${what}`}
      title={copied ? "Copied" : `Copy ${value}`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => setCopied(true));
      }}
    >
      {copied ? <CheckIcon className="text-green" /> : <CopyIcon />}
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied" : ""}
      </span>
    </Button>
  );
}

/** A material or a source as it links (DR-0664): a URL in a new tab, a record or a search on its page, else text. */
export function Linked({ link, children }: { link: Link; children: React.ReactNode }) {
  if (link.kind === "url") {
    return (
      <a
        href={link.href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-green underline-offset-4 hover:underline"
        dir="auto"
      >
        {children}
        <ExternalLinkIcon aria-hidden className="ml-1 inline size-3 align-baseline" />
      </a>
    );
  }
  if (link.kind === "record" || link.kind === "recording" || link.kind === "query") {
    return (
      <AppLink href={link.href} className="text-green underline-offset-4 hover:underline" dir="auto">
        {children}
      </AppLink>
    );
  }
  return <span dir="auto">{children}</span>;
}

function Call({
  item,
  drop,
  problem,
  onCall,
  onDrop,
}: {
  item: ItemPanel;
  drop: boolean;
  problem: string;
  onCall: PanelProps["onCall"];
  onDrop: PanelProps["onDrop"];
}) {
  const { pending, today } = useActions();
  if (!item.callable) {
    return (
      <Section label="Your call">
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={pending} onClick={() => void onCall({ op: "reopen" })}>
            Reopen
          </Button>
        </div>
        {problem && <Refusal>{problem}</Refusal>}
      </Section>
    );
  }
  const snoozeTo = (day: string) => void onCall({ op: "snooze", until: day });
  return (
    <Section label="Your call" className="gap-3">
      <div role="group" aria-label="Call" className="grid grid-cols-4">
        {item.verdicts.map((button) => (
          <button
            key={button.verdict}
            type="button"
            disabled={pending}
            aria-pressed={button.pressed}
            title={button.title}
            onClick={() => void onCall({ op: "triage", verdict: button.writes })}
            className={cn(
              "tap -ml-px flex h-8 min-w-0 cursor-default items-center justify-center gap-1.5 border border-control bg-sheet px-2 text-sm first:ml-0 first:rounded-l-sm last:rounded-r-sm hover:bg-hover focus-visible:z-10 disabled:opacity-50",
              button.pressed && isVerdict(button.verdict) && STAMP_PRESSED[button.verdict],
            )}
          >
            {button.label}
            <span
              className={cn("text-xs pointer-coarse:hidden", isVerdict(button.verdict) && STAMP_INK[button.verdict])}
            >
              {button.key}
            </span>
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={pending}
          title="Clear the call (0)"
          onClick={() => void onCall({ op: "triage", verdict: null })}
        >
          No call <span className="text-xs text-ink-muted pointer-coarse:hidden">0</span>
        </Button>
        <Button size="sm" disabled={pending} title="Done (e)" onClick={() => void onCall({ op: "done" })}>
          <CheckIcon />
          Done <span className="text-xs text-ink-muted pointer-coarse:hidden">e</span>
        </Button>
        {item.state === "snoozed" && (
          <Button size="sm" disabled={pending} onClick={() => void onCall({ op: "reopen" })}>
            Reopen now
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto text-red hover:bg-red-wash"
          aria-expanded={drop}
          aria-controls={`close-${item.ref}`}
          title="Drop: say why (Delete)"
          onClick={() => onDrop(!drop)}
        >
          Drop, or done with a why <span className="text-xs text-ink-muted pointer-coarse:hidden">Delete</span>
        </Button>
      </div>
      {drop && <Close item={item} onCall={onCall} />}
      {problem && <Refusal>{problem}</Refusal>}
      <div role="group" aria-label="Snooze" className="flex flex-wrap items-center gap-1">
        <span className="mr-1 text-xs font-semibold text-ink-muted">
          Snooze <span className="font-normal pointer-coarse:hidden">s</span>
        </span>
        {item.snooze.map((choice, at) => (
          <Button
            key={choice.until}
            id={at === 0 ? `snooze-${item.ref}` : undefined}
            size="sm"
            disabled={pending}
            title={choice.title}
            onClick={() => snoozeTo(choice.until)}
          >
            {choice.label}
          </Button>
        ))}
        <DatePicker
          id={`until-${item.ref}`}
          value=""
          empty="Another day"
          label="Snooze to a day"
          min={item.tomorrow}
          today={today}
          size="sm"
          disabled={pending}
          onPick={snoozeTo}
          className="text-ink"
        />
      </div>
    </Section>
  );
}

/** Drop, or done with a why: the writer needs a why for a drop, and Delete opens this with its why focused. */
function Close({ item, onCall }: { item: ItemPanel; onCall: PanelProps["onCall"] }) {
  const { pending } = useActions();
  const [why, setWhy] = React.useState("");
  const [outcome, setOutcome] = React.useState(item.outcome);
  const [seen, setSeen] = React.useState(item.outcome);
  if (item.outcome !== seen) {
    setSeen(item.outcome);
    if (outcome === seen) setOutcome(item.outcome);
  }
  const whyKey = draftKey(item.ref, "why");
  const outcomeKey = draftKey(item.ref, "close-outcome");
  useUnsaved(whyKey, why.trim() !== "");
  useUnsaved(outcomeKey, outcome !== item.outcome);
  const close = async (op: "drop" | "done") => {
    const body: Partial<Body> = { op, note: why, ...(outcome !== item.outcome ? { outcome } : {}) };
    if (await onCall(body, [whyKey, outcomeKey])) setWhy("");
  };
  return (
    <div id={`close-${item.ref}`} className="grid gap-2 rounded-lg border border-rule bg-paper p-3">
      <div className="grid gap-1">
        <label htmlFor={`why-${item.ref}`} className="text-xs font-semibold text-ink-muted">
          Why
        </label>
        <Input
          id={`why-${item.ref}`}
          value={why}
          dir="auto"
          autoComplete="off"
          placeholder="a drop needs one"
          onChange={(event) => setWhy(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void close("drop");
            } else leaveOnEscape(event);
          }}
        />
      </div>
      <div className="grid gap-1">
        <label htmlFor={`close-outcome-${item.ref}`} className="text-xs font-semibold text-ink-muted">
          Outcome
        </label>
        <Textarea
          id={`close-outcome-${item.ref}`}
          value={outcome}
          dir="auto"
          rows={2}
          onChange={(event) => setOutcome(event.target.value)}
          onKeyDown={leaveOnEscape}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="destructive" disabled={pending} onClick={() => void close("drop")}>
          Drop
        </Button>
        <Button size="sm" disabled={pending} onClick={() => void close("done")}>
          Mark done
        </Button>
      </div>
    </div>
  );
}

/** The materials of an item or a meeting, `owner` its ref: each linked, viewed, opened or removed, and one added. */
export function Materials({
  owner,
  materials,
  viewer,
  onView,
}: {
  owner: string;
  materials: Link[];
  viewer: ViewerState | null;
  onView: PanelProps["onView"];
}) {
  const { write, open, pending } = useActions();
  const [ref, setRef] = React.useState("");
  const [label, setLabel] = React.useState("");
  const [problem, setProblem] = React.useState("");
  useUnsaved(draftKey(owner, "material"), ref.trim() !== "" || label.trim() !== "");
  const add = async () => {
    const outcome = await write({ op: "material", items: [owner], ref, label });
    setProblem(refusal(outcome));
    if (outcome.ok) {
      setRef("");
      setLabel("");
    }
  };
  const act = async (material: string, how: "open" | "show" | "remove") => {
    const outcome =
      how === "remove"
        ? await write({ op: "material", items: [owner], ref: material, remove: true })
        : await open(owner, material, how === "show");
    setProblem(refusal(outcome));
  };
  return (
    <Section title="Materials" id={`materials-${owner}`}>
      {materials.length ? (
        <ul className="grid">
          {materials.map((m) => (
            <li key={m.ref} className="grid gap-1 border-t border-rule py-2 first:border-t-0 first:pt-0">
              <div className="flex min-w-0 items-start gap-2">
                <span className="min-w-0 flex-1 text-sm break-words">
                  <Linked link={m}>{m.label}</Linked>
                </span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="size-6"
                  disabled={pending}
                  aria-label={`Remove ${m.ref}`}
                  title={`Remove ${m.ref}`}
                  onClick={() => void act(m.ref, "remove")}
                >
                  <XIcon />
                </Button>
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-1">
                <code className="min-w-0 font-mono text-xs break-all text-ink-muted">{m.ref}</code>
                <CopyButton value={m.ref} what={m.ref} />
              </div>
              {(m.kind === "path" || m.kind === "text") && (
                <div className="flex flex-wrap gap-1">
                  {m.kind === "text" && (
                    <Button
                      size="sm"
                      aria-label={`View ${m.label}`}
                      aria-pressed={viewer?.ref === m.ref}
                      onClick={() => onView(viewer?.ref === m.ref ? undefined : m.ref)}
                    >
                      <FileTextIcon />
                      View
                    </Button>
                  )}
                  <Button
                    size="sm"
                    aria-label={`Open ${m.label}`}
                    disabled={pending}
                    onClick={() => void act(m.ref, "open")}
                  >
                    Open
                  </Button>
                  <Button
                    size="sm"
                    aria-label={`Show in folder: ${m.label}`}
                    disabled={pending}
                    onClick={() => void act(m.ref, "show")}
                  >
                    <FolderOpenIcon />
                    Show in folder
                  </Button>
                </div>
              )}
              {viewer?.ref === m.ref && <TextViewer viewer={viewer} onClose={() => onView(undefined)} />}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-muted">No materials yet: files, drafts and links this needs or made.</p>
      )}
      {viewer && !materials.some((m) => m.ref === viewer.ref) && (
        <TextViewer viewer={viewer} onClose={() => onView(undefined)} />
      )}
      <form
        className="grid gap-2 sm:grid-cols-[1fr_10rem_auto] sm:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <div className="grid gap-1">
          <label htmlFor={`material-ref-${owner}`} className="text-xs font-semibold text-ink-muted">
            Path, URL or locator
          </label>
          <Input
            id={`material-ref-${owner}`}
            value={ref}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setRef(event.target.value)}
            onKeyDown={leaveOnEscape}
          />
        </div>
        <div className="grid gap-1">
          <label htmlFor={`material-label-${owner}`} className="text-xs font-semibold text-ink-muted">
            Label
          </label>
          <Input
            id={`material-label-${owner}`}
            value={label}
            autoComplete="off"
            dir="auto"
            onChange={(event) => setLabel(event.target.value)}
            onKeyDown={leaveOnEscape}
          />
        </div>
        <Button type="submit" disabled={pending}>
          Add
        </Button>
      </form>
      {problem && <Refusal>{problem}</Refusal>}
    </Section>
  );
}

function TextViewer({ viewer, onClose }: { viewer: ViewerState; onClose: () => void }) {
  const box = React.useRef<HTMLDivElement>(null);
  // Opened from its View or from the URL, the viewer comes into sight once its text is in.
  const shown = viewer.data !== undefined || viewer.problem !== "";
  React.useEffect(() => {
    if (shown) box.current?.scrollIntoView({ block: "nearest" });
  }, [viewer.ref, shown]);
  return (
    <div
      ref={box}
      className="grid gap-1 rounded-lg border border-rule bg-paper"
      role="region"
      aria-label={`The text of ${viewer.ref}`}
    >
      <div className="flex min-h-8 items-center gap-2 border-b border-rule px-2">
        <FileTextIcon aria-hidden className="size-4 text-ink-muted" />
        <code className="min-w-0 flex-1 truncate font-mono text-xs">{viewer.ref}</code>
        {viewer.data?.cut && (
          <span className="rounded-sm border border-rule px-1 text-xs text-ink-muted">its first 256 KB</span>
        )}
        <Button variant="ghost" size="icon-sm" className="size-6" aria-label="Close the text" onClick={onClose}>
          <XIcon />
        </Button>
      </div>
      {viewer.problem ? (
        <p role="alert" className="px-3 py-2 text-sm text-ink">
          {viewer.problem}
        </p>
      ) : viewer.loading && !viewer.data ? (
        <p className="px-3 py-2 text-sm text-ink-muted">Reading it.</p>
      ) : (
        <pre
          tabIndex={0}
          className="max-h-96 overflow-auto px-3 py-2 font-mono text-xs leading-5 whitespace-pre-wrap break-words"
          dir="auto"
        >
          {viewer.data?.text}
        </pre>
      )}
    </div>
  );
}

/** The history of an item or a meeting, `owner` its ref, newest first: Undo and Redo per line, and a line added. */
export function History({
  owner,
  history,
  onUndo,
}: {
  owner: string;
  history: HistoryLine[];
  onUndo: PanelProps["onUndo"];
}) {
  const { write, pending } = useActions();
  const [line, setLine] = React.useState("");
  const [problem, setProblem] = React.useState("");
  useUnsaved(draftKey(owner, "log"), line.trim() !== "");
  const add = async () => {
    const outcome = await write({ op: "log", items: [owner], note: line });
    setProblem(refusal(outcome));
    if (outcome.ok) setLine("");
  };
  return (
    <Section title="History, newest first" id={`history-${owner}`}>
      <form
        className="flex items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <div className="grid min-w-0 flex-1 gap-1">
          <label htmlFor={`log-${owner}`} className="text-xs font-semibold text-ink-muted">
            Add a line
          </label>
          <Input
            id={`log-${owner}`}
            value={line}
            dir="auto"
            autoComplete="off"
            onChange={(event) => setLine(event.target.value)}
            onKeyDown={leaveOnEscape}
          />
        </div>
        <Button type="submit" disabled={pending}>
          Add
        </Button>
      </form>
      {problem && <Refusal>{problem}</Refusal>}
      <ol className="grid" data-history={owner}>
        {history.map((h: HistoryLine) => (
          <li key={h.op} className={cn("grid gap-0.5 border-t border-rule py-2", h.undone_by && "text-ink-muted")}>
            <p className="flex flex-wrap items-center gap-x-2 text-xs text-ink-muted">
              <time dateTime={h.stamp} className="tabular-nums">
                {h.when}
              </time>
              <span>{h.by}</span>
              <span>via {h.via}</span>
              {h.button && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto h-6 px-1.5 text-xs"
                  disabled={pending}
                  aria-label={`${h.button}: ${h.line}`}
                  onClick={() => void onUndo(h.op, h.button === "Redo" ? "redo" : "undo")}
                >
                  {h.button}
                </Button>
              )}
            </p>
            <p className={cn("text-sm break-words", h.undone_by && "line-through decoration-ink-muted")} dir="auto">
              {h.line}
            </p>
            {h.undone_by && (
              <p className="text-xs">
                undone by <code className="font-mono">{h.undone_by}</code>
              </p>
            )}
          </li>
        ))}
      </ol>
    </Section>
  );
}

export interface PanelProps {
  item: ItemPanel;
  ask: Ask | null;
  /** The drop form is open, as the URL's drop=1 says. */
  drop: boolean;
  onDrop: (open: boolean) => void;
  callProblem: string;
  viewer: ViewerState | null;
  /** A call on this item; true when it wrote. `except` names the unsaved text the call itself sends. */
  onCall: (body: Partial<Body>, except?: string[]) => Promise<boolean>;
  onUndo: (op: string, said: "undo" | "redo") => Promise<void>;
  onView: (ref: string | undefined) => void;
}

/** The panel's top bar: its number, kind and call, the heading a screen reader names it by. */
export function PanelHead({ item }: { item: ItemPanel }) {
  return (
    <>
      <h2 id="panel-title" tabIndex={-1} className="flex min-w-0 items-center gap-2 text-sm outline-offset-2">
        <span className="font-mono text-xs text-ink-muted">{item.ref}</span>
        <span className="sr-only">{item.title}</span>
      </h2>
      <KindTab kind={item.kind} />
      {isVerdict(item.triage) && <StampSmall verdict={item.triage} />}
      {item.archived && <span className="rounded-sm border border-rule px-1 text-xs text-ink-muted">archived</span>}
    </>
  );
}

export function StampSmall({ verdict }: { verdict: "discuss" | "claude" | "me" | "chase" }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded-sm border-[3px] border-double px-1.5 text-xs leading-none font-semibold",
        STAMP_PRESSED[verdict],
      )}
    >
      {verdict}
    </span>
  );
}

export function ItemPanelBody({ item, ask, drop, callProblem, viewer, onCall, onDrop, onUndo, onView }: PanelProps) {
  // A key's ask lands once the item it asked about is shown, the drop form open if it asked for the why; each ask
  // lands once, so a later click elsewhere keeps its own focus.
  const handled = React.useRef(0);
  React.useEffect(() => {
    if (!ask || ask.ref !== item.ref || ask.n === handled.current) return;
    const id = ask.what === "why" ? `why-${item.ref}` : ask.what === "snooze" ? `snooze-${item.ref}` : "panel-title";
    const target = document.getElementById(id);
    if (!target) return;
    handled.current = ask.n;
    target.focus({ preventScroll: ask.what === "heading" });
    if (ask.what !== "heading") target.scrollIntoView({ block: "nearest" });
  }, [ask, item.ref, drop]);
  return (
    <div className="grid gap-6 px-4 py-4">
      <div className="grid gap-2">
        <Field item={item.ref} name="title" label="Title" value={item.title} />
        <p className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-sm bg-hover px-1.5 py-0.5 text-ink">{item.state_line}</span>
          <span className="text-ink-muted">{item.stream}</span>
        </p>
      </div>
      {!item.archived && <Call item={item} drop={drop} problem={callProblem} onCall={onCall} onDrop={onDrop} />}
      <Section label="The item" className="grid-cols-2 gap-x-3 gap-y-3">
        <Field item={item.ref} name="who" label="Who" value={item.who} />
        <Field item={item.ref} name="due" label="Due" value={item.due} />
        <Field item={item.ref} name="stream" label="Stream" value={item.stream} />
        <Field item={item.ref} name="kind" label="Kind" value={item.kind} />
      </Section>
      {item.meeting && (
        <Section title={`On ${item.meeting.ref}: ${item.meeting.subject}`} id={`meeting-${item.ref}`}>
          <p className="text-xs text-ink-muted tabular-nums">{item.meeting.when}</p>
          {item.meeting.points.length ? (
            <ol className="grid gap-1 text-sm">
              {item.meeting.points.map((p) => (
                <li key={p.n} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-xs text-ink-muted">#{p.n}</span>
                  <span className="text-xs text-ink-muted">{p.state}</span>
                  <span dir="auto">{p.text}</span>
                  {p.answer && (
                    <span className="text-ink-muted" dir="auto">
                      {p.answer}
                    </span>
                  )}
                  {p.item && <span className="font-mono text-xs text-ink-muted">{p.item}</span>}
                  {p.proposed && (
                    <span className="rounded-sm border border-rule px-1 text-xs text-ink-muted">proposed</span>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-ink-muted">No talking points yet.</p>
          )}
        </Section>
      )}
      <Section label="Notes" className="gap-3">
        <Field item={item.ref} name="description" label="Description" value={item.description} rows={2} />
        <Field item={item.ref} name="notes" label="Working notes" value={item.notes} rows={4} />
        <Field item={item.ref} name="outcome" label="Outcome" value={item.outcome} rows={2} />
      </Section>
      <Materials owner={item.ref} materials={item.materials} viewer={viewer} onView={onView} />
      <Section title="Where it came from" id={`source-${item.ref}`}>
        <div className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
          <span className="min-w-0 font-mono text-xs break-all">
            <Linked link={item.source}>{item.source.ref}</Linked>
          </span>
          <CopyButton value={item.source.ref} what="the source" />
        </div>
      </Section>
      <p className="text-xs text-ink-muted">{item.facts}</p>
      <History owner={item.ref} history={item.history} onUndo={onUndo} />
    </div>
  );
}
