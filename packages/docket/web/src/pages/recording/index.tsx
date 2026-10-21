/**
 * /recording (DR-0545, DR-0776): a recording's summary beside its transcript. The URL holds the
 * rid, the doc shown and the time `at` marks; a link that sets `at` ends in #at, and the page scrolls to the line it
 * marks. A summary line's anchor marks the lines it points at; a line's time plays the stored audio from there, which
 * the player fetches by byte range for the browser's session only (DR-0777). A held recording shows only that it is
 * held; a rid that isn't a recording, or that the raw log doesn't hold, says so.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useRouter, useRouterState, useSearch } from "@tanstack/react-router";
import { AudioLinesIcon, KeyboardIcon } from "lucide-react";
import * as React from "react";
import { AppLink } from "@/components/app-link";
import { Locator, Note, PartHead, SummaryLines } from "@/components/doc";
import { Empty, Page, PageHeader, Refusal } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { one, type Search, stringifySearch, writeBack } from "@/lib/search";
import { cn } from "@/lib/utils";
import { wordsOf } from "@/pages/day/api";
import { leaveOnEscape } from "@/pages/day/field";
import { CopyButton } from "@/pages/day/panel";
import { type RecordingView, readRecording, type TranscriptLine } from "./api";
import { Pair, type Playing, Transcript } from "./transcript";

const PATH = "/recording";
const WAIT_MS = 5000;

/** The start: a rid or a locator to open, as a transcript hit on Search links here. */
function Open({ given }: { given?: string }) {
  const router = useRouter();
  const [rid, setRid] = React.useState(given ?? "");
  return (
    <form
      className="grid max-w-xl grid-cols-[minmax(0,1fr)_auto] gap-x-2 gap-y-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        const value = rid.trim();
        if (value) void router.navigate({ href: `${PATH}${stringifySearch({ rid: value })}` });
      }}
    >
      <Label htmlFor="rid" className="col-span-2">
        Rid or locator
      </Label>
      <Input
        id="rid"
        value={rid}
        autoComplete="off"
        spellCheck={false}
        placeholder="r:calls:rec-0100@02:30"
        className="font-mono"
        onChange={(event) => setRid(event.target.value)}
        onKeyDown={leaveOnEscape}
      />
      <Button type="submit" variant="default">
        Open
      </Button>
    </form>
  );
}

/** The browser's own player, its source fixed for the recording, so marking another line never reloads it. */
function Player({ src, audio }: { src: string; audio: React.RefObject<HTMLAudioElement | null> }) {
  const [source] = React.useState(src);
  return (
    <audio ref={audio} controls preload="none" src={source} className="h-10 w-full max-w-xl">
      The audio plays in a browser that plays it.
    </audio>
  );
}

function Head({
  view,
  audio,
  problem,
}: {
  view: RecordingView;
  audio: React.RefObject<HTMLAudioElement | null>;
  problem: string;
}) {
  return (
    <div className="grid shrink-0 gap-2 border-b border-rule px-3 py-3 sm:px-4">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-muted">
        <span className="inline-flex items-center gap-1 text-ink">
          <AudioLinesIcon aria-hidden className="size-3.5" />
          recording
        </span>
        {view.meta.map((part) => (
          <span key={part} className="tabular-nums">
            {part}
          </span>
        ))}
      </p>
      <p className="flex min-w-0 items-center gap-1">
        <Locator value={view.locator} className="text-ink" />
        <CopyButton value={view.locator} what="the locator" />
      </p>
      <section aria-label="Audio" data-why={view.audio?.why} className="grid gap-1.5">
        {view.player ? (
          <>
            <Player key={view.player.split("#")[0]} src={view.player} audio={audio} />
            <p className="text-xs text-ink-muted">{view.audio?.message}</p>
          </>
        ) : (
          view.audio && <Note>{view.audio.message}</Note>
        )}
        {view.audio?.speech && <p className="text-xs text-ink-muted">{view.audio.speech}</p>}
        {problem && <Refusal>{problem}</Refusal>}
      </section>
      {view.problems.map((p) => (
        <Note key={p} tone="warn">
          {p}
        </Note>
      ))}
    </div>
  );
}

function Summary({ view }: { view: RecordingView }) {
  const docs = view.docs;
  return (
    <section aria-labelledby="summary-title" className="grid content-start gap-2 py-3 lg:min-h-0 lg:overflow-y-auto">
      <PartHead id="summary-title" level={2} className="px-3 sm:px-4">
        Summary
      </PartHead>
      {(docs.length > 1 || (docs.length === 1 && !docs[0]?.on)) && (
        <nav aria-label="Docs on this recording" className="px-3 sm:px-4">
          <ul className="flex flex-wrap gap-1.5">
            {docs.map((doc) => (
              <li key={doc.id}>
                <AppLink
                  href={doc.url}
                  aria-current={doc.on ? "true" : undefined}
                  className={cn(
                    "inline-flex min-h-7 items-center rounded-sm border px-2 font-mono text-xs break-all pointer-coarse:min-h-11",
                    doc.on
                      ? "border-green bg-selection font-semibold text-ink"
                      : "border-control text-ink hover:bg-hover",
                  )}
                >
                  {doc.id}
                </AppLink>
              </li>
            ))}
          </ul>
        </nav>
      )}
      {view.summary ? (
        <>
          <p className="px-3 text-xs text-ink-muted sm:px-4">
            <Locator value={view.summary.id} />, {view.summary.type}, rev {view.summary.rev}
          </p>
          {view.note && <p className="px-3 text-xs text-ink-muted sm:px-4">{view.note}</p>}
          <div className="border-t border-rule px-1 sm:px-2">
            <SummaryLines lines={view.summary.lines} citedSays="points at the marked lines" />
          </div>
        </>
      ) : (
        view.no_summary && <p className="px-3 text-sm text-ink-muted sm:px-4">{view.no_summary}</p>
      )}
    </section>
  );
}

function Transcripts({ view, playing }: { view: RecordingView; playing: Playing }) {
  const others = view.others.length > 0 && (
    <p className="flex flex-wrap items-baseline gap-x-2 px-3 text-xs text-ink-muted sm:px-4">
      Also transcribed:
      {view.others.map((other) => (
        <AppLink
          key={other.rid}
          href={other.url}
          className="inline-flex min-h-6 items-center font-mono break-all text-green underline-offset-4 hover:underline pointer-coarse:min-h-11"
        >
          {other.rid}
        </AppLink>
      ))}
    </p>
  );
  const title = view.pair ? "Transcripts" : "Transcript";
  return (
    <section
      aria-labelledby="transcript-title"
      className="grid content-start gap-2 border-t border-rule py-3 lg:min-h-0 lg:overflow-y-auto lg:border-t-0 lg:border-l"
      data-scroll="transcript"
    >
      <PartHead id="transcript-title" level={2} className="px-3 sm:px-4">
        {title}
      </PartHead>
      {others}
      <div className="px-1 sm:px-2">
        {view.pair ? (
          <Pair pair={view.pair} playing={playing} />
        ) : view.transcripts.length ? (
          view.transcripts.map((t) => <Transcript key={t.rid} transcript={t} playing={playing} />)
        ) : (
          <p className="px-2 text-sm text-ink-muted">{view.no_transcript}</p>
        )}
      </div>
    </section>
  );
}

export function RecordingPage() {
  const search = useSearch({ strict: false }) as Search;
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const router = useRouter();
  const showKeys = useShowKeys();
  const read = useQuery({
    queryKey: ["recording", searchStr],
    queryFn: () => readRecording(searchStr),
    placeholderData: keepPreviousData,
    // A page opened again asks once, so a recording kept from before isn't stale.
    refetchOnMount: "always",
    refetchInterval: (q) => (q.state.data?.waiting ? WAIT_MS : false),
  });
  const data = read.data;
  const view = data?.view ?? undefined;
  const recording = view?.mode === "recording" ? view : undefined;
  useTitle(recording ? `${recording.title} - Recording` : "Recording");

  React.useEffect(() => {
    if (data && data.asked === searchStr) {
      writeBack({ location: router.history.location, replace: (href) => router.history.replace(href) }, data.canonical);
    }
  }, [data, searchStr, router]);

  // The line `at` marks, scrolled to once for each URL, when its read answers; a line played in place is in sight.
  const scrolled = React.useRef<string | null>(null);
  const playedTo = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!data || data.asked !== searchStr || read.isPlaceholderData || scrolled.current === searchStr) return;
    scrolled.current = searchStr;
    if (playedTo.current === searchStr) return;
    document.getElementById("at")?.scrollIntoView({ block: "center" });
  }, [data, searchStr, read.isPlaceholderData]);

  const audio = React.useRef<HTMLAudioElement | null>(null);
  const [playingLine, setPlayingLine] = React.useState<string | null>(null);
  const [problem, setProblem] = React.useState("");
  const playing: Playing = {
    line: playingLine,
    plays: Boolean(recording?.player),
    onPlay: (line: TranscriptLine) => {
      const player = audio.current;
      if (!player || line.start === null || line.start === undefined) return;
      player.currentTime = line.start;
      player.play().then(
        () => setProblem(""),
        () => setProblem("The audio didn't start: press play on the player."),
      );
      setPlayingLine(line.url);
      playedTo.current = new URL(line.url, window.location.origin).search;
      void router.navigate({ href: line.url, replace: true });
    },
  };

  let body: React.ReactNode;
  if (read.error) {
    body = (
      <div className="grid justify-items-start gap-4 px-4 py-6">
        <Refusal>{wordsOf(read.error)}</Refusal>
        <Open given={one(search, "rid")} />
      </div>
    );
  } else if (!data) {
    body = <p className="px-4 py-6 text-sm text-ink-muted">Reading the recording.</p>;
  } else if (data.waiting || !view) {
    body = (
      <p role="status" className="px-4 py-6 text-sm">
        {data.waiting}. This page asks again every few seconds.
      </p>
    );
  } else if (view.mode === "start") {
    body = (
      <Empty title="Open a recording">
        <p className="mb-3">A transcript hit on the Search page links here. So does a recording's rid or locator:</p>
        <Open />
      </Empty>
    );
  } else if (view.mode !== "recording") {
    const held = view.mode === "held";
    body = (
      <div className="grid max-w-2xl justify-items-start gap-3 px-4 py-6">
        <p className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
          <AudioLinesIcon aria-hidden className="size-3.5" />
          <Locator value={view.rid} className="text-ink" />
        </p>
        {held ? <Note>{view.message}</Note> : <Refusal>{view.message}</Refusal>}
        {view.link &&
          (held ? (
            // The Staging page is still the server's own.
            <a href={view.link} className={cn(buttonVariants({ size: "sm" }))}>
              {view.link_label}
            </a>
          ) : (
            <AppLink href={view.link} className={cn(buttonVariants({ size: "sm" }))}>
              {view.link_label}
            </AppLink>
          ))}
      </div>
    );
  } else {
    body = (
      <div
        className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:overflow-hidden"
        role="region"
        aria-label="The recording"
      >
        <Head view={view} audio={audio} problem={problem} />
        <div className="grid lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <Summary view={view} />
          <Transcripts view={view} playing={playing} />
        </div>
      </div>
    );
  }

  return (
    <Page>
      <PageHeader title={recording?.title || "Recording"}>
        <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
          <KeyboardIcon />
        </Button>
      </PageHeader>
      {body}
    </Page>
  );
}
