/**
 * The open record (DR-0383, DR-0785): as promotion would write it, redacted by today's rules, with its facts and why
 * it waits. A transcript lists its utterances and a text its lines, each with a tick that cuts it, and a field takes
 * typed ranges; Preview reads the record again with them, naming what goes; Work classifies it with exactly what is
 * ticked and typed; Private asks once more, then drops it whole. A record opened with nothing chosen starts from the
 * cuts its file holds. Utterances its recording's release cuts show as gone, with no tick. A record a hold covers shows
 * only its rid and why.
 */
import * as React from "react";
import { Note } from "@/components/doc";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { StagingPanel } from "./api";

/** What is ticked and typed on the open record: the owner's own until Preview or Work sends it. */
export interface Choice {
  u: number[];
  lines: number[];
  typed: string;
}

/** What the read showed as chosen: the URL's ticks, or the cuts the record's file holds. */
export function chosenOf(panel: StagingPanel): Choice {
  return {
    u: (panel.utterances ?? []).filter((u) => u.checked).map((u) => u.n),
    lines: (panel.lines ?? []).filter((l) => l.checked).map((l) => l.n),
    typed: panel.typed,
  };
}

function same(a: number[], b: number[]): boolean {
  const left = [...a].sort((x, y) => x - y);
  const right = [...b].sort((x, y) => x - y);
  return left.length === right.length && left.every((n, at) => n === right[at]);
}

/** Whether the ticks or the typed ranges moved since the read, so its preview and Work's words no longer fit them. */
export function moved(panel: StagingPanel, choice: Choice): boolean {
  const read = chosenOf(panel);
  return !same(read.u, choice.u) || !same(read.lines, choice.lines) || read.typed.trim() !== choice.typed.trim();
}

function toggle(list: number[], n: number, on: boolean): number[] {
  return on ? [...new Set([...list, n])] : list.filter((m) => m !== n);
}

function Facts({ pairs }: { pairs: { name: string; value: string }[] }) {
  return (
    <dl className="grid grid-cols-[repeat(auto-fill,minmax(136px,1fr))] gap-x-4 gap-y-1.5">
      {pairs.map(({ name, value }) => (
        <div key={name} className="grid min-w-0 gap-0.5">
          <dt className="text-xs text-ink-muted">{name}</dt>
          <dd dir="auto" className="text-sm break-words">
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Folded({ summary, children }: { summary: string; children: React.ReactNode }) {
  return (
    <details className="group/fold border-t border-rule px-4 py-2">
      <summary className="flex min-h-6 cursor-default items-center text-xs text-ink-muted hover:text-ink pointer-coarse:min-h-11">
        {summary}
      </summary>
      <div className="pt-2">{children}</div>
    </details>
  );
}

const PRE = "max-h-96 overflow-auto rounded-sm bg-paper px-3 py-2 font-mono text-xs break-words whitespace-pre-wrap";

function Utterances({
  panel,
  choice,
  fresh,
  onChange,
}: {
  panel: StagingPanel;
  choice: Choice;
  fresh: boolean;
  onChange: (choice: Choice) => void;
}) {
  const said = panel.utterances ?? [];
  return (
    <fieldset className="grid min-w-0 gap-1">
      <legend className="mb-1 text-xs font-semibold text-ink-muted">Tick the utterances to cut</legend>
      <ol className="grid">
        {said.map((u) => {
          const id = `u-${u.n}`;
          const gone = u.was_cut || u.released;
          // What goes is the read's word: once the ticks move, Preview says it again.
          const going = fresh && u.going && !gone;
          return (
            <li
              key={u.n}
              data-utterance={u.n}
              data-going={going || undefined}
              className={cn(
                "grid grid-cols-[auto_4.75rem_minmax(0,1fr)] items-start gap-x-2 rounded-sm py-0.5 pr-2",
                choice.u.includes(u.n) && "bg-me-wash",
              )}
            >
              {gone ? (
                <span aria-hidden className="size-6 pointer-coarse:size-11" />
              ) : (
                <Checkbox
                  id={id}
                  checked={choice.u.includes(u.n)}
                  onCheckedChange={(on) => onChange({ ...choice, u: toggle(choice.u, u.n, on === true) })}
                  aria-label={`Cut ${u.span}`}
                />
              )}
              <span className="pt-0.5 font-mono text-xs text-ink-muted tabular-nums">{u.span}</span>
              {gone ? (
                <span className="pt-0.5 text-sm text-ink-muted italic">
                  {u.was_cut ? "[cut]" : "cut by its release"}
                </span>
              ) : (
                <label htmlFor={id} className="min-w-0 cursor-default pt-0.5 text-sm">
                  <span dir="auto" className="mr-1.5 font-semibold">
                    {u.speaker}
                  </span>
                  <span
                    dir="auto"
                    className={cn("break-words", going && "text-ink-muted line-through decoration-red decoration-2")}
                  >
                    {u.text}
                  </span>
                  {going && <span className="sr-only"> (goes with these cuts)</span>}
                </label>
              )}
            </li>
          );
        })}
      </ol>
    </fieldset>
  );
}

function Lines({
  panel,
  choice,
  onChange,
}: {
  panel: StagingPanel;
  choice: Choice;
  onChange: (choice: Choice) => void;
}) {
  return (
    <fieldset className="grid min-w-0 gap-1">
      <legend className="mb-1 text-xs font-semibold text-ink-muted">
        Tick the lines to cut; each starts at the character beside it
      </legend>
      <ol className="grid">
        {(panel.lines ?? []).map((line) => {
          const id = `line-${line.n}`;
          return (
            <li
              key={line.n}
              data-line={line.n}
              className={cn(
                "grid grid-cols-[auto_3rem_minmax(0,1fr)] items-start gap-x-2 rounded-sm py-0.5 pr-2",
                choice.lines.includes(line.n) && "bg-me-wash",
              )}
            >
              <Checkbox
                id={id}
                checked={choice.lines.includes(line.n)}
                onCheckedChange={(on) => onChange({ ...choice, lines: toggle(choice.lines, line.n, on === true) })}
                aria-label={`Cut line ${line.n}`}
              />
              <span className="pt-0.5 text-right font-mono text-xs text-ink-muted tabular-nums">{line.offset}</span>
              <label
                htmlFor={id}
                dir="auto"
                className="min-h-5 min-w-0 cursor-default pt-0.5 text-sm break-words whitespace-pre-wrap"
              >
                {line.text}
              </label>
            </li>
          );
        })}
      </ol>
    </fieldset>
  );
}

export function RecordBody({
  panel,
  choice,
  changed,
  refused,
  onChange,
  onSubmit,
}: {
  panel: StagingPanel;
  choice: Choice;
  /** Why the last write wrote nothing because the record moved, said above it as it stands now. */
  changed: string;
  /** Why the last write was refused, in the writer's words. */
  refused: string;
  onChange: (choice: Choice) => void;
  /** Enter in the typed field: Preview. */
  onSubmit: () => void;
}) {
  const fresh = !moved(panel, choice);
  const cuts = panel.cuts.length ? `; cuts ${panel.cuts.join(", ")}` : "";
  const facts = [
    { name: "kind", value: panel.kind },
    { name: "source", value: panel.source },
    { name: "said", value: panel.when },
    { name: "staged", value: panel.staged_at },
    { name: "state", value: `${panel.state}, classified by ${panel.by}${cuts}` },
  ];
  return (
    <div className="grid gap-3 pb-4">
      <div className="grid gap-3 px-4 pt-3">
        <Facts pairs={facts} />
        <p className="grid gap-0.5">
          <span className="text-xs text-ink-muted">key</span>
          <code className="font-mono text-xs break-all">{panel.key}</code>
        </p>
        <p className={cn("text-sm", panel.on_hold && "text-red")} data-why>
          {panel.why_line}
        </p>
        {changed && <Note tone="warn">{changed}</Note>}
        {refused && <Refusal>{refused}</Refusal>}
        {!panel.on_hold &&
          panel.notes.map((note) => (
            <Note key={note} tone="warn">
              {note}
            </Note>
          ))}
        {!panel.on_hold && panel.problem && fresh && <Refusal>{panel.problem}</Refusal>}
      </div>
      {!panel.on_hold && panel.confirm && (
        <div className="mx-4 grid gap-2 rounded-sm border-l-2 border-red bg-red-wash px-3 py-2" data-confirm>
          <p className="text-sm">
            Drop this {panel.kind} for good? It leaves staging, and a later pull of this version is skipped. Nothing of
            it reaches the raw log.
          </p>
        </div>
      )}
      {!panel.on_hold && !panel.confirm && (
        <div className="grid gap-3 px-4">
          {panel.utterances ? (
            <Utterances panel={panel} choice={choice} fresh={fresh} onChange={onChange} />
          ) : panel.lines ? (
            <Lines panel={panel} choice={choice} onChange={onChange} />
          ) : (
            panel.text && (
              <pre dir="auto" className={PRE}>
                {panel.text}
              </pre>
            )
          )}
          <label className="grid gap-1 text-sm">
            <span className="text-xs font-semibold text-ink-muted">Also cut, as ranges</span>
            <Input
              value={choice.typed}
              placeholder={panel.utterances ? "12:30-14:05" : "120-340"}
              spellCheck={false}
              autoComplete="off"
              className="font-mono"
              onChange={(event) => onChange({ ...choice, typed: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  onSubmit();
                }
              }}
            />
          </label>
          {panel.preview && fresh && (
            <section aria-labelledby="preview-title" className="grid gap-1.5 border-t border-rule pt-2" data-preview>
              <h3 id="preview-title" className="text-xs font-semibold text-ink-muted">
                After the cuts
              </h3>
              <p className="text-sm">{panel.preview.summary}</p>
              {panel.preview.text !== null && panel.preview.text !== undefined && (
                <pre dir="auto" className={PRE}>
                  {panel.preview.text}
                </pre>
              )}
            </section>
          )}
          {!fresh && <p className="text-xs text-ink-muted">Preview shows what these cuts take; Work writes them.</p>}
        </div>
      )}
      {!panel.on_hold && panel.facts.length > 0 && (
        <div className="border-t border-rule px-4 pt-3">
          <Facts pairs={panel.facts} />
        </div>
      )}
      {!panel.on_hold && panel.native && (
        <Folded summary="Native fields, as the source gave them">
          <pre className={PRE}>{panel.native}</pre>
        </Folded>
      )}
      {!panel.on_hold && panel.cut_before && (
        <Folded summary={`The version in raw that was cut before, run ${panel.cut_before.run}`}>
          <pre dir="auto" className={PRE}>
            {panel.cut_before.text}
          </pre>
        </Folded>
      )}
    </div>
  );
}

/** The panel's foot: Preview, Work and Private, or the drop's question's two answers. */
export function RecordActions({
  panel,
  choice,
  pending,
  onPreview,
  onWork,
  onPrivate,
  onDrop,
  onKeep,
}: {
  panel: StagingPanel;
  choice: Choice;
  pending: boolean;
  onPreview: () => void;
  onWork: () => void;
  onPrivate: () => void;
  onDrop: () => void;
  onKeep: () => void;
}) {
  if (panel.on_hold) return null;
  const fresh = !moved(panel, choice);
  const any = choice.u.length > 0 || choice.lines.length > 0 || choice.typed.trim() !== "";
  const work = fresh ? panel.work_label : any ? "Work, with these cuts" : "Work, whole";
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-t border-rule bg-paper px-3 py-2" data-actions>
      {panel.confirm ? (
        <>
          <Button variant="destructive" disabled={pending} onClick={onDrop}>
            Drop it as private
          </Button>
          <Button disabled={pending} onClick={onKeep}>
            Keep it
          </Button>
        </>
      ) : (
        <>
          <Button disabled={pending} onClick={onPreview}>
            Preview
          </Button>
          {(panel.can_work || !fresh) && (
            <Button variant="default" disabled={pending} onClick={onWork}>
              {work}
            </Button>
          )}
          <Button variant="destructive" className="ml-auto" disabled={pending} onClick={onPrivate}>
            Private: drop it
          </Button>
        </>
      )}
    </div>
  );
}
