/**
 * Meetings' list (DR-0666): the days from today, then the earlier days, newest first, each under a sticky head with
 * its count, and each meeting a ruled row on the day pages' own row (ListRow): its time in the margin, the subject
 * with its badge, then how many of its points and items are open. A meeting under way says now; one that is over has
 * its time in muted ink; a cancelled or declined one, and one with nothing on it, is dimmed.
 */
import { ListTodoIcon, MessagesSquareIcon } from "lucide-react";
import * as React from "react";
import { Empty } from "@/components/shell/page";
import type { Search } from "@/lib/search";
import { cn } from "@/lib/utils";
import type { Day, MeetingLine, Meetings } from "@/pages/day/api";
import { ListRow, rowHref } from "@/pages/day/list";

export interface MeetingRowsProps {
  path: string;
  data: Meetings;
  search: Search;
  selected: string | undefined;
  open: string | undefined;
  onSelect: (ref: string) => void;
  onOpen: (ref: string) => void;
}

/** A meeting's response as its badge: what changes whether the owner goes. */
export function ResponseBadge({ response, className }: { response: string; className?: string }) {
  return (
    <span
      data-slot="response"
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-sm border border-control px-1 text-xs leading-none text-ink-muted",
        response === "tentative" && "border-me text-me",
        className,
      )}
    >
      {response}
    </span>
  );
}

/** A meeting under way. */
function Now() {
  return (
    <span className="inline-flex h-5 shrink-0 items-center rounded-sm bg-green px-1 text-xs leading-none font-semibold text-on-green">
      now
    </span>
  );
}

function Count({
  n,
  of,
  icon: Icon,
  said,
  className,
}: {
  n: number;
  of: number;
  icon: typeof ListTodoIcon;
  said: string;
  className?: string;
}) {
  return (
    <span
      title={said}
      className={cn(
        "flex w-9 shrink-0 items-center justify-end gap-1 text-xs tabular-nums",
        n ? "text-ink" : "text-ink-muted",
        !of && "invisible",
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      {n}
      <span className="sr-only">, {said}</span>
    </span>
  );
}

function MeetingRow({ m, props, tabbable }: { m: MeetingLine; props: MeetingRowsProps; tabbable: boolean }) {
  const points = m.points_open === m.points ? `${m.points} open` : `${m.points_open} of ${m.points} open`;
  return (
    <ListRow
      rowRef={m.ref}
      marks={{ "data-dim": m.dim ? "1" : "" }}
      selected={m.ref === props.selected}
      tabbable={tabbable}
      opened={m.ref === props.open}
      href={rowHref(props.path, props.search, m.ref)}
      onSelect={props.onSelect}
      onOpen={props.onOpen}
    >
      <span
        className={cn("w-[5.75rem] shrink-0 pl-1.5 text-xs whitespace-nowrap tabular-nums", m.over && "text-ink-muted")}
      >
        <time dateTime={m.start} title={m.when}>
          {m.time}
        </time>
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className={cn("truncate", m.dim && "text-ink-muted")} dir="auto">
            {m.subject}
          </span>
          {m.now && <Now />}
          {m.response && <ResponseBadge response={m.response} />}
        </span>
        {m.organiser && (
          <span className="block truncate text-xs text-ink-muted @2xl:hidden" dir="auto">
            {m.organiser}
          </span>
        )}
      </span>
      <span className="hidden w-32 shrink-0 truncate text-xs text-ink-muted @2xl:block" dir="auto">
        {m.organiser}
      </span>
      <span className="hidden w-20 shrink-0 truncate text-xs text-ink-muted @4xl:block">{m.stream}</span>
      <Count n={m.points_open} of={m.points} icon={MessagesSquareIcon} said={`talking points: ${points}`} />
      <Count n={m.items} of={m.items} icon={ListTodoIcon} said={`${m.items} open item${m.items === 1 ? "" : "s"}`} />
      <span className="hidden w-14 shrink-0 text-right font-mono text-xs text-ink-muted @lg:block">{m.ref}</span>
    </ListRow>
  );
}

function DayBlock({ day, props, tab }: { day: Day; props: MeetingRowsProps; tab: string | undefined }) {
  const id = `day-${day.key}`;
  const today = day.label.startsWith("Today");
  return (
    <section aria-labelledby={id}>
      <h2
        id={id}
        className="sticky top-0 z-10 flex min-h-8 items-center gap-2 border-b border-rule bg-paper px-3 text-xs font-semibold text-ink-muted sm:px-4"
      >
        <span className={cn(today && "text-ink")}>{day.label}</span>
        <span className="font-normal tabular-nums">{day.meetings.length}</span>
      </h2>
      <ul>
        {day.meetings.map((m) => (
          <MeetingRow key={m.id} m={m} props={props} tabbable={m.ref === tab} />
        ))}
      </ul>
    </section>
  );
}

export function MeetingRows(props: MeetingRowsProps) {
  const { list } = props.data;
  const listed = list.days.flatMap((d) => d.meetings.map((m) => m.ref));
  const tab = props.selected !== undefined && listed.includes(props.selected) ? props.selected : listed[0];
  if (!list.days.length) {
    const blank = list.empty.startsWith("No meetings");
    return (
      <Empty title={list.empty}>
        {blank ? (
          <p>The calendar's sync adds your meetings, and Claude proposes the points worth raising in them.</p>
        ) : null}
      </Empty>
    );
  }
  const earlier = list.days.findIndex((d) => !d.ahead);
  return (
    <>
      {list.days.map((day, at) => (
        <React.Fragment key={day.key}>
          {at === earlier && (
            <p className="border-b border-rule bg-paper px-3 pt-4 pb-1.5 text-xs text-ink-muted sm:px-4">
              Earlier days, newest first
            </p>
          )}
          <DayBlock day={day} props={props} tab={tab} />
        </React.Fragment>
      ))}
    </>
  );
}
