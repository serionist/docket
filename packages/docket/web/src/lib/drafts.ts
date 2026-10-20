/**
 * Text typed and not yet saved or sent, by where it was typed, and the saves in flight (DR-0710, DR-0711). The leave
 * guard asks before either is dropped, and the poll holds while there is any. A key names its place, such as
 * `panel:A-0012:notes` or `act:why`, so the panel's guard asks only about the panel's own text.
 */
import { useEffect } from "react";

export const LEAVE = "Leave the text you typed unsaved?";

const unsaved = new Set<string>();
let saving = 0;
let waiting: (() => void)[] = [];

export function setUnsaved(key: string, on: boolean): void {
  if (on) unsaved.add(key);
  else unsaved.delete(key);
}

/** Whether text is typed and unsaved under a place, leaving out the keys a write is about to send. */
export function anyUnsaved(prefix = "", except: readonly string[] = []): boolean {
  for (const key of unsaved) if (key.startsWith(prefix) && !except.includes(key)) return true;
  return false;
}

/** A component's text marked unsaved while `on`, and no longer once it unmounts. */
export function useUnsaved(key: string, on: boolean): void {
  useEffect(() => {
    setUnsaved(key, on);
    return () => setUnsaved(key, false);
  }, [key, on]);
}

/** A save begins; the function it gives ends it. */
export function startSave(): () => void {
  saving += 1;
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    saving = Math.max(0, saving - 1);
    if (saving === 0) {
      const now = waiting;
      waiting = [];
      for (const done of now) done();
    }
  };
}

export function saves(): number {
  return saving;
}

/** Resolves once no save is in flight. */
export function settled(): Promise<void> {
  if (saving === 0) return Promise.resolve();
  return new Promise((done) => waiting.push(done));
}

/** Whether the page may drop what is typed under a place: nothing is, or the owner says to leave it. */
export function mayLeave(prefix = "", except: readonly string[] = []): boolean {
  return !anyUnsaved(prefix, except) || window.confirm(LEAVE);
}

/** Forgets everything, so a test starts clean. */
export function resetDrafts(): void {
  unsaved.clear();
  saving = 0;
  waiting = [];
}
