/**
 * The day pages' two rules that run in the browser, as the old tasks app ran them (DR-0710, DR-0711): what a verdict's
 * button writes over the rows it acts on, decided before the write so no write toggles, and where the selection goes
 * after a call.
 */

/** The verdict a button writes over rows holding these calls: none, which clears it, when every row holds it. */
export function writes(verdict: string, held: readonly (string | null | undefined)[]): string | null {
  return pressed(verdict, held) ? null : verdict;
}

/** Whether every row a button acts on holds its verdict, so the button shows pressed and clears it. */
export function pressed(verdict: string, held: readonly (string | null | undefined)[]): boolean {
  return held.length > 0 && held.every((h) => h === verdict);
}

/**
 * Where the selection goes after a call on `acted`: the first row after the selected one that wasn't acted on, else
 * the nearest before it. Undefined, so it stays, when the selection wasn't among the acted rows.
 */
export function after(
  order: readonly string[],
  selected: string | undefined,
  acted: readonly string[],
): string | undefined {
  const here = selected ?? (acted.length === 1 ? acted[0] : undefined);
  if (here === undefined || !acted.includes(here)) return undefined;
  const at = order.indexOf(here);
  if (at < 0) return undefined;
  const later = order.slice(at + 1).find((ref) => !acted.includes(ref));
  const earlier = order
    .slice(0, at)
    .reverse()
    .find((ref) => !acted.includes(ref));
  return later ?? earlier;
}

/** The row j or k moves to: the next or previous one, the first or last when none is selected. */
export function step(order: readonly string[], selected: string | undefined, by: 1 | -1): string | undefined {
  if (!order.length) return undefined;
  const at = selected === undefined ? -1 : order.indexOf(selected);
  if (at < 0) return by > 0 ? order[0] : order[order.length - 1];
  return order[Math.min(order.length - 1, Math.max(0, at + by))];
}

/** The row that takes a selected row's place when a refetch no longer lists it: the one now at its index. */
export function standIn(order: readonly string[], index: number): string | undefined {
  if (!order.length) return undefined;
  return order[Math.min(Math.max(index, 0), order.length - 1)];
}
