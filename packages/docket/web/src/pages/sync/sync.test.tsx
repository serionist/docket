/**
 * The Sync page on a fake service: the parts in their order with the server's canonical URL written back; a run opened
 * from its row and by the keys, its card in the panel and Esc closing it; while a run is open the runs part read again
 * every 5 seconds, and the whole page and the sidebar read again once it closes; a source narrowed to nothing saying so
 * with the way back; a failed read with a retry that asks again; and no write anywhere. The service's own answers are
 * tested in Python (tests/test_web_sync.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import type { RunRead, RunsRead, SyncPage } from "./api";
import { withRun } from "./api";

const CLOSED = "run-20261020T081500-a1b2c3d4e5f60718";
const DROPPED = "run-20261020T063000-0f1e2d3c4b5a6978";
const OPEN = "run-20261020T093000-0011223344556677";

function run(id: string, fields: Partial<RunRead> = {}): RunRead {
  return {
    id,
    source: "calls",
    state: "closed",
    word: "closed",
    badges: [],
    opened: "20 Oct 2026 08:11",
    took: "4 min",
    facts: ["9 captures, 37 KB", "mark moved to 20 Oct 08:15"],
    why: null,
    lists: [{ title: "expired", ids: ["c-301", "c-302"], more: 0 }],
    url: `/sync?run=${id}`,
    ...fields,
  };
}

let live = false;
let failing = false;
let reads: string[] = [];
let posts = 0;
let shellReads = 0;

function runsOf(query: URLSearchParams): RunsRead {
  const recent = [
    run(CLOSED),
    run(DROPPED, { source: "memo", state: "dropped", word: "mapper", why: "the mapper failed" }),
  ];
  const open = live ? [run(OPEN, { source: "memo", state: "open", word: "open", took: "2 min so far" })] : [];
  const asked = query.get("run");
  const focus = [...open, ...recent].find((r) => r.id === asked) ?? null;
  return { as_of: "20 Oct 2026 09:30", live, focus, open, recent, more: null };
}

function pageOf(query: URLSearchParams): SyncPage {
  const source = query.get("source");
  const runs = runsOf(query);
  const narrowed = source === "chat";
  return {
    // The server's one canonical form: source, run and k in that order, k's default and anything else left out.
    canonical: withRun(
      {
        ...(source ? { source } : {}),
        ...(query.get("k") && query.get("k") !== "20" ? { k: query.get("k") ?? "" } : {}),
      },
      query.get("run") ?? undefined,
    ),
    lead: "1 source due a sync; 0 runs open; last whole run 1 h ago",
    chips: [
      { label: "All", url: "/sync", on: !source },
      { label: "chat", url: "/sync?source=chat", on: source === "chat" },
    ],
    notes: [],
    needs: narrowed
      ? []
      : [
          {
            source: "memo",
            kind: "due",
            word: "due a sync",
            line: "due: never synced",
            paste: 'Sync memo: run_open("memo")',
          },
        ],
    paste_all: null,
    live_sources: 2,
    failures: narrowed
      ? []
      : [
          {
            kind: "mapper",
            label: "mapper",
            source: "memo",
            what: "the mapper failed",
            todo: "run the mapper's tests",
            when: "20 Oct 2026 06:30",
            ids: [],
            run: DROPPED,
            run_url: `/sync?run=${DROPPED}`,
            severity: "error",
          },
        ],
    runs: narrowed ? { ...runs, open: [], recent: [] } : runs,
    cursors: [],
    machine: "ui-box",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (request.method !== "GET") posts += 1;
  if (url.pathname === "/api/web/shell") {
    shellReads += 1;
    return Promise.resolve(
      json({
        vault: "v",
        machine: "m",
        zone: "UTC",
        index_line: "",
        untriaged: 0,
        staging: 0,
        sync_due: [],
        problem: null,
      }),
    );
  }
  reads.push(`${url.pathname}${url.search}`);
  if (url.pathname === "/api/web/sync/runs") {
    return Promise.resolve(json({ canonical: "/sync", runs: runsOf(url.searchParams) }));
  }
  if (url.pathname !== "/api/web/sync") return Promise.resolve(json({ error: "not here" }, 404));
  if (failing) return Promise.resolve(json({ error: "Something failed in Docket." }, 500));
  if (url.searchParams.get("k") === "0") return Promise.resolve(json({ error: "k is a whole number" }, 400));
  return Promise.resolve(json(pageOf(url.searchParams)));
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

async function open(url: string, settled: string, timeout = 1000) {
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
  await waitFor(() => expect(document.querySelector(settled)).toBeTruthy(), { timeout });
  return router;
}

function where(router: Awaited<ReturnType<typeof open>>): string {
  const { pathname, search } = router.history.location;
  return `${pathname}${search}`;
}

beforeAll(() => installKeys());

beforeEach(() => {
  live = false;
  failing = false;
  reads = [];
  posts = 0;
  shellReads = 0;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the Sync page", () => {
  it("shows its parts in order and writes the canonical URL back", async () => {
    const router = await open("/sync?k=20&other=x", "[data-lead]");
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.firstChild?.textContent ?? "");
    expect(headings.slice(0, 4)).toEqual(["Needs", "Failures", "Runs", "Cursors"]);
    expect(screen.getByText('Sync memo: run_open("memo")')).toBeTruthy();
    await waitFor(() => expect(where(router)).toBe("/sync"));
    expect(posts).toBe(0);
  });

  it("opens a run's card in the panel from its row and from the keys, and Esc closes it", async () => {
    const router = await open("/sync", "[data-lead]");
    fireEvent.click(within(document.querySelector(`[data-row="${CLOSED}"]`) as HTMLElement).getByRole("link"));
    await waitFor(() => expect(where(router)).toBe(`/sync?run=${CLOSED}`));
    const panel = await screen.findByRole("complementary");
    expect(within(panel).getByText("c-301")).toBeTruthy();
    await waitFor(() => expect(document.activeElement?.id).toBe("panel-title"));
    fireEvent.keyDown(document, { key: "j" });
    await waitFor(() => expect(where(router)).toBe(`/sync?run=${DROPPED}`));
    await waitFor(() =>
      expect(within(screen.getByRole("complementary")).getByRole("alert").textContent).toBe("the mapper failed"),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(where(router)).toBe("/sync"));
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("reads the runs part while a run is open, and the whole page and the sidebar once it closes", async () => {
    live = true;
    await open("/sync", "[data-lead]");
    expect(screen.getByText(/refreshes while a run is open/)).toBeTruthy();
    const pageReads = () => reads.filter((r) => r.startsWith("/api/web/sync?") || r === "/api/web/sync").length;
    const before = pageReads();
    const shellBefore = shellReads;
    await waitFor(() => expect(reads.some((r) => r.startsWith("/api/web/sync/runs"))).toBe(true), { timeout: 7000 });
    live = false;
    await waitFor(() => expect(pageReads()).toBeGreaterThan(before), { timeout: 7000 });
    await waitFor(() => expect(shellReads).toBeGreaterThan(shellBefore));
    await waitFor(() => expect(screen.queryByText(/refreshes while a run is open/)).toBeNull());
  }, 20000);

  it("says a source it narrowed to has nothing, with the way back to every source", async () => {
    await open("/sync?source=chat", "[data-lead]");
    expect(screen.getByText(/No run on this machine for chat/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Every source" }).length).toBeGreaterThan(0);
  });

  it("shows a failed read with a retry that asks again, never an empty page", { timeout: 15000 }, async () => {
    failing = true;
    // A 5xx is asked three times before the page says it failed.
    await open("/sync", "[role=alert]", 8000);
    expect(screen.queryByText(/No run yet/)).toBeNull();
    const asked = reads.length;
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(document.querySelector("[data-lead]")).toBeTruthy());
    expect(reads.length).toBeGreaterThan(asked);
  });

  it("gives a URL it refused the way back to the whole page", async () => {
    const router = await open("/sync?k=0", "[role=alert]");
    expect(screen.getByRole("alert").textContent).toContain("k is a whole number");
    fireEvent.click(screen.getByRole("button", { name: "The whole page" }));
    await waitFor(() => expect(where(router)).toBe("/sync"));
  });

  it("writes a run's URL with the source and k kept, in the server's order", () => {
    expect(withRun({ k: "40", source: "memo" }, CLOSED)).toBe(`/sync?source=memo&run=${CLOSED}&k=40`);
    expect(withRun({ run: CLOSED }, undefined)).toBe("/sync");
  });
});
