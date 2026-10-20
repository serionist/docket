import { describe, expect, it } from "vitest";
import canonical from "@/test/canonical.json";
import { encode, one, parseSearch, stringifySearch, writeBack } from "./search";

describe("the URL's encoding, as the server's urlencode with URL_SAFE", () => {
  it("keeps ASCII letters and digits, _ . - ~ and : @ / , as they are", () => {
    expect(encode("AZaz09_.-~:@/,")).toBe("AZaz09_.-~:@/,");
  });

  it("writes a space as + and encodes * ! ' ( ) and the rest in upper-case hex", () => {
    expect(encode("a b*!'()")).toBe("a+b%2A%21%27%28%29");
    expect(encode("?&=#%+")).toBe("%3F%26%3D%23%25%2B");
  });

  it("encodes letters past ASCII as their UTF-8 bytes", () => {
    expect(encode("Őr ü")).toBe("%C5%90r+%C3%BC");
  });
});

describe("parseSearch and stringifySearch", () => {
  it("read every value as a string and a repeated key as a list, never JSON", () => {
    expect(parseSearch("?k=20&sources=chat&sources=calls&verify=1")).toEqual({
      k: "20",
      sources: ["chat", "calls"],
      verify: "1",
    });
    expect(parseSearch("q=%7B%22a%22%3A1%7D")).toEqual({ q: '{"a":1}' });
  });

  it("write a list as its key repeated, in order, and leave out what is empty", () => {
    expect(stringifySearch({ sources: ["chat", "calls"], q: "", hit: undefined })).toBe("?sources=chat&sources=calls");
    expect(stringifySearch({})).toBe("");
  });

  it.each(canonical.urls)("give back the server's canonical %s byte for byte", (url) => {
    const [path, query = ""] = url.split("?");
    expect(`${path}${stringifySearch(parseSearch(query))}`).toBe(url);
  });

  it("hold one URL from each page", () => {
    const pages = new Set(canonical.urls.map((url) => url.split("?")[0]));
    expect([...pages].sort()).toEqual(
      ["/briefs", "/curate", "/ledger", "/queue", "/recording", "/search", "/staging", "/sync"].sort(),
    );
  });

  it("take the first of a repeated key where one value is wanted", () => {
    expect(one({ a: ["1", "2"] }, "a")).toBe("1");
    expect(one({ a: "1" }, "a")).toBe("1");
    expect(one({}, "a")).toBeUndefined();
  });
});

describe("writeBack", () => {
  function history(pathname: string, search: string) {
    const replaced: string[] = [];
    return { replaced, location: { pathname, search, hash: "#raw" }, replace: (href: string) => replaced.push(href) };
  }

  it("writes the server's canonical URL as it came, keeping the hash", () => {
    const h = history("/search", "?sources=chat&query=x");
    expect(writeBack(h, "/search?query=x&sources=chat")).toBe(true);
    expect(h.replaced).toEqual(["/search?query=x&sources=chat#raw"]);
  });

  it("writes nothing when the address bar already holds it", () => {
    const h = history("/search", "?query=x");
    expect(writeBack(h, "/search?query=x")).toBe(false);
    expect(h.replaced).toEqual([]);
  });
});
