/**
 * A day page over the ledger's items, the Ledger's or the Queue's list, on the frame every day page shares (frame.tsx:
 * the reads, the one selection, the writes with their notice, the poll, the guard and the moving keys). What is the
 * items' own is here: the ticks and the act bar, the calls by key and by button on the ticked rows, the open item or
 * the selected row, each refusal shown where its button is, the item panel and the new item.
 */
import { KeyboardIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import { NoticeBar, Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { useBand } from "@/hooks/use-width";
import { writes } from "@/lib/day";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { one } from "@/lib/search";
import { ACT_SNOOZE, ACT_WHY, ActBar } from "./act-bar";
import * as calls from "./api";
import type { Body, DayList, ItemPanel, PanelRead, Read, Row } from "./api";
import { Actions, type DayActions } from "./context";
import { Filters } from "./filters";
import { DAY, useDayFrame } from "./frame";
import { DayRows } from "./list";
import { blankDraft, type NewDraft, NewItem } from "./new-item";
import { ItemPanelBody, PanelHead } from "./panel";
import { WHO_KEY } from "./who-field";

type ItemPage = "ledger" | "queue";
type Scope = { kind: "act"; rows: Row[] } | { kind: "panel"; item: ItemPanel } | { kind: "row"; row: Row };

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
  const { path, search, open, data, selected, setSelected, notice, pending, write, go, hrefOf, openRow } = frame;
  const drop = one(search, "drop") === "1";

  const rows = React.useMemo(() => (data ? rowsOf(data) : []), [data]);
  const byRef = React.useMemo(() => new Map(rows.map((r) => [r.ref, r])), [rows]);

  const [ticked, setTicked] = React.useState<ReadonlySet<string>>(new Set());
  const tickedRows = rows.filter((r) => ticked.has(r.ref) && r.callable);
  React.useLayoutEffect(() => {
    tickedCount.current = tickedRows.length;
  });
  const [actProblem, setActProblem] = React.useState("");
  const [callProblem, setCallProblem] = React.useState<{ ref: string; words: string } | null>(null);
  const [newOpen, setNewOpen] = React.useState(false);
  const [newDraft, setNewDraft] = React.useState<NewDraft>(() => blankDraft());

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
      // A who saved may be a name no item held before, which the next who list then offers (DR-0714); a write's
      // refetch covers the names already, and a save refetches only what it missed.
      if (!missed && body.fields && "who" in body.fields) void frame.client.invalidateQueries({ queryKey: WHO_KEY });
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

  const item = open !== undefined ? (frame.panel.data?.panel ?? undefined) : undefined;
  const scope = (): Scope | null => {
    if (tickedRows.length) return { kind: "act", rows: tickedRows };
    if (open !== undefined) return item && item.callable ? { kind: "panel", item } : null;
    const row = selected !== undefined ? byRef.get(selected) : undefined;
    return row && row.callable ? { kind: "row", row } : null;
  };

  /** A call on the ticked rows, the open item or the selected row, each refusal shown where its button is. */
  const callOn = async (target: Scope, body: Partial<Body>, except: string[] = []): Promise<boolean> => {
    const op = body.op ?? "triage";
    if (target.kind === "act") {
      const outcome = await write({ ...body, op, items: target.rows.map((r) => r.ref) }, { bulk: true, except });
      if (outcome.ok) setTicked(new Set());
      setActProblem(outcome.ok ? "" : outcome.error);
      return outcome.ok;
    }
    const ref = target.kind === "panel" ? target.item.ref : target.row.ref;
    const outcome = await write({ ...body, op, items: [ref] }, { except });
    if (target.kind === "panel") setCallProblem(outcome.ok ? null : { ref, words: outcome.error });
    else frame.setRowProblem(outcome.ok ? "" : outcome.error);
    return outcome.ok;
  };

  const verdictKey = (index: number) => {
    const target = scope();
    if (!target) return;
    if (target.kind === "act") {
      const button = data?.verdicts[index];
      if (button)
        void callOn(target, {
          op: "triage",
          verdict: writes(
            button.verdict,
            target.rows.map((r) => r.triage),
          ),
        });
      return;
    }
    const buttons = target.kind === "panel" ? target.item.verdicts : target.row.quick;
    const button = buttons[index];
    if (button) void callOn(target, { op: "triage", verdict: button.writes });
  };

  const dropKey = () => {
    const target = scope();
    if (!target) return;
    if (target.kind === "act") document.getElementById(ACT_WHY)?.focus();
    else if (target.kind === "panel") {
      if (!drop) void go({ drop: "1" }, { replace: true });
      frame.askPanel("why", target.item.ref);
    } else void openRow(target.row.ref, { drop: true, focus: "why" });
  };

  const snoozeKey = () => {
    const target = scope();
    if (!target) return;
    if (target.kind === "act") document.getElementById(ACT_SNOOZE)?.focus();
    else if (target.kind === "panel") frame.askPanel("snooze", target.item.ref);
    else void openRow(target.row.ref, { focus: "snooze" });
  };

  const tick = (refs: string[], on: boolean) => {
    setTicked((was) => {
      const next = new Set(was);
      for (const ref of refs) {
        if (on) next.add(ref);
        else next.delete(ref);
      }
      return next;
    });
    if (!on) setActProblem("");
  };
  const clearTicks = () => {
    setTicked(new Set());
    setActProblem("");
  };

  const opener = React.useRef<Element | null>(null);
  const openNew = () => {
    opener.current = document.activeElement;
    if (!newDraft.title && !newDraft.who && data?.filters.stream && !newDraft.stream) {
      setNewDraft({ ...newDraft, stream: data.filters.stream });
    }
    setNewOpen(true);
  };

  const idle = () => !pending;
  const selectedRow = () => (selected !== undefined ? byRef.get(selected) : undefined);
  const commands: KeyCommand[] = [
    frame.keys.next,
    frame.keys.previous,
    frame.keys.open,
    {
      keys: ["x", " "],
      what: "tick the selected row",
      run: () => selected !== undefined && tick([selected], !ticked.has(selected)),
      when: () => Boolean(selectedRow()?.callable),
    },
    {
      keys: ["1", "2", "3", "4"],
      what: "discuss, claude, me, chase; again clears it",
      run: (event) => verdictKey(Number(event.key) - 1),
      when: () => idle() && scope() !== null,
    },
    {
      keys: ["0"],
      what: "no call",
      run: () => {
        const target = scope();
        if (target && (target.kind !== "row" || target.row.triage))
          void callOn(target, { op: "triage", verdict: null });
      },
      when: () => idle() && scope() !== null,
    },
    {
      keys: ["e"],
      what: "done",
      run: () => {
        const target = scope();
        if (target) void callOn(target, { op: "done" });
      },
      when: () => idle() && scope() !== null,
    },
    { keys: ["Delete", "Backspace"], what: "drop: say why", run: dropKey, when: () => scope() !== null },
    { keys: ["s"], what: "snooze", run: snoozeKey, when: () => scope() !== null },
    frame.keys.undo,
    frame.keys.undoCtrl,
    { keys: ["n"], what: "a new item", run: openNew },
    frame.keys.find,
    {
      keys: ["Escape"],
      what: "clear the ticks, or close the panel",
      run: () => (tickedRows.length ? clearTicks() : void frame.closePanel()),
      when: () => tickedRows.length > 0 || open !== undefined,
    },
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
            ticked={ticked}
            pending={pending}
            narrow={band === "narrow"}
            onSelect={setSelected}
            onOpen={(ref) => void openRow(ref)}
            onTick={tick}
            onCall={(row, body) => void callOn({ kind: "row", row }, body)}
            onDrop={(row) => void openRow(row.ref, { drop: true, focus: "why" })}
            onMore={(k) => void go({ k: String(k) })}
            onNew={openNew}
          />
        ) : (
          <p className="px-4 py-6 text-sm text-ink-muted">Reading the {title.toLowerCase()}.</p>
        )}
      </div>
      {data && tickedRows.length > 0 && (
        <ActBar
          rows={tickedRows}
          verdicts={data.verdicts}
          snooze={data.snooze}
          today={data.today}
          tomorrow={data.tomorrow}
          pending={pending}
          problem={actProblem}
          onCall={(body) => callOn({ kind: "act", rows: tickedRows }, body, ["act:why"])}
          onClear={clearTicks}
        />
      )}
      {/* What the list holds now, said to a screen reader as it changes, as Search says its results. */}
      <p className="sr-only" aria-live="polite">
        {data ? `${data.list.shown} of ${data.list.total} items` : ""}
      </p>
      {frame.rowProblem && <Refusal className="mx-3 my-2 sm:mx-4">{frame.rowProblem}</Refusal>}
      {notice && (
        <NoticeBar
          text={notice.text}
          lines={notice.lines}
          action={notice.button}
          onAction={frame.undo}
          disabled={pending}
          onDismiss={() => frame.setNotice(null)}
        />
      )}
    </>
  );

  const panelPart =
    open === undefined ? undefined : (
      <Panel
        label={item ? `${item.ref}, ${item.title}` : `${open}`}
        onClose={() => void frame.closePanel()}
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
          <ItemPanelBody
            key={item.id}
            item={item}
            ask={frame.ask}
            drop={drop}
            callProblem={callProblem?.ref === item.ref ? callProblem.words : ""}
            viewer={frame.viewer}
            onCall={(body, except) => callOn({ kind: "panel", item }, body, except)}
            onDrop={(shown) => void go({ drop: shown ? "1" : undefined }, { replace: true })}
            onUndo={async (op, said) => {
              const outcome = await write({ op: "undo", of: op, said });
              setCallProblem(outcome.ok ? null : { ref: item.ref, words: outcome.error });
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
        <PageHeader title={title}>
          <Button size="sm" onClick={openNew}>
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
        <NewItem
          open={newOpen}
          onOpenChange={setNewOpen}
          draft={newDraft}
          onDraft={setNewDraft}
          rowHref={(ref) => hrefOf({ open: ref, at: undefined, drop: undefined, text: undefined })}
          returnTo={opener}
          onAdded={(ref) => {
            setNewOpen(false);
            setNewDraft(blankDraft());
            if (ref) void openRow(ref);
          }}
          onShow={(ref) => {
            setNewOpen(false);
            void openRow(ref);
          }}
        />
      </Page>
    </Actions.Provider>
  );
}
