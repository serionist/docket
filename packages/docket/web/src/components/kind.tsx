import type * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The five kinds of item (DR-0715), each a ledger's margin tab in a hue of its own: a bar of its ink at the left, a
 * wash of it behind, its name written in it. Quieter than a verdict's stamp, which is the page's one bold thing, and
 * never colour alone: the name is always there.
 */
export const KINDS = ["reply", "decision", "task", "invite", "waiting"] as const;
export type Kind = (typeof KINDS)[number];

// Each spelt out, so Tailwind finds the class names in the source.
const TAB: Record<Kind, string> = {
  reply: "border-reply bg-reply-wash text-reply",
  decision: "border-decision bg-decision-wash text-decision",
  task: "border-task bg-task-wash text-task",
  invite: "border-invite bg-invite-wash text-invite",
  waiting: "border-waiting bg-waiting-wash text-waiting",
};

/** The dot a kind's choice carries in a menu, in its ink. */
export const KIND_DOT: Record<Kind, string> = {
  reply: "bg-reply",
  decision: "bg-decision",
  task: "bg-task",
  invite: "bg-invite",
  waiting: "bg-waiting",
};

export function isKind(value: string | null | undefined): value is Kind {
  return (KINDS as readonly string[]).includes(value ?? "");
}

/** A kind as its tab; a kind the app doesn't know reads as plain meta text. */
export function KindTab({ kind, className, ...props }: React.ComponentProps<"span"> & { kind: string }) {
  if (!isKind(kind)) {
    return (
      <span className={cn("text-xs text-ink-muted", className)} {...props}>
        {kind}
      </span>
    );
  }
  return (
    <span
      data-slot="kind"
      data-kind={kind}
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-r-sm border-l-[3px] pr-1.5 pl-1 text-xs leading-none whitespace-nowrap",
        TAB[kind],
        className,
      )}
      {...props}
    >
      {kind}
    </span>
  );
}
