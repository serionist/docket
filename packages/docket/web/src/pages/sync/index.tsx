/**
 * /sync (DR-0788): what needs the owner or a Claude session, with the words to paste; what
 * failed and what to do; the runs open and recent; each machine's cursors. Read-only: nothing here opens, closes or
 * drops a run. While a run is open the runs part is read again every 5 seconds, and when the last one closes the page
 * reads itself again, with the sidebar's due mark. A run in the URL opens its card in the panel. Every state is the
 * URL, written back as the server wrote it, so Today's sync links and a bookmark open the same page.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useRouterState } from "@tanstack/react-router";
import { KeyboardIcon } from "lucide-react";
import * as React from "react";
import { Chips } from "@/components/chips";
import { Note } from "@/components/doc";
import { COPIED_MS } from "@/components/paste";
import { SHELL_KEY } from "@/components/shell/app-sidebar";
import { Loading, NoticeBar, Page, PageHeader, Panel, ReadFailed, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { useBand } from "@/hooks/use-width";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { POLL_MS } from "@/lib/poll";
import { one, parseSearch, type Search, writeBack } from "@/lib/search";
import { PATH, readRuns, readSync, type RunRead, withRun } from "./api";
import { Cursors, Failures, Needs, Notes, RunCard, Runs, rowsOf } from "./parts";

export const SYNC = ["sync"] as const;

function focusRow(id: string | undefined): void {
  if (id === undefined) return;
  const row = document.querySelector<HTMLElement>(`#main [data-row="${CSS.escape(id)}"]`);
  if (!row || row.closest("[hidden]")) return;
  row.focus({ preventScroll: true });
  row.scrollIntoView({ block: "nearest" });
}

function step(order: RunRead[], at: string | undefined, by: 1 | -1): RunRead | undefined {
  if (!order.length) return undefined;
  const now = order.findIndex((run) => run.id === at);
  if (now < 0) return by > 0 ? order[0] : order[order.length - 1];
  return order[Math.min(order.length - 1, Math.max(0, now + by))];
}

export function SyncPage() {
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const router = useRouter();
  const client = useQueryClient();
  const band = useBand();
  const showKeys = useShowKeys();
  useTitle("Sync");
  const search: Search = parseSearch(searchStr);
  const open = one(search, "run");

  const read = useQuery({
    queryKey: [...SYNC, "page", searchStr],
    queryFn: () => readSync(searchStr),
    placeholderData: (previous) => previous,
    // A page opened again asks once, so a page kept from before isn't stale.
    refetchOnMount: "always",
  });
  const data = read.data;
  // While a run is open, its part alone every 5 s: the service's memory, no cursor file and no check (DR-0788).
  const live = Boolean(data?.runs.live);
  const runsRead = useQuery({
    queryKey: [...SYNC, "runs", searchStr],
    queryFn: () => readRuns(searchStr),
    enabled: live,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  });
  const fresh = runsRead.data && runsRead.data.asked === searchStr && runsRead.dataUpdatedAt > read.dataUpdatedAt;
  const runs = fresh ? runsRead.data?.runs : data?.runs;

  // The last open run closed: what is due, the failures and the cursors moved too, and the sidebar's mark.
  const [wasLive, setWasLive] = React.useState(false);
  const runsLive = runs?.live ?? false;
  if (runsLive !== wasLive) {
    setWasLive(runsLive);
    if (wasLive && !runsLive) {
      void client.invalidateQueries({ queryKey: [...SYNC, "page"] });
      void client.invalidateQueries({ queryKey: SHELL_KEY });
    }
  }

  // The server's canonical URL, written back as it came.
  React.useEffect(() => {
    const history = { location: router.history.location, replace: (href: string) => router.history.replace(href) };
    if (data && data.asked === searchStr) writeBack(history, data.canonical);
  }, [data, searchStr, router]);

  const order = React.useMemo(() => rowsOf(runs), [runs]);
  const [selected, setSelected] = React.useState<string | undefined>(open);
  const [seenOpen, setSeenOpen] = React.useState(open);
  if (open !== seenOpen) {
    setSeenOpen(open);
    if (open !== undefined) setSelected(open);
  }
  const now = React.useRef({ selected, open, order });
  React.useLayoutEffect(() => {
    now.current = { selected, open, order };
  });

  // What takes the focus once the next read answers: the panel's heading or a row.
  const wanted = React.useRef<string | null>(null);
  const focus = runs?.focus;
  React.useEffect(() => {
    const want = wanted.current;
    if (want === null) return;
    if (want === "heading") {
      if (focus) {
        document.getElementById("panel-title")?.focus({ preventScroll: true });
        wanted.current = null;
      }
      return;
    }
    if (order.some((run) => run.id === want)) {
      focusRow(want);
      wanted.current = null;
    }
  });

  const go = (href: string, replace = false) => void router.navigate({ href, replace });

  const openRun = (run: RunRead, focusOn: "heading" | "row" = "heading") => {
    setSelected(run.id);
    wanted.current = focusOn === "heading" ? "heading" : run.id;
    go(run.url, now.current.open !== undefined);
  };

  const closePanel = () => {
    wanted.current = now.current.selected ?? now.current.open ?? null;
    go(withRun(search, undefined));
  };

  const move = (by: 1 | -1) => {
    const next = step(now.current.order, now.current.selected, by);
    if (next === undefined) return;
    if (now.current.open !== undefined && next.id !== now.current.open) openRun(next, "row");
    else {
      setSelected(next.id);
      focusRow(next.id);
    }
  };

  const commands: KeyCommand[] = [
    { keys: ["j"], what: "next run; with one open, it opens", run: () => move(1), when: () => order.length > 0 },
    { keys: ["k"], what: "previous run", run: () => move(-1), when: () => order.length > 0 },
    {
      keys: ["Enter", "o"],
      what: "open the run beside the list",
      run: () => {
        const run = order.find((r) => r.id === selected);
        if (run) openRun(run);
      },
      when: () => order.some((r) => r.id === selected),
    },
    { keys: ["Escape"], what: "close the run", run: closePanel, when: () => open !== undefined },
  ];
  useKeys("sync", "Sync", commands);

  const [copied, setCopied] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (copied === null) return;
    const timer = setTimeout(() => setCopied(null), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = (text: string) => void navigator.clipboard?.writeText(text).then(() => setCopied(text));

  const covered = open !== undefined && (band === "narrow" || band === "small");
  const foot = copied && <NoticeBar text="Copied" lines={[copied]} onDismiss={() => setCopied(null)} />;

  let body: React.ReactNode;
  const narrowed = data?.chips.find((chip) => chip.on && chip.url !== PATH)?.label;
  if (read.error && !data) {
    body = <ReadFailed error={read.error} onRetry={() => void read.refetch()} whole={() => go(PATH)} />;
  } else if (!data || !runs) {
    body = <Loading what="Reading what the syncs did." />;
  } else {
    body = (
      <>
        <div className="flex shrink-0 items-center gap-x-4 overflow-x-auto border-b border-rule px-2 py-1.5 sm:px-3">
          <Chips label="Sources" chips={data.chips} />
        </div>
        <div
          className="min-h-0 flex-1 overflow-y-auto"
          role="region"
          aria-label="What the syncs did"
          aria-busy={read.isFetching || undefined}
          tabIndex={-1}
        >
          <p data-lead className="px-3 pt-3 pb-3 text-base sm:px-4">
            {data.lead}
          </p>
          <Notes notes={data.notes.filter((note) => !open || !note.includes(open))} />
          <Needs page={data} onCopy={copy} narrowed={narrowed} />
          <Failures failures={data.failures} onRun={(href) => go(href, open !== undefined)} narrowed={narrowed} />
          <Runs
            runs={runs}
            narrowed={narrowed}
            onMore={(href) => go(href)}
            rows={{ selected, open, onSelect: setSelected, onOpen: (run) => openRun(run) }}
          />
          <Cursors page={data} />
        </div>
      </>
    );
  }

  const missing = open !== undefined && data && !focus ? data.notes.find((note) => note.includes(open)) : undefined;
  const heading = focus ? `${focus.source}, ${focus.word}` : missing ? "Not found" : "The run";
  const panelPart =
    open !== undefined ? (
      <Panel
        label={`The run: ${heading}`}
        onClose={closePanel}
        back="Back to the runs"
        footer={covered ? foot : undefined}
        header={
          <h2 id="panel-title" tabIndex={-1} className="truncate text-sm font-semibold outline-none" title={heading}>
            {heading}
          </h2>
        }
      >
        {focus ? (
          <RunCard run={focus} />
        ) : missing ? (
          <div className="p-4">
            <Note tone="warn">{missing}</Note>
          </div>
        ) : (
          <p className="p-4 text-sm text-ink-muted">Reading the run.</p>
        )}
      </Panel>
    ) : undefined;

  return (
    <Page>
      <PageHeader title="Sync">
        <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
          <KeyboardIcon />
        </Button>
      </PageHeader>
      <Workspace
        list={
          <>
            {body}
            {!covered && foot}
          </>
        }
        panel={panelPart}
      />
    </Page>
  );
}
