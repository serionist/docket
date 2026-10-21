/**
 * What every day page runs, whatever its rows are (DR-0664 to DR-0666, DR-0710, DR-0711): the list and panel read
 * with the URL's query string and the canonical URL written back, one selection the panel follows, the writes with
 * their notice and Undo, a save's versions, the 5 s poll that holds while the owner is mid-change, the guard on
 * unsaved text, the text viewer, and the keys that move, open, undo and find. The Ledger and the Queue add their
 * items' ticks and calls on top (page.tsx), Meetings its points (pages/meetings); none of this is forked.
 */
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useBlocker, useNavigate, useRouter, useRouterState, useSearch } from "@tanstack/react-router";
import * as React from "react";
import { SHELL_KEY } from "@/components/shell/app-sidebar";
import { after, step, standIn } from "@/lib/day";
import { anyUnsaved, mayLeave, saves, settled } from "@/lib/drafts";
import type { KeyCommand } from "@/lib/keys";
import { holding, usePoll } from "@/lib/poll";
import { one, type Search, stringifySearch, writeBack } from "@/lib/search";
import * as calls from "./api";
import type { Body, DayPageName, Notice, Read, Row, Wrote } from "./api";
import type { How, Outcome } from "./context";
import { FIND } from "./filters";
import type { Ask, ViewerState } from "./panel";
import { listKey, type Param, quiet, withSearch } from "./url";

const CALLS = ["triage", "snooze", "done", "drop", "reopen"];
/** What every day page's reads are cached under, so a write or the poll refetches them all at once. */
export const DAY = ["day"] as const;
export const PATHS = {
  ledger: "/ledger",
  queue: "/queue",
  meetings: "/meetings",
} as const satisfies Record<DayPageName, string>;
export type Change = Partial<Record<Param, string | undefined>>;

/** What a page's list read holds that the frame reads: the canonical URL, the version the poll compares, the notice
 * a URL from before still names. */
export interface FrameList {
  canonical: string;
  version: string;
  notice?: Notice | null;
}

export interface FrameOptions<L extends FrameList, P extends { canonical: string }> {
  page: DayPageName;
  title: string;
  readList: (query: string) => Promise<Read<L>>;
  readPanel: (query: string) => Promise<Read<P>>;
  /** The rows' refs in the list's order: j and k move through them, and a call moves the selection along them. */
  orderOf: (list: L) => string[];
  /** What the panel's read depends on besides `open`, such as Meetings' suggest. */
  panelKey?: (search: Search) => string;
  /** How many rows are ticked now, which holds the poll. */
  ticked?: () => number;
}

export const refused = (error: unknown): { ok: false; error: string; alikes: Row[] } => ({
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

export function useDayFrame<L extends FrameList, P extends { canonical: string }>(options: FrameOptions<L, P>) {
  const { page, readList, readPanel, orderOf, panelKey } = options;
  const path = PATHS[page];
  const search = useSearch({ strict: false }) as Search;
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const navigate = useNavigate();
  const router = useRouter();
  const client = useQueryClient();
  const latest = React.useRef(options);
  React.useLayoutEffect(() => {
    latest.current = options;
  });

  const open = one(search, "open");
  const text = one(search, "text");

  const list = useQuery({
    queryKey: [...DAY, page, "list", listKey(search)],
    queryFn: () => readList(searchStr),
    placeholderData: keepPreviousData,
  });
  const panel = useQuery({
    queryKey: [...DAY, page, "panel", open, panelKey?.(search) ?? ""],
    queryFn: () => readPanel(searchStr),
    enabled: open !== undefined,
  });
  const viewerQuery = useQuery({
    queryKey: [...DAY, "text", open, text],
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
  const order = React.useMemo(() => (data ? orderOf(data) : []), [data, orderOf]);

  // One selection: the open row, else the row the URL's at names, else the one last selected.
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

  const [notice, setNotice] = React.useState<Notice | null>(null);
  const [adopted, setAdopted] = React.useState(false);
  if (!adopted && data) {
    // A URL that still carries what a write said shows its notice once; the canonical URL leaves it out.
    setAdopted(true);
    if (data.notice) setNotice(data.notice);
  }
  const [pending, setPending] = React.useState(false);
  const [rowProblem, setRowProblem] = React.useState("");
  const [ask, setAsk] = React.useState<Ask | null>(null);

  // What async work and the poll read as it stands now, not as the render that started them saw it.
  const now = React.useRef({ selected, open, pending: false, version: undefined as string | undefined });
  React.useLayoutEffect(() => {
    now.current.selected = selected;
    now.current.open = open;
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
    void client.invalidateQueries({ queryKey: DAY });
    void client.invalidateQueries({ queryKey: SHELL_KEY });
  };

  usePoll({
    version: () => now.current.version,
    ask: calls.changed,
    hold: () => now.current.pending || holding(latest.current.ticked?.() ?? 0),
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
      to: path,
      search: (prev) => withSearch(quiet(prev as Search), change),
      replace: opts.replace,
      ignoreBlocker: true,
    });
  const hrefOf = (change: Change) => `${path}${stringifySearch(withSearch(quiet(search), change))}`;

  /** Open a row's panel, once the panel's own unsaved text, if any, may go. */
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
    void go({ open: undefined, drop: undefined, text: undefined, suggest: undefined, at: shown });
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
      wrote = await calls.write({ ...body, page, at: at ?? null, bulk: Boolean(how.bulk) });
    } catch (error) {
      return refused(error);
    } finally {
      setPending(false);
    }
    now.current.version = wrote.version;
    setNotice(wrote.notice);
    setRowProblem("");
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

  /**
   * A field saved on change: `put` places what the save answered in the page's reads. A write from the CLI, Claude or
   * a sync that landed while the poll held for the field moved the version before the save's own write; adopting the
   * save's version alone would hide it from the poll for good, so the page then refetches its reads.
   */
  const save = async (
    body: Body,
    put: (answer: calls.SaveAnswer, missed: boolean) => void,
  ): Promise<Outcome<calls.SaveAnswer>> => {
    let saved: calls.SaveAnswer;
    try {
      saved = await calls.save({ ...body, page });
    } catch (error) {
      return refused(error);
    }
    const missed = saved.before !== now.current.version;
    now.current.version = saved.version;
    put(saved, missed);
    if (missed) refetch();
    return { ok: true, answer: saved };
  };

  const openMaterial = async (item: string, ref: string, reveal: boolean): Promise<Outcome<null>> => {
    try {
      setNotice(await calls.openMaterial(page, item, ref, reveal));
    } catch (error) {
      return refused(error);
    }
    return { ok: true, answer: null };
  };

  const undo = () => {
    if (!notice?.batch || !notice.then) return;
    void write({ op: "undo", batch: notice.batch, said: notice.then }).then((outcome) =>
      setRowProblem(outcome.ok ? "" : outcome.error),
    );
  };

  const has = (ref: string | undefined) => ref !== undefined && order.includes(ref);
  const idle = () => !pending;
  /** The keys every day page answers: j, k, Enter or o, u and Ctrl+Z, and / to the find box. */
  const keys = {
    next: {
      keys: ["j"],
      what: "next row; with the panel open, its item",
      run: () => void move(1),
      when: () => order.length > 0,
    },
    previous: { keys: ["k"], what: "previous row", run: () => void move(-1), when: () => order.length > 0 },
    open: {
      keys: ["Enter", "o"],
      what: "open the selected row beside the list",
      run: () => selected !== undefined && void openRow(selected, { focus: "row" }),
      when: () => has(selected),
    },
    undo: {
      keys: ["u"],
      what: "undo the last change, or redo it; Ctrl+Z too",
      run: undo,
      when: () => idle() && Boolean(notice?.batch),
    },
    undoCtrl: { keys: ["z"], ctrl: true, what: "undo, as u", run: undo, when: () => idle() && Boolean(notice?.batch) },
    find: { keys: ["/"], what: "to the search box", run: () => document.getElementById(FIND)?.focus() },
  } satisfies Record<string, KeyCommand>;

  const viewer: ViewerState | null =
    open !== undefined && text !== undefined
      ? {
          ref: text,
          data: viewerQuery.data,
          problem: viewerQuery.error ? calls.wordsOf(viewerQuery.error) : "",
          loading: viewerQuery.isFetching,
        }
      : null;

  return {
    path,
    search,
    open,
    list,
    panel,
    data,
    order,
    selected,
    setSelected,
    notice,
    setNotice,
    pending,
    rowProblem,
    setRowProblem,
    ask,
    askPanel,
    focusSoon,
    refetch,
    go,
    hrefOf,
    openRow,
    closePanel,
    move,
    write,
    save,
    openMaterial,
    undo,
    keys,
    viewer,
    client,
  };
}
