/**
 * What the owner copies out of a page: a command or the words to paste into Claude Code, in the mono face, with the
 * button that copies it; and a margin tab, the ruled rows' mark of a state in its ink. Staging, Sync and Settings
 * draw both.
 */
import { CopyIcon } from "lucide-react";
import type * as React from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** A command or the words to paste, with the button that copies it. */
export function Paste({ text, label, onCopy }: { text: string; label: string; onCopy: (text: string) => void }) {
  return (
    <div className="flex items-start gap-2 rounded-sm border border-rule bg-paper py-1 pr-1 pl-2">
      <code dir="ltr" className="min-w-0 flex-1 py-1 font-mono text-xs break-all text-ink" data-paste>
        {text}
      </code>
      <Button size="sm" variant="ghost" onClick={() => onCopy(text)} aria-label={`Copy ${label}`}>
        <CopyIcon />
        Copy
      </Button>
    </div>
  );
}

export type Tone = "green" | "me" | "red" | "muted";

const TONES: Record<Tone, string> = {
  green: "border-green bg-selection text-green",
  me: "border-me bg-me-wash text-me",
  red: "border-red bg-red-wash text-red",
  muted: "border-control bg-hover text-ink-muted",
};

/** A state as a margin tab in its ink: ledger green for what goes ahead, ochre for what waits on the owner, red for a
 * refusal or a failure, muted for what is done or off. Its word is always written on it. */
export function Tab({ tone, className, ...props }: React.ComponentProps<"span"> & { tone: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-r-sm border-l-[3px] pr-1.5 pl-1 text-xs leading-none whitespace-nowrap",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

/** What the page says once a copy went: the copied text at the foot, for a moment. */
export const COPIED_MS = 2500;
