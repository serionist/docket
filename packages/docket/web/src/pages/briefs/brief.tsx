/**
 * An open brief (DR-0780): its head, with the way back to the list, its type, rev and age, its state and locator,
 * and for a person their items on the Ledger (DR-0669); why it is stale, its newest new records; then its lines. A
 * line that cites something is a link that opens its raw beside the brief, the open one in the selection's green; a
 * line that cites a line that changed since is marked in ochre, a Decisions line no confirmed decision backs says so
 * (DR-0565), and a line a never-store list now names is hidden and opens nothing (DR-0558).
 */
import { ArrowLeftIcon, NotebookTextIcon } from "lucide-react";
import type * as React from "react";
import { AppLink, plainClick } from "@/components/app-link";
import { Locator, Note, PartHead } from "@/components/doc";
import { cn } from "@/lib/utils";
import { CopyButton } from "@/pages/day/panel";
import type { BriefLine, BriefsView } from "./api";
import { Cites, StateBadge } from "./parts";

const LINK = "inline-flex min-h-6 items-center text-green underline-offset-4 hover:underline pointer-coarse:min-h-11";

/** The way back to the list, which keeps the type the list was narrowed to. */
export function Back({ href }: { href: string }) {
  return (
    <AppLink href={href} className={cn(LINK, "gap-1 text-sm")}>
      <ArrowLeftIcon aria-hidden className="size-4" />
      All briefs
    </AppLink>
  );
}

/** What the page is about, above its title's words: a brief or an entity's decisions, with its facts. */
export function Eyebrow({ kind, children }: { kind: string; children?: React.ReactNode }) {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-muted">
      <span className="inline-flex items-center gap-1 text-ink">
        <NotebookTextIcon aria-hidden className="size-3.5" />
        {kind}
      </span>
      {children}
    </p>
  );
}

export function BriefHead({ view }: { view: BriefsView }) {
  return (
    <div className="grid shrink-0 gap-2 border-b border-rule px-3 py-3 sm:px-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <Back href={view.back} />
        {view.ledger && (
          <AppLink href={view.ledger.url} dir="auto" className={cn(LINK, "text-sm")}>
            {view.ledger.text}
          </AppLink>
        )}
      </div>
      <Eyebrow kind="brief">
        {view.meta.map((part) => (
          <span key={part} className="tabular-nums">
            {part}
          </span>
        ))}
        <StateBadge state={view.state} />
      </Eyebrow>
      <p className="flex min-w-0 items-center gap-1">
        <Locator value={view.id} className="text-ink" />
        <CopyButton value={view.id} what="the brief's id" />
      </p>
      {view.notices.map((notice) => (
        <Note key={notice} tone="warn">
          {notice}
        </Note>
      ))}
      {view.new.length > 0 && (
        <ul aria-label="New records" className="flex flex-wrap gap-x-3">
          {view.new.map((link) => (
            <li key={link.url}>
              <AppLink href={link.url} title={link.title || undefined} className={cn(LINK, "font-mono text-xs")}>
                {link.text}
              </AppLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Line({ line, onOpen }: { line: BriefLine; onOpen: (line: BriefLine) => void }) {
  const marked = line.flagged || line.unconfirmed;
  const body = (
    <span
      dir="auto"
      className={cn(
        "text-sm text-pretty break-words",
        line.style === "heading" && "font-semibold",
        line.style === "bullet" && "before:mr-1.5 before:text-ink-muted before:content-['•']",
        line.hidden && "text-ink-muted italic",
      )}
    >
      {line.body}
    </span>
  );
  return (
    <li
      data-line={line.n}
      data-open={line.open ? "1" : undefined}
      data-flagged={marked ? "1" : undefined}
      className={cn(
        "relative grid grid-cols-[2.25rem_minmax(0,1fr)] gap-x-2 py-1 pr-3 sm:pr-4",
        line.open && "bg-selection",
        line.style === "heading" && "pt-3",
      )}
    >
      {(line.open || marked) && (
        <span aria-hidden className={cn("absolute inset-y-0 left-0 w-0.5", line.open ? "bg-green" : "bg-me")} />
      )}
      <span aria-hidden className="pt-px text-right font-mono text-xs text-ink-muted tabular-nums">
        {line.n}
      </span>
      <span className="min-w-0">
        {line.url ? (
          <a
            href={line.url}
            data-row={String(line.n)}
            aria-current={line.open ? "true" : undefined}
            onClick={(event) => {
              if (!plainClick(event)) return;
              event.preventDefault();
              onOpen(line);
            }}
            className="cursor-default rounded-sm text-ink outline-offset-2 hover:underline hover:underline-offset-4"
          >
            {body}
            <span className="sr-only">{line.open ? " (its raw is open)" : ", open its raw"}</span>
          </a>
        ) : (
          body
        )}
        {line.flagged && (
          <span className="ml-2 inline-flex h-5 items-center rounded-sm border border-me px-1.5 align-middle text-xs whitespace-nowrap text-me">
            changed under it
          </span>
        )}
        {line.unconfirmed && (
          <span className="ml-2 inline-flex h-5 items-center rounded-sm border border-me px-1.5 align-middle text-xs whitespace-nowrap text-me">
            not a confirmed decision
          </span>
        )}
        <Cites anchors={line.anchors} refs={line.refs} className="mt-0.5" />
      </span>
    </li>
  );
}

/** The brief's lines, after its frontmatter; the open line's raw is the panel's. */
export function BriefLines({ view, onOpen }: { view: BriefsView; onOpen: (line: BriefLine) => void }) {
  const shown = view.lines.filter((line) => line.style !== "blank");
  return (
    <section aria-labelledby="brief-title" className="grid gap-1 py-3">
      <PartHead id="brief-title" level={2} className="px-3 sm:px-4">
        The brief
      </PartHead>
      {!view.raw && view.choose && <p className="px-3 text-xs text-ink-muted sm:px-4">{view.choose}</p>}
      <ol className="border-t border-rule px-1 sm:px-2">
        {shown.map((line) => (
          <Line key={line.n} line={line} onOpen={onOpen} />
        ))}
      </ol>
    </section>
  );
}
