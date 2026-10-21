/**
 * The open hit beside the results (DR-0775): its locator to copy and the panel's tools (verify the summary, the
 * Recording page, less or more raw around it), then the raw it came from with the hit in the selection's green, then
 * the summary covering it with the lines pointing here marked; or the summary verified, each line above the raw its
 * anchors point at, a line whose anchors don't resolve flagged in red.
 */
import { AudioLinesIcon, ListChecksIcon, MinusIcon, PlusIcon } from "lucide-react";
import type * as React from "react";
import { AppLink } from "@/components/app-link";
import { Locator, Note, PartHead, RawSpan, SummaryLines } from "@/components/doc";
import { Refusal } from "@/components/shell/page";
import { buttonVariants } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";
import { CopyButton } from "@/pages/day/panel";
import type { HitPanel, VerifiedLine } from "./api";

const KEY = "h-4 min-w-4 shadow-none pointer-coarse:hidden";

function Tools({ panel }: { panel: HitPanel }) {
  if (!panel.verify && !panel.recording && !panel.unit) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {panel.verify && (
        <AppLink href={panel.verify} className={cn(buttonVariants({ size: "sm" }))}>
          <ListChecksIcon aria-hidden />
          {panel.verify_label}
          <Kbd className={KEY}>v</Kbd>
        </AppLink>
      )}
      {panel.recording && (
        <AppLink href={panel.recording} className={cn(buttonVariants({ size: "sm" }))}>
          <AudioLinesIcon aria-hidden />
          On the Recording page
        </AppLink>
      )}
      {panel.unit && (
        <span role="group" aria-label="Raw around the hit" className="ml-auto inline-flex items-center gap-0.5">
          <Step href={panel.less} label="Less around" keyName="[">
            <MinusIcon aria-hidden />
          </Step>
          <span className="min-w-20 text-center text-xs text-ink-muted tabular-nums">{panel.around_text}</span>
          <Step href={panel.more} label="More around" keyName="]">
            <PlusIcon aria-hidden />
          </Step>
        </span>
      )}
    </div>
  );
}

function Step({
  href,
  label,
  keyName,
  children,
}: {
  href: string | null | undefined;
  label: string;
  keyName: string;
  children: React.ReactNode;
}) {
  const look = cn(buttonVariants({ variant: "ghost", size: "icon-sm" }));
  if (!href) {
    return (
      <span aria-hidden className={cn(look, "opacity-40")}>
        {children}
      </span>
    );
  }
  return (
    <AppLink href={href} aria-label={`${label} (${keyName})`} title={`${label} (${keyName})`} className={look}>
      {children}
    </AppLink>
  );
}

function Pair({ pair, onCopy }: { pair: VerifiedLine; onCopy: (locator: string) => void }) {
  return (
    <li data-flagged={pair.flagged ? "1" : undefined} className="relative border-b border-rule py-1">
      {pair.flagged && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-red" />}
      <SummaryLines lines={[pair.line]} citedSays="points at this hit" />
      {pair.problems.map((problem) => (
        <p key={problem} className="ml-11 text-sm text-red">
          {problem}
        </p>
      ))}
      <div className="ml-9">
        {pair.spans.length ? (
          pair.spans.map((span) => <RawSpan key={`${span.rid}${span.anchor}`} span={span} onCopy={onCopy} />)
        ) : (
          <p className="px-3 py-1 text-sm text-ink-muted">No raw for this line.</p>
        )}
      </div>
    </li>
  );
}

/** The panel's body, below the head the page draws. */
export function HitBody({ panel, onCopy }: { panel: HitPanel; onCopy: (locator: string) => void }) {
  if (panel.mode === "error") {
    return (
      <div className="grid gap-3 p-3 sm:p-4">
        {panel.locator && <Locator value={panel.locator} />}
        <Refusal>{panel.error}</Refusal>
      </div>
    );
  }
  const summary = panel.summary;
  return (
    <div className="grid gap-3 pb-6">
      <div className="grid gap-2 px-3 pt-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-1">
          <Locator value={panel.locator} className="text-ink" />
          <CopyButton value={panel.locator} what="the locator" />
        </div>
        <Tools panel={panel} />
        {panel.resolved && (
          <p className="text-xs text-ink-muted">
            Read as <Locator value={panel.resolved} />
          </p>
        )}
        {panel.problems.map((problem) => (
          <Note key={problem} tone="warn">
            {problem}
          </Note>
        ))}
      </div>
      {panel.mode === "verify" ? (
        <section aria-labelledby="pairs-title" className="grid gap-1">
          <PartHead id="pairs-title" className="px-3 sm:px-4">
            Each line above its raw
          </PartHead>
          {panel.no_summary && <p className="px-3 text-sm text-ink-muted sm:px-4">{panel.no_summary}</p>}
          {panel.pairs.length ? (
            <ol className="border-t border-rule">
              {panel.pairs.map((pair) => (
                <Pair key={pair.line.n} pair={pair} onCopy={onCopy} />
              ))}
            </ol>
          ) : (
            !panel.no_summary && (
              <p className="px-3 text-sm text-ink-muted sm:px-4">No line here has anchors to check.</p>
            )
          )}
        </section>
      ) : (
        <>
          <section aria-labelledby="raw-title" className="grid gap-1">
            <PartHead id="raw-title" className="px-3 sm:px-4">
              Raw
            </PartHead>
            {panel.spans.length ? (
              <div className="border-t border-rule">
                {panel.spans.map((span) => (
                  <RawSpan key={`${span.rid}${span.anchor}`} span={span} onCopy={onCopy} />
                ))}
              </div>
            ) : (
              <p className="px-3 text-sm text-ink-muted sm:px-4">No raw to show.</p>
            )}
          </section>
          <section aria-labelledby="summary-title" className="grid gap-1">
            <PartHead id="summary-title" className="px-3 sm:px-4">
              Summary
            </PartHead>
            {summary && (
              <>
                <p className="px-3 text-xs text-ink-muted sm:px-4">
                  <Locator value={summary.id} />, {summary.type}, rev {summary.rev}
                </p>
                <div className="border-t border-rule px-1 sm:px-2">
                  <SummaryLines lines={summary.lines} citedSays="points at this hit" />
                </div>
              </>
            )}
            {panel.no_summary && <p className="px-3 text-sm text-ink-muted sm:px-4">{panel.no_summary}</p>}
            {panel.citing.length > 0 && (
              <p className="px-3 text-xs text-ink-muted sm:px-4">Also pointing here: {panel.citing.join("; ")}</p>
            )}
            {panel.legacy.length > 0 && (
              <p className="px-3 text-xs text-ink-muted sm:px-4">
                Legacy notes that cover it: {panel.legacy.join("; ")}
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
