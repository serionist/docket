import type * as React from "react";
import { cn } from "@/lib/utils";

/** The four calls on an item (DR-0707): the one bold thing on a page, each an ink stamp of its own. */
export const VERDICTS = ["discuss", "claude", "me", "chase"] as const;
export type Verdict = (typeof VERDICTS)[number];

const INK: Record<Verdict, string> = {
  discuss: "border-discuss bg-discuss-wash text-discuss",
  claude: "border-claude bg-claude-wash text-claude",
  me: "border-me bg-me-wash text-me",
  chase: "border-chase bg-chase-wash text-chase",
};

/** A call's button while it holds: in the call's own ink, so the panel's buttons and the row's stamp agree. */
export const STAMP_PRESSED: Record<Verdict, string> = {
  discuss: "relative z-[1] border-discuss bg-discuss-wash font-semibold text-discuss hover:bg-discuss-wash",
  claude: "relative z-[1] border-claude bg-claude-wash font-semibold text-claude hover:bg-claude-wash",
  me: "relative z-[1] border-me bg-me-wash font-semibold text-me hover:bg-me-wash",
  chase: "relative z-[1] border-chase bg-chase-wash font-semibold text-chase hover:bg-chase-wash",
};

/** A call's small key button on a row, its digit in the call's own ink; pressed, in its wash too. */
export const STAMP_KEY: Record<Verdict, string> = {
  discuss: "border-discuss text-discuss hover:bg-discuss-wash aria-pressed:bg-discuss-wash",
  claude: "border-claude text-claude hover:bg-claude-wash aria-pressed:bg-claude-wash",
  me: "border-me text-me hover:bg-me-wash aria-pressed:bg-me-wash",
  chase: "border-chase text-chase hover:bg-chase-wash aria-pressed:bg-chase-wash",
};

/** A call's key, beside its name on a button, in the call's own ink. */
export const STAMP_INK: Record<Verdict, string> = {
  discuss: "text-discuss",
  claude: "text-claude",
  me: "text-me",
  chase: "text-chase",
};

/** Whether a string names one of the four calls. */
export function isVerdict(value: string | null | undefined): value is Verdict {
  return (VERDICTS as readonly string[]).includes(value ?? "");
}

/** What each call means, for a tooltip or a screen reader. */
export const MEANING: Record<Verdict, string> = {
  discuss: "talk it through with Claude",
  claude: "Claude does it",
  me: "you do it",
  chase: "nudge someone",
};

/** A verdict as a rubber stamp: its ink, a double rule, a wash of the same ink behind. */
export function Stamp({ verdict, className, ...props }: React.ComponentProps<"span"> & { verdict: Verdict }) {
  return (
    <span
      data-slot="stamp"
      data-verdict={verdict}
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-sm border-[3px] border-double px-1.5 text-xs leading-none font-semibold whitespace-nowrap",
        INK[verdict],
        className,
      )}
      {...props}
    >
      {verdict}
    </span>
  );
}
