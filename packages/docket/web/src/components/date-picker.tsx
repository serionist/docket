/**
 * A day field (DR-0710): a button that shows the day as the app writes it, "Mon 26 Oct", whatever the browser's
 * language, and opens a month to pick from, weeks from Monday, with a box under it that takes a day typed as
 * 2026-10-23. A day picked or typed is the field's change at once. The month opens in a popover, a dialog, so no key
 * pressed in it reaches the page's keys (DR-0711), and Esc closes it alone.
 */
import { CalendarDaysIcon } from "lucide-react";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { dayText, isoOf, parseDay } from "@/lib/dates";
import { cn } from "@/lib/utils";

export interface DatePickerProps {
  id: string;
  /** The day as ISO writes it, or "" for none. */
  value: string;
  /** A day picked or typed, as ISO writes it; "" when cleared. */
  onPick: (day: string) => void;
  /** The owner's today from the server, which the month marks and the year rule reads. */
  today: string;
  /** What the day is for, which names the button with its day and the month's dialog: "Due", "Snooze to a day". */
  label: string;
  /** What the button says with no day. */
  empty?: string;
  /** The first day it takes, as ISO writes it. */
  min?: string;
  /** The words of the button that clears the day, when it may be cleared. */
  clear?: string;
  size?: "default" | "sm";
  disabled?: boolean;
  /** The id of words that say why the day was refused, which also mark the button red. */
  problem?: string;
  className?: string;
}

export function DatePicker({
  id,
  value,
  onPick,
  today,
  label,
  empty = "No date",
  min,
  clear,
  size = "default",
  disabled,
  problem,
  className,
}: DatePickerProps) {
  const [open, setOpen] = React.useState(false);
  const [typed, setTyped] = React.useState("");
  const [wrong, setWrong] = React.useState("");
  const picked = parseDay(value);
  const first = min ? parseDay(min) : undefined;
  const now = parseDay(today) ?? new Date();
  const shown = picked ? dayText(value, today) : empty;
  const typedId = `${id}-typed`;

  const pick = (day: string) => {
    setOpen(false);
    onPick(day);
  };
  const take = () => {
    const day = parseDay(typed);
    if (!day) setWrong("Write the day as 2026-10-23.");
    else if (first && day < first) setWrong(`The first day it takes is ${dayText(isoOf(first), today)}.`);
    else pick(isoOf(day));
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setTyped(value);
          setWrong("");
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          id={id}
          size={size}
          disabled={disabled}
          aria-label={`${label}, ${shown}`}
          aria-describedby={problem}
          className={cn(
            "justify-start gap-2 font-normal tabular-nums",
            !picked && "text-ink-muted",
            problem && "border-red",
            className,
          )}
        >
          <CalendarDaysIcon aria-hidden className="text-ink-muted" />
          {shown}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        aria-label={label}
        className="w-auto p-0"
        // The month focuses its picked day, or today, so the arrows move from there.
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <Calendar
          mode="single"
          required
          selected={picked}
          onSelect={(day: Date | undefined) => day && pick(isoOf(day))}
          weekStartsOn={1}
          today={now}
          defaultMonth={picked ?? first ?? now}
          disabled={first ? { before: first } : undefined}
          autoFocus
        />
        <div className="grid gap-1 border-t border-rule px-3 pt-2 pb-3">
          <label htmlFor={typedId} className="text-xs font-semibold text-ink-muted">
            Or type the day
          </label>
          <div className="flex gap-1">
            <Input
              id={typedId}
              value={typed}
              placeholder="2026-10-23"
              autoComplete="off"
              spellCheck={false}
              inputMode="numeric"
              aria-invalid={wrong ? true : undefined}
              aria-describedby={wrong ? `${typedId}-problem` : undefined}
              onChange={(event) => {
                setTyped(event.target.value);
                setWrong("");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  take();
                }
              }}
              className="h-7 flex-1 tabular-nums"
            />
            <Button type="button" size="sm" onClick={take}>
              Set
            </Button>
          </div>
          {wrong && (
            <p id={`${typedId}-problem`} role="alert" className="text-xs text-red">
              {wrong}
            </p>
          )}
          {clear && picked && (
            <Button type="button" size="sm" variant="ghost" className="justify-self-start" onClick={() => pick("")}>
              {clear}
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
