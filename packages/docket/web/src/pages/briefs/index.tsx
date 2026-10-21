/**
 * /briefs (DR-0780, DR-0781): the briefs by entity type, and a brief opened with its lines, the open line's raw in the
 * panel beside it, and for a project, topic or stream its decisions, where only the owner's pick settles one. Every
 * state is the URL, written back as the server wrote it, so Claude's links, a meeting's people and a bookmark open the
 * same page; a link that sets `line` ends in #raw. The keys: j and k through the briefs or the lines that cite, the
 * open raw following; o opens; Esc closes the raw.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useRouterState } from "@tanstack/react-router";
import { KeyboardIcon } from "lucide-react";
import * as React from "react";
import { Note } from "@/components/doc";
import { NoticeBar, Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { useBand } from "@/hooks/use-width";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { one, parseSearch, type Search, stringifySearch, writeBack } from "@/lib/search";
import { wordsOf } from "@/pages/day/api";
import { type BriefLine, type BriefsView, type Card, decide, type Pick, readBriefs } from "./api";
import { Back, BriefHead, BriefLines, Eyebrow } from "./brief";
import { DECISIONS_TITLE, Decisions, type Picking } from "./decisions";
import { BriefList } from "./list";
import { RawBody, rawTitle } from "./raw";

export const BRIEFS = ["briefs"] as const;
const PATH = "/briefs";
const WAIT_MS = 5000;

/** The brief a query string opens, or "" for the list: a read kept while the next comes is the same brief's only. */
function briefOf(query: string): string {
  return one(parseSearch(query), "brief") ?? "";
}

/** The rows j and k move through, in the page's order: the briefs, or a brief's lines that cite. */
function rows(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>("#main [data-row]")].filter((row) => !row.closest("[hidden]"));
}

function focusRow(key: string | null): void {
  if (key === null) return;
  const row = rows().find((r) => r.dataset.row === key);
  row?.focus({ preventScroll: true });
  row?.scrollIntoView({ block: "nearest" });
}

export function BriefsPage() {
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const router = useRouter();
  const client = useQueryClient();
  const band = useBand();
  const showKeys = useShowKeys();
  const search: Search = parseSearch(searchStr);
  const brief = one(search, "brief") ?? "";
  const openLine = brief ? one(search, "line") : undefined;

  const read = useQuery({
    queryKey: [...BRIEFS, searchStr],
    queryFn: () => readBriefs(searchStr),
    // The same brief, or the list, stays as it was until the next read answers; another brief's never shows.
    placeholderData: (previous) => (previous && briefOf(previous.asked) === brief ? previous : undefined),
    // A page opened again asks once, so a brief kept from before isn't stale.
    refetchOnMount: "always",
    // The index still being built: ask again until it answers.
    refetchInterval: (q) => (q.state.data?.waiting ? WAIT_MS : false),
  });
  const data = read.data;
  const fresh = data !== undefined && data.asked === searchStr;
  const view: BriefsView | undefined = data?.view ?? undefined;
  const title = !view || view.mode === "list" ? "Briefs" : view.title;
  useTitle(!view || view.mode === "list" ? "Briefs" : `${view.title} - Briefs`);

  // The server's canonical URL, written back as it came, with the #raw a link carries.
  React.useEffect(() => {
    if (data && data.asked === searchStr) {
      writeBack({ location: router.history.location, replace: (href) => router.history.replace(href) }, data.canonical);
    }
  }, [data, searchStr, router]);

  // What the last pick did, or a URL from before the app that still names it, said once at the list's foot.
  const [notice, setNotice] = React.useState<string | null>(null);
  const [refused, setRefused] = React.useState("");
  const [seenNotice, setSeenNotice] = React.useState<string | null>(null);
  if (fresh && data.notice && seenNotice !== data.asked) {
    setSeenNotice(data.asked);
    setNotice(data.notice);
  }

  // What takes the focus once the next read answers: the panel's heading, a line's row, or the decisions' heading.
  const wanted = React.useRef<string | null>(null);
  // The open line scrolled into sight once for each URL, as a link from Claude lands on it.
  const scrolled = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!fresh || read.isPlaceholderData) return;
    if (scrolled.current !== searchStr) {
      scrolled.current = searchStr;
      document.querySelector("#main [data-open]")?.scrollIntoView({ block: "nearest" });
    }
    const want = wanted.current;
    if (want === null) return;
    wanted.current = null;
    if (want === "panel") document.getElementById("panel-title")?.focus({ preventScroll: true });
    else if (want === "decisions") document.getElementById(DECISIONS_TITLE)?.focus({ preventScroll: true });
    else focusRow(want);
  });

  const go = (href: string, replace = false) => void router.navigate({ href, replace });
  const lines = (view?.mode === "brief" ? view.lines : []).filter((line) => line.url);

  const openRaw = (line: BriefLine) => {
    if (!line.url) return;
    wanted.current = "panel";
    go(line.url, openLine !== undefined);
  };

  const closeRaw = () => {
    const raw = view?.raw;
    if (openLine === undefined) return;
    wanted.current = openLine;
    const rest = Object.fromEntries(Object.entries(search).filter(([key]) => key !== "line"));
    go(raw?.close || `${PATH}${stringifySearch(rest)}`);
  };

  const move = (by: 1 | -1) => {
    if (openLine !== undefined && lines.length) {
      const at = lines.findIndex((line) => String(line.n) === openLine);
      const next = lines[Math.min(lines.length - 1, Math.max(0, at < 0 ? 0 : at + by))];
      if (next && String(next.n) !== openLine) {
        wanted.current = String(next.n);
        go(next.url as string, true);
      }
      return;
    }
    const all = rows();
    if (!all.length) return;
    const at = all.findIndex((row) => row === document.activeElement);
    const next = at < 0 ? (by > 0 ? all[0] : all[all.length - 1]) : all[Math.min(all.length - 1, Math.max(0, at + by))];
    next?.focus({ preventScroll: true });
    next?.scrollIntoView({ block: "nearest" });
  };

  const commands: KeyCommand[] = [
    {
      keys: ["j"],
      what: "next brief, or next line; with its raw open, that line's",
      run: () => move(1),
      when: () => rows().length > 0,
    },
    { keys: ["k"], what: "previous brief, or previous line", run: () => move(-1), when: () => rows().length > 0 },
    {
      keys: ["o"],
      what: "open the brief, or the line's raw",
      run: () => (document.activeElement as HTMLElement | null)?.click(),
      when: () => document.activeElement instanceof HTMLElement && document.activeElement.matches("[data-row]"),
    },
    { keys: ["Escape"], what: "close the line's raw", run: closeRaw, when: () => openLine !== undefined },
  ];
  useKeys("briefs", "Briefs", commands);

  const [pending, setPending] = React.useState<string | null>(null);
  const picking: Picking = {
    pending,
    onPick: (card: Card, pick: Pick) => {
      setPending(card.id);
      setRefused("");
      decide({ brief, decision: card.id, rev: card.rev, pick: pick.value })
        .then(
          (done) => setNotice(done.notice),
          (error: unknown) => {
            // A decision that moved since wrote nothing: the read below shows it as it stands now.
            setNotice(null);
            setRefused(wordsOf(error));
          },
        )
        .finally(() => {
          setPending(null);
          wanted.current = "decisions";
          void client.invalidateQueries({ queryKey: BRIEFS });
        });
    },
  };

  const covered = openLine !== undefined && (band === "narrow" || band === "small");
  const foot = notice && <NoticeBar text={notice} lines={[]} onDismiss={() => setNotice(null)} />;

  let body: React.ReactNode;
  if (read.error && !data) {
    body = (
      <div className="grid justify-items-start gap-3 px-4 py-6">
        <Refusal>{wordsOf(read.error)}</Refusal>
        <Back href={PATH} />
      </div>
    );
  } else if (!data) {
    body = <p className="px-4 py-6 text-sm text-ink-muted">Reading the briefs.</p>;
  } else if (data.waiting || !view) {
    body = (
      <p role="status" className="px-4 py-6 text-sm">
        {data.waiting}. This page asks again every few seconds.
      </p>
    );
  } else if (view.mode === "list") {
    body = <BriefList view={view} />;
  } else {
    body = (
      <div
        className="min-h-0 flex-1 overflow-y-auto"
        role="region"
        aria-label={view.mode === "brief" ? "The brief" : title}
        aria-busy={read.isFetching || undefined}
        tabIndex={-1}
      >
        {view.mode === "brief" ? (
          <>
            <BriefHead view={view} />
            <BriefLines view={view} onOpen={openRaw} />
          </>
        ) : (
          <div className="grid justify-items-start gap-3 border-b border-rule px-3 py-3 sm:px-4">
            <Back href={view.back || view.link?.url || PATH} />
            {view.mode === "entity" && <Eyebrow kind="decisions" />}
            {view.mode === "missing" ? <Refusal>{view.message}</Refusal> : <Note>{view.message}</Note>}
          </div>
        )}
        {data.decisions && <Decisions part={data.decisions} refused={refused} picking={picking} />}
      </div>
    );
  }

  const raw = view?.mode === "brief" ? view.raw : undefined;
  const heading = raw ? rawTitle(raw) : openLine ? `Line ${openLine}` : "The raw";
  const panel =
    openLine !== undefined && view?.mode === "brief" ? (
      <Panel
        label={`The raw of ${heading.toLowerCase()}`}
        onClose={closeRaw}
        back="Back to the brief"
        footer={covered ? foot : undefined}
        header={
          <h2 id="panel-title" tabIndex={-1} className="truncate text-sm font-semibold outline-none">
            {heading}
          </h2>
        }
      >
        {raw && fresh ? (
          <RawBody raw={raw} onCopy={(locator) => void navigator.clipboard?.writeText(locator)} />
        ) : (
          <p className="p-4 text-sm text-ink-muted">Reading the line.</p>
        )}
      </Panel>
    ) : undefined;

  return (
    <Page>
      <PageHeader title={title}>
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
        panel={panel}
      />
    </Page>
  );
}
