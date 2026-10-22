/**
 * /curate (DR-0468, DR-0786): this machine's round of cards, the new people and terms Docket mined and the spellings
 * search misses for entities it has, each decided only by the owner's click, beside the open card with its snippets
 * and its edit form; what tier 1 applied by itself, the measures, the log with Undo and the stoplist with Lift. Every
 * state is the URL, written back as the server wrote it. The keys move and open; no key decides a card (DR-0711).
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
import { HttpError } from "@/lib/query";
import { one, parseSearch, type Search, writeBack } from "@/lib/search";
import { wordsOf } from "@/pages/day/api";
import {
  type CardPanel,
  type CardRow,
  decide,
  type Done,
  lift,
  listQuery,
  PATH,
  readCard,
  readCurate,
  startRound,
  undo,
} from "./api";
import { CardActions, CardBody, type Draft, draftOf, editedOf } from "./card";
import { Applied, Cards, cardsOf, Dropped, Log, Questions, RoundHead, Stoplist } from "./round";

export const CURATE = ["curate"] as const;

function focusRow(id: string | undefined): void {
  if (id === undefined) return;
  const row = document.querySelector<HTMLElement>(`#main [data-row="${CSS.escape(id)}"]`);
  if (!row || row.closest("[hidden]")) return;
  row.focus({ preventScroll: true });
  row.scrollIntoView({ block: "nearest" });
}

function step(order: CardRow[], at: string | undefined, by: 1 | -1): CardRow | undefined {
  if (!order.length) return undefined;
  const now = order.findIndex((row) => row.id === at);
  if (now < 0) return by > 0 ? order[0] : order[order.length - 1];
  return order[Math.min(order.length - 1, Math.max(0, now + by))];
}

/** The card a query string opens, or "": a panel kept while the next read comes is the same card's only. */
function cardOf(query: string): string {
  const search = parseSearch(query);
  return one(search, "edit") ?? one(search, "open") ?? "";
}

/** The next card still open after this one, round the list, for the panel to move on to once it is decided. */
function nextOpen(order: CardRow[], id: string): CardRow | undefined {
  const at = order.findIndex((card) => card.id === id);
  const after = [...order.slice(at + 1), ...order.slice(0, Math.max(0, at))];
  return after.find((card) => card.state === "open");
}

export function CuratePage() {
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const router = useRouter();
  const client = useQueryClient();
  const band = useBand();
  const showKeys = useShowKeys();
  useTitle("Curate");
  const search: Search = parseSearch(searchStr);
  const open = one(search, "edit") ?? one(search, "open");
  const listing = listQuery(search);

  const list = useQuery({
    queryKey: [...CURATE, "list", listing],
    queryFn: () => readCurate(listing),
    placeholderData: (previous) => previous,
    refetchOnMount: "always",
  });
  const cardRead = useQuery({
    queryKey: [...CURATE, "panel", searchStr],
    queryFn: () => readCard(searchStr),
    enabled: open !== undefined,
    refetchOnMount: "always",
    // The same card read again, with its form or without, stays as it was until the read answers.
    placeholderData: (previous) => (previous && cardOf(previous.asked) === (open ?? "") ? previous : undefined),
  });
  const data = list.data;
  const view = data?.view;
  const read = cardRead.data;
  const shown = open !== undefined && read?.asked === searchStr ? read : undefined;
  const card: CardPanel | undefined = open !== undefined ? (read?.panel ?? undefined) : undefined;

  React.useEffect(() => {
    const history = { location: router.history.location, replace: (href: string) => router.history.replace(href) };
    if (shown) writeBack(history, shown.canonical);
    else if (open === undefined && data && data.asked === listing) writeBack(history, data.canonical);
  }, [shown, data, open, listing, router]);

  const [notice, setNotice] = React.useState<string | null>(null);
  const [seenNotice, setSeenNotice] = React.useState<string | null>(null);
  if (data?.notice && data.asked === listing && seenNotice !== data.asked) {
    setSeenNotice(data.asked);
    setNotice(data.notice);
  }

  // The edit form as typed, the read's until the owner changes it; each new read starts again, and a refusal reads
  // nothing again, so what was typed stays beside its words.
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [draftOfRead, setDraftOfRead] = React.useState<string | null>(null);
  const readAt = read ? `${read.asked} ${cardRead.dataUpdatedAt}` : null;
  if (read && !cardRead.isPlaceholderData && draftOfRead !== readAt) {
    setDraftOfRead(readAt);
    setDraft(read.panel?.form ? draftOf(read.panel.form) : null);
  }

  const [refused, setRefused] = React.useState<{ card: string; words: string; moved: boolean } | null>(null);
  if (refused && refused.card !== open) setRefused(null);
  const [listRefused, setListRefused] = React.useState("");
  const [pending, setPending] = React.useState(false);

  const order = React.useMemo(() => cardsOf(view), [view]);
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
    if (order.some((row) => row.id === want)) {
      focusRow(want);
      wanted.current = null;
    }
  });

  const go = (href: string, replace = false) => void router.navigate({ href, replace });

  const openCard = (row: CardRow, focus: "heading" | "row" = "heading") => {
    setSelected(row.id);
    wanted.current = focus === "heading" ? "heading" : row.id;
    go(row.url, now.current.open !== undefined);
  };

  const closePanel = () => {
    const back = now.current.selected ?? now.current.open;
    wanted.current = back ?? null;
    go(card?.close ?? `${PATH}${listQuery(search)}`);
  };

  const move = (by: 1 | -1) => {
    const next = step(now.current.order, now.current.selected, by);
    if (next === undefined) return;
    if (now.current.open !== undefined && next.id !== now.current.open) openCard(next, "row");
    else {
      setSelected(next.id);
      focusRow(next.id);
    }
  };

  const commands: KeyCommand[] = [
    { keys: ["j"], what: "next card; with one open, it opens", run: () => move(1), when: () => order.length > 0 },
    { keys: ["k"], what: "previous card", run: () => move(-1), when: () => order.length > 0 },
    {
      keys: ["Enter", "o"],
      what: "open the card beside the list",
      run: () => {
        const row = order.find((r) => r.id === selected);
        if (row) openCard(row);
      },
      when: () => order.some((r) => r.id === selected),
    },
    { keys: ["Escape"], what: "close the card", run: closePanel, when: () => open !== undefined },
  ];
  useKeys("curate", "Curate", commands);

  const settle = () => void client.invalidateQueries({ queryKey: CURATE });

  /** A write on the whole page: the round, an undo or a lift, its refusal said over the list. */
  const listWrite = (send: () => Promise<Done>) => {
    setPending(true);
    setListRefused("");
    send()
      .then(
        (done) => setNotice(done.notice),
        (error: unknown) => setListRefused(wordsOf(error)),
      )
      .finally(() => {
        setPending(false);
        settle();
      });
  };

  const onDecide = (act: "accept" | "reject" | "edit") => {
    if (!card) return;
    const id = card.id;
    const edit = act === "edit" && card.form && draft ? editedOf(card.form, draft) : undefined;
    setPending(true);
    setRefused(null);
    decide({ act, round: card.round, card: id, hash: card.hash, ...(edit ? { edit } : {}) })
      .then(
        (done) => {
          setNotice(done.notice);
          // The round goes on: the next card still open, as a call on a day page moves to the row after.
          const next = nextOpen(now.current.order, id);
          if (next) openCard(next);
          else {
            wanted.current = id;
            go(card.close);
          }
          settle();
        },
        (error: unknown) => {
          // An entity file that changed since wrote nothing: read again, the card shows as it stands now.
          const movedSince = error instanceof HttpError && error.status === 409;
          setRefused({ card: id, words: wordsOf(error), moved: movedSince });
          if (movedSince) settle();
        },
      )
      .finally(() => setPending(false));
  };

  const covered = open !== undefined && (band === "narrow" || band === "small");
  const foot = notice && <NoticeBar text={notice} lines={[]} onDismiss={() => setNotice(null)} />;

  let body: React.ReactNode;
  if (list.error && !data) {
    body = (
      <div className="grid justify-items-start gap-3 px-4 py-6">
        <Refusal>{wordsOf(list.error)}</Refusal>
        <Button size="sm" onClick={() => go(PATH)}>
          The round
        </Button>
      </div>
    );
  } else if (!view || !data) {
    body = <p className="px-4 py-6 text-sm text-ink-muted">Reading the round.</p>;
  } else {
    body = (
      <div
        className="min-h-0 flex-1 overflow-y-auto"
        role="region"
        aria-label="The round"
        aria-busy={list.isFetching || undefined}
        tabIndex={-1}
      >
        <RoundHead view={view} pending={pending} onStart={() => listWrite(startRound)} />
        {listRefused && <Refusal className="m-3 sm:mx-4">{listRefused}</Refusal>}
        <Cards view={view} rows={{ selected, open, onSelect: setSelected, onOpen: (row) => openCard(row) }} />
        <Applied view={view} pending={pending} onUndo={(line) => listWrite(() => undo(line.id))} />
        <Questions view={view} />
        <Dropped view={view} />
        <Log view={view} pending={pending} onUndo={(line) => listWrite(() => undo(line.id))} />
        <Stoplist lifts={data.lifts} pending={pending} onLift={(key) => listWrite(() => lift(key.id))} />
      </div>
    );
  }

  const missing = open !== undefined ? read?.missing : undefined;
  const heading = card ? `${card.n}. ${card.kind}: ${card.title}` : missing ? "Not in this round" : "The open card";
  const panelPart =
    open !== undefined ? (
      <Panel
        label={`The open card: ${heading}`}
        onClose={closePanel}
        footer={
          <>
            {card && (
              <CardActions
                card={card}
                pending={pending}
                onAccept={() => onDecide("accept")}
                onEdit={() => go(card.edit_url, true)}
                onReject={() => onDecide("reject")}
                onSaveEdit={() => onDecide("edit")}
                onCancel={() => go(card.back, true)}
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
        {cardRead.error ? (
          <div className="p-4">
            <Refusal>{wordsOf(cardRead.error)}</Refusal>
          </div>
        ) : card ? (
          <CardBody
            card={card}
            draft={draft}
            changed={refused?.moved ? refused.words : ""}
            refused={refused && !refused.moved ? refused.words : ""}
            onDraft={setDraft}
          />
        ) : missing ? (
          <div className="p-4">
            <Note tone="warn">{missing}</Note>
          </div>
        ) : (
          <p className="p-4 text-sm text-ink-muted">Reading the card.</p>
        )}
      </Panel>
    ) : undefined;

  return (
    <Page>
      <PageHeader title="Curate">
        {view && (
          <p className="hidden text-xs text-ink-muted sm:block" data-minutes>
            {view.minutes}
          </p>
        )}
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
