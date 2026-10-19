/**
 * Whether the browser's session has lapsed: /api/web answered 401. The page keeps everything typed in it; a dialog
 * names `docket open` and Retry asks again (the shell's SessionDialog).
 */
import { useSyncExternalStore } from "react";

let lapsed = false;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function markLapsed(): void {
  if (lapsed) return;
  lapsed = true;
  emit();
}

export function clearLapsed(): void {
  if (!lapsed) return;
  lapsed = false;
  emit();
}

export function isLapsed(): boolean {
  return lapsed;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLapsed(): boolean {
  return useSyncExternalStore(subscribe, isLapsed);
}

/** The command that opens this page again, quoted for a shell: its path and its query string as they stand. */
export function openCommand(location: { pathname: string; searchStr: string }): string {
  return `docket open "${location.pathname}${location.searchStr}"`;
}
