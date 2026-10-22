/**
 * The held recordings (DR-0382, DR-0600): per source, its held ids with the commands that read and release each one
 * at the owner's console, quoted, `--from` and `--vault` filled when they can be, and the words that hand them to
 * Claude's cuts skill. Nothing here releases: the page shows the command, and the owner runs it, or says yes to
 * Claude. A value a shell would act on stays its placeholder, listed beside the commands.
 */
import { ChevronRightIcon, CopyIcon } from "lucide-react";
import { Locator } from "@/components/doc";
import { Button } from "@/components/ui/button";
import type { HeldSource, StagingList } from "./api";

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** A command or the words to paste, in the mono face, with the button that copies it. */
export function Paste({ text, label, onCopy }: { text: string; label: string; onCopy: (text: string) => void }) {
  return (
    <div className="flex items-start gap-2 rounded-sm border border-rule bg-paper py-1 pr-1 pl-2">
      <code dir="ltr" className="min-w-0 flex-1 py-1 font-mono text-xs break-all text-ink" data-paste>
        {text}
      </code>
      <Button size="sm" variant="ghost" onClick={() => onCopy(text)} aria-label={`Copy ${label}`}>
        <CopyIcon />
        Copy
      </Button>
    </div>
  );
}

function Source({ held, onCopy }: { held: HeldSource; onCopy: (text: string) => void }) {
  const id = `held-${held.source}`;
  return (
    <article aria-labelledby={id} className="grid gap-2 border-b border-rule px-3 py-3 sm:px-4">
      <h3 id={id} className="flex flex-wrap items-baseline gap-x-2 text-sm">
        <span className="font-semibold">{held.source}</span>
        <span className="text-xs text-ink-muted">
          {plural(held.ids.length, "recording held", "recordings held")}
          {held.on_hold > 0 &&
            `; ${plural(held.on_hold, "staged record", "staged records")} on hold, which ${held.on_hold === 1 ? "stops" : "stop"} every ingest of ${held.source}`}
        </span>
      </h3>
      <p className="text-xs text-ink-muted">
        A release goes in on your yes: Claude places the cuts, shows them to you and runs the release, or you run the
        command yourself. Hand them to Claude with:
      </p>
      <Paste
        text={held.paste}
        label={`the words that hand ${held.source}'s held recordings to Claude`}
        onCopy={onCopy}
      />
      {held.unfilled.length > 0 && (
        <div className="grid gap-0.5 text-xs">
          <p className="text-ink-muted">Left as placeholders, since a shell would act on them:</p>
          <dl className="grid gap-0.5">
            {held.unfilled.map(({ name, value }) => (
              <div key={`${name}-${value}`} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2">
                <dt className="font-mono text-ink-muted">&lt;{name}&gt;</dt>
                <dd>
                  <Locator value={value} className="text-ink" />
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      <ul className="grid gap-1">
        {held.ids.map((one) => (
          <li key={one.id} className="grid gap-1" data-held={one.id}>
            <details className="group/cmd">
              <summary className="flex min-h-8 cursor-default flex-wrap items-center gap-x-2 rounded-sm hover:bg-hover pointer-coarse:min-h-11">
                <ChevronRightIcon
                  aria-hidden
                  className="size-3.5 shrink-0 text-ink-muted transition-transform group-open/cmd:rotate-90"
                />
                <Locator value={one.id} className="text-ink" />
                {one.again && (
                  <span className="inline-flex h-5 items-center rounded-sm border border-red/50 px-1.5 text-xs text-red">
                    held again after its release
                  </span>
                )}
                <span className="text-xs text-ink-muted">commands</span>
              </summary>
              <div className="grid gap-1.5 pt-1 pb-1 pl-5">
                {one.again && <p className="text-xs text-ink-muted">{one.again}</p>}
                {one.commands.map((command) => (
                  <div key={command.text} className="grid gap-0.5">
                    <p className="text-xs text-ink-muted">{command.label}</p>
                    <Paste text={command.text} label={`${command.label} ${one.id}`} onCopy={onCopy} />
                  </div>
                ))}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function Held({ held, onCopy }: { held: HeldSource[]; onCopy: (text: string) => void }) {
  return (
    <section aria-labelledby="held-title" className="border-t border-rule">
      <h2
        id="held-title"
        className="flex h-8 items-center gap-2 border-b border-rule bg-paper px-3 text-xs font-semibold sm:px-4"
      >
        Held
        <span className="font-normal text-ink-muted tabular-nums">
          {held.reduce((n, source) => n + source.ids.length, 0)}
        </span>
      </h2>
      {held.length ? (
        held.map((source) => <Source key={source.source} held={source} onCopy={onCopy} />)
      ) : (
        <p className="px-3 py-3 text-sm text-ink-muted sm:px-4">No recording is held.</p>
      )}
    </section>
  );
}

export function Unreadable({ view }: { view: StagingList }) {
  if (!view.unreadable.length) return null;
  return (
    <section aria-labelledby="unreadable-title" className="border-t border-rule">
      <h2
        id="unreadable-title"
        className="flex h-8 items-center border-b border-rule bg-paper px-3 text-xs font-semibold text-red sm:px-4"
      >
        Files that don't read
      </h2>
      <ul className="grid gap-1 px-3 py-2 text-sm sm:px-4">
        {view.unreadable.map(({ path, message }) => (
          <li key={path} className="grid gap-0.5">
            <Locator value={path} className="text-ink" />
            <span className="text-ink-muted">{message}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
