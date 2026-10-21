/**
 * The Briefs page on a fake service: the list by entity type with what waits for the owner, the server's canonical URL
 * written back; a brief's line opening its raw beside it with the panel's heading taking the focus, j following with
 * no new history entry, Esc closing it and giving the focus back to the line; j and o on the list; a pick posting the
 * rev the page showed, its buttons waiting for the answer, its notice once and the page read again; a decision moved
 * since refused in the server's words and the page read again; a URL from before the app saying its click once; a
 * refusal of the URL in the server's words. The service's own answers are tested in Python (tests/test_web_briefs.py,
 * test_web_decisions.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import type { BriefLine, BriefsRead, BriefsView, Card, DecisionsPart } from "./api";

const NORTHWIND = "brief:northwind";
const FLEET = "brief:fleet-management";
const PILOT = "brief:harbour-pilot";
const WEEKLY = "decision:2026-10-14-weekly-review";

function blank(mode: BriefsView["mode"], title = "Briefs"): BriefsView {
  return {
    mode,
    title,
    message: "",
    link: null,
    summary: "",
    types: [],
    groups: [],
    hidden_note: "",
    empty: "",
    empty_how: "",
    to_decide: [],
    id: "",
    state: null,
    meta: [],
    back: "/briefs",
    notices: [],
    new: [],
    lines: [],
    raw: null,
    choose: "",
    ledger: null,
  };
}

function listView(): BriefsView {
  const row = (id: string, name: string, state: "stale" | "current") => ({
    id,
    name,
    url: `/briefs?brief=${id}`,
    state,
    rev: 1,
    age: "2 days ago",
    new: 0,
    flagged: state === "stale" ? 1 : 0,
    why: null,
    orphan: "",
  });
  return {
    ...blank("list"),
    summary: "2 briefs: 1 stale, 1 current",
    types: [
      { name: "All", count: 2, url: "/briefs", on: true },
      { name: "customer", count: 1, url: "/briefs?type=customer", on: false },
      { name: "site", count: 1, url: "/briefs?type=site", on: false },
    ],
    groups: [
      { type: "customer", rows: [row(NORTHWIND, "Northwind", "stale")] },
      { type: "site", rows: [row("brief:harbour-site", "Harbour site", "current")] },
    ],
    to_decide: [{ text: "Fleet management: 2 decisions", url: `/briefs?brief=${FLEET}`, title: "" }],
  };
}

function line(n: number, body: string, open: number | null, cites = true): BriefLine {
  return {
    n,
    body,
    style: "bullet",
    url: cites ? `/briefs?brief=${NORTHWIND}&line=${n}#raw` : null,
    anchors: cites
      ? [{ text: "[r:calls:rec-0100@04:00]", url: "/recording?rid=calls:rec-0100&at=04:00#at", title: "" }]
      : [],
    refs: [],
    flagged: n === 17,
    hidden: false,
    open: open === n,
    unconfirmed: false,
  };
}

function briefView(open: number | null): BriefsView {
  return {
    ...blank("brief", "Northwind"),
    id: NORTHWIND,
    state: "stale",
    meta: ["customer", "rev 1", "2 days ago"],
    notices: ["1 line cites a line that changed since; marked below."],
    lines: [
      { ...line(15, "What changed", open, false), style: "heading" },
      line(17, "The consultant's price is above the budget.", open),
      line(18, "The offer comes by mail.", open),
      line(20, "No citation here.", open, false),
      line(22, "7 Oct, supplier call: Cleo sends the offer in writing by Friday.", open),
    ],
    raw:
      open === null
        ? null
        : {
            n: open,
            body: `the words of line ${open}`,
            hidden: false,
            problems: [],
            flags: [],
            spans: [],
            cited: [],
            empty: `Line ${open} cites nothing: it has no anchors or refs.`,
            close: `/briefs?brief=${NORTHWIND}`,
          },
    choose: open === null ? "Choose a line to see the raw it cites and the doc lines it was built from." : "",
  };
}

function card(id: string, title: string, rev: number, standing: Card["standing"], picks = false): Card {
  return {
    id,
    title,
    standing,
    said: "14 Oct 2026",
    rev,
    by: "claude-test",
    verify: `/search?hit=${id}`,
    lines: [],
    picks: picks
      ? [
          { value: "confirm", label: "Confirm", primary: true },
          { value: "reject", label: "Reject", primary: false },
        ]
      : [],
    against: [],
    folded: [],
    contradicted: [],
    note: "",
  };
}

let confirmed = false;
let moved = false;
let reads: string[] = [];
let posts: unknown[] = [];

function fleetPart(): DecisionsPart {
  const weekly = card(WEEKLY, "a review every week", confirmed ? 2 : 1, confirmed ? "current" : "proposed", !confirmed);
  const dashboard = card("decision:2026-10-10-dashboard-first", "the dashboard ships before the pilot", 2, "current");
  return {
    waiting: confirmed ? [] : [weekly],
    current: confirmed ? [weekly, dashboard] : [dashboard],
    rejected: [],
    hidden_note: "",
    empty: "",
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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function read(canonical: string, view: BriefsView, extra: Partial<BriefsRead> = {}): Response {
  return json({ canonical, view, decisions: null, notice: null, waiting: "", ...extra });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/api/web/shell") return json(SHELL);
  if (url.pathname === "/api/web/briefs/decision") {
    const body = (await request.json()) as Record<string, unknown>;
    posts.push(body);
    if (moved) {
      confirmed = true;
      return json({ error: `${WEEKLY} is confirmed already` }, 409);
    }
    confirmed = true;
    return json({ decision: WEEKLY, pick: "confirm", notice: "Confirmed: it is a decision now.", rev: 2 });
  }
  if (url.pathname !== "/api/web/briefs") return json({ error: "not here" }, 404);
  reads.push(url.search);
  const params = url.searchParams;
  if (params.get("type") === "people") return json({ error: "type is an entity type, not 'people'" }, 400);
  const brief = params.get("brief");
  const notice = params.get("done") === "confirm" ? "Confirmed: it is a decision now." : null;
  if (brief === PILOT) {
    const view = { ...blank("entity", "Harbour pilot"), message: "No brief yet." };
    const pilot = card("decision:2026-10-16-pilot-crane", "the crane goes first", 1, "proposed", true);
    const part = { waiting: [pilot], current: [], rejected: [], hidden_note: "", empty: "" };
    return read(`/briefs?brief=${PILOT}`, view, { decisions: part });
  }
  if (brief === FLEET) {
    const view = { ...blank("entity", "Fleet management"), message: "No brief yet." };
    return read(`/briefs?brief=${FLEET}`, view, { decisions: fleetPart(), notice });
  }
  if (brief === NORTHWIND) {
    const open = params.get("line");
    const canonical = `/briefs?brief=${NORTHWIND}${open ? `&line=${open}` : ""}`;
    return read(canonical, briefView(open ? Number(open) : null));
  }
  const type = params.get("type");
  return read(type ? `/briefs?type=${type}` : "/briefs", listView());
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

async function open(url: string, settled: string) {
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

/** What the notice at the list's foot says, or "" when none shows; a note beside a part is a status too. */
function said(): string {
  return screen.queryAllByRole("status").find((el) => el.textContent?.startsWith("Confirmed"))?.textContent ?? "";
}

function press(key: string) {
  fireEvent.keyDown(document.activeElement ?? document.body, { key });
}

beforeAll(() => installKeys());

beforeEach(() => {
  confirmed = false;
  moved = false;
  reads = [];
  posts = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the list", () => {
  it("shows the briefs by type, what waits for the owner, and writes the canonical URL back", async () => {
    const router = await open("/briefs?type=site&other=1", "[data-row]");
    await waitFor(() => expect(where(router)).toBe("/briefs?type=site"));
    expect(document.title).toBe("Briefs - Docket");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Briefs");
    const waiting = screen.getByRole("region", { name: "Decisions waiting for you" });
    expect(within(waiting).getByRole("link").getAttribute("href")).toBe(`/briefs?brief=${FLEET}`);
    const northwind = document.querySelector(`[data-row="${NORTHWIND}"]`) as HTMLElement;
    expect(northwind.textContent).toContain("stale");
    expect(northwind.textContent).toContain("1 flagged line");
    expect(within(screen.getByRole("navigation", { name: "Entity types" })).getAllByRole("link")).toHaveLength(3);
  });

  it("moves with j and opens the brief with o", async () => {
    const router = await open("/briefs", "[data-row]");
    press("j");
    expect(document.activeElement?.getAttribute("data-row")).toBe(NORTHWIND);
    press("j");
    expect(document.activeElement?.getAttribute("data-row")).toBe("brief:harbour-site");
    press("k");
    expect(document.activeElement?.getAttribute("data-row")).toBe(NORTHWIND);
    press("o");
    await waitFor(() => expect(where(router)).toBe(`/briefs?brief=${NORTHWIND}`));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Northwind"));
  });

  it("says a refusal of its URL in the server's words, with the way back", async () => {
    await open("/briefs?type=people", "[role=alert]");
    expect(screen.getByRole("alert").textContent).toBe("type is an entity type, not 'people'");
    expect(screen.getByRole("link", { name: "All briefs" }).getAttribute("href")).toBe("/briefs");
  });
});

describe("a brief", () => {
  it("opens a line's raw beside it, follows j with no new entry, and closes with Esc back to the line", async () => {
    const router = await open(`/briefs?brief=${NORTHWIND}`, "[data-line]");
    expect(document.title).toBe("Northwind - Briefs - Docket");
    expect(screen.queryByRole("complementary")).toBeNull();
    // A line that cites nothing is no link.
    expect(document.querySelector('[data-line="20"] a[data-row]')).toBeNull();
    fireEvent.click(document.querySelector('[data-row="18"]') as HTMLElement);
    await waitFor(() => expect(where(router)).toBe(`/briefs?brief=${NORTHWIND}&line=18#raw`));
    const panel = await screen.findByRole("complementary", { name: "The raw of line 18" });
    await waitFor(() => expect(within(panel).getByText("the words of line 18")).toBeTruthy());
    await waitFor(() => expect(document.activeElement?.id).toBe("panel-title"));
    expect(document.querySelector('[data-line="18"]')?.getAttribute("data-open")).toBe("1");
    const before = router.history.length;
    press("j");
    await waitFor(() => expect(where(router)).toBe(`/briefs?brief=${NORTHWIND}&line=22#raw`));
    expect(router.history.length).toBe(before);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Line 22" })).toBeTruthy());
    press("Escape");
    await waitFor(() => expect(where(router)).toBe(`/briefs?brief=${NORTHWIND}`));
    await waitFor(() => expect(document.activeElement?.getAttribute("data-row")).toBe("22"));
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("lands on a line from Claude's link, its raw open", async () => {
    await open(`/briefs?brief=${NORTHWIND}&line=17#raw`, "#raw");
    expect(screen.getByRole("heading", { name: "Line 17" })).toBeTruthy();
    const flagged = document.querySelector('[data-line="17"]') as HTMLElement;
    expect(flagged.getAttribute("data-flagged")).toBe("1");
    expect(flagged.textContent).toContain("changed under it");
  });
});

describe("a pick", () => {
  it("posts the rev the page showed, waits for its answer, says what it did and reads the page again", async () => {
    await open(`/briefs?brief=${FLEET}`, "[data-decision]");
    const weekly = document.querySelector(`[data-decision="${WEEKLY}"]`) as HTMLElement;
    const confirm = within(weekly).getByRole("button", { name: "Confirm" });
    const asked = reads.length;
    fireEvent.click(confirm);
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    expect((within(weekly).getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(said()).toContain("Confirmed: it is a decision now."));
    expect(posts).toEqual([{ brief: FLEET, decision: WEEKLY, rev: 1, pick: "confirm" }]);
    await waitFor(() => expect(reads.length).toBeGreaterThan(asked));
    await waitFor(() => expect(document.querySelector('[aria-labelledby="decisions-waiting"]')).toBeNull());
    const decided = document.querySelector('[aria-labelledby="decisions-current"]') as HTMLElement;
    expect(within(decided).getByText("a review every week")).toBeTruthy();
    expect(within(decided).queryByRole("button", { name: "Confirm" })).toBeNull();
    await waitFor(() => expect(document.activeElement?.id).toBe("decisions-title"));
  });

  it("is refused in the server's words when the decision moved since, and the page shows it as it stands", async () => {
    moved = true;
    const router = await open(`/briefs?brief=${FLEET}`, "[data-decision]");
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(`${WEEKLY} is confirmed already`));
    expect(said()).toBe("");
    await waitFor(() => expect(document.querySelector('[aria-labelledby="decisions-waiting"]')).toBeNull());
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    // Another entity's decisions never show it, nor does the first's when the page comes back to it.
    await act(async () => {
      await router.navigate({ href: `/briefs?brief=${PILOT}` });
    });
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Harbour pilot"));
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => {
      await router.navigate({ href: `/briefs?brief=${FLEET}` });
    });
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Fleet management"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("from a URL before the app says its click once and leaves the URL without it", async () => {
    const router = await open(`/briefs?brief=${FLEET}&done=confirm`, "[data-decision]");
    await waitFor(() => expect(where(router)).toBe(`/briefs?brief=${FLEET}`));
    expect(said()).toContain("Confirmed: it is a decision now.");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss the notice" }));
    expect(said()).toBe("");
  });
});
