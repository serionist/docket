/**
 * /ledger: every item, grouped by due or as the filters say, with one selection the panel follows (DR-0664 to
 * DR-0666, DR-0710, DR-0711). The page reads /api/web/ledger with its URL's query string and writes back the
 * canonical URL; the keys run what the buttons run; a write moves the selection to the row after the acted ones and
 * shows its notice with Undo; the 5 s poll refetches in place unless the owner is mid-change; and unsaved text is
 * never dropped without asking.
 */
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useBlocker, useNavigate, useRouter, useRouterState, useSearch } from "@tanstack/react-router";
import { KeyboardIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import { SHELL_KEY } from "@/components/shell/app-sidebar";
import { NoticeBar, Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { useBand } from "@/hooks/use-width";
import { after, step, standIn, writes } from "@/lib/day";
import { anyUnsaved, mayLeave, saves, settled } from "@/lib/drafts";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { holding, usePoll } from "@/lib/poll";
import { one, type Search, stringifySearch, writeBack } from "@/lib/search";
import { ACT_SNOOZE, ACT_WHY, ActBar } from "./act-bar";
import * as calls from "./api";
import type { Body, ItemPanel, Notice, Row, Wrote } from "./api";
import { Actions, type How, type LedgerActions, type Outcome } from "./context";
import { FIND, Filters } from "./filters";
import { LedgerRows } from "./list";
import { blankDraft, type NewDraft, NewItem } from "./new-item";
import { type Ask, ItemPanelBody, PanelHead, type ViewerState } from "./panel";
import { listKey, type Param, quiet, withSearch } from "./url";

const CALLS = ["triage", "snooze", "done", "drop", "reopen"];
const LEDGER = ["ledger"] as const;
type Change = Partial<Record<Param, string | undefined>>;
type Scope = { kind: "act"; rows: Row[] } | { kind: "panel"; item: ItemPanel } | { kind: "row"; row: Row };

const refused = (error: unknown): { ok: false; error: string; alikes: Row[] } => ({
  ok: false,
  error: calls.wordsOf(error),
  alikes: calls.alikesOf(error),
});
const CALLED_OFF = { ok: false as const, error: "", alikes: [] };

/** Focus a row of the list, or the panel's heading when the list is hidden behind an item at phone width. */
function focusRow(ref: string): boolean {
  const row = document.querySelector<HTMLElement>(`[data-row="${CSS.escape(ref)}"]`);
  if (!row) return false;
  if (row.closest("[hidden]")) {
    document.getElementById("panel-title")?.focus({ preventScroll: true });
    return true;
  }
  row.focus({ preventScroll: true });
  row.scrollIntoView({ block: "nearest" });
  return true;
}

export function LedgerPage() {
  useTitle("Ledger");
  const search = useSearch({ from: "/ledger" }) as Search;
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const navigate = useNavigate();
  const router = useRouter();
  const client = useQueryClient();
  const band = useBand();
  const showKeys = useShowKeys();

  const open = one(search, "open");
  const text = one(search, "text");
  const drop = one(search, "drop") === "1";

  const list = useQuery({
    queryKey: [...LEDGER, "list", listKey(search)],
    queryFn: () => calls.readLedger(searchStr),
    placeholderData: keepPreviousData,
  });
  const panel = useQuery({
    queryKey: [...LEDGER, "panel", open],
    queryFn: () => calls.readPanel(searchStr),
    enabled: open !== undefined,
  });
  const viewerQuery = useQuery({
    queryKey: [...LEDGER, "text", open, text],
    queryFn: () => calls.readText(stringifySearch({ open: open ?? "", text: text ?? "" })),
    enabled: open !== undefined && text !== undefined,
  });

  // The server's canonical URL, written back as it came, once a read answers for the URL the bar holds now.
  React.useEffect(() => {
    for (const read of [list.data, panel.data]) {
      if (read && read.asked === searchStr) {
        writeBack(
          {
            location: router.history.location,
            replace: (href) => router.history.replace(href, undefined, { ignoreBlocker: true }),
          },
          read.canonical,
        );
      }
    }
  }, [list.data, panel.data, searchStr, router]);

  const data = list.data;
  const rows = React.useMemo(() => data?.list.groups.flatMap((g) => g.rows) ?? [], [data]);
  const order = React.useMemo(() => rows.map((r) => r.ref), [rows]);
  const byRef = React.useMemo(() => new Map(rows.map((r) => [r.ref, r])), [rows]);

  // One selection: the open item, else the row the URL's at names, else the one last selected.
  const [selected, setSelected] = React.useState<string | undefined>(open ?? one(search, "at"));
  const [seenOpen, setSeenOpen] = React.useState(open);
  if (open !== seenOpen) {
    setSeenOpen(open);
    if (open !== undefined) setSelected(open);
  }
  // A selected row a refetch no longer lists hands the selection to the row now in its place.
  const [seenOrder, setSeenOrder] = React.useState(order);
  if (order !== seenOrder) {
    setSeenOrder(order);
    if (selected !== undefined && !order.includes(selected) && seenOrder.includes(selected) && selected !== open) {
      setSelected(standIn(order, seenOrder.indexOf(selected)));
    }
  }

  const [ticked, setTicked] = React.useState<ReadonlySet<string>>(new Set());
  const tickedRows = rows.filter((r) => ticked.has(r.ref) && r.callable);
  const [notice, setNotice] = React.useState<Notice | null>(null);
  const [adopted, setAdopted] = React.useState(false);
  if (!adopted && data) {
    // A URL that still carries what a write said shows its notice once; the canonical URL leaves it out.
    setAdopted(true);
    if (data.notice) setNotice(data.notice);
  }
  const [pending, setPending] = React.useState(false);
  const [rowProblem, setRowProblem] = React.useState("");
  const [actProblem, setActProblem] = React.useState("");
  const [callProblem, setCallProblem] = React.useState<{ ref: string; words: string } | null>(null);
  const [ask, setAsk] = React.useState<Ask | null>(null);
  const [newOpen, setNewOpen] = React.useState(false);
  const [newDraft, setNewDraft] = React.useState<NewDraft>(() => blankDraft());

  // What async work and the poll read as it stands now, not as the render that started them saw it.
  const now = React.useRef({ selected, open, ticked: 0, pending: false, version: undefined as string | undefined });
  React.useLayoutEffect(() => {
    now.current.selected = selected;
    now.current.open = open;
    now.current.ticked = tickedRows.length;
    now.current.pending = pending;
  });
  React.useEffect(() => {
    if (data) now.current.version = data.version;
  }, [data]);
  const asks = React.useRef(0);
  const wanted = React.useRef<string | null>(null);
  React.useLayoutEffect(() => {
    if (wanted.current !== null && focusRow(wanted.current)) wanted.current = null;
  });
  const focusSoon = (ref: string | undefined) => {
    if (ref === undefined) return;
    if (!focusRow(ref)) wanted.current = ref;
  };
  const askPanel = (what: Ask["what"], ref: string) => setAsk({ what, ref, n: ++asks.current });

  const refetch = () => {
    void client.invalidateQueries({ queryKey: LEDGER });
    void client.invalidateQueries({ queryKey: SHELL_KEY });
  };

  usePoll({
    version: () => now.current.version,
    ask: calls.changed,
    hold: () => now.current.pending || holding(now.current.ticked),
    changed: refetch,
  });

  // Leaving the page, or the panel's item, with text typed and unsaved asks first, once any save in flight is done.
  useBlocker({
    shouldBlockFn: async ({ current, next }) => {
      const leaving = next.pathname !== current.pathname;
      const moving = one(next.search as Search, "open") !== one(current.search as Search, "open");
      if (!leaving && !moving) return false;
      await settled();
      return !mayLeave(leaving ? "" : "panel:");
    },
    enableBeforeUnload: () => anyUnsaved() || saves() > 0,
  });

  const go = (change: Change, opts: { replace?: boolean } = {}) =>
    navigate({
      to: "/ledger",
      search: (prev) => withSearch(quiet(prev as Search), change),
      replace: opts.replace,
      ignoreBlocker: true,
    });
  const hrefOf = (change: Change) => `/ledger${stringifySearch(withSearch(quiet(search), change))}`;

  /** Open an item's panel, once the panel's own unsaved text, if any, may go. */
  const openRow = async (ref: string, how: { drop?: boolean; focus?: Ask["what"] | "row" } = {}) => {
    const shown = now.current.open;
    if (shown !== undefined && shown !== ref) {
      await settled();
      if (!mayLeave("panel:")) return;
    }
    setSelected(ref);
    if (how.focus === "row") focusSoon(ref);
    else askPanel(how.focus ?? "heading", ref);
    void go(
      { open: ref, at: undefined, text: undefined, drop: how.drop ? "1" : undefined },
      { replace: shown !== undefined },
    );
  };

  const closePanel = async () => {
    const shown = now.current.open;
    if (shown === undefined) return;
    await settled();
    if (!mayLeave("panel:")) return;
    setSelected(shown);
    void go({ open: undefined, drop: undefined, text: undefined, at: shown });
    focusSoon(shown);
  };

  const move = async (by: 1 | -1) => {
    const next = step(order, now.current.selected, by);
    if (next === undefined) return;
    if (now.current.open !== undefined && next !== now.current.open) await openRow(next, { focus: "row" });
    else {
      setSelected(next);
      focusSoon(next);
    }
  };

  const write = async (body: Body, how: How = {}): Promise<Outcome<Wrote>> => {
    const items = body.items ?? [];
    const isCall = CALLS.includes(body.op);
    const at = isCall ? after(order, now.current.selected, items) : undefined;
    const shown = now.current.open;
    // One selection: when a call moves it and a panel is open, the panel goes with it, as the old app's did.
    const moves = isCall && shown !== undefined && at !== undefined && at !== shown;
    if (moves) {
      await settled();
      if (!mayLeave("panel:", how.except)) return CALLED_OFF;
    }
    setPending(true);
    let wrote: Wrote;
    try {
      wrote = await calls.write({ ...body, at: at ?? null, bulk: Boolean(how.bulk) });
    } catch (error) {
      return refused(error);
    } finally {
      setPending(false);
    }
    now.current.version = wrote.version;
    setNotice(wrote.notice);
    setRowProblem("");
    if (how.bulk) setTicked(new Set());
    if (isCall || body.op === "undo") {
      // The focus goes with the selection, so Enter never presses the button just clicked again.
      const next = wrote.at ?? now.current.selected;
      if (wrote.at) setSelected(wrote.at);
      if (moves && wrote.at) void go({ open: wrote.at, drop: undefined, text: undefined }, { replace: true });
      focusSoon(next);
    }
    refetch();
    return { ok: true, answer: wrote };
  };

  const save = async (body: Body): Promise<Outcome<calls.Saved>> => {
    let saved: calls.Saved;
    try {
      saved = await calls.save(body);
    } catch (error) {
      return refused(error);
    }
    // A write from the CLI, Claude or a sync that landed while the poll held for the field moved the version before
    // the save's own write; adopting the save's version alone would hide it from the poll for good.
    const missed = saved.before !== now.current.version;
    now.current.version = saved.version;
    client.setQueriesData<calls.Read<calls.Ledger>>({ queryKey: [...LEDGER, "list"] }, (old) =>
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
    client.setQueriesData<calls.Read<calls.PanelRead>>({ queryKey: [...LEDGER, "panel"] }, (old) =>
      old?.panel && old.panel.id === saved.panel.id ? { ...old, panel: saved.panel } : old,
    );
    if (missed) refetch();
    return { ok: true, answer: saved };
  };

  const openMaterial = async (item: string, ref: string, reveal: boolean): Promise<Outcome<null>> => {
    try {
      setNotice(await calls.openMaterial(item, ref, reveal));
    } catch (error) {
      return refused(error);
    }
    return { ok: true, answer: null };
  };

  const actions: LedgerActions = {
    write,
    save,
    open: openMaterial,
    pending,
    streams: data?.list.streams ?? [],
    kinds: data?.kinds ?? [],
    today: data?.today ?? "",
  };

  const item = open !== undefined ? (panel.data?.panel ?? undefined) : undefined;
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
      setActProblem(outcome.ok ? "" : outcome.error);
      return outcome.ok;
    }
    const ref = target.kind === "panel" ? target.item.ref : target.row.ref;
    const outcome = await write({ ...body, op, items: [ref] }, { except });
    if (target.kind === "panel") setCallProblem(outcome.ok ? null : { ref, words: outcome.error });
    else setRowProblem(outcome.ok ? "" : outcome.error);
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
      askPanel("why", target.item.ref);
    } else void openRow(target.row.ref, { drop: true, focus: "why" });
  };

  const snoozeKey = () => {
    const target = scope();
    if (!target) return;
    if (target.kind === "act") document.getElementById(ACT_SNOOZE)?.focus();
    else if (target.kind === "panel") askPanel("snooze", target.item.ref);
    else void openRow(target.row.ref, { focus: "snooze" });
  };

  const undo = () => {
    if (!notice?.batch || !notice.then) return;
    void write({ op: "undo", batch: notice.batch, said: notice.then }).then((outcome) =>
      setRowProblem(outcome.ok ? "" : outcome.error),
    );
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
    {
      keys: ["j"],
      what: "next row; with the panel open, its item",
      run: () => void move(1),
      when: () => order.length > 0,
    },
    { keys: ["k"], what: "previous row", run: () => void move(-1), when: () => order.length > 0 },
    {
      keys: ["Enter", "o"],
      what: "open the selected row beside the list",
      run: () => selected !== undefined && void openRow(selected, { focus: "row" }),
      when: () => selected !== undefined && byRef.has(selected),
    },
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
    {
      keys: ["u"],
      what: "undo the last change, or redo it; Ctrl+Z too",
      run: undo,
      when: () => idle() && Boolean(notice?.batch),
    },
    { keys: ["z"], ctrl: true, what: "undo, as u", run: undo, when: () => idle() && Boolean(notice?.batch) },
    { keys: ["n"], what: "a new item", run: openNew },
    { keys: ["/"], what: "to the search box", run: () => document.getElementById(FIND)?.focus() },
    {
      keys: ["Escape"],
      what: "clear the ticks, or close the panel",
      run: () => (tickedRows.length ? clearTicks() : void closePanel()),
      when: () => tickedRows.length > 0 || open !== undefined,
    },
  ];
  useKeys("ledger", "The Ledger", commands);

  const viewer: ViewerState | null =
    open !== undefined && text !== undefined
      ? {
          ref: text,
          data: viewerQuery.data,
          problem: viewerQuery.error ? calls.wordsOf(viewerQuery.error) : "",
          loading: viewerQuery.isFetching,
        }
      : null;

  const listPart = (
    <>
      <div className="@container min-h-0 flex-1 overflow-y-auto" role="region" aria-label="Items" tabIndex={-1}>
        {list.error && !data ? (
          <div className="grid justify-items-start gap-3 px-4 py-6">
            <Refusal>{calls.wordsOf(list.error)}</Refusal>
            <a href="/ledger" className="text-sm text-green underline-offset-4 hover:underline">
              Open the Ledger as it opens
            </a>
          </div>
        ) : data ? (
          <LedgerRows
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
          <p className="px-4 py-6 text-sm text-ink-muted">Reading the ledger.</p>
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
      {rowProblem && <Refusal className="mx-3 my-2 sm:mx-4">{rowProblem}</Refusal>}
      {notice && (
        <NoticeBar
          text={notice.text}
          lines={notice.lines}
          action={notice.button}
          onAction={undo}
          disabled={pending}
          onDismiss={() => setNotice(null)}
        />
      )}
    </>
  );

  const panelPart =
    open === undefined ? undefined : (
      <Panel
        label={item ? `${item.ref}, ${item.title}` : `${open}`}
        onClose={() => void closePanel()}
        header={
          item ? (
            <PanelHead item={item} />
          ) : (
            <h2 id="panel-title" tabIndex={-1} className="text-sm font-semibold">
              {panel.data?.missing ? "Nothing to open" : open}
            </h2>
          )
        }
      >
        {item ? (
          <ItemPanelBody
            key={item.id}
            item={item}
            ask={ask}
            drop={drop}
            callProblem={callProblem?.ref === item.ref ? callProblem.words : ""}
            viewer={viewer}
            onCall={(body, except) => callOn({ kind: "panel", item }, body, except)}
            onDrop={(shown) => void go({ drop: shown ? "1" : undefined }, { replace: true })}
            onUndo={async (op, said) => {
              const outcome = await write({ op: "undo", of: op, said });
              setCallProblem(outcome.ok ? null : { ref: item.ref, words: outcome.error });
            }}
            onView={(ref) => void go({ text: ref }, { replace: true })}
          />
        ) : panel.error ? (
          <div className="p-4">
            <Refusal>{calls.wordsOf(panel.error)}</Refusal>
          </div>
        ) : panel.data?.missing ? (
          <div className="p-4">
            <Refusal>{panel.data.missing}</Refusal>
          </div>
        ) : (
          <p className="p-4 text-sm text-ink-muted">Reading {open}.</p>
        )}
      </Panel>
    );

  return (
    <Actions.Provider value={actions}>
      <Page>
        <PageHeader title="Ledger">
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
          <Filters data={data} narrow={band === "narrow"} hrefOf={hrefOf} onChange={(change) => void go(change)} />
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
