/**
 * What a day page over items adds on the frame (frame.tsx), for the Ledger, the Queue and Today alike (DR-0710,
 * DR-0711): the ticks and the act bar, the calls by key and by button on the ticked rows, the open item or the
 * selected row, each refusal shown where its button is, the item panel's body and the new item. The item keys act on
 * item rows and an item's panel only, never on a meeting's row or panel.
 */
import type { QueryClient } from "@tanstack/react-query";
import * as React from "react";
import { writes } from "@/lib/day";
import type { KeyCommand } from "@/lib/keys";
import { one } from "@/lib/search";
import { ACT_SNOOZE, ACT_WHY, ActBar } from "./act-bar";
import type { Body, ItemPanel, Row, Snooze, Button as Verdict } from "./api";
import type { useDayFrame } from "./frame";
import { blankDraft, type NewDraft, NewItem } from "./new-item";
import { ItemPanelBody } from "./panel";
import { WHO_KEY } from "./who-field";

type Frame = ReturnType<typeof useDayFrame>;
type Scope = { kind: "act"; rows: Row[] } | { kind: "panel"; item: ItemPanel } | { kind: "row"; row: Row };

export interface ItemCallsOptions {
  frame: Frame;
  /** The item rows the page lists, in its order: the ticks, the keys and the act bar take these. */
  rows: Row[];
  /** The open item's panel, while the panel is an item's. */
  item: ItemPanel | undefined;
  /** The act bar's verdicts, as no row holds them. */
  verdicts: Verdict[];
  /** How many rows are ticked, which the frame's poll reads to hold. */
  tickedCountRef: React.RefObject<number>;
  /** The stream a new item starts with while nothing is typed in it, such as the list's filter. */
  stream?: string;
}

/** A who saved may be a name no item held before, which the next who list then offers (DR-0714); a write's refetch
 * covers the names already, and a save refetches only what it missed. */
export function whoSaved(client: QueryClient, body: Body, missed: boolean): void {
  if (!missed && body.fields && "who" in body.fields) void client.invalidateQueries({ queryKey: WHO_KEY });
}

export function useItemCalls({ frame, rows, item, verdicts, tickedCountRef, stream }: ItemCallsOptions) {
  const { search, open, selected, pending, write, go, openRow } = frame;
  const drop = one(search, "drop") === "1";
  const byRef = React.useMemo(() => new Map(rows.map((r) => [r.ref, r])), [rows]);

  const [ticked, setTicked] = React.useState<ReadonlySet<string>>(new Set());
  const tickedRows = rows.filter((r) => ticked.has(r.ref) && r.callable);
  React.useLayoutEffect(() => {
    tickedCountRef.current = tickedRows.length;
  });
  const [actProblem, setActProblem] = React.useState("");
  const [callProblem, setCallProblem] = React.useState<{ ref: string; words: string } | null>(null);
  const [newOpen, setNewOpen] = React.useState(false);
  const [newDraft, setNewDraft] = React.useState<NewDraft>(() => blankDraft());

  const scope = (): Scope | null => {
    if (tickedRows.length) return { kind: "act", rows: tickedRows };
    if (open !== undefined) return item && item.callable ? { kind: "panel", item } : null;
    const row = selected !== undefined ? byRef.get(selected) : undefined;
    return row && row.callable ? { kind: "row", row } : null;
  };

  /** A call on the ticked rows, the open item or the selected row, each refusal shown where its button is. */
  const callOn = async (target: Scope, body: Partial<Body>, except: string[] = []): Promise<boolean> => {
    const op = body.op ?? "triage";
    if (target.kind === "act") {
      const outcome = await write({ ...body, op, items: target.rows.map((r) => r.ref) }, { bulk: true, except });
      if (outcome.ok) setTicked(new Set());
      setActProblem(outcome.ok ? "" : outcome.error);
      return outcome.ok;
    }
    const ref = target.kind === "panel" ? target.item.ref : target.row.ref;
    const outcome = await write({ ...body, op, items: [ref] }, { except });
    if (target.kind === "panel") setCallProblem(outcome.ok ? null : { ref, words: outcome.error });
    else frame.setRowProblem(outcome.ok ? "" : outcome.error);
    return outcome.ok;
  };

  const verdictKey = (index: number) => {
    const target = scope();
    if (!target) return;
    if (target.kind === "act") {
      const button = verdicts[index];
      if (button)
        void callOn(target, {
          op: "triage",
          verdict: writes(
            button.verdict,
            target.rows.map((r) => r.triage),
          ),
        });
      return;
    }
    const buttons = target.kind === "panel" ? target.item.verdicts : target.row.quick;
    const button = buttons[index];
    if (button) void callOn(target, { op: "triage", verdict: button.writes });
  };

  const dropKey = () => {
    const target = scope();
    if (!target) return;
    if (target.kind === "act") document.getElementById(ACT_WHY)?.focus();
    else if (target.kind === "panel") {
      if (!drop) void go({ drop: "1" }, { replace: true });
      frame.askPanel("why", target.item.ref);
    } else void openRow(target.row.ref, { drop: true, focus: "why" });
  };

  const snoozeKey = () => {
    const target = scope();
    if (!target) return;
    if (target.kind === "act") document.getElementById(ACT_SNOOZE)?.focus();
    else if (target.kind === "panel") frame.askPanel("snooze", target.item.ref);
    else void openRow(target.row.ref, { focus: "snooze" });
  };

  const tick = (refs: string[], on: boolean) => {
    setTicked((was) => {
      const next = new Set(was);
      for (const ref of refs) {
        if (on) next.add(ref);
        else next.delete(ref);
      }
      return next;
    });
    if (!on) setActProblem("");
  };
  const clearTicks = () => {
    setTicked(new Set());
    setActProblem("");
  };

  const opener = React.useRef<Element | null>(null);
  const openNew = () => {
    opener.current = document.activeElement;
    if (!newDraft.title && !newDraft.who && stream && !newDraft.stream) setNewDraft({ ...newDraft, stream });
    setNewOpen(true);
  };

  const idle = () => !pending;
  const selectedRow = () => (selected !== undefined ? byRef.get(selected) : undefined);
  /** The calls' keys, in the ? dialog's order: x, 1 to 4, 0, e, Delete and s. */
  const callKeys: KeyCommand[] = [
    {
      keys: ["x", " "],
      what: "tick the selected row",
      run: () => selected !== undefined && tick([selected], !ticked.has(selected)),
      when: () => Boolean(selectedRow()?.callable),
    },
    {
      keys: ["1", "2", "3", "4"],
      what: "discuss, claude, me, chase; again clears it",
      run: (event) => verdictKey(Number(event.key) - 1),
      when: () => idle() && scope() !== null,
    },
    {
      keys: ["0"],
      what: "no call",
      run: () => {
        const target = scope();
        if (target && (target.kind !== "row" || target.row.triage))
          void callOn(target, { op: "triage", verdict: null });
      },
      when: () => idle() && scope() !== null,
    },
    {
      keys: ["e"],
      what: "done",
      run: () => {
        const target = scope();
        if (target) void callOn(target, { op: "done" });
      },
      when: () => idle() && scope() !== null,
    },
    { keys: ["Delete", "Backspace"], what: "drop: say why", run: dropKey, when: () => scope() !== null },
    { keys: ["s"], what: "snooze", run: snoozeKey, when: () => scope() !== null },
  ];
  const newKey: KeyCommand = { keys: ["n"], what: "a new item", run: openNew };
  /** Esc clears the ticks first, then closes the panel. */
  const escapeKey: KeyCommand = {
    keys: ["Escape"],
    what: "clear the ticks, or close the panel",
    run: () => (tickedRows.length ? clearTicks() : void frame.closePanel()),
    when: () => tickedRows.length > 0 || open !== undefined,
  };

  /** What a list row's ticks and quick buttons call. */
  const rowCalls = {
    onTick: tick,
    onCall: (row: Row, body: Partial<Body>) => void callOn({ kind: "row", row }, body),
    onDrop: (row: Row) => void openRow(row.ref, { drop: true, focus: "why" }),
  };

  /** The act bar over the ticked rows, once a row is ticked. */
  const actBar = (bar: { snooze: Snooze[]; today: string; tomorrow: string }) =>
    tickedRows.length > 0 ? (
      <ActBar
        rows={tickedRows}
        verdicts={verdicts}
        snooze={bar.snooze}
        today={bar.today}
        tomorrow={bar.tomorrow}
        pending={pending}
        problem={actProblem}
        onCall={(body) => callOn({ kind: "act", rows: tickedRows }, body, ["act:why"])}
        onClear={clearTicks}
      />
    ) : null;

  /** The open item's panel body, its calls, drop form, Undo per line and viewer wired to the frame. */
  const itemBody = (shown: ItemPanel) => (
    <ItemPanelBody
      key={shown.id}
      item={shown}
      ask={frame.ask}
      drop={drop}
      callProblem={callProblem?.ref === shown.ref ? callProblem.words : ""}
      viewer={frame.viewer}
      onCall={(body, except) => callOn({ kind: "panel", item: shown }, body, except)}
      onDrop={(on) => void go({ drop: on ? "1" : undefined }, { replace: true })}
      onUndo={async (op, said) => {
        const outcome = await write({ op: "undo", of: op, said });
        setCallProblem(outcome.ok ? null : { ref: shown.ref, words: outcome.error });
      }}
      onView={(ref) => void go({ text: ref }, { replace: true })}
    />
  );

  /** The new item form, which n and the header's button open; an add opens the new item's panel. */
  const newItem = (
    <NewItem
      open={newOpen}
      onOpenChange={setNewOpen}
      draft={newDraft}
      onDraft={setNewDraft}
      rowHref={(ref) => frame.hrefOf({ open: ref, at: undefined, drop: undefined, text: undefined })}
      returnTo={opener}
      onAdded={(ref) => {
        setNewOpen(false);
        setNewDraft(blankDraft());
        if (ref) void openRow(ref);
      }}
      onShow={(ref) => {
        setNewOpen(false);
        void openRow(ref);
      }}
    />
  );

  return { ticked, tickedRows, callKeys, newKey, escapeKey, rowCalls, actBar, itemBody, newItem, openNew };
}
