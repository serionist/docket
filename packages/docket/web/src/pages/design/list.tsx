/**
 * The shell's list and panel on the invented week: rows as a day page rules them, one selection the panel follows,
 * ticks, the verdict keys and the notice's undo. Nothing is written anywhere; the calls live in this page only.
 */
import * as React from "react";
import { KindTab } from "@/components/kind";
import { Empty, ListGroup, NoticeBar, Panel, Workspace } from "@/components/shell/page";
import { MEANING, STAMP_PRESSED, Stamp, VERDICTS, type Verdict } from "@/components/stamp";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useBand } from "@/hooks/use-width";
import { useKeys } from "@/lib/keys";
import { cn } from "@/lib/utils";
import { GROUPS, ITEMS, type Item } from "./data";

interface Change {
  refs: string[];
  before: Record<string, Verdict | undefined>;
  after: Verdict | undefined;
  undone: boolean;
}

function line(change: Change): string {
  const what = change.after ? `call: ${change.after}` : "call cleared";
  const refs = change.refs.length > 2 ? `${change.refs.length} items` : change.refs.join(", ");
  return change.undone ? `Undone: ${refs} ${what}` : `${refs} ${what}`;
}

function Row({
  item,
  verdict,
  selected,
  ticked,
  onSelect,
  onTick,
  narrow,
}: {
  item: Item;
  verdict: Verdict | undefined;
  selected: boolean;
  ticked: boolean;
  onSelect: () => void;
  onTick: (on: boolean) => void;
  narrow: boolean;
}) {
  return (
    <li
      data-ref={item.ref}
      aria-current={selected ? "true" : undefined}
      className={cn(
        "group/row relative flex min-h-8 items-center gap-2 border-b border-rule pr-3 pl-1.5 text-sm pointer-coarse:min-h-[45px] sm:pr-4 sm:pl-2.5",
        selected ? "bg-selection" : ticked ? "bg-hover" : "hover:bg-hover",
      )}
    >
      {selected && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-green" />}
      <Checkbox checked={ticked} onCheckedChange={(on) => onTick(on === true)} aria-label={`Tick ${item.ref}`} />
      <button
        type="button"
        onClick={onSelect}
        className="flex min-w-0 flex-1 cursor-default items-center gap-3 self-stretch text-left outline-offset-[-2px]"
      >
        {!narrow && <span className="w-14 shrink-0 font-mono text-xs text-ink-muted">{item.ref}</span>}
        {!narrow && (
          <span className="flex w-[4.5rem] shrink-0">
            <KindTab kind={item.kind} />
          </span>
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate">{item.title}</span>
          {narrow && (
            <span className="flex min-w-0 items-center gap-1.5 text-xs text-ink-muted">
              <span className="font-mono">{item.ref}</span>
              <KindTab kind={item.kind} className="h-4" />
              <span className="truncate">{item.who}</span>
            </span>
          )}
        </span>
        {verdict && <Stamp verdict={verdict} />}
        {!narrow && <span className="hidden w-28 shrink-0 truncate text-xs text-ink-muted xl:block">{item.who}</span>}
        <span
          className={cn(
            "w-14 shrink-0 text-right text-xs tabular-nums",
            item.late ? "font-semibold text-red" : "text-ink-muted",
          )}
        >
          {item.due}
        </span>
      </button>
    </li>
  );
}

function Detail({
  item,
  verdict,
  onVerdict,
}: {
  item: Item;
  verdict: Verdict | undefined;
  onVerdict: (verdict: Verdict | undefined) => void;
}) {
  const fields: [string, React.ReactNode][] = [
    ["Who", item.who || <span className="text-ink-muted">nobody named</span>],
    [
      "Due",
      item.due ? (
        <span className={item.late ? "text-red" : undefined}>
          {item.due}
          {item.late ? `, ${item.late}` : ""}
        </span>
      ) : (
        <span className="text-ink-muted">no date</span>
      ),
    ],
    ["Stream", item.stream],
    ["Source", <code className="font-mono text-xs break-all">{item.source}</code>],
  ];
  return (
    <div className="grid gap-5 px-4 py-4">
      <h2 className="text-lg font-semibold text-pretty">{item.title}</h2>
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Call" className="flex w-full sm:w-auto">
          {VERDICTS.map((v, at) => (
            <button
              key={v}
              type="button"
              aria-pressed={verdict === v}
              title={`${MEANING[v]} (${at + 1})`}
              onClick={() => onVerdict(verdict === v ? undefined : v)}
              className={cn(
                "tap -ml-px flex h-7 flex-1 cursor-default items-center justify-center gap-1.5 border border-control bg-sheet px-2.5 text-sm first:ml-0 first:rounded-l-sm last:rounded-r-sm hover:bg-hover focus-visible:z-10 sm:flex-none",
                verdict === v && STAMP_PRESSED[v],
              )}
            >
              {v}
              <span className="text-xs text-ink-muted tabular-nums">{at + 1}</span>
            </button>
          ))}
        </div>
        <div className="flex gap-2 sm:ml-auto">
          <Button size="sm">
            Done <span className="text-xs text-ink-muted">e</span>
          </Button>
          <Button size="sm" variant="destructive">
            Drop
          </Button>
        </div>
      </div>
      <dl className="grid grid-cols-[6rem_1fr] text-sm">
        {fields.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="border-t border-rule py-2 text-ink-muted">{name}</dt>
            <dd className="min-w-0 border-t border-rule py-2">{value}</dd>
          </div>
        ))}
      </dl>
      {item.notes && <p className="max-w-prose text-base">{item.notes}</p>}
      <section aria-labelledby="history" className="grid gap-1">
        <h3 id="history" className="text-xs font-semibold text-ink-muted">
          History
        </h3>
        <ol className="grid gap-1 text-xs">
          {item.history.map((entry) => {
            const [at, what] = entry.split("  ");
            return (
              <li key={entry} className="grid grid-cols-[7.5rem_1fr] gap-2">
                <span className="text-ink-muted tabular-nums">{at}</span>
                <span>{what}</span>
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}

export function DesignList({ open, onOpen }: { open: string | undefined; onOpen: (ref: string | undefined) => void }) {
  const band = useBand();
  const narrow = band === "narrow";
  const [selected, setSelected] = React.useState<string>(open ?? ITEMS[0]?.ref ?? "");
  const [ticked, setTicked] = React.useState<Set<string>>(new Set());
  const [verdicts, setVerdicts] = React.useState<Record<string, Verdict | undefined>>(() =>
    Object.fromEntries(ITEMS.map((i) => [i.ref, i.verdict])),
  );
  const [change, setChange] = React.useState<Change | null>(null);
  const listRef = React.useRef<HTMLDivElement>(null);

  // The selection follows the item the URL opens, as the router sets it: back and forward included.
  const [seenOpen, setSeenOpen] = React.useState(open);
  if (open !== seenOpen) {
    setSeenOpen(open);
    if (open) setSelected(open);
  }

  const select = (ref: string, follow: boolean) => {
    setSelected(ref);
    if (follow) onOpen(ref);
    listRef.current?.querySelector(`[data-ref="${ref}"]`)?.scrollIntoView({ block: "nearest" });
  };

  const move = (step: number) => {
    const at = ITEMS.findIndex((i) => i.ref === selected);
    const next = ITEMS[Math.min(ITEMS.length - 1, Math.max(0, at + step))];
    if (next) select(next.ref, open !== undefined);
  };

  const call = (verdict: Verdict | undefined) => {
    const refs = ticked.size ? [...ticked] : [selected];
    // The old app's rule: a call clears when every row it acts on holds it, and sets it on all otherwise.
    const after = verdict && refs.every((r) => verdicts[r] === verdict) ? undefined : verdict;
    const before = Object.fromEntries(refs.map((r) => [r, verdicts[r]]));
    setVerdicts((was) => ({ ...was, ...Object.fromEntries(refs.map((r) => [r, after])) }));
    setChange({ refs, before, after, undone: false });
  };

  const undo = () => {
    if (!change) return;
    if (change.undone) {
      setVerdicts((was) => ({ ...was, ...Object.fromEntries(change.refs.map((r) => [r, change.after])) }));
    } else {
      setVerdicts((was) => ({ ...was, ...change.before }));
    }
    setChange({ ...change, undone: !change.undone });
  };

  const tick = (ref: string, on: boolean) =>
    setTicked((was) => {
      const next = new Set(was);
      if (on) next.add(ref);
      else next.delete(ref);
      return next;
    });

  useKeys("design-list", "This list", [
    { keys: ["j"], what: "next row; with the panel open, its item", run: () => move(1) },
    { keys: ["k"], what: "previous row", run: () => move(-1) },
    { keys: ["Enter", "o"], what: "open the selected row beside the list", run: () => onOpen(selected) },
    { keys: ["x", " "], what: "tick the selected row", run: () => tick(selected, !ticked.has(selected)) },
    {
      keys: ["1", "2", "3", "4"],
      what: "discuss, claude, me, chase; again clears it",
      run: (event) => call(VERDICTS[Number(event.key) - 1]),
    },
    { keys: ["0"], what: "no call", run: () => call(undefined) },
    { keys: ["u"], what: "undo the last change, or redo it", run: undo, when: () => change !== null },
    { keys: ["z"], ctrl: true, what: "undo, as u", run: undo, when: () => change !== null },
    {
      keys: ["Escape"],
      what: "clear the ticks, or close the panel",
      run: () => (ticked.size ? setTicked(new Set()) : onOpen(undefined)),
      when: () => ticked.size > 0 || open !== undefined,
    },
  ]);

  const openItem = ITEMS.find((i) => i.ref === open);
  const list = (
    <>
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto" role="region" aria-label="Items" tabIndex={-1}>
        {GROUPS.length === 0 ? (
          <Empty title="Nothing open">New asks land here after a sync.</Empty>
        ) : (
          GROUPS.map((group) => (
            <ListGroup key={group.key} id={`group-${group.key}`} title={group.title} count={group.items.length}>
              {group.items.map((item) => (
                <Row
                  key={item.ref}
                  item={item}
                  verdict={verdicts[item.ref]}
                  selected={item.ref === selected}
                  ticked={ticked.has(item.ref)}
                  narrow={narrow}
                  onSelect={() => {
                    setSelected(item.ref);
                    onOpen(item.ref);
                  }}
                  onTick={(on) => tick(item.ref, on)}
                />
              ))}
            </ListGroup>
          ))
        )}
      </div>
      {change && (
        <NoticeBar
          lines={[line(change)]}
          action={change.undone ? "Redo" : "Undo"}
          onAction={undo}
          onDismiss={() => setChange(null)}
        />
      )}
    </>
  );

  return (
    <Workspace
      list={list}
      panel={
        openItem && (
          <Panel
            label={`${openItem.ref}, ${openItem.title}`}
            onClose={() => onOpen(undefined)}
            header={
              <>
                <span className="font-mono text-xs text-ink-muted">{openItem.ref}</span>
                <KindTab kind={openItem.kind} />
                {verdicts[openItem.ref] && <Stamp verdict={verdicts[openItem.ref] as Verdict} />}
              </>
            }
          >
            <Detail
              item={openItem}
              verdict={verdicts[openItem.ref]}
              onVerdict={(v) => {
                setSelected(openItem.ref);
                const before = { [openItem.ref]: verdicts[openItem.ref] };
                setVerdicts((was) => ({ ...was, [openItem.ref]: v }));
                setChange({ refs: [openItem.ref], before, after: v, undone: false });
              }}
            />
          </Panel>
        )
      }
    />
  );
}
