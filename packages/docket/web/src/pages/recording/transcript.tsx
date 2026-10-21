/**
 * A recording's transcript (DR-0545, DR-0776, DR-0777): a line per turn, its time, who spoke and what was said; the
 * lines the URL's `at` marks in the selection's green, the first of them the place the page scrolls to (#at); a cut
 * stretch shown as cut, silent and never played. With the audio stored, a line's time plays from there and stays
 * marked as playing; without it, the time is a link to the line. Whisper's transcript, when raw holds one, stands
 * beside the recorder's in a table, a row per stretch of time, and under it at phone width.
 */
import { PlayIcon, ScissorsIcon } from "lucide-react";
import { AppLink, plainClick } from "@/components/app-link";
import { Locator, Note } from "@/components/doc";
import { cn } from "@/lib/utils";
import type { TranscriptLine, TranscriptPair, TranscriptView } from "./api";

export interface Playing {
  /** The URL of the line playing, which names it: its time on its transcript. */
  line: string | null;
  /** Whether a line's time plays the audio, or links to the line. */
  plays: boolean;
  /** A plain click on a playable line's time: play from there. */
  onPlay: (line: TranscriptLine) => void;
}

/**
 * A line: its time, who spoke and the words. `named` false for Whisper's, which names no speaker; `column` gives the
 * speaker a column of their own, where the transcript has the width for it.
 */
function Line({
  line,
  playing,
  named,
  column,
}: {
  line: TranscriptLine;
  playing: Playing;
  named: boolean;
  column: boolean;
}) {
  const plays = playing.plays && line.start !== null && line.start !== undefined;
  const now = playing.line === line.url;
  return (
    <li
      id={line.target ? "at" : undefined}
      data-mark={line.mark ? "1" : undefined}
      data-cut={line.cut ? "1" : undefined}
      data-playing={now ? "1" : undefined}
      className={cn(
        "relative grid scroll-mt-16 grid-cols-[3.5rem_minmax(0,1fr)] gap-x-2 py-1 pr-2 pl-2",
        named && column && "sm:grid-cols-[3.5rem_7.5rem_minmax(0,1fr)]",
        line.mark && "bg-selection",
        line.cut && "border-l-2 border-dashed border-control",
      )}
    >
      {line.mark && !line.cut && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      <AppLink
        href={line.url}
        replace={plays}
        title={plays ? "Play from this line" : "Link to this line"}
        onClick={(event) => {
          if (!plays || !plainClick(event)) return;
          event.preventDefault();
          playing.onPlay(line);
        }}
        className={cn(
          "inline-flex h-6 items-center gap-1 self-start font-mono text-xs tabular-nums underline-offset-4 hover:underline pointer-coarse:h-11 pointer-coarse:items-start pointer-coarse:pt-0.5",
          now ? "font-semibold text-green" : "text-ink-muted",
        )}
      >
        {now && <PlayIcon aria-hidden className="size-3 fill-current" />}
        {line.at}
        {now && <span className="sr-only"> (playing)</span>}
      </AppLink>
      {named && column && (
        <span dir="auto" className="hidden truncate pt-0.5 text-sm font-semibold sm:block" title={line.speaker}>
          {line.speaker}
        </span>
      )}
      <span className="min-w-0 pt-0.5 text-sm">
        {named && line.speaker && (
          <span dir="auto" className={cn("mr-1.5 font-semibold", column && "sm:hidden")}>
            {line.speaker}
          </span>
        )}
        {line.cut ? (
          <span className="inline-flex items-center gap-1.5 text-ink-muted italic">
            <ScissorsIcon aria-hidden className="size-3.5 not-italic" />
            {line.text}
            <span className="sr-only">: a stretch left out, never played</span>
          </span>
        ) : (
          <span dir="auto" className="break-words whitespace-pre-wrap">
            {line.text}
          </span>
        )}
      </span>
    </li>
  );
}

export function Lines({
  lines,
  playing,
  named = true,
  column = false,
  label,
}: {
  lines: TranscriptLine[];
  playing: Playing;
  named?: boolean;
  column?: boolean;
  label?: string;
}) {
  return (
    <ol aria-label={label} className="grid">
      {lines.map((line, at) => (
        // Two lines may start in the same second, and so share a URL; their order never changes.
        <Line key={`${at}-${line.url}`} line={line} playing={playing} named={named} column={column} />
      ))}
    </ol>
  );
}

export function Transcript({ transcript, playing }: { transcript: TranscriptView; playing: Playing }) {
  return (
    <div className="grid gap-2">
      <p className="px-2 text-xs text-ink-muted">
        <Locator value={transcript.meta} />
      </p>
      {transcript.problem && (
        <Note tone="warn" className="mx-2">
          {transcript.problem}
        </Note>
      )}
      <Lines lines={transcript.lines} playing={playing} column label="The transcript's lines" />
    </div>
  );
}

/** The recorder's transcript and Whisper's, a row per stretch of time, so what each heard sits side by side. */
export function Pair({ pair, playing }: { pair: TranscriptPair; playing: Playing }) {
  return (
    <div className="grid gap-2">
      {pair.left.problem && (
        <Note tone="warn" className="mx-2">
          {pair.left.problem}
        </Note>
      )}
      {/* At phone width a row stacks: the recorder's lines, then Whisper's under them, set in. */}
      <table className="w-full table-fixed border-collapse max-sm:block">
        <caption className="sr-only">The recorder's transcript and Whisper's, aligned by time</caption>
        <thead className="max-sm:block">
          <tr className="text-left align-bottom max-sm:grid max-sm:gap-1 max-sm:pb-1">
            <th scope="col" className="px-2 pb-1 text-xs font-semibold text-ink-muted max-sm:block max-sm:pb-0">
              The recorder's
              <span className="block font-normal break-words">{pair.left.meta}</span>
            </th>
            <th
              scope="col"
              className="border-l border-rule px-2 pb-1 text-xs font-semibold text-ink-muted max-sm:block max-sm:border-l-0 max-sm:pb-0"
            >
              Whisper's
              <span className="block font-normal break-words">{pair.right.meta}</span>
            </th>
          </tr>
        </thead>
        <tbody className="max-sm:block">
          {pair.rows.map((row, at) => (
            // The rows come in time order and never move.
            <tr key={at} className="border-t border-rule align-top max-sm:block">
              <td className="p-0 max-sm:block">
                {row.left.length > 0 && <Lines lines={row.left} playing={playing} />}
              </td>
              <td className="border-l border-rule p-0 max-sm:ml-6 max-sm:block">
                {row.right.length > 0 && (
                  <>
                    <span className="block px-2 pt-1 text-xs text-ink-muted sm:hidden">Whisper's</span>
                    <Lines lines={row.right} playing={playing} named={false} />
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
