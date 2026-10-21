/**
 * What Search's panel and the Recording page both draw (DR-0775, DR-0776): a snippet with the words search found
 * under the highlighter, a summary's lines with their anchors, the raw an anchor points at, and the notes a page says
 * beside them. Every text here is a record's or a doc's, shown as text: nothing is parsed as markup.
 */
import { CircleAlertIcon, InfoIcon } from "lucide-react";
import type * as React from "react";
import { AppLink } from "@/components/app-link";
import { cn } from "@/lib/utils";
import type { components } from "@/api/schema";

type Schemas = components["schemas"];
type Piece = Schemas["Piece"];
type SummaryLine = Schemas["SummaryLine"];
type SpanView = Schemas["SpanView"];

/** The words a search found, under the highlighter; the rest as written. */
export function Snippet({ pieces, className }: { pieces: Piece[]; className?: string }) {
  return (
    <span dir="auto" className={className}>
      {pieces.map((piece, at) =>
        piece.mark ? (
          // The pieces' order never changes for one snippet, so its place is its key.
          <mark key={at} className="rounded-[2px] bg-mark px-px text-ink">
            {piece.text}
          </mark>
        ) : (
          <span key={at}>{piece.text}</span>
        ),
      )}
    </span>
  );
}

/** A note beside what it concerns: a warning in ochre, or a plain fact. */
export function Note({
  tone = "info",
  children,
  className,
}: {
  tone?: "info" | "warn";
  children: React.ReactNode;
  className?: string;
}) {
  const Icon = tone === "warn" ? CircleAlertIcon : InfoIcon;
  return (
    <p
      role="status"
      className={cn(
        "flex items-start gap-2 rounded-sm border-l-2 px-3 py-2 text-sm text-ink",
        tone === "warn" ? "border-me bg-me-wash" : "border-rule bg-hover",
        className,
      )}
    >
      <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", tone === "warn" ? "text-me" : "text-ink-muted")} />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

/** A heading over a part of a panel or a column, in the list heads' small muted words. */
export function PartHead({ id, children, className }: { id: string; children: React.ReactNode; className?: string }) {
  return (
    <h3 id={id} className={cn("text-xs font-semibold text-ink-muted", className)}>
      {children}
    </h3>
  );
}

/**
 * A summary's lines: each its number, its words styled as the markdown said, and its anchors, each a link to the raw
 * it points at. A line that points at what the page marks stands in the selection's green, with words for a screen
 * reader saying so.
 */
export function SummaryLines({ lines, citedSays }: { lines: SummaryLine[]; citedSays: string }) {
  const shown = lines.filter((line) => line.style !== "blank");
  return (
    <ol className="grid">
      {shown.map((line) => (
        <li
          key={line.n}
          data-line={line.n}
          data-cited={line.cited ? "1" : undefined}
          className={cn(
            "relative grid grid-cols-[2.25rem_minmax(0,1fr)] gap-x-2 py-1 pr-2",
            line.cited && "bg-selection",
            line.style === "heading" && "pt-3",
          )}
        >
          {line.cited && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
          <span aria-hidden className="pt-px text-right font-mono text-xs text-ink-muted tabular-nums">
            {line.n}
          </span>
          <span className="min-w-0">
            <span
              dir="auto"
              className={cn(
                "text-sm text-pretty break-words",
                line.style === "heading" && "font-semibold",
                line.style === "bullet" && "before:mr-1.5 before:text-ink-muted before:content-['•']",
              )}
            >
              {line.body}
            </span>
            {line.cited && <span className="sr-only"> ({citedSays})</span>}
            {line.anchors.length > 0 && (
              <span className="mt-0.5 flex flex-wrap gap-x-2">
                {line.anchors.map((anchor, at) =>
                  anchor.url ? (
                    <AppLink
                      // An anchor's text may repeat on a line; its place doesn't.
                      key={at}
                      href={anchor.url}
                      title={anchor.title || undefined}
                      className="tap inline-flex min-h-6 items-center font-mono text-xs break-all text-green underline-offset-4 hover:underline"
                    >
                      {anchor.text}
                    </AppLink>
                  ) : (
                    <span key={at} className="font-mono text-xs break-all text-ink-muted">
                      {anchor.text}
                    </span>
                  ),
                )}
              </span>
            )}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** A locator, in the mono face, broken anywhere so a long one wraps. */
export function Locator({ value, className }: { value: string; className?: string }) {
  return (
    <code dir="ltr" className={cn("min-w-0 font-mono text-xs break-all text-ink-muted", className)}>
      {value}
    </code>
  );
}

/**
 * The raw an anchor or a hit points at: its locator and what it is, then its lines with their times, or its
 * paragraphs numbered as the anchors count them; the hit's own stretch in the selection's green.
 */
export function RawSpan({ span, onCopy }: { span: SpanView; onCopy: (locator: string) => void }) {
  return (
    <article
      data-span={span.rid}
      className={cn("relative grid gap-1.5 border-b border-rule py-2 pr-3 pl-3", span.mark && "bg-selection")}
    >
      {span.mark && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <Locator value={span.anchor} className="text-ink" />
        <span dir="auto" className="text-xs text-ink-muted">
          {span.head}
        </span>
        {span.versions && (
          <span className="rounded-sm border border-rule px-1.5 text-xs text-ink-muted">{span.versions}</span>
        )}
      </header>
      {span.problem && <Note tone="warn">{span.problem}</Note>}
      {span.note && <Note>{span.note}</Note>}
      {span.segments.length > 0 && (
        <ol className="grid">
          {span.segments.map((seg) => (
            <li
              key={seg.locator}
              data-mark={seg.mark ? "1" : undefined}
              className={cn(
                "grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-2 rounded-sm px-1 py-0.5",
                seg.mark && "bg-selection",
              )}
            >
              <button
                type="button"
                title={`Copy ${seg.locator}`}
                aria-label={`Copy ${seg.locator}`}
                onClick={() => onCopy(seg.locator)}
                className="tap h-6 cursor-default self-start rounded-sm text-left font-mono text-xs text-ink-muted tabular-nums hover:text-ink"
              >
                {seg.at}
              </button>
              <span className="min-w-0 text-sm">
                <span dir="auto" className="mr-1.5 font-semibold">
                  {seg.speaker}
                </span>
                <span dir="auto" className="break-words whitespace-pre-wrap">
                  {seg.text}
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {span.paragraphs.length > 0 && (
        <ol className="grid gap-1">
          {span.paragraphs.map((para) => (
            <li
              key={para.n}
              value={para.n}
              data-mark={para.mark ? "1" : undefined}
              className={cn(
                "grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2 rounded-sm px-1 py-0.5",
                para.mark && "bg-selection",
              )}
            >
              <span aria-hidden className="text-right font-mono text-xs text-ink-muted tabular-nums">
                {para.n}
              </span>
              <span dir="auto" className="min-w-0 text-sm break-words whitespace-pre-wrap">
                {para.text}
              </span>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}
