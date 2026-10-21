/**
 * /today, the start page (DR-0669, DR-0773): the owner's day as `docket ledger day` reads it, the next meeting and its
 * prep first, then what is overdue and due today, then the rest of the day's meetings and what closed today, with the
 * last sync per source in the bar above. It runs on the day pages' frame (frame.tsx) with the items' calls on its due
 * rows (items.tsx), and opens either panel: an item's as the Ledger's, a meeting's as Meetings'. The item keys never
 * act on a meeting's row or panel (DR-0711).
 */
import { KeyboardIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import { Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { useBand } from "@/hooks/use-width";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { one, type Search } from "@/lib/search";
import * as calls from "@/pages/day/api";
import type { Body, DayMeetingLine, Read, Row, Today, TodayList, TodayPanel } from "@/pages/day/api";
import { Actions, type DayActions } from "@/pages/day/context";
import { DAY, useDayFrame } from "@/pages/day/frame";
import { useItemCalls, whoSaved } from "@/pages/day/items";
import { PanelHead } from "@/pages/day/panel";
import { MeetingHead, MeetingPanelBody } from "@/pages/meetings/panel";
import { DayBar } from "./day-bar";
import { itemRowsOf, orderOf, TodayRows } from "./list";

const TITLE = "Today";
const suggestOf = (search: Search): string => one(search, "suggest") ?? "";
const untilOf = (data: Today): string => data.list.until;

/** The day's list with an item's row put back wherever it shows: a due group, the closed ones, a meeting's prep. */
function withRow(list: TodayList, row: Row): TodayList {
  const swap = (r: Row) => (r.id === row.id ? row : r);
  const prep = (m: DayMeetingLine) => ({ ...m, items: m.items.map(swap) });
  return {
    ...list,
    lead: list.lead ? { ...list.lead, meeting: prep(list.lead.meeting) } : list.lead,
    due: list.due.map((g) => ({ ...g, rows: g.rows.map(swap) })),
    meetings: list.meetings.map((g) => ({ ...g, meetings: g.meetings.map(prep) })),
    closed: list.closed ? { ...list.closed, rows: list.closed.rows.map(swap) } : list.closed,
  };
}

/** The day's list with a meeting's line put back, as its save answered it. */
function withLine(list: TodayList, line: DayMeetingLine["line"]): TodayList {
  const swap = (m: DayMeetingLine) => (m.line.id === line.id ? { ...m, line } : m);
  return {
    ...list,
    lead: list.lead ? { ...list.lead, meeting: swap(list.lead.meeting) } : list.lead,
    meetings: list.meetings.map((g) => ({ ...g, meetings: g.meetings.map(swap) })),
  };
}

export function TodayPage() {
  useTitle(TITLE);
  const band = useBand();
  const showKeys = useShowKeys();
  const tickedCount = React.useRef(0);
  const frame = useDayFrame<Today, TodayPanel>({
    page: "today",
    title: TITLE,
    readList: calls.readToday,
    readPanel: calls.readTodayPanel,
    orderOf,
    panelKey: suggestOf,
    ticked: () => tickedCount.current,
    staleAt: untilOf,
  });
  const { path, search, open, data, selected, setSelected, pending, write, go, openRow } = frame;
  const suggest = one(search, "suggest") === "1";
  const [meetingProblem, setMeetingProblem] = React.useState<{ ref: string; words: string } | null>(null);

  const rows = React.useMemo(() => (data ? itemRowsOf(data) : []), [data]);
  const shown = open !== undefined ? frame.panel.data : undefined;
  const item = shown?.item ?? undefined;
  const meeting = shown?.meeting ?? undefined;
  const items = useItemCalls({ frame, rows, item, verdicts: data?.verdicts ?? [], tickedCountRef: tickedCount });

  const save = (body: Body) =>
    frame.save(body, (saved, missed) => {
      const listKey = [...DAY, "today", "list"];
      const panelKey = [...DAY, "today", "panel"];
      if ("row" in saved) {
        frame.client.setQueriesData<Read<Today>>({ queryKey: listKey }, (old) =>
          old ? { ...old, list: withRow(old.list, saved.row) } : old,
        );
        frame.client.setQueriesData<Read<TodayPanel>>({ queryKey: panelKey }, (old) =>
          old?.item && old.item.id === saved.panel.id ? { ...old, item: saved.panel } : old,
        );
        whoSaved(frame.client, body, missed);
        return;
      }
      frame.client.setQueriesData<Read<Today>>({ queryKey: listKey }, (old) =>
        old ? { ...old, list: withLine(old.list, saved.line) } : old,
      );
      // The save's panel carries no suggestions, which the panel read asked for apart: those shown stay.
      frame.client.setQueriesData<Read<TodayPanel>>({ queryKey: panelKey }, (old) =>
        old?.meeting && old.meeting.id === saved.panel.id
          ? { ...old, meeting: { ...saved.panel, suggestions: old.meeting.suggestions } }
          : old,
      );
    });

  const actions: DayActions = {
    write,
    save,
    open: frame.openMaterial,
    pending,
    streams: data?.list.streams ?? [],
    kinds: data?.kinds ?? [],
    today: data?.today ?? "",
  };

  const commands: KeyCommand[] = [
    frame.keys.next,
    frame.keys.previous,
    frame.keys.open,
    ...items.callKeys,
    frame.keys.undo,
    frame.keys.undoCtrl,
    items.newKey,
    items.escapeKey,
  ];
  useKeys("today", TITLE, commands);

  const listPart = (
    <>
      <div className="@container min-h-0 flex-1 overflow-y-auto" role="region" aria-label="The day" tabIndex={-1}>
        {frame.list.error && !data ? (
          <div className="grid justify-items-start gap-3 px-4 py-6">
            <Refusal>{calls.wordsOf(frame.list.error)}</Refusal>
            <a href={path} className="text-sm text-green underline-offset-4 hover:underline">
              Open Today as it opens
            </a>
          </div>
        ) : data ? (
          <TodayRows
            path={path}
            data={data}
            search={search}
            selected={selected}
            open={open}
            ticked={items.ticked}
            pending={pending}
            narrow={band === "narrow"}
            onSelect={setSelected}
            onOpen={(ref) => void openRow(ref)}
            {...items.rowCalls}
            onNew={items.openNew}
          />
        ) : (
          <p className="px-4 py-6 text-sm text-ink-muted">Reading the day.</p>
        )}
      </div>
      {data && items.actBar(data)}
      {frame.listFoot}
    </>
  );

  const label = item ? `${item.ref}, ${item.title}` : meeting ? `${meeting.ref}, ${meeting.subject}` : `${open}`;
  const panelPart =
    open === undefined ? undefined : (
      <Panel
        label={label}
        onClose={() => void frame.closePanel()}
        footer={frame.panelFoot}
        header={
          item ? (
            <PanelHead item={item} />
          ) : meeting ? (
            <MeetingHead meeting={meeting} />
          ) : (
            <h2 id="panel-title" tabIndex={-1} className="text-sm font-semibold">
              {shown?.missing ? "Nothing to open" : open}
            </h2>
          )
        }
      >
        {item ? (
          items.itemBody(item)
        ) : meeting ? (
          <MeetingPanelBody
            key={meeting.id}
            meeting={meeting}
            ask={frame.ask}
            suggest={suggest}
            viewer={frame.viewer}
            problem={meetingProblem?.ref === meeting.ref ? meetingProblem.words : ""}
            onSuggest={(on) => void go({ suggest: on ? "1" : undefined }, { replace: true })}
            onUndo={async (op, said) => {
              const outcome = await write({ op: "undo", of: op, said });
              setMeetingProblem(outcome.ok ? null : { ref: meeting.ref, words: outcome.error });
            }}
            onView={(ref) => void go({ text: ref }, { replace: true })}
          />
        ) : frame.panel.error ? (
          <div className="p-4">
            <Refusal>{calls.wordsOf(frame.panel.error)}</Refusal>
          </div>
        ) : shown?.missing ? (
          <div className="p-4">
            <Refusal>{shown.missing}</Refusal>
          </div>
        ) : (
          <p className="p-4 text-sm text-ink-muted">Reading {open}.</p>
        )}
      </Panel>
    );

  return (
    <Actions.Provider value={actions}>
      <Page>
        <PageHeader title={TITLE}>
          <Button size="sm" onClick={items.openNew}>
            <PlusIcon />
            New item <Kbd className="h-4 min-w-4 shadow-none pointer-coarse:hidden">n</Kbd>
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
            <KeyboardIcon />
          </Button>
        </PageHeader>
        {/* At phone width an open row takes the screen, the day's bar with the list. */}
        {data && !(band === "narrow" && open !== undefined) && <DayBar list={data.list} />}
        <Workspace list={listPart} panel={panelPart} />
        <datalist id="ledger-streams">
          {(data?.list.streams ?? []).map((stream) => (
            <option key={stream} value={stream} />
          ))}
        </datalist>
        {items.newItem}
      </Page>
    </Actions.Provider>
  );
}
