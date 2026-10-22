/**
 * A page's state is its URL as a plain query string (DR-0792). The router reads it as URLSearchParams does, every
 * value a string and a repeated key repeated, never JSON, and writes it as the server's urlencode does with its one
 * URL_SAFE (docket/web/urls.py): ASCII letters and digits, `_ . - ~` and `: @ / ,` as they are, a space as `+`, and
 * every other byte of the UTF-8 percent-encoded in upper-case hex, `* ! ' ( )` included. So a URL the app writes is
 * the URL the server writes, byte for byte, and Claude's links, bookmarks and the tasks skill open the same state.
 */

export type SearchValue = string | string[];
export type Search = Record<string, SearchValue>;

const SAFE = /^[A-Za-z0-9_.\-~:@/,]$/;
const utf8 = new TextEncoder();

/** One key or value as the server's `quote_plus(value, safe=":@/,")` writes it. */
export function encode(value: string): string {
  let out = "";
  for (const byte of utf8.encode(value)) {
    const char = String.fromCharCode(byte);
    if (byte < 0x80 && SAFE.test(char)) out += char;
    else if (byte === 0x20) out += "+";
    else out += `%${byte.toString(16).toUpperCase().padStart(2, "0")}`;
  }
  return out;
}

/** The query string, with or without its `?`, as strings: a key given twice becomes a list, in its order. */
export function parseSearch(query: string): Search {
  const out: Search = {};
  for (const [key, value] of new URLSearchParams(query.startsWith("?") ? query.slice(1) : query)) {
    const before = out[key];
    if (before === undefined) out[key] = value;
    else if (Array.isArray(before)) before.push(value);
    else out[key] = [before, value];
  }
  return out;
}

/** The search as a query string with its `?`, or "" when it holds nothing: keys in their order, a list repeated. */
export function stringifySearch(search: Record<string, unknown>): string {
  const pairs: string[] = [];
  for (const [key, value] of Object.entries(search)) {
    for (const one of Array.isArray(value) ? value : [value]) {
      if (one === undefined || one === null || one === "") continue;
      pairs.push(`${encode(key)}=${encode(String(one))}`);
    }
  }
  return pairs.length ? `?${pairs.join("&")}` : "";
}

/** One value of a key: the first when the key was repeated. */
export function one(search: Search, key: string): string | undefined {
  const value = search[key];
  return Array.isArray(value) ? value[0] : value;
}

export interface History {
  location: { pathname: string; search: string; hash: string };
  replace: (href: string) => void;
}

/**
 * The server's canonical URL for the state a page read, written into the address bar as the server sent it: the
 * history's replace takes the string as it is, where navigate({href}) would parse and encode it again. Nothing is
 * written when the bar already holds it. True when it wrote.
 */
export function writeBack(history: History, canonical: string): boolean {
  const { pathname, search } = history.location;
  if (`${pathname}${search}` === canonical) return false;
  history.replace(canonical + history.location.hash);
  return true;
}
