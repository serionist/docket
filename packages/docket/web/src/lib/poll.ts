/**
 * The day pages' poll (DR-0711): every 5 s the page asks whether the op files moved since the version it read, and
 * once when it mounts, so a page shown again from the cache never shows a stale read. A change refetches the page's
 * reads in place, so the selection, the ticks, the scroll and anything typed stay. It holds while the owner is
 * mid-change: a field focused or holding unsaved text, a save in flight, rows ticked, a dialog or menu open, or the
 * tab hidden; the first tick after that asks.
 */
import { useEffect, useLayoutEffect, useRef } from "react";
import { anyUnsaved, saves } from "@/lib/drafts";

export const POLL_MS = 5000;
const FIELDS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const OPEN = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]';

/** Whether the poll holds now, with `ticked` rows ticked on the page. */
export function holding(ticked: number, doc: Document = document): boolean {
  if (saves() > 0 || doc.hidden || ticked > 0 || anyUnsaved()) return true;
  if (doc.querySelector(OPEN)) return true;
  const active = doc.activeElement;
  if (!(active instanceof HTMLElement)) return false;
  return FIELDS.has(active.tagName) || active.isContentEditable === true;
}

export interface Poll {
  /** The version the page read, or undefined while it has read nothing yet. */
  version: () => string | undefined;
  /** Whether the op files moved since a version. */
  ask: (version: string) => Promise<{ changed: boolean }>;
  hold: () => boolean;
  /** What a change does: refetch the page's reads. */
  changed: () => void;
}

export function usePoll(poll: Poll): void {
  const latest = useRef(poll);
  useLayoutEffect(() => {
    latest.current = poll;
  });
  useEffect(() => {
    let alive = true;
    let asking = false;
    const tick = async () => {
      const { version, ask, hold, changed } = latest.current;
      const given = version();
      if (asking || given === undefined || hold()) return;
      asking = true;
      try {
        const answer = await ask(given);
        if (alive && answer.changed) changed();
      } catch {
        // A poll that fails says nothing changed: the next one asks again.
      } finally {
        asking = false;
      }
    };
    const shown = () => {
      if (!document.hidden) void tick();
    };
    void tick();
    const timer = setInterval(() => void tick(), POLL_MS);
    document.addEventListener("visibilitychange", shown);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", shown);
    };
  }, []);
}
