/**
 * Search on a fake service: the results read with the panel's parameters left out and the server's canonical URL
 * written back; the groups with the words found under the highlighter and a record's markup kept as text; the live
 * region's words; Search's own keys, / to the box, j and k carrying the panel once it is open, Enter or o, v, [ and ],
 * c and Esc; a query sent with the filters it carries; the dates; a refusal in the server's words; a query that is one
 * locator opening it beside the results; and a transcript hit's link to the Recording page, in the app. The service's
 * own answers are tested in Python (tests/test_web_search.py, test_web_panel.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import type { HitLine, HitPanel, Results, SearchRead } from "./api";

const CALL = "r:calls:rec-0100@01:50-03:20";
const NOTE = "recording-note:2026-10-07-supplier-call";

function hit(locator: string, words: string, extra: Partial<HitLine> = {}): HitLine {
  return {
    locator,
    icon: "recording",
    stretch: "01:50-03:20",
    when: "7 Oct 2026 10:01",
    who: "Cleo Test",
    snippet: [
      { text: "Can we ask ", mark: false },
      { text: "Nortwin", mark: true },
      { text: words, mark: false },
    ],
    entities: ["Northwind"],
    found: "some words",
    also: [],
    url: `/search?query=Nortwin&sources=calls&hit=${locator}`,
    recording: null,
    ...extra,
  };
}

const RESULTS: Results = {
  reading: ['"nortwin" is Northwind'],
  chips: [{ label: "source: calls", url: "/search?query=Nortwin" }],
  facets: [
    {
      key: "sources",
      title: "Sources",
      favours: false,
      items: [{ label: "calls", count: 6, on: true, url: "/search?query=Nortwin", note: "" }],
    },
    {
      key: "people",
      title: "People",
      favours: true,
      items: [
        {
          label: "Ben Sample",
          count: 4,
          on: false,
          url: "/search?query=Nortwin&sources=calls&people=ben-sample",
          note: "",
        },
      ],
    },
  ],
  groups: [
    {
      kind: "recording",
      icon: "recording",
      title: "Supplier call with Northwind",
      source: "calls",
      when: "7 Oct 2026 10:00-10:05",
      summary: {
        id: "minutes:2026-10-07-supplier-call",
        title: "Minutes: supplier call",
        type: "minutes",
        rev: 1,
        url: "/search?query=Nortwin&sources=calls&hit=minutes:2026-10-07-supplier-call",
      },
      hits: [
        hit(CALL, " for a smaller scope? <script>alert(1)</script>", {
          recording: "/recording?rid=calls:rec-0100&at=01:50-03:20#at",
        }),
        hit(NOTE, " notes", {
          icon: "doc",
          stretch: "lines 21-22",
          url: `/search?query=Nortwin&sources=calls&hit=${NOTE}&lines=21-22`,
        }),
      ],
    },
  ],
  hits: 2,
  candidates: 12,
  more: null,
  drops: [],
  announce: "2 hits in 1 group",
};

const READ: SearchRead = {
  canonical: "/search?query=Nortwin&sources=calls",
  results: RESULTS,
  locator: null,
  waiting: "",
  index_line: "48 chunks from 22 records and 7 docs, updated 09:30",
};

function panelOf(locator: string, extra: Partial<HitPanel> = {}): HitPanel {
  return {
    mode: "raw",
    locator,
    title: "Supplier call with Northwind",
    close: "/search?query=Nortwin&sources=calls",
    resolved: null,
    problems: [],
    summary: {
      id: "minutes:2026-10-07-supplier-call",
      type: "minutes",
      rev: 1,
      title: "Minutes: supplier call",
      lines: [
        {
          n: 14,
          body: "Decision: ask Northwind for a smaller scope first.",
          style: "bullet",
          anchors: [{ text: "@01:50-02:30", url: "/search?hit=r:calls:rec-0100@01:50-02:30", title: "" }],
          cited: true,
        },
      ],
    },
    no_summary: "",
    citing: [],
    legacy: [],
    spans: [
      {
        rid: "calls:rec-0100",
        anchor: `[${locator}]`,
        head: "recording - 7 Oct 2026 10:00",
        versions: "",
        problem: null,
        segments: [
          { at: "01:50", locator: "r:calls:rec-0100@01:50", speaker: "Cleo Test", text: "Can we ask?", mark: true },
        ],
        paragraphs: [],
        mark: false,
        note: null,
      },
    ],
    pairs: [],
    around: 1,
    unit: "min",
    around_text: "± 1 min",
    less: `/search?query=Nortwin&sources=calls&hit=${locator}&around=0`,
    more: `/search?query=Nortwin&sources=calls&hit=${locator}&around=2`,
    verify: `/search?query=Nortwin&sources=calls&hit=${locator}&verify=1`,
    verify_label: "Verify the summary",
    error: "",
    recording: "/recording?rid=calls:rec-0100&at=01:50-03:20#at",
    ...extra,
  };
}

const SHELL = {
  vault: "vault",
  machine: "ui-box",
  zone: "Europe/Budapest",
  index_line: "",
  untriaged: 0,
  staging: 0,
  sync_due: [],
  problem: null,
};

interface Sent {
  path: string;
  query: string;
}

let sent: Sent[] = [];
let results: (query: URLSearchParams) => Response = () => json(READ);

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  sent.push({ path: url.pathname, query: url.search });
  if (url.pathname === "/api/web/shell") return json(SHELL);
  if (url.pathname === "/api/web/search") return results(url.searchParams);
  if (url.pathname === "/api/web/search/panel") {
    const shown = url.searchParams.get("hit") ?? url.searchParams.get("query") ?? "";
    return json({ canonical: `/search${url.search}`, panel: panelOf(shown) });
  }
  if (url.pathname === "/api/web/recording") {
    return json({ canonical: `/recording${url.search}`, view: { mode: "start" }, waiting: "" });
  }
  return json({ error: "not here" }, 404);
}

function key(name: string, target: EventTarget = document.activeElement ?? document.body) {
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }));
  });
}

function matchMedia(query: string): MediaQueryList {
  const wide = /min-width: (\d+)px/.exec(query);
  return {
    matches: wide ? 1440 >= Number(wide[1]) : false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  };
}

async function open(url: string, settled = "[data-row]") {
  vi.stubGlobal("fetch", vi.fn(serve));
  vi.stubGlobal("matchMedia", matchMedia);
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  const router = createAppRouter(createMemoryHistory({ initialEntries: [url] }));
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await act(async () => {
    await router.load();
  });
  await waitFor(() => expect(document.querySelector(settled)).toBeTruthy());
  return router;
}

function where(router: Awaited<ReturnType<typeof open>>): string {
  const { pathname, search, hash } = router.history.location;
  return `${pathname}${search}${hash}`;
}

function current(): string | undefined {
  return document.querySelector('li[aria-current="true"]')?.getAttribute("data-locator") ?? undefined;
}

function asked(path: string): string[] {
  return sent.filter((s) => s.path === path).map((s) => s.query);
}

beforeAll(() => installKeys());

beforeEach(() => {
  sent = [];
  results = () => json(READ);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the results", () => {
  it("are read without the panel's parameters, and the server's canonical URL is written back", async () => {
    const router = await open("/search?sources=calls&query=Nortwin");
    expect(asked("/api/web/search")).toEqual(["?sources=calls&query=Nortwin"]);
    await waitFor(() => expect(where(router)).toBe("/search?query=Nortwin&sources=calls"));
    expect(document.title).toBe("Nortwin - Search - Docket");
    const nav = screen.getByRole("navigation", { name: "Pages" });
    expect(within(nav).getByRole("link", { name: "Search" }).getAttribute("aria-current")).toBe("page");
  });

  it("are asked again when the page opens again, so a search kept from before isn't stale", async () => {
    const router = await open("/search?query=Nortwin&sources=calls");
    await act(async () => {
      await router.navigate({ to: "/design" });
    });
    await act(async () => {
      router.history.back();
    });
    await waitFor(() => expect(document.querySelector("[data-row]")).toBeTruthy());
    await waitFor(() => expect(asked("/api/web/search")).toHaveLength(2));
  });

  it("put the words found under the highlighter and keep a record's markup as text", async () => {
    await open("/search?query=Nortwin&sources=calls");
    const row = document.querySelector(`[data-locator="${CALL}"]`) as HTMLElement;
    expect([...row.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["Nortwin"]);
    expect(row.textContent).toContain("<script>alert(1)</script>");
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText("Minutes: supplier call")).toBeTruthy();
    expect(screen.getByRole("list", { name: "How search read the query" }).textContent).toContain("Northwind");
  });

  it("say what came back in the live region", async () => {
    await open("/search?query=Nortwin&sources=calls");
    await waitFor(() => expect(document.querySelector("[data-announce]")?.textContent).toBe("2 hits in 1 group"));
    expect(document.querySelector("[data-announce]")?.getAttribute("aria-live")).toBe("polite");
  });

  it("open a transcript hit's place on the Recording page in the app", async () => {
    const router = await open("/search?query=Nortwin&sources=calls");
    const link = screen.getAllByRole("link", { name: /On the Recording page/ })[0] as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/recording?rid=calls:rec-0100&at=01:50-03:20#at");
    fireEvent.click(link);
    await waitFor(() => expect(where(router)).toBe("/recording?rid=calls:rec-0100&at=01:50-03:20#at"));
  });

  it("say a refusal in the server's words, and the dates apply with the search kept", async () => {
    results = () => json({ error: "since is a date or a time, not 'soon'" }, 400);
    const router = await open("/search?query=fleet&sources=chat&since=soon", '[role="alert"]');
    expect(screen.getByRole("alert").textContent).toBe("since is a date or a time, not 'soon'");
    fireEvent.click(screen.getByRole("button", { name: /Dates/ }));
    const since = await screen.findByLabelText("Since");
    expect((since as HTMLInputElement).value).toBe("soon");
    fireEvent.change(since, { target: { value: "last week" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply the dates" }));
    await waitFor(() => expect(where(router)).toBe("/search?query=fleet&sources=chat&since=last+week"));
  });

  it("open a query that is one locator beside the results, unsearched", async () => {
    results = (q) =>
      json({ ...READ, canonical: `/search?query=${q.get("query")}`, results: null, locator: q.get("query") });
    await open(`/search?query=${CALL}`, "#panel-title");
    expect(asked("/api/web/search/panel")).toEqual([`?query=${CALL}`]);
    expect(screen.getByText(/is a locator, so its words aren't searched/)).toBeTruthy();
  });
});

describe("the box", () => {
  it("takes the focus on /, keeps its words on Esc and sends them with the filters the page holds", async () => {
    const router = await open(`/search?query=Nortwin&sources=calls&k=40&hit=${CALL}`, "#panel-title");
    key("/", document.body);
    const box = screen.getByRole("searchbox");
    expect(document.activeElement).toBe(box);
    fireEvent.change(box, { target: { value: "fleet  dashboard" } });
    key("Escape", box);
    expect(document.activeElement).not.toBe(box);
    expect((box as HTMLInputElement).value).toBe("fleet  dashboard");
    fireEvent.submit(box.closest("form") as HTMLFormElement);
    // The filters go with the new words; its k and its panel don't.
    await waitFor(() => expect(where(router)).toBe("/search?query=fleet++dashboard&sources=calls"));
  });
});

describe("the keys", () => {
  it("move with j and k, open with Enter, carry the panel with j, and close it with Esc", async () => {
    const router = await open("/search?query=Nortwin&sources=calls");
    const before = router.history.length;
    key("j", document.body);
    expect(current()).toBe("minutes:2026-10-07-supplier-call");
    key("j");
    expect(current()).toBe(CALL);
    key("Enter");
    await waitFor(() => expect(where(router)).toBe(`/search?query=Nortwin&sources=calls&hit=${CALL}`));
    expect(router.history.length).toBe(before + 1);
    await waitFor(() => expect(document.activeElement?.id).toBe("panel-title"));
    // The results aren't read again for a panel.
    expect(asked("/api/web/search")).toHaveLength(1);
    key("j");
    await waitFor(() => expect(where(router)).toBe(`/search?query=Nortwin&sources=calls&hit=${NOTE}&lines=21-22`));
    expect(router.history.length).toBe(before + 1);
    expect(current()).toBe(NOTE);
    key("Escape");
    await waitFor(() => expect(where(router)).toBe("/search?query=Nortwin&sources=calls"));
    await waitFor(() => expect(document.activeElement?.getAttribute("data-locator")).toBe(NOTE));
  });

  it("verify with v and widen with [ and ], each the URL the panel gives", async () => {
    const router = await open(`/search?query=Nortwin&sources=calls&hit=${CALL}`, "#panel-title");
    await screen.findByText("Raw");
    key("]", document.body);
    await waitFor(() => expect(where(router)).toBe(`/search?query=Nortwin&sources=calls&hit=${CALL}&around=2`));
    await screen.findByText("Raw");
    key("v", document.body);
    await waitFor(() => expect(where(router)).toContain("&verify=1"));
    await screen.findByText("Raw");
    key("[", document.body);
    await waitFor(() => expect(where(router)).toContain("&around=0"));
  });

  it("walk through two groups that share a summary, each row once, and open it from either", async () => {
    const [first] = RESULTS.groups;
    if (!first) throw new Error("the fixture has a group");
    const thread = {
      ...first,
      kind: "thread",
      icon: "thread" as const,
      title: "The supplier thread",
      hits: [
        hit("r:chat:C0OPS/1.000100", " in the thread", {
          icon: "thread",
          url: "/search?query=Nortwin&sources=calls&hit=r:chat:C0OPS/1.000100",
        }),
      ],
    };
    results = () => json({ ...READ, results: { ...RESULTS, groups: [first, thread], hits: 3 } });
    const router = await open("/search?query=Nortwin&sources=calls");
    const walked: (string | undefined)[] = [];
    for (let n = 0; n < 6; n++) {
      key("j", document.activeElement ?? document.body);
      walked.push(current());
    }
    const summary = "minutes:2026-10-07-supplier-call";
    expect(walked).toEqual([summary, CALL, NOTE, summary, "r:chat:C0OPS/1.000100", "r:chat:C0OPS/1.000100"]);
    expect(document.querySelectorAll('li[aria-current="true"]')).toHaveLength(1);
    // The second summary opens the doc, its row staying the selected one.
    key("k");
    key("Enter");
    await waitFor(() => expect(where(router)).toBe(`/search?query=Nortwin&sources=calls&hit=${summary}`));
    const selected = document.querySelector('li[aria-current="true"]') as HTMLElement;
    expect(selected.closest("section")?.textContent).toContain("The supplier thread");
  });

  it("copy the selected hit's locator with c", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    await open("/search?query=Nortwin&sources=calls");
    key("j", document.body);
    key("j");
    key("c");
    expect(writeText).toHaveBeenCalledWith(CALL);
    await screen.findByText("Copied");
  });
});
