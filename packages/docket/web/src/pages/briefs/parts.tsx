/**
 * What the Briefs page's parts share: a brief's state and a decision's standing as their badges, and a line's
 * citations, each anchor a link to the raw it names on the Search or the Recording page and each ref a link to the doc
 * line it cites. Every text is a record's or a doc's, shown as text.
 */
import type * as React from "react";
import { AppLink } from "@/components/app-link";
import { cn } from "@/lib/utils";
import type { BriefLink, BriefRow, Card } from "./api";

const BADGE = "inline-flex h-5 shrink-0 items-center rounded-sm border px-1.5 text-xs leading-none whitespace-nowrap";

/** A brief's state: stale and orphaned in ochre, since they wait for a new rev; current quiet. */
export function StateBadge({ state, className }: { state: BriefRow["state"] | null | undefined; className?: string }) {
  if (!state) return null;
  if (state === "current") {
    return <span className={cn("text-xs whitespace-nowrap text-ink-muted", className)}>current</span>;
  }
  return (
    <span data-state={state} className={cn(BADGE, "border-me bg-me-wash text-me", className)}>
      {state}
    </span>
  );
}

const STANDING: Record<Card["standing"], string> = {
  proposed: "border-me bg-me-wash text-me",
  current: "border-green text-green",
  superseded: "border-rule text-ink-muted",
  rejected: "border-rule text-ink-muted",
};

/** Where a decision stands: proposed in the owner's ochre, current in ledger green, the rest muted. */
export function StandingBadge({ standing }: { standing: Card["standing"] }) {
  return <span className={cn(BADGE, STANDING[standing])}>{standing}</span>;
}

const CITE =
  "inline-flex min-h-6 items-center font-mono text-xs wrap-anywhere text-green underline-offset-4 hover:underline pointer-coarse:min-h-11";

/** A line's anchors, then its refs, each a link where it names a record or a doc line. */
export function Cites({
  anchors,
  refs,
  className,
}: {
  anchors: BriefLink[];
  refs: BriefLink[];
  className?: string;
}): React.ReactNode {
  if (!anchors.length && !refs.length) return null;
  return (
    <span className={cn("flex flex-wrap gap-x-2", className)}>
      {[...anchors, ...refs].map((link, at) =>
        link.url ? (
          // An anchor's text may repeat on a line; its place doesn't.
          <AppLink key={at} href={link.url} title={link.title || undefined} className={CITE}>
            {link.text}
          </AppLink>
        ) : (
          <span key={at} className="font-mono text-xs break-all text-ink-muted">
            {link.text}
          </span>
        ),
      )}
    </span>
  );
}
