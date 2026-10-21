/**
 * The Queue page on a fake service: its groups in the order the read gives them, a call's stamp heading each group
 * by call, the one selection moving down the queue across the groups, a verdict's key on the selected row and on
 * ticked rows by the old bulk rule, each write sent as page queue, and the sidebar's untriaged count. The service's
 * own answers, the order among them, are tested in Python (tests/test_web_queue.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { resetDrafts } from "@/lib/drafts";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import type { Button, Group, Queue, Row } from "@/pages/day/api";
import { createAppRouter } from "@/router";

const VERDICTS = ["discuss", "claude", "me", "chase"] as const;

function buttons(held: string | null): Button[] {
  return VERDICTS.map((verdict, at) => ({
    verdict,
    label: verdict[0]?.toUpperCase() + verdict.slice(1),
    writes: held === verdict ? null : verdict,
    key: String(at + 1),
    title: verdict,
    pressed: held === verdict,
  }));
}

function row(ref: string, title: string, triage: string | null, kind = "task", woke = false): Row {
  return {
    ref,
    id: `01ID${ref}`,
    kind,
    title,
    who: "Ada Example",
    stream: "pilot",
    meeting: null,
    state: woke ? "snoozed" : "open",
    triage,
    woke,
    archived: false,
    due: woke ? { text: "snooze ended", title: "", tone: "today" } : { text: "", title: "", tone: "" },
    age: 1,
    callable: true,
    quick: buttons(triage),
  };
}

function group(key: string, label: string, rows: Row[], verdict: string | null = null): Group {
  return { key, label, tone: "", rows, total: rows.length, selectable: true, verdict };
}

const QUEUE: Queue = {
  canonical: "/queue",
  version: "v1",
  today: "2026-10-20",
  list: {
    groups: [
      group("woke", "Snooze ended", [row("A-0007", "Renew the badge", null, "task", true)]),
      group("untriaged", "Untriaged", [row("A-0002", "Book the room", null), row("A-0003", "Pick the venue", null)]),
      group("verdict-claude", "Claude", [row("A-0004", "Plan the visit", "claude", "reply")], "claude"),
      group("verdict-chase", "Chase", [row("A-0001", "Send Ada the dates", "chase", "waiting")], "chase"),
    ],
    counts: "5 open: 3 untriaged (1 with their snooze ended), 1 claude, 1 chase; 0 overdue",
    chips: [],
    shown: 5,
    total: 5,
    more: null,
    empty: "",
    streams: ["pilot"],
  },
  filters: { q: "", stream: "", kind: "", who: "", k: 100, state: "open", group: "due", archived: false },
  kinds: ["reply", "decision", "task", "invite", "waiting"],
  verdicts: buttons(null),
  snooze: [{ label: "Wed", until: "2026-10-21", title: "Snooze to the next working day" }],
  tomorrow: "2026-10-21",
  notice: null,
};

const SHELL = {
  vault: "vault",
  machine: "ui-box",
  zone: "Europe/Budapest",
  index_line: "",
  untriaged: 3,
  staging: 0,
  sync_due: [],
  problem: null,
};

interface Sent {
  path: string;
  query: string;
  body: Record<string, unknown> | null;
}

let sent: Sent[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const body = request.method === "POST" ? ((await request.clone().json()) as Record<string, unknown>) : null;
  sent.push({ path: url.pathname, query: url.search, body });
  if (url.pathname === "/api/web/shell") return json(SHELL);
  if (url.pathname === "/api/web/queue") return json(QUEUE);
  if (url.pathname === "/api/web/ledger/changed") return json({ changed: false, version: "v1" });
  if (url.pathname === "/api/web/ledger/write") {
    return json({
      notice: { kind: "info", text: "Saved.", lines: [], button: "Undo", batch: "01BATCH", then: "undo" },
      done: "01BATCH",
      skipped: [],
      at: body?.at ?? null,
      added: null,
      version: "v2",
    });
  }
  return json({ error: "not here" }, 404);
}

function writes(): Record<string, unknown>[] {
  return sent.filter((s) => s.path === "/api/web/ledger/write").map((s) => s.body ?? {});
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

async function open(url: string) {
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
  await waitFor(() => expect(document.querySelector('[data-row="A-0007"]')).toBeTruthy());
  return router;
}

function current(): string | undefined {
  return document.querySelector('li[aria-current="true"]')?.getAttribute("data-row") ?? undefined;
}

beforeAll(() => installKeys());

beforeEach(() => {
  sent = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetDrafts();
});

describe("the Queue", () => {
  it("lists the groups in the read's order, each call's group under its stamp, and reads its own route", async () => {
    await open("/queue");
    expect(screen.getByRole("heading", { level: 1, name: "Queue" })).toBeTruthy();
    expect(document.title).toBe("Queue - Docket");
    const heads = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(heads).toEqual([
      expect.stringMatching(/^Snooze ended1/),
      expect.stringMatching(/^Untriaged2/),
      expect.stringMatching(/^claudeClaude does it1/),
      expect.stringMatching(/^chasenudge someone1/),
    ]);
    expect(sent.some((s) => s.path === "/api/web/queue")).toBe(true);
    expect(sent.some((s) => s.path === "/api/web/ledger")).toBe(false);
    // Each row shows its kind as text in the kind's tab, never by colour alone.
    const reply = document.querySelector('[data-row="A-0004"] [data-slot="kind"]');
    expect(reply?.textContent).toBe("reply");
    expect(reply?.getAttribute("data-kind")).toBe("reply");
    // No state chips: the Queue lists the open items only.
    expect(screen.queryByRole("navigation", { name: "States" })).toBeNull();
    expect(screen.getByText(/^5 open: 3 untriaged/)).toBeTruthy();
  });

  it("puts the Queue after Today among the day pages in the sidebar, with its untriaged count", async () => {
    await open("/queue");
    const pages = screen.getByRole("navigation", { name: "Pages" });
    // The brand comes first, then the day pages, Today leading them (DR-0669).
    const links = within(pages).getAllByRole("link");
    expect(links.slice(1, 4).map((l) => l.getAttribute("href"))).toEqual(["/today", "/queue", "/ledger"]);
    expect(links[2]?.getAttribute("aria-current")).toBe("page");
    await waitFor(() => expect(links[2]?.textContent).toContain("3 untriaged"));
  });

  it("moves down the queue across its groups with j, and a verdict's key writes as page queue and moves on", async () => {
    await open("/queue");
    key("j");
    key("j");
    expect(current()).toBe("A-0002");
    key("j");
    key("j");
    expect(current()).toBe("A-0004");
    key("k");
    expect(current()).toBe("A-0003");
    key("2");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      op: "triage",
      items: ["A-0003"],
      verdict: "claude",
      page: "queue",
      at: "A-0004",
      bulk: false,
    });
    await waitFor(() => expect(current()).toBe("A-0004"));
  });

  it("calls ticked rows by the bulk rule: set on all while one lacks it, cleared once all hold it", async () => {
    await open("/queue");
    key("j");
    key("j");
    key("j");
    key("j");
    key("x");
    key("j");
    key("x");
    const bar = await screen.findByRole("region", { name: "The ticked rows" });
    expect(within(bar).getByText("2 ticked")).toBeTruthy();
    // A-0004 holds claude and A-0001 chase: 2 sets claude on both.
    key("2", document.body);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "triage", items: ["A-0004", "A-0001"], verdict: "claude", bulk: true });
    expect(writes()[0]?.page).toBe("queue");
  });

  it("clears a call when every ticked row holds it", async () => {
    await open("/queue");
    key("j");
    key("j");
    key("j");
    key("j");
    key("x");
    await screen.findByRole("region", { name: "The ticked rows" });
    key("2", document.body);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "triage", items: ["A-0004"], verdict: null, bulk: true });
  });
});
