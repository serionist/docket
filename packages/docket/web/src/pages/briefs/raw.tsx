/**
 * The open line's raw beside the brief (DR-0780): the line's words, what verify says of it, the raw its anchors point
 * at with a link to where each opens, then each doc line it was built from, followed one level, as it reads now with
 * its own raw. A line a never-store list now names opens nothing; a cited line a list now names is shown as hidden.
 */
import { AppLink } from "@/components/app-link";
import { Note, PartHead, RawSpan } from "@/components/doc";
import { Refusal } from "@/components/shell/page";
import { cn } from "@/lib/utils";
import type { RawView } from "./api";

const LINK = "inline-flex min-h-6 items-center text-green underline-offset-4 hover:underline pointer-coarse:min-h-11";

type SpanList = RawView["spans"];

function Spans({ spans, onCopy }: { spans: SpanList; onCopy: (locator: string) => void }) {
  return (
    <>
      {spans.map((raw) => (
        <div key={`${raw.span.rid}${raw.span.anchor}`}>
          <RawSpan span={raw.span} onCopy={onCopy} />
          {raw.link && (
            <p className="border-b border-rule px-3 py-1 text-xs">
              <AppLink href={raw.link.url} title={raw.link.title || undefined} className={LINK}>
                {raw.link.text}
              </AppLink>
            </p>
          )}
        </div>
      ))}
    </>
  );
}

export function RawBody({ raw, onCopy }: { raw: RawView; onCopy: (locator: string) => void }) {
  return (
    <div id="raw" className="grid gap-3 pb-6">
      <div className="grid gap-2 px-3 pt-3 sm:px-4">
        {raw.body && (
          <blockquote dir="auto" className="border-l-2 border-green bg-selection px-3 py-2 text-sm break-words">
            {raw.body}
          </blockquote>
        )}
        {raw.flags.map((flag) => (
          <Note key={flag} tone="warn">
            {flag}
          </Note>
        ))}
        {raw.problems.map((problem) => (
          <Refusal key={problem}>{problem}</Refusal>
        ))}
        {raw.empty && <p className="text-sm text-ink-muted">{raw.empty}</p>}
      </div>
      {raw.spans.length > 0 && (
        <section aria-labelledby="raw-spans" className="grid gap-1">
          <PartHead id="raw-spans" className="px-3 sm:px-4">
            Raw
          </PartHead>
          <div className="border-t border-rule">
            <Spans spans={raw.spans} onCopy={onCopy} />
          </div>
        </section>
      )}
      {raw.cited.length > 0 && (
        <section aria-labelledby="raw-cited" className="grid gap-1">
          <PartHead id="raw-cited" className="px-3 sm:px-4">
            Built from
          </PartHead>
          <ol className="border-t border-rule">
            {raw.cited.map((cited, at) => (
              <li key={`${at}-${cited.link.url}`} data-cited-line className="border-b border-rule">
                <p className="px-3 pt-2 sm:px-4">
                  <AppLink
                    href={cited.link.url}
                    title={cited.link.title || undefined}
                    className={cn(LINK, "font-mono text-xs break-all")}
                  >
                    {cited.link.text}
                  </AppLink>
                </p>
                <div className="px-3 pt-1 pb-2 sm:px-4">
                  {cited.problem ? (
                    <Note tone="warn">{cited.problem}</Note>
                  ) : (
                    <p dir="auto" className={cn("text-sm break-words", cited.hidden && "text-ink-muted italic")}>
                      {cited.text}
                    </p>
                  )}
                </div>
                {cited.spans.length > 0 && (
                  <div className="ml-3 border-t border-rule sm:ml-4">
                    <Spans spans={cited.spans} onCopy={onCopy} />
                  </div>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

/** The panel's heading words: the line it shows. */
export function rawTitle(raw: RawView): string {
  return `Line ${raw.n}`;
}
