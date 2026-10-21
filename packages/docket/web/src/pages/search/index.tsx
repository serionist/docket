/**
 * /search (DR-0174, DR-0175, DR-0775): the search Claude runs, in the shell. The box takes words, names, a phrase, a
 * time or a locator; the bar narrows by source and month, favours people and entities and takes dates; the results
 * are grouped by recording, thread, record or doc, and the open hit sits beside them. Every state is the URL, written
 * back as the server wrote it, so Claude's links and a bookmark open the same page. The keys are Search's own: / to
 * the box, j and k, Enter or o, v, [ and ], c and Esc; the results are announced in a polite live region.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useRouter, useRouterState, useSearch } from "@tanstack/react-router";
import { KeyboardIcon, SearchIcon } from "lucide-react";
import * as React from "react";
import { AppLink } from "@/components/app-link";
import { Empty, NoticeBar, Page, PageHeader, Panel, Refusal, Workspace } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useBand } from "@/hooks/use-width";
import { type KeyCommand, useKeys } from "@/lib/keys";
import { one, parseSearch, type Search, writeBack } from "@/lib/search";
import { cn } from "@/lib/utils";
import { leaveOnEscape } from "@/pages/day/field";
import { wordsOf } from "@/pages/day/api";
import { readHit, readResults } from "./api";
import { FacetBar } from "./facets";
import { HitBody } from "./panel";
import { ResultGroups, type ResultRow, rowsOf } from "./results";
import { hitOf, PATH, queryHref, resultsQuery } from "./url";

export const QUERY = "query";
const WAIT_MS = 5000;
const COPIED_MS = 2500;
export const SEARCH = ["search"] as const;

/** Move to the row `by` from `at`, staying on the list's ends; the first or last when none is selected. */
function step(order: ResultRow[], at: string | undefined, by: 1 | -1): ResultRow | undefined {
  if (!order.length) return undefined;
  const now = order.findIndex((row) => row.key === at);
  if (now < 0) return by > 0 ? order[0] : order[order.length - 1];
  return order[Math.min(order.length - 1, Math.max(0, now + by))];
}

function focusRow(key: string | undefined): void {
  if (key === undefined) return;
  const row = document.querySelector<HTMLElement>(`[data-row="${CSS.escape(key)}"]`);
  if (!row || row.closest("[hidden]")) return;
  row.focus({ preventScroll: true });
  row.scrollIntoView({ block: "nearest" });
}

function Start({ indexLine }: { indexLine: string }) {
  return (
    <Empty title="Find anything you kept">
      <ul className="grid list-disc gap-1 pl-5">
        <li>words and names, in any of your languages: a name finds its aliases too</li>
        <li>"a quoted phrase", word for word</li>
        <li>
          a time: last week, yesterday, <span className="whitespace-nowrap">2026-10</span> or{" "}
          <span className="whitespace-nowrap">2026-10-07</span>
        </li>
        <li>
          a locator from Claude's answer, such as{" "}
          <code className="font-mono text-xs whitespace-nowrap">r:calls:rec-0100@02:30</code>, to open it
        </li>
      </ul>
      {indexLine && <p className="mt-3 text-xs">{indexLine}</p>}
    </Empty>
  );
}

export function SearchPage() {
  const search = useSearch({ strict: false }) as Search;
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const router = useRouter();
  const band = useBand();
  const showKeys = useShowKeys();
  const query = one(search, QUERY) ?? "";
  useTitle(query ? `${query} - Search` : "Search");

  const listQuery = resultsQuery(search);
  const list = useQuery({
    queryKey: [...SEARCH, "results", listQuery],
    queryFn: () => readResults(listQuery),
    placeholderData: keepPreviousData,
    // A page opened again asks once, as a day page's poll does on mount, so a search kept from before isn't stale.
    refetchOnMount: "always",
    // The index still being built: ask again until it answers.
    refetchInterval: (q) => (q.state.data?.waiting ? WAIT_MS : false),
  });
  const data = list.data;
  const fresh = data !== undefined && data.asked === listQuery;
  const open = hitOf(search);
  const panelOpen = open !== undefined || (fresh && Boolean(data.locator));
  const panel = useQuery({
    queryKey: [...SEARCH, "panel", searchStr],
    queryFn: () => readHit(searchStr),
    enabled: panelOpen,
    refetchOnMount: "always",
    // The index still being built: ask again until it answers, as the results do.
    refetchInterval: (q) => (q.state.data?.waiting ? WAIT_MS : false),
    // The same hit read again, wider or verified, stays as it was until the read answers; another hit's never shows.
    placeholderData: (previous) => (previous && hitOf(parseSearch(previous.asked)) === open ? previous : undefined),
  });

  // The server's canonical URL, written back as it came, once a read answers for the URL the bar holds now.
  React.useEffect(() => {
    for (const read of [data, panel.data]) {
      if (read && read.asked === searchStr) {
        writeBack(
          { location: router.history.location, replace: (href) => router.history.replace(href) },
          read.canonical,
        );
      }
    }
  }, [data, panel.data, searchStr, router]);

  const results = data?.results;
  const order = React.useMemo(() => rowsOf(results), [results]);
  // One selection: a URL that opens a hit selects its row, the first that opens it unless the selected one does too.
  const [selected, setSelected] = React.useState<string | undefined>(open);
  const [seenOpen, setSeenOpen] = React.useState(open);
  if (open !== seenOpen) {
    setSeenOpen(open);
    if (open !== undefined && order.find((row) => row.key === selected)?.opens !== open) setSelected(open);
  }
  const now = React.useRef({ selected, open, order });
  React.useLayoutEffect(() => {
    now.current = { selected, open, order };
  });

  // What the panel's heading or a row takes once the panel's read answers.
  const wanted = React.useRef<"heading" | string | null>(null);
  React.useEffect(() => {
    if (wanted.current === null) return;
    if (wanted.current === "heading") {
      if (panel.data && panel.data.asked === searchStr) {
        document.getElementById("panel-title")?.focus({ preventScroll: true });
        document
          .querySelector("[data-panel-body] [data-mark], [data-panel-body] [data-cited]")
          ?.scrollIntoView({ block: "center" });
        wanted.current = null;
      }
      return;
    }
    focusRow(wanted.current);
    wanted.current = null;
  });

  const go = (href: string, replace = false) => void router.navigate({ href, replace });

  const openRow = (row: ResultRow, focus: "heading" | "row" = "heading") => {
    setSelected(row.key);
    wanted.current = focus === "heading" ? "heading" : row.key;
    go(row.url, now.current.open !== undefined);
  };

  const closePanel = () => {
    const closed = panel.data?.panel?.close ?? `${PATH}${listQuery}`;
    const back = now.current.selected ?? now.current.open;
    wanted.current = back ?? null;
    go(closed);
  };

  const move = (by: 1 | -1) => {
    const next = step(now.current.order, now.current.selected, by);
    if (next === undefined) return;
    if (now.current.open !== undefined && next.opens !== now.current.open) openRow(next, "row");
    else {
      setSelected(next.key);
      focusRow(next.key);
    }
  };

  const [copied, setCopied] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (copied === null) return;
    const timer = setTimeout(() => setCopied(null), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = (locator: string | undefined) => {
    if (!locator) return;
    void navigator.clipboard?.writeText(locator).then(() => setCopied(locator));
  };

  const shown = panelOpen ? panel.data?.panel : undefined;
  const commands: KeyCommand[] = [
    {
      keys: ["/"],
      what: "to the query box",
      run: () => document.getElementById(QUERY)?.focus(),
    },
    { keys: ["j"], what: "next hit; with the panel open, it opens", run: () => move(1), when: () => order.length > 0 },
    { keys: ["k"], what: "previous hit", run: () => move(-1), when: () => order.length > 0 },
    {
      keys: ["Enter", "o"],
      what: "open the hit beside the results",
      run: () => {
        const row = order.find((r) => r.key === selected);
        if (row) openRow(row);
      },
      when: () => order.some((r) => r.key === selected),
    },
    {
      keys: ["v"],
      what: "verify the summary, and back",
      run: () => shown?.verify && go(shown.verify),
      when: () => Boolean(shown?.verify),
    },
    {
      keys: ["["],
      what: "less raw around the hit",
      run: () => shown?.less && go(shown.less),
      when: () => Boolean(shown?.less),
    },
    {
      keys: ["]"],
      what: "more raw around the hit",
      run: () => shown?.more && go(shown.more),
      when: () => Boolean(shown?.more),
    },
    {
      keys: ["c"],
      what: "copy the hit's locator",
      run: () => copy(order.find((r) => r.key === selected)?.locator ?? shown?.locator),
      when: () => order.some((r) => r.key === selected) || Boolean(shown?.locator),
    },
    { keys: ["Escape"], what: "close the panel", run: closePanel, when: () => panelOpen },
  ];
  useKeys("search", "Search", commands);

  // The box's text: the URL's query until the owner types, and again whenever the URL's query changes.
  const [words, setWords] = React.useState(query);
  const [seenQuery, setSeenQuery] = React.useState(query);
  if (query !== seenQuery) {
    setSeenQuery(query);
    setWords(query);
  }

  // The live region says what came back once a search answers.
  const said = fresh ? (data.results?.announce ?? (data.waiting ? "the index is being built" : "")) : "";
  const refused = list.error && !list.isFetching ? wordsOf(list.error) : "";
  const since = one(search, "since") ?? "";
  const until = one(search, "until") ?? "";
  const covered = panelOpen && (band === "narrow" || band === "small");
  const foot = copied && <NoticeBar text="Copied" lines={[copied]} onDismiss={() => setCopied(null)} />;

  const listPart = (
    <>
      {(results || list.error) && (
        <FacetBar
          facets={results?.facets ?? []}
          chips={results?.chips ?? []}
          candidates={results?.candidates ?? 0}
          since={since}
          until={until}
          reading={results?.reading ?? []}
          onDates={(dates) => go(queryHref(search, query, dates))}
        />
      )}
      <div
        className="@container min-h-0 flex-1 overflow-y-auto"
        role="region"
        aria-label="Results"
        aria-busy={list.isFetching || undefined}
        tabIndex={-1}
      >
        {list.error ? (
          <div className="grid justify-items-start gap-3 px-4 py-6">
            <Refusal>{wordsOf(list.error)}</Refusal>
            <p className="text-sm text-ink-muted">Fix it in the box or under Dates; the rest of the search stays.</p>
          </div>
        ) : !data ? (
          <p className="px-4 py-6 text-sm text-ink-muted">Searching.</p>
        ) : data.waiting ? (
          <div className="px-4 py-6">
            <p role="status" className="text-sm">
              {data.waiting}. This page asks again every few seconds.
            </p>
          </div>
        ) : results && results.groups.length ? (
          <div className={list.isPlaceholderData ? "opacity-60" : undefined}>
            <ResultGroups
              results={results}
              selected={selected}
              open={open}
              onSelect={setSelected}
              onOpen={(row) => openRow(row)}
            />
            {results.more && (
              <div className="px-3 py-3 sm:px-4">
                <Button size="sm" onClick={() => results.more && go(results.more)}>
                  Show 20 more
                </Button>
              </div>
            )}
          </div>
        ) : results ? (
          <Empty title="Nothing found">
            {results.drops.length ? (
              <>
                <p>Try without one of these:</p>
                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {results.drops.map((drop) => (
                    <li key={drop.url}>
                      <AppLink href={drop.url} className={cn(buttonVariants({ size: "sm" }))}>
                        {drop.label}
                      </AppLink>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p>Fewer words, another spelling, or a name's alias may find it.</p>
            )}
          </Empty>
        ) : data.locator ? (
          <Empty title="Opened beside the results">
            <p>
              <code className="font-mono text-xs break-all">{data.locator}</code> is a locator, so its words aren't
              searched; add a word to search instead.
            </p>
          </Empty>
        ) : (
          <Start indexLine={data.index_line} />
        )}
      </div>
      {!covered && foot}
    </>
  );

  const heading = shown?.title || shown?.locator || (panel.isFetching ? "Reading the hit" : "The open hit");
  const panelPart = panelOpen ? (
    <Panel
      label={`The open hit: ${heading}`}
      onClose={closePanel}
      footer={covered ? foot : undefined}
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
      <div data-panel-body>
        {panel.error ? (
          <div className="p-4">
            <Refusal>{wordsOf(panel.error)}</Refusal>
          </div>
        ) : shown ? (
          <HitBody panel={shown} onCopy={copy} />
        ) : panel.data?.waiting ? (
          <p role="status" className="p-4 text-sm">
            {panel.data.waiting}. This panel asks again every few seconds.
          </p>
        ) : (
          <p className="p-4 text-sm text-ink-muted">Reading the hit.</p>
        )}
      </div>
    </Panel>
  ) : undefined;

  return (
    <Page>
      <PageHeader title="Search">
        <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
          <KeyboardIcon />
        </Button>
      </PageHeader>
      {!(band === "narrow" && panelOpen) && (
        <form
          role="search"
          className="flex shrink-0 items-center gap-2 border-b border-rule px-3 py-2.5 sm:px-4"
          onSubmit={(event) => {
            event.preventDefault();
            go(queryHref(search, words));
          }}
        >
          <label htmlFor={QUERY} className="sr-only">
            Search what you kept
          </label>
          <div className="relative min-w-0 flex-1">
            <SearchIcon
              aria-hidden
              className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-muted"
            />
            <Input
              id={QUERY}
              type="search"
              autoComplete="off"
              spellCheck={false}
              value={words}
              placeholder='Words, names, "a phrase", last week, 2026-10-07, or a locator'
              className="h-10 pl-8 text-base"
              onChange={(event) => setWords(event.target.value)}
              onKeyDown={leaveOnEscape}
            />
          </div>
          <Button type="submit" variant="default" className="h-10">
            Search
          </Button>
        </form>
      )}
      <Workspace list={listPart} panel={panelPart} />
      <p role="status" aria-live="polite" className="sr-only" data-announce>
        {refused || said}
      </p>
    </Page>
  );
}
