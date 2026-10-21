/**
 * A day page over the ledger's items, the Ledger's or the Queue's list, on the frame every day page shares (frame.tsx:
 * the reads, the one selection, the writes with their notice, the poll, the guard and the moving keys) and the items'
 * own calls (items.tsx: the ticks and the act bar, the calls by key and by button, the item panel and the new item).
 * What is the list's own is here: its filters, its rows and how a save puts its row back.
 */
import { KeyboardIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import { Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { useBand } from "@/hooks/use-width";
import { type KeyCommand, useKeys } from "@/lib/keys";
import * as calls from "./api";
import type { Body, DayList, PanelRead, Read, Row } from "./api";
import { Actions, type DayActions } from "./context";
import { Filters } from "./filters";
import { DAY, useDayFrame } from "./frame";
import { useItemCalls, whoSaved } from "./items";
import { DayRows } from "./list";
import { PanelHead } from "./panel";

type ItemPage = "ledger" | "queue";

const rowsOf = (data: DayList): Row[] => data.list.groups.flatMap((g) => g.rows);
const orderOf = (data: DayList): string[] => rowsOf(data).map((r) => r.ref);
const READERS = {
  ledger: {
    list: (query: string) => calls.readList("ledger", query),
    panel: (query: string) => calls.readPanel("ledger", query),
  },
  queue: {
    list: (query: string) => calls.readList("queue", query),
    panel: (query: string) => calls.readPanel("queue", query),
  },
} satisfies Record<ItemPage, unknown>;

export function DayPage({ page, title }: { page: ItemPage; title: string }) {
  useTitle(title);
  const band = useBand();
  const showKeys = useShowKeys();
  const tickedCount = React.useRef(0);
  const frame = useDayFrame<DayList, PanelRead>({
    page,
    title,
    readList: READERS[page].list,
    readPanel: READERS[page].panel,
    orderOf,
    ticked: () => tickedCount.current,
  });
  const { path, search, open, data, selected, setSelected, pending, write, go, hrefOf, openRow } = frame;

  const rows = React.useMemo(() => (data ? rowsOf(data) : []), [data]);
  const item = open !== undefined ? (frame.panel.data?.panel ?? undefined) : undefined;
  const items = useItemCalls({
    frame,
    rows,
    item,
    verdicts: data?.verdicts ?? [],
    tickedCountRef: tickedCount,
    stream: data?.filters.stream,
  });

  const save = (body: Body) =>
    frame.save(body, (saved, missed) => {
      if (!("row" in saved)) return;
      frame.client.setQueriesData<Read<DayList>>({ queryKey: [...DAY, page, "list"] }, (old) =>
        old
          ? {
              ...old,
              list: {
                ...old.list,
                groups: old.list.groups.map((g) => ({
                  ...g,
                  rows: g.rows.map((r) => (r.id === saved.row.id ? saved.row : r)),
                })),
              },
            }
          : old,
      );
      frame.client.setQueriesData<Read<PanelRead>>({ queryKey: [...DAY, page, "panel"] }, (old) =>
        old?.panel && old.panel.id === saved.panel.id ? { ...old, panel: saved.panel } : old,
      );
      whoSaved(frame.client, body, missed);
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
    frame.keys.find,
    items.escapeKey,
  ];
  useKeys(page, `The ${title}`, commands);

  const listPart = (
    <>
      <div className="@container min-h-0 flex-1 overflow-y-auto" role="region" aria-label="Items" tabIndex={-1}>
        {frame.list.error && !data ? (
          <div className="grid justify-items-start gap-3 px-4 py-6">
            <Refusal>{calls.wordsOf(frame.list.error)}</Refusal>
            <a href={path} className="text-sm text-green underline-offset-4 hover:underline">
              Open the {title} as it opens
            </a>
          </div>
        ) : data ? (
          <DayRows
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
            onMore={(k) => void go({ k: String(k) })}
            onNew={items.openNew}
          />
        ) : (
          <p className="px-4 py-6 text-sm text-ink-muted">Reading the {title.toLowerCase()}.</p>
        )}
      </div>
      {data && items.actBar(data)}
      {/* What the list holds now, said to a screen reader as it changes, as Search says its results. */}
      <p className="sr-only" aria-live="polite">
        {data ? `${data.list.shown} of ${data.list.total} items` : ""}
      </p>
      {frame.listFoot}
    </>
  );

  const panelPart =
    open === undefined ? undefined : (
      <Panel
        label={item ? `${item.ref}, ${item.title}` : `${open}`}
        onClose={() => void frame.closePanel()}
        footer={frame.panelFoot}
        header={
          item ? (
            <PanelHead item={item} />
          ) : (
            <h2 id="panel-title" tabIndex={-1} className="text-sm font-semibold">
              {frame.panel.data?.missing ? "Nothing to open" : open}
            </h2>
          )
        }
      >
        {item ? (
          items.itemBody(item)
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
        <PageHeader title={title}>
          <Button size="sm" onClick={items.openNew}>
            <PlusIcon />
            New item <Kbd className="h-4 min-w-4 shadow-none pointer-coarse:hidden">n</Kbd>
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
            <KeyboardIcon />
          </Button>
        </PageHeader>
        {/* At phone width an open item takes the screen, the filters with the list. */}
        {data && !(band === "narrow" && open !== undefined) && (
          <Filters
            page={page}
            filters={data.filters}
            counts={data.list.counts}
            chips={data.list.chips}
            streams={data.list.streams}
            kinds={data.kinds}
            narrow={band === "narrow"}
            hrefOf={hrefOf}
            onChange={(change) => void go(change)}
          />
        )}
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
