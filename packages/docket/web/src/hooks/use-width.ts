import { useSyncExternalStore } from "react";

/**
 * The shell's widths (DR-0708): from 1280 px the sidebar, the list and the panel stand side by side; from 1024 the
 * sidebar folds to its icons; from 768 the sidebar is a sheet and the panel slides over the list; below 768 the list
 * stands alone and an open item takes the screen.
 */
export const WIDE = 1280;
export const MEDIUM = 1024;
export const NARROW = 768;

export type Band = "wide" | "medium" | "small" | "narrow";

export function bandOf(width: number): Band {
  if (width >= WIDE) return "wide";
  if (width >= MEDIUM) return "medium";
  if (width >= NARROW) return "small";
  return "narrow";
}

const queries = [WIDE, MEDIUM, NARROW].map((px) => `(min-width: ${px}px)`);

function subscribe(listener: () => void): () => void {
  const lists = queries.map((query) => window.matchMedia(query));
  for (const list of lists) list.addEventListener("change", listener);
  return () => {
    for (const list of lists) list.removeEventListener("change", listener);
  };
}

/** The band the window's width falls in, kept current as it is resized. */
export function useBand(): Band {
  return useSyncExternalStore(subscribe, () => bandOf(window.innerWidth));
}
