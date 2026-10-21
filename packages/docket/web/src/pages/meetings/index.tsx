/**
 * /meetings: the meetings by day (DR-0666), from today, then the earlier days, newest first, and the open meeting's
 * panel with its talking points and outcome (DR-0667): the day pages' frame (frame.tsx) over Meetings' own read. The
 * items' keys and calls never act on a meeting (DR-0711): here j and k move, Enter opens, Esc closes, u undoes, and /
 * finds.
 */
import { KeyboardIcon } from "lucide-react";
import * as React from "react";
import { Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { useBand } from "@/hooks/use-width";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { one, type Search } from "@/lib/search";
import * as calls from "@/pages/day/api";
import type { Body, MeetingRead, Meetings, Read } from "@/pages/day/api";
import { Actions, type DayActions } from "@/pages/day/context";
import { Filters } from "@/pages/day/filters";
import { DAY, useDayFrame } from "@/pages/day/frame";
import { MeetingRows } from "./list";
import { MeetingHead, MeetingPanelBody } from "./panel";

const TITLE = "Meetings";
const orderOf = (data: Meetings): string[] => data.list.days.flatMap((d) => d.meetings.map((m) => m.ref));
const suggestOf = (search: Search): string => one(search, "suggest") ?? "";

export function MeetingsPage() {
  useTitle(TITLE);
  const band = useBand();
  const showKeys = useShowKeys();
  const frame = useDayFrame<Meetings, MeetingRead>({
    page: "meetings",
    title: TITLE,
    readList: calls.readMeetings,
    readPanel: calls.readMeeting,
    orderOf,
    panelKey: suggestOf,
  });
  const { path, search, open, data, selected, setSelected, pending, write, go, hrefOf, openRow } = frame;
  const suggest = one(search, "suggest") === "1";
  const [problem, setProblem] = React.useState<{ ref: string; words: string } | null>(null);

  const save = (body: Body) =>
    frame.save(body, (saved) => {
      if (!("line" in saved)) return;
      frame.client.setQueriesData<Read<Meetings>>({ queryKey: [...DAY, "meetings", "list"] }, (old) =>
        old
          ? {
              ...old,
              list: {
                ...old.list,
                days: old.list.days.map((d) => ({
                  ...d,
                  meetings: d.meetings.map((m) => (m.id === saved.line.id ? saved.line : m)),
                })),
              },
            }
          : old,
      );
      // The save's panel carries no suggestions, which the panel read asked for apart: those shown stay.
      frame.client.setQueriesData<Read<MeetingRead>>({ queryKey: [...DAY, "meetings", "panel"] }, (old) =>
        old?.panel && old.panel.id === saved.panel.id
          ? { ...old, panel: { ...saved.panel, suggestions: old.panel.suggestions } }
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

  const meeting = open !== undefined ? (frame.panel.data?.panel ?? undefined) : undefined;
  const commands: KeyCommand[] = [
    frame.keys.next,
    frame.keys.previous,
    frame.keys.open,
    frame.keys.undo,
    frame.keys.undoCtrl,
    frame.keys.find,
    {
      keys: ["Escape"],
      what: "close the panel",
      run: () => void frame.closePanel(),
      when: () => open !== undefined,
    },
  ];
  useKeys("meetings", "Meetings", commands);

  const listPart = (
    <>
      <div className="@container min-h-0 flex-1 overflow-y-auto" role="region" aria-label="Meetings" tabIndex={-1}>
        {frame.list.error && !data ? (
          <div className="grid justify-items-start gap-3 px-4 py-6">
            <Refusal>{calls.wordsOf(frame.list.error)}</Refusal>
            <a href={path} className="text-sm text-green underline-offset-4 hover:underline">
              Open Meetings as it opens
            </a>
          </div>
        ) : data ? (
          <MeetingRows
            path={path}
            data={data}
            search={search}
            selected={selected}
            open={open}
            onSelect={setSelected}
            onOpen={(ref) => void openRow(ref)}
          />
        ) : (
          <p className="px-4 py-6 text-sm text-ink-muted">Reading the meetings.</p>
        )}
      </div>
      <p className="sr-only" aria-live="polite">
        {data ? `${data.list.shown} meetings` : ""}
      </p>
      {frame.listFoot}
    </>
  );

  const panelPart =
    open === undefined ? undefined : (
      <Panel
        label={meeting ? `${meeting.ref}, ${meeting.subject}` : open}
        onClose={() => void frame.closePanel()}
        footer={frame.panelFoot}
        header={
          meeting ? (
            <MeetingHead meeting={meeting} />
          ) : (
            <h2 id="panel-title" tabIndex={-1} className="text-sm font-semibold">
              {frame.panel.data?.missing ? "Nothing to open" : open}
            </h2>
          )
        }
      >
        {meeting ? (
          <MeetingPanelBody
            key={meeting.id}
            meeting={meeting}
            ask={frame.ask}
            suggest={suggest}
            viewer={frame.viewer}
            problem={problem?.ref === meeting.ref ? problem.words : ""}
            onSuggest={(shown) => void go({ suggest: shown ? "1" : undefined }, { replace: true })}
            onUndo={async (op, said) => {
              const outcome = await write({ op: "undo", of: op, said });
              setProblem(outcome.ok ? null : { ref: meeting.ref, words: outcome.error });
            }}
            onView={(ref) => void go({ text: ref }, { replace: true })}
          />
        ) : frame.panel.error ? (
          <div className="p-4">
            <Refusal>{calls.wordsOf(frame.panel.error)}</Refusal>
          </div>
        ) : frame.panel.data?.missing ? (
          <div className="p-4">
            <Refusal>{frame.panel.data.missing}</Refusal>
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
          <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
            <KeyboardIcon />
          </Button>
        </PageHeader>
        {/* At phone width an open meeting takes the screen, the filters with the list. */}
        {data && !(band === "narrow" && open !== undefined) && (
          <Filters
            page="meetings"
            filters={data.filters}
            counts={data.list.counts}
            chips={[]}
            streams={data.list.streams}
            kinds={data.kinds}
            narrow={band === "narrow"}
            hrefOf={hrefOf}
            onChange={(change) => void go(change)}
          />
        )}
        <Workspace list={listPart} panel={panelPart} />
      </Page>
    </Actions.Provider>
  );
}
