/**
 * Today's list (DR-0773), read at a glance in the morning's order: the next meeting first, its start set large in the
 * margin, where it is and who is in it, and its prep, the open points and the items on it; then the items overdue,
 * due today and back from a snooze, as the Ledger's own rows with their ticks and quick buttons; then the rest of
 * today's meetings and the next working day's, each with its prep cut short; then what closed today. Every row is the
 * day pages' ruled row (ListRow), so j and k, Enter and the panel work as on the other day pages.
 */
import { ListTodoIcon, PlusIcon } from "lucide-react";
import { Link } from "@tanstack/react-router";
import * as React from "react";
import { KindTab } from "@/components/kind";
import { Empty } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { DayMeetingLine, MeetingGroup, Prep, Row, Today } from "@/pages/day/api";
import { GroupBlock, ListRow, type RowProps, rowHref } from "@/pages/day/list";
import { InLink } from "@/pages/meetings/links";
import { MeetingRow, Now, ResponseBadge } from "@/pages/meetings/list";

const TONE: Record<string, string> = {
  late: "font-semibold text-red",
  today: "font-semibold text-ink",
  soon: "text-ink",
  "": "text-ink-muted",
};

export interface TodayRowsProps extends RowProps {
  data: Today;
  onNew: () => void;
}

/** The rows in the order the list shows them, which j and k move through. */
export function orderOf(data: Today): string[] {
  const { list } = data;
  return [
    ...(list.lead ? [list.lead.meeting.line.ref] : []),
    ...list.due.flatMap((g) => g.rows.map((r) => r.ref)),
    ...list.meetings.flatMap((g) => g.meetings.map((m) => m.line.ref)),
    ...(list.closed?.rows.map((r) => r.ref) ?? []),
  ];
}

/** The item rows Today lists, which the ticks, the act bar and the item keys take. */
export function itemRowsOf(data: Today): Row[] {
  return [...data.list.due.flatMap((g) => g.rows), ...(data.list.closed?.rows ?? [])];
}

function Head({ id, children, className }: { id: string; children: React.ReactNode; className?: string }) {
  return (
    <h2
      id={id}
      className={cn(
        "sticky top-0 z-10 flex min-h-8 items-center gap-2 border-b border-rule bg-paper px-3 text-xs font-semibold text-ink-muted sm:px-4",
        className,
      )}
    >
      {children}
    </h2>
  );
}

/** An open point as prep: read here, answered on the meeting's panel. One Claude proposed is drawn in pencil. */
function PrepPoint({ point }: { point: Prep }) {
  return (
    <li
      className={cn(
        "flex min-w-0 items-baseline gap-2",
        point.proposed && "-mx-1.5 w-fit max-w-full rounded-sm border border-dashed border-control px-1.5",
      )}
    >
      <span aria-hidden className="relative top-px size-2 shrink-0 rounded-full border border-ink-muted" />
      <span className="min-w-0" dir="auto">
        {point.text}
        {point.answer && <span className="text-ink-muted">: {point.answer}</span>}
      </span>
      {point.proposed && <span className="shrink-0 text-xs text-ink-muted">proposed</span>}
    </li>
  );
}

/** An item on a meeting: its kind's tab, its number one click from its panel here, its title and how near its due is. */
function PrepItem({ row, href }: { row: Row; href: string }) {
  return (
    <li className="flex min-w-0 items-center gap-2">
      <KindTab kind={row.kind} className="h-4" />
      <InLink href={href} className="tap inline-flex h-6 shrink-0 items-center font-mono text-xs">
        {row.ref}
      </InLink>
      <span className="min-w-0 flex-1 truncate" dir="auto" title={row.title}>
        {row.title}
      </span>
      <span title={row.due.title} className={cn("shrink-0 text-xs tabular-nums", TONE[row.due.tone] ?? "")}>
        {row.due.text}
      </span>
    </li>
  );
}

function more(n: number, one: string, many: string): string {
  return `and ${n} more ${n === 1 ? one : many} on its panel`;
}

/** A meeting's prep under its line: its open points, then the items on it, each cut as the server cut them. */
function PrepList({ m, props, lead }: { m: DayMeetingLine; props: RowProps; lead?: boolean }) {
  if (!m.points.length && !m.items.length) {
    return lead ? <p className="text-sm text-ink-muted">Nothing to prepare: no open points or items on it.</p> : null;
  }
  return (
    <div className={cn("grid gap-2", lead ? "text-sm" : "text-xs")}>
      {m.points.length > 0 && (
        <ul aria-label={`Open points on ${m.line.ref}`} className="grid gap-1">
          {m.points.map((p) => (
            <PrepPoint key={p.id} point={p} />
          ))}
          {m.more_points > 0 && <li className="text-xs text-ink-muted">{more(m.more_points, "point", "points")}</li>}
        </ul>
      )}
      {m.items.length > 0 && (
        <ul aria-label={`Open items on ${m.line.ref}`} className="grid">
          {m.items.map((row) => (
            <PrepItem key={row.id} row={row} href={rowHref(props.path, props.search, row.ref)} />
          ))}
          {m.more_items > 0 && <li className="text-xs text-ink-muted">{more(m.more_items, "item", "items")}</li>}
        </ul>
      )}
    </div>
  );
}

/** The next meeting: its start large in the margin, its end under it, the subject, where and who, then its prep. */
function LeadRow({ m, props, tabbable }: { m: DayMeetingLine; props: RowProps; tabbable: boolean }) {
  const { line } = m;
  const [start, end] = line.time.split("-");
  const facts = [m.where, m.people ? `with ${m.people}` : ""].filter(Boolean).join(", ");
  return (
    <ListRow
      rowRef={line.ref}
      marks={{ "data-lead": "1" }}
      selected={line.ref === props.selected}
      tabbable={tabbable}
      opened={line.ref === props.open}
      href={rowHref(props.path, props.search, line.ref)}
      onSelect={props.onSelect}
      onOpen={props.onOpen}
      below={
        <div className="pr-3 pb-4 pl-[5.125rem] sm:pr-4 sm:pl-[7.125rem]">
          <PrepList m={m} props={props} lead />
        </div>
      }
    >
      <span className="flex w-full items-start gap-3 pt-2">
        <span className="grid w-16 shrink-0 pl-1.5 tabular-nums sm:w-[5.75rem]">
          <time dateTime={line.start} title={line.when} className="text-[21px] leading-7 font-semibold">
            {start}
          </time>
          <span className="text-xs text-ink-muted">to {end}</span>
        </span>
        <span className="grid min-w-0 flex-1 gap-0.5">
          {/* The next meeting's subject is never cut: it wraps, its badges after it. */}
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="min-w-0 text-base leading-7 font-semibold text-pretty" dir="auto">
              {line.subject}
            </span>
            {line.now && <Now />}
            {line.response && <ResponseBadge response={line.response} />}
            <span className="ml-auto hidden shrink-0 font-mono text-xs text-ink-muted @lg:block">{line.ref}</span>
          </span>
          {facts && (
            <span className="text-xs text-pretty text-ink-muted" dir="auto">
              {facts}
            </span>
          )}
        </span>
      </span>
    </ListRow>
  );
}

function MeetingBlock({ group, props, tab }: { group: MeetingGroup; props: RowProps; tab: string | undefined }) {
  const id = `today-${group.key}`;
  return (
    <section aria-labelledby={id}>
      <Head id={id}>
        <span>{group.label}</span>
        <span className="font-normal tabular-nums">{group.meetings.length}</span>
      </Head>
      <ul>
        {group.meetings.map((m) => (
          <MeetingRow
            key={m.line.id}
            m={m.line}
            props={props}
            tabbable={m.line.ref === tab}
            below={
              m.points.length || m.items.length ? (
                <div className="pr-3 pb-2 pl-[6.875rem] sm:pr-4 sm:pl-[7.125rem]">
                  <PrepList m={m} props={props} />
                </div>
              ) : undefined
            }
          />
        ))}
      </ul>
    </section>
  );
}

export function TodayRows(props: TodayRowsProps) {
  const { list } = props.data;
  const listed = orderOf(props.data);
  const tab = props.selected !== undefined && listed.includes(props.selected) ? props.selected : listed[0];
  if (list.empty) {
    return (
      <Empty title={list.empty}>
        <div className="grid justify-items-start gap-3">
          <p>No meeting is left today or on the next working day, and nothing is due. The Queue holds what is open.</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="default" asChild>
              <Link to="/queue">
                <ListTodoIcon />
                Open the Queue
              </Link>
            </Button>
            <Button onClick={props.onNew}>
              <PlusIcon />
              New item
            </Button>
          </div>
        </div>
      </Empty>
    );
  }
  return (
    <>
      {list.lead && (
        <section aria-labelledby="today-lead">
          <Head id="today-lead" className="text-ink">
            {list.lead.label}
          </Head>
          <ul>
            <LeadRow m={list.lead.meeting} props={props} tabbable={list.lead.meeting.line.ref === tab} />
          </ul>
        </section>
      )}
      {list.due.map((group) => (
        <React.Fragment key={group.key}>
          <GroupBlock group={group} props={props} tab={tab} />
          {group.rows.length < group.total && (
            <p className="border-b border-rule px-3 py-1 text-xs text-ink-muted sm:px-4">
              {group.total - group.rows.length} more:{" "}
              <InLink href="/ledger" className="tap inline-flex h-6 items-center">
                the rest on the Ledger
              </InLink>
            </p>
          )}
        </React.Fragment>
      ))}
      {list.meetings.map((group) => (
        <MeetingBlock key={group.key} group={group} props={props} tab={tab} />
      ))}
      {list.closed && <GroupBlock group={list.closed} props={props} tab={tab} />}
    </>
  );
}
