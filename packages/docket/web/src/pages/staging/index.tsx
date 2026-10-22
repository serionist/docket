/**
 * /staging (DR-0380, DR-0382, DR-0784, DR-0785): what waits at the staging gate, worked through the gate's own calls.
 * The list by source with why each record waits, the ready bar's promotions, the held recordings with the commands
 * that release them at the owner's console, and the open record beside the list, as promotion would write it, with
 * its ticks, Preview, Work and Private. Every state is the URL, written back as the server wrote it, so a link from
 * Claude or the Recording page opens the same list. The keys move and open; no key writes (DR-0711).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useRouterState } from "@tanstack/react-router";
import { KeyboardIcon } from "lucide-react";
import * as React from "react";
import { SHELL_KEY } from "@/components/shell/app-sidebar";
import { Note } from "@/components/doc";
import { NoticeBar, Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { useBand } from "@/hooks/use-width";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { HttpError } from "@/lib/query";
import { one, parseSearch, type Search, writeBack } from "@/lib/search";
import { wordsOf } from "@/pages/day/api";
import {
  chosenHref,
  classify,
  listQuery,
  PATH,
  promote,
  readRecord,
  readStaging,
  type StagedRow,
  type StagingPanel,
} from "./api";
import { Held, Unreadable } from "./held";
import { FilterBar, rowsOf, WaitingList } from "./list";
import { type Choice, chosenOf, RecordActions, RecordBody } from "./record";

export const STAGING = ["staging"] as const;
const COPIED_MS = 2500;

function focusRow(key: string | undefined): void {
  if (key === undefined) return;
  const row = document.querySelector<HTMLElement>(`#main [data-row="${CSS.escape(key)}"]`);
  if (!row || row.closest("[hidden]")) return;
  row.focus({ preventScroll: true });
  row.scrollIntoView({ block: "nearest" });
}

function step(order: StagedRow[], at: string | undefined, by: 1 | -1): StagedRow | undefined {
  if (!order.length) return undefined;
  const now = order.findIndex((row) => row.key === at);
  if (now < 0) return by > 0 ? order[0] : order[order.length - 1];
  return order[Math.min(order.length - 1, Math.max(0, now + by))];
}

/** The record a query string opens, or "": a panel kept while the next read comes is the same record's only. */
function openOf(query: string): string {
  return one(parseSearch(query), "open") ?? "";
}

export function StagingPage() {
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const router = useRouter();
  const client = useQueryClient();
  const band = useBand();
  const showKeys = useShowKeys();
  useTitle("Staging");
  const search: Search = parseSearch(searchStr);
  const open = one(search, "open");
  const listing = listQuery(search);

  const list = useQuery({
    queryKey: [...STAGING, "list", listing],
    queryFn: () => readStaging(listing),
    placeholderData: (previous) => previous,
    // A page opened again asks once, so a list kept from before isn't stale.
    refetchOnMount: "always",
  });
  const panelRead = useQuery({
    queryKey: [...STAGING, "panel", searchStr],
    queryFn: () => readRecord(searchStr),
    enabled: open !== undefined,
    refetchOnMount: "always",
    // The same record read again, previewed, stays as it was until the read answers; another record's never shows.
    placeholderData: (previous) => (previous && openOf(previous.asked) === (open ?? "") ? previous : undefined),
  });
  const data = list.data;
  const view = data?.view;
  const shown = open !== undefined && panelRead.data?.asked === searchStr ? panelRead.data : undefined;
  const panel: StagingPanel | undefined = open !== undefined ? (panelRead.data?.panel ?? undefined) : undefined;

  // The server's canonical URL, written back as it came: the panel's for the whole URL, the list's with none open.
  React.useEffect(() => {
    const history = { location: router.history.location, replace: (href: string) => router.history.replace(href) };
    if (shown) writeBack(history, shown.canonical);
    else if (open === undefined && data && data.asked === listing) writeBack(history, data.canonical);
  }, [shown, data, open, listing, router]);

  // What the last write did, or a URL from before the app that still names it, said once at the list's foot.
  const [notice, setNotice] = React.useState<string | null>(null);
  const [seenNotice, setSeenNotice] = React.useState<string | null>(null);
  if (data?.notice && data.asked === listing && seenNotice !== data.asked) {
    setSeenNotice(data.asked);
    setNotice(data.notice);
  }

  // What is ticked and typed on the open record, the read's until the owner changes it; each new read starts again,
  // and a refusal reads nothing again, so what was ticked stays beside its words.
  const [choice, setChoice] = React.useState<Choice>({ u: [], lines: [], typed: "" });
  const [choiceOf, setChoiceOf] = React.useState<string | null>(null);
  const read = panelRead.data;
  const readAt = read ? `${read.asked} ${panelRead.dataUpdatedAt}` : null;
  if (read && read.panel && !panelRead.isPlaceholderData && choiceOf !== readAt) {
    setChoiceOf(readAt);
    setChoice(chosenOf(read.panel));
  }

  // Why the last write on the open record wrote nothing, with the record it was on: it goes when the record does.
  const [refused, setRefused] = React.useState<{ key: string; words: string; moved: boolean } | null>(null);
  if (refused && refused.key !== open) setRefused(null);
  const [pending, setPending] = React.useState(false);
  const [promoteRefused, setPromoteRefused] = React.useState("");

  const order = React.useMemo(() => rowsOf(view), [view]);
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
  React.useEffect(() => {
    const want = wanted.current;
    if (want === null) return;
    if (want === "heading") {
      if (shown) {
        document.getElementById("panel-title")?.focus({ preventScroll: true });
        wanted.current = null;
      }
      return;
    }
    if (order.some((row) => row.key === want)) {
      focusRow(want);
      wanted.current = null;
    }
  });

  const go = (href: string, replace = false) => void router.navigate({ href, replace });

  const openRow = (row: StagedRow, focus: "heading" | "row" = "heading") => {
    setSelected(row.key);
    wanted.current = focus === "heading" ? "heading" : row.key;
    go(row.url, now.current.open !== undefined);
  };

  const closePanel = () => {
    const back = now.current.selected ?? now.current.open;
    wanted.current = back ?? null;
    go(panel?.close ?? `${PATH}${listQuery(search)}`);
  };

  const move = (by: 1 | -1) => {
    const next = step(now.current.order, now.current.selected, by);
    if (next === undefined) return;
    if (now.current.open !== undefined && next.key !== now.current.open) openRow(next, "row");
    else {
      setSelected(next.key);
      focusRow(next.key);
    }
  };

  const commands: KeyCommand[] = [
    { keys: ["j"], what: "next record; with one open, it opens", run: () => move(1), when: () => order.length > 0 },
    { keys: ["k"], what: "previous record", run: () => move(-1), when: () => order.length > 0 },
    {
      keys: ["Enter", "o"],
      what: "open the record beside the list",
      run: () => {
        const row = order.find((r) => r.key === selected);
        if (row) openRow(row);
      },
      when: () => order.some((r) => r.key === selected),
    },
    { keys: ["Escape"], what: "close the record", run: closePanel, when: () => open !== undefined },
  ];
  useKeys("staging", "Staging", commands);

  const [copied, setCopied] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (copied === null) return;
    const timer = setTimeout(() => setCopied(null), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = (text: string) => void navigator.clipboard?.writeText(text).then(() => setCopied(text));

  // A write read again: the list, the open record and the sidebar's count of what waits.
  const settle = () => {
    void client.invalidateQueries({ queryKey: STAGING });
    void client.invalidateQueries({ queryKey: SHELL_KEY });
  };

  const preview = () => {
    if (!panel) return;
    setRefused(null);
    go(chosenHref(search, panel.key, choice.u, choice.lines, choice.typed), true);
  };

  const write = (label: "work" | "private") => {
    if (!panel) return;
    const key = panel.key;
    setPending(true);
    setRefused(null);
    const body =
      label === "work"
        ? {
            key,
            label,
            u: choice.u.map(String),
            line: choice.lines.map(String),
            cut: choice.typed.trim() ? [choice.typed] : [],
            seen: panel.seen,
          }
        : { key, label, u: [], line: [], cut: [], seen: panel.seen };
    classify(body)
      .then(
        (done) => {
          setNotice(done.notice);
          wanted.current = key;
          go(panel.close);
          settle();
        },
        (error: unknown) => {
          // A record that changed since wrote nothing: read again, it shows as it stands now, with its own ticks.
          const movedSince = error instanceof HttpError && error.status === 409;
          setRefused({ key, words: wordsOf(error), moved: movedSince });
          if (movedSince) settle();
        },
      )
      .finally(() => setPending(false));
  };

  const onPromote = (source: string | null) => {
    setPending(true);
    setPromoteRefused("");
    promote(source)
      .then(
        (done) => setNotice(done.notice),
        (error: unknown) => setPromoteRefused(wordsOf(error)),
      )
      .finally(() => {
        setPending(false);
        settle();
      });
  };

  const covered = open !== undefined && (band === "narrow" || band === "small");
  const foot = (notice || copied) && (
    <NoticeBar
      text={copied ? "Copied" : (notice ?? "")}
      lines={copied ? [copied] : []}
      onDismiss={() => (copied ? setCopied(null) : setNotice(null))}
    />
  );

  let body: React.ReactNode;
  if (list.error && !data) {
    body = (
      <div className="grid justify-items-start gap-3 px-4 py-6">
        <Refusal>{wordsOf(list.error)}</Refusal>
        <Button size="sm" onClick={() => go(PATH)}>
          The whole list
        </Button>
      </div>
    );
  } else if (!view || !data) {
    body = <p className="px-4 py-6 text-sm text-ink-muted">Reading what waits at the gate.</p>;
  } else {
    body = (
      <>
        <FilterBar view={view} />
        <div
          className="min-h-0 flex-1 overflow-y-auto"
          role="region"
          aria-label="Records at the gate"
          aria-busy={list.isFetching || undefined}
          tabIndex={-1}
        >
          {promoteRefused && <Refusal className="m-3 sm:mx-4">{promoteRefused}</Refusal>}
          <WaitingList
            view={view}
            pending={pending}
            onPromote={onPromote}
            onMore={(href) => go(href)}
            rows={{ selected, open, onSelect: setSelected, onOpen: (row) => openRow(row) }}
          >
            <Held held={data.held} onCopy={copy} />
            <Unreadable view={view} />
          </WaitingList>
        </div>
      </>
    );
  }

  const missing = open !== undefined ? read?.missing : undefined;
  const heading = panel
    ? panel.on_hold
      ? `on hold: ${panel.kind}`
      : panel.title || "(no title)"
    : missing
      ? "Not staged any more"
      : "The open record";
  const panelPart =
    open !== undefined ? (
      <Panel
        label={`The open record: ${heading}`}
        onClose={closePanel}
        footer={
          <>
            {panel && (
              <RecordActions
                panel={panel}
                choice={choice}
                pending={pending}
                onPreview={preview}
                onWork={() => write("work")}
                onPrivate={() => {
                  setRefused(null);
                  go(panel.confirm_url, true);
                }}
                onDrop={() => write("private")}
                onKeep={() => go(panel.back, true)}
              />
            )}
            {covered && foot}
          </>
        }
        header={
          <h2
            id="panel-title"
            tabIndex={-1}
            dir="auto"
            className="truncate text-sm font-semibold outline-none"
            title={heading}
          >
            {heading}
          </h2>
        }
      >
        {panelRead.error ? (
          <div className="p-4">
            <Refusal>{wordsOf(panelRead.error)}</Refusal>
          </div>
        ) : panel ? (
          <RecordBody
            panel={panel}
            choice={choice}
            changed={refused?.moved ? refused.words : ""}
            refused={refused && !refused.moved ? refused.words : ""}
            onChange={setChoice}
            onSubmit={preview}
          />
        ) : missing ? (
          <div className="p-4">
            <Note tone="warn">{missing.note}</Note>
          </div>
        ) : (
          <p className="p-4 text-sm text-ink-muted">Reading the record.</p>
        )}
      </Panel>
    ) : undefined;

  return (
    <Page>
      <PageHeader title="Staging">
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
