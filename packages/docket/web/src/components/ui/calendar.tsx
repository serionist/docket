import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import * as React from "react";
import { type DayButton, DayPicker, getDefaultClassNames } from "react-day-picker";
import { Button, buttonVariants } from "@/components/ui/button";
import { dayName } from "@/lib/dates";
import { cn } from "@/lib/utils";

/**
 * shadcn/ui's Calendar on react-day-picker, in the ledger's tokens: a month of ruled weeks, 32 px days (44 on a
 * coarse pointer), the picked day in ledger green and today underlined in it. A day's button is named in full with
 * its number as a word, "Friday 23 October 2026", so its name holds the number it shows.
 */
function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  components,
  labels,
  ...props
}: React.ComponentProps<typeof DayPicker>) {
  const defaults = getDefaultClassNames();
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      labels={{
        labelDayButton: (date, modifiers) =>
          `${dayName(date)}${modifiers.today ? ", today" : ""}${modifiers.selected ? ", picked" : ""}`,
        ...labels,
      }}
      className={cn(
        "group/calendar bg-sheet p-3 [--cell-size:--spacing(8)] pointer-coarse:[--cell-size:--spacing(11)]",
        className,
      )}
      classNames={{
        root: cn("w-fit", defaults.root),
        months: cn("relative flex flex-col gap-4", defaults.months),
        month: cn("flex w-full flex-col gap-2", defaults.month),
        nav: cn("absolute inset-x-0 top-0 flex w-full items-center justify-between gap-1", defaults.nav),
        button_previous: cn(
          buttonVariants({ variant: "ghost" }),
          "size-(--cell-size) p-0 select-none aria-disabled:opacity-50",
          defaults.button_previous,
        ),
        button_next: cn(
          buttonVariants({ variant: "ghost" }),
          "size-(--cell-size) p-0 select-none aria-disabled:opacity-50",
          defaults.button_next,
        ),
        month_caption: cn(
          "flex h-(--cell-size) w-full items-center justify-center px-(--cell-size)",
          defaults.month_caption,
        ),
        caption_label: cn("text-sm font-semibold select-none", defaults.caption_label),
        month_grid: cn("w-full border-collapse", defaults.month_grid),
        weekdays: cn("flex", defaults.weekdays),
        weekday: cn(
          "flex h-6 w-(--cell-size) items-center justify-center text-xs font-normal text-ink-muted select-none",
          defaults.weekday,
        ),
        week: cn("flex w-full border-t border-rule", defaults.week),
        day: cn("group/day relative size-(--cell-size) p-0 text-center select-none", defaults.day),
        today: cn("text-green", defaults.today),
        outside: cn("text-ink-muted", defaults.outside),
        disabled: cn("text-ink-muted opacity-50", defaults.disabled),
        hidden: cn("invisible", defaults.hidden),
        ...classNames,
      }}
      components={{
        Root: ({ className: rootClass, rootRef, ...rest }) => (
          <div data-slot="calendar" ref={rootRef} className={cn(rootClass)} {...rest} />
        ),
        Chevron: ({ className: chevronClass, orientation, ...rest }) => {
          const Icon =
            orientation === "left" ? ChevronLeftIcon : orientation === "right" ? ChevronRightIcon : ChevronDownIcon;
          return <Icon className={cn("size-4", chevronClass)} {...rest} />;
        },
        DayButton: CalendarDayButton,
        ...components,
      }}
      {...props}
    />
  );
}

function CalendarDayButton({ className, day, modifiers, ...props }: React.ComponentProps<typeof DayButton>) {
  const defaults = getDefaultClassNames();
  const ref = React.useRef<HTMLButtonElement>(null);
  React.useEffect(() => {
    if (modifiers.focused) ref.current?.focus();
  }, [modifiers.focused]);
  return (
    <Button
      ref={ref}
      variant="ghost"
      data-day={day.isoDate}
      data-selected-single={modifiers.selected || undefined}
      className={cn(
        "size-(--cell-size) rounded-sm p-0 font-normal tabular-nums group-data-[today=true]/day:font-semibold group-data-[today=true]/day:underline group-data-[today=true]/day:decoration-2 group-data-[today=true]/day:underline-offset-4 data-[selected-single=true]:bg-green data-[selected-single=true]:font-semibold data-[selected-single=true]:text-on-green data-[selected-single=true]:no-underline",
        defaults.day_button,
        className,
      )}
      {...props}
    />
  );
}

export { Calendar, CalendarDayButton };
