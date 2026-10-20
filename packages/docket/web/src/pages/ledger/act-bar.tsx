/**
 * The act bar over the ticked rows (DR-0710): every call on all of them at once, through a dry run that skips and
 * names the rows its op doesn't take. A verdict writes by the old bulk rule: it clears when every ticked row holds it
 * and sets it on all of them otherwise. Enter in the why drops; Enter in the day snoozes to it.
 */
import { XIcon } from "lucide-react";
import * as React from "react";
import { Refusal } from "@/components/shell/page";
import { isVerdict, STAMP_INK, STAMP_PRESSED } from "@/components/stamp";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { pressed, writes } from "@/lib/day";
import { useUnsaved } from "@/lib/drafts";
import { cn } from "@/lib/utils";
import type { Body, Button as Verdict, Row, Snooze } from "./api";
import { leaveOnEscape } from "./field";

export const ACT_WHY = "act-why";
export const ACT_SNOOZE = "act-snooze";

export function ActBar({
  rows,
  verdicts,
  snooze,
  tomorrow,
  pending,
  problem,
  onCall,
  onClear,
}: {
  rows: Row[];
  verdicts: Verdict[];
  snooze: Snooze[];
  tomorrow: string;
  pending: boolean;
  problem: string;
  onCall: (body: Partial<Body>) => Promise<boolean>;
  onClear: () => void;
}) {
  const [why, setWhy] = React.useState("");
  const [until, setUntil] = React.useState("");
  useUnsaved("act:why", why.trim() !== "");
  const held = rows.map((r) => r.triage);
  const drop = async () => {
    if (await onCall({ op: "drop", note: why })) setWhy("");
  };
  const snoozeTo = async (day: string) => {
    if (await onCall({ op: "snooze", until: day })) setUntil("");
  };
  return (
    <section
      aria-label="The ticked rows"
      className="grid max-h-[45vh] shrink-0 gap-2 overflow-y-auto border-t border-rule bg-sheet px-3 py-2 shadow-[0_-1px_0_var(--rule)] sm:px-4"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-sm font-semibold tabular-nums">{rows.length} ticked</p>
        <div role="group" aria-label="Call" className="flex">
          {verdicts.map((button) => {
            const on = pressed(button.verdict, held);
            return (
              <button
                key={button.verdict}
                type="button"
                disabled={pending}
                aria-pressed={on}
                title={`${button.title}${on ? "; again clears it" : ""}`}
                onClick={() => void onCall({ op: "triage", verdict: writes(button.verdict, held) })}
                className={cn(
                  "tap -ml-px flex h-7 cursor-default items-center gap-1.5 border border-control bg-sheet px-2 text-sm first:ml-0 first:rounded-l-sm last:rounded-r-sm hover:bg-hover focus-visible:z-10 disabled:opacity-50",
                  on && isVerdict(button.verdict) && STAMP_PRESSED[button.verdict],
                )}
              >
                {button.label}
                <span
                  className={cn(
                    "text-xs pointer-coarse:hidden",
                    isVerdict(button.verdict) && STAMP_INK[button.verdict],
                  )}
                >
                  {button.key}
                </span>
              </button>
            );
          })}
        </div>
        <Button size="sm" disabled={pending} onClick={() => void onCall({ op: "triage", verdict: null })}>
          No call <span className="text-xs text-ink-muted pointer-coarse:hidden">0</span>
        </Button>
        <Button size="sm" disabled={pending} onClick={() => void onCall({ op: "done" })}>
          Done <span className="text-xs text-ink-muted pointer-coarse:hidden">e</span>
        </Button>
        <Button size="sm" disabled={pending} onClick={() => void onCall({ op: "reopen" })}>
          Reopen
        </Button>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClear}>
          <XIcon />
          Clear <Kbd className="h-4 min-w-4 shadow-none pointer-coarse:hidden">Esc</Kbd>
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div role="group" aria-label="Snooze" className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-xs font-semibold text-ink-muted">Snooze</span>
          {snooze.map((choice, at) => (
            <Button
              key={choice.until}
              id={at === 0 ? ACT_SNOOZE : undefined}
              size="sm"
              disabled={pending}
              title={choice.title}
              onClick={() => void snoozeTo(choice.until)}
            >
              {choice.label}
            </Button>
          ))}
          <label className="sr-only" htmlFor="act-until">
            Snooze to a day
          </label>
          <Input
            id="act-until"
            type="date"
            min={tomorrow}
            value={until}
            onChange={(event) => setUntil(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void snoozeTo(until);
              } else leaveOnEscape(event);
            }}
            className="h-7 w-36"
          />
          <Button size="sm" disabled={pending} onClick={() => void snoozeTo(until)}>
            Snooze
          </Button>
        </div>
        <div className="flex min-w-60 flex-1 items-center gap-1">
          <label htmlFor={ACT_WHY} className="mr-1 text-xs font-semibold text-ink-muted">
            Why
          </label>
          <Input
            id={ACT_WHY}
            value={why}
            autoComplete="off"
            dir="auto"
            placeholder="a drop needs one"
            onChange={(event) => setWhy(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void drop();
              } else leaveOnEscape(event);
            }}
            className="h-7 flex-1"
          />
          <Button size="sm" variant="destructive" disabled={pending} onClick={() => void drop()}>
            Drop
          </Button>
        </div>
      </div>
      {problem && <Refusal>{problem}</Refusal>}
    </section>
  );
}
