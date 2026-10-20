/**
 * What a who field suggests for the text typed in it (DR-0714): the names in the order the server gives them, the
 * most used first, narrowed to those the text finds. A name whose start, or a word's start, is the text comes first,
 * then one an other name or alias finds so, then any that holds the text; each tier keeps the server's order. Case and
 * accents aside, so "dora" finds Dóra.
 */

export interface Named {
  name: string;
  uses: number;
  also: readonly string[];
}

export interface Suggestion {
  name: string;
  uses: number;
  /** The other name the text found it by, when its own name didn't. */
  via?: string;
}

/** Text as the match reads it: lower case, accents off, spaces made one. */
export function folded(text: string): string {
  return text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/\s+/g, " ").trim();
}

/** How well a name holds the text: 0 at its start or a word's, 1 anywhere in it, undefined not at all. */
function tierOf(name: string, text: string): number | undefined {
  const name_ = folded(name);
  if (name_.startsWith(text) || name_.split(/[\s,.-]+/).some((word) => word.startsWith(text))) return 0;
  return name_.includes(text) ? 1 : undefined;
}

/** The suggestions for the text typed, at most `max` of them. */
export function suggest(people: readonly Named[], typed: string, max = 50): Suggestion[] {
  const text = folded(typed);
  if (!text) return people.slice(0, max).map(({ name, uses }) => ({ name, uses }));
  const found: { rank: number; at: number; suggestion: Suggestion }[] = [];
  people.forEach((person, at) => {
    const own = tierOf(person.name, text);
    if (own !== undefined) {
      found.push({ rank: own === 0 ? 0 : 2, at, suggestion: { name: person.name, uses: person.uses } });
      return;
    }
    for (const other of person.also) {
      const tier = tierOf(other, text);
      if (tier !== undefined) {
        found.push({ rank: tier === 0 ? 1 : 2, at, suggestion: { name: person.name, uses: person.uses, via: other } });
        return;
      }
    }
  });
  found.sort((a, b) => a.rank - b.rank || a.at - b.at);
  return found.slice(0, max).map((f) => f.suggestion);
}

/** Whether the text typed is a name suggested already, case and accents aside, so it needs no "as typed" choice. */
export function named(suggestions: readonly Suggestion[], typed: string): boolean {
  const text = folded(typed);
  return suggestions.some((s) => folded(s.name) === text);
}
