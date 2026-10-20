/**
 * The Ledger page on a fake service: the keys and the one selection, the writes each key and button sends, the bulk
 * rule, the notice's undo, the leave guard and a field's save on change. The service's own answers are tested in
 * Python (tests/test_web_ledger.py); here they are fixed, so each test reads what the page sent.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { resetDrafts } from "@/lib/drafts";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import type { Button, ItemPanel, Ledger, Row } from "./api";

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

function row(ref: string, title: string, triage: string | null, due = ""): Row {
  return {
    ref,
    id: `01ID${ref}`,
    kind: "task",
    title,
    who: "Ada Example",
    stream: "pilot",
    meeting: null,
    state: "open",
    triage,
    woke: false,
    archived: false,
    due: { text: due, title: "", tone: "" },
    age: 2,
    callable: true,
    quick: buttons(triage),
  };
}

const ROWS = [row("A-0001", "Send Ada the dates", "chase", "2d late"), row("A-0002", "Book the room", null)];
const LATER = row("A-0003", "Pick the venue", null, "Fri");
const SNOOZE = [{ label: "Wed", until: "2026-10-21", title: "Snooze to the next working day" }];

const LEDGER: Ledger = {
  canonical: "/ledger",
  version: "v1",
  today: "2026-10-20",
  list: {
    groups: [
      { key: "overdue", label: "Overdue", tone: "late", rows: ROWS, total: 2, selectable: true },
      { key: "week", label: "Due this week", tone: "", rows: [LATER], total: 1, selectable: true },
    ],
    counts: "3 open, 0 snoozed, 0 closed; 1 overdue",
    chips: [
      { key: "open", label: "Open", count: 3, on: true },
      { key: "all", label: "All", count: 3, on: false },
    ],
    shown: 3,
    total: 3,
    more: null,
    empty: "",
    streams: ["pilot"],
  },
  filters: { q: "", stream: "", kind: "", who: "", k: 100, state: "open", group: "due", archived: false },
  kinds: ["reply", "decision", "task", "invite", "waiting"],
  verdicts: buttons(null),
  snooze: SNOOZE,
  tomorrow: "2026-10-21",
  notice: null,
};

function item(source: Row): ItemPanel {
  return {
    ref: source.ref,
    id: source.id,
    kind: source.kind,
    title: source.title,
    who: source.who,
    stream: source.stream,
    state: "open",
    triage: source.triage,
    woke: false,
    archived: false,
    due: "",
    state_line: "open, no call yet",
    facts: "opened 2026-10-18 by claude; open 2 days",
    description: "",
    notes: "",
    outcome: "",
    source: { ref: "chat:C0TEST/1", label: "chat:C0TEST/1", kind: "record", href: "/search?hit=r:chat:C0TEST/1" },
    meeting: null,
    materials: [],
    history: [
      {
        when: "18 Oct 2026 09:00",
        stamp: "2026-10-18T07:00:00+00:00",
        by: "claude",
        via: "cli",
        line: "added",
        undone_by: null,
        button: null,
        op: `01OP${source.ref}`,
      },
    ],
    verdicts: buttons(source.triage),
    snooze: SNOOZE,
    tomorrow: "2026-10-21",
    callable: true,
    closed: false,
  };
}

const SHELL = {
  vault: "vault",
  machine: "ui-box",
  zone: "Europe/Budapest",
  index_line: "",
  untriaged: 2,
  staging: 0,
  sync_due: [],
  problem: null,
};

interface Sent {
  method: string;
  path: string;
  query: string;
  body: Record<string, unknown> | null;
}

let sent: Sent[] = [];
let answers: Record<string, () => Response> = {};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function wrote(batch: string, at: string | null, said: "Saved." | "Undone." = "Saved.") {
  return json({
    notice: {
      kind: "info",
      text: said,
      lines: ["A-0001: triage: claude"],
      button: said === "Saved." ? "Undo" : "Redo",
      batch,
      then: said === "Saved." ? "undo" : "redo",
    },
    done: batch,
    skipped: [],
    at,
    added: null,
    version: "v2",
  });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const body = request.method === "POST" ? ((await request.clone().json()) as Record<string, unknown>) : null;
  sent.push({ method: request.method, path: url.pathname, query: url.search, body });
  const own = answers[`${request.method} ${url.pathname}`];
  if (own) return own();
  if (url.pathname === "/api/web/shell") return json(SHELL);
  if (url.pathname === "/api/web/ledger") return json(LEDGER);
  if (url.pathname === "/api/web/ledger/panel") {
    const open = url.searchParams.get("open");
    const found = [...ROWS, LATER].find((r) => r.ref === open);
    // The server's canonical of a query already canonical is the query itself.
    return json({ canonical: `/ledger${url.search}`, panel: found ? item(found) : null, missing: "" });
  }
  if (url.pathname === "/api/web/ledger/changed") return json({ changed: false, version: "v1" });
  if (url.pathname === "/api/web/ledger/write") return wrote("01BATCH", (body?.at as string | null) ?? null);
  return json({ error: "not here" }, 404);
}

function writes(): Record<string, unknown>[] {
  return sent.filter((s) => s.path === "/api/web/ledger/write").map((s) => s.body ?? {});
}

function key(
  name: string,
  init: KeyboardEventInit = {},
  target: EventTarget = document.activeElement ?? document.body,
) {
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true, ...init }));
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
  await waitFor(() => expect(document.querySelector('[data-row="A-0001"]')).toBeTruthy());
  return router;
}

function current(): string | undefined {
  return document.querySelector('li[aria-current="true"]')?.getAttribute("data-row") ?? undefined;
}

beforeAll(() => installKeys());

beforeEach(() => {
  sent = [];
  answers = {};
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetDrafts();
});

describe("the Ledger's list", () => {
  it("shows the groups and rows the read gave, and writes back the server's canonical URL", async () => {
    const router = await open("/ledger?state=open&x=y");
    expect(screen.getByRole("heading", { level: 1, name: "Ledger" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: /Overdue/ })).toBeTruthy();
    expect(document.title).toBe("Ledger - Docket");
    await waitFor(() => expect(router.state.location.href).toBe("/ledger"));
    const read = sent.find((s) => s.path === "/api/web/ledger");
    expect(read?.query).toBe("?state=open&x=y");
    expect(screen.getByText("3 of 3 items").getAttribute("aria-live")).toBe("polite");
  });

  it("moves the one selection with j and k, keeping the focus on it, and opens it with Enter", async () => {
    const router = await open("/ledger");
    key("j");
    expect(current()).toBe("A-0001");
    expect(document.activeElement?.getAttribute("data-row")).toBe("A-0001");
    key("j");
    key("k");
    expect(current()).toBe("A-0001");
    key("Enter");
    await waitFor(() => expect(router.state.location.search).toEqual({ open: "A-0001" }));
    await screen.findByRole("complementary", { name: "A-0001, Send Ada the dates" });
    // The panel follows the selection, and the focus stays on the row.
    key("j");
    await waitFor(() => expect(router.state.location.search).toEqual({ open: "A-0002" }));
    expect(document.activeElement?.getAttribute("data-row")).toBe("A-0002");
    key("Escape");
    await waitFor(() => expect(router.state.location.search).toEqual({ at: "A-0002" }));
  });
});

describe("a call by key", () => {
  it("writes on the selected row, names the row after it, moves there and offers Undo, which u presses", async () => {
    await open("/ledger");
    key("j");
    key("2");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({ op: "triage", items: ["A-0001"], verdict: "claude", at: "A-0002", bulk: false });
    const notice = await screen.findByRole("status");
    expect(notice.textContent).toContain("Saved.");
    await waitFor(() => expect(current()).toBe("A-0002"));
    expect(document.activeElement?.getAttribute("data-row")).toBe("A-0002");
    answers["POST /api/web/ledger/write"] = () => wrote("01UNDO", null, "Undone.");
    key("u");
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1]).toEqual({ op: "undo", batch: "01BATCH", said: "undo", at: null, bulk: false });
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Undone."));
    key("z", { ctrlKey: true });
    await waitFor(() => expect(writes()).toHaveLength(3));
    expect(writes()[2]).toMatchObject({ op: "undo", batch: "01UNDO", said: "redo" });
  });

  it("clears the call a row holds, and leaves a key held down alone", async () => {
    await open("/ledger");
    key("j");
    key("4", { repeat: true });
    key("4");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "triage", items: ["A-0001"], verdict: null });
  });

  it("acts on the ticked rows by the bulk rule, through the act bar", async () => {
    await open("/ledger");
    key("j");
    key("x");
    key("j");
    key(" ");
    const bar = await screen.findByRole("region", { name: "The ticked rows" });
    expect(within(bar).getByText("2 ticked")).toBeTruthy();
    // One of the two holds chase, so 4 sets it on both.
    key("4");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      op: "triage",
      items: ["A-0001", "A-0002"],
      verdict: "chase",
      at: "A-0003",
      bulk: true,
    });
    await waitFor(() => expect(screen.queryByRole("region", { name: "The ticked rows" })).toBeNull());
  });

  it("clears a call every ticked row holds, and Escape clears the ticks", async () => {
    await open("/ledger");
    fireEvent.click(screen.getByRole("checkbox", { name: "Tick A-0001" }));
    const bar = await screen.findByRole("region", { name: "The ticked rows" });
    expect(within(bar).getByRole("button", { name: /Chase/ }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(bar).getByRole("button", { name: /Chase/ }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "triage", items: ["A-0001"], verdict: null, bulk: true });
    fireEvent.click(screen.getByRole("checkbox", { name: "Tick A-0003" }));
    await screen.findByRole("region", { name: "The ticked rows" });
    key("Escape", {}, document.body);
    await waitFor(() => expect(screen.queryByRole("region", { name: "The ticked rows" })).toBeNull());
  });

  it("shows the writer's refusal and writes nothing more", async () => {
    answers["POST /api/web/ledger/write"] = () => json({ error: "a drop needs a note: why", alikes: [] }, 400);
    await open("/ledger");
    key("j");
    key("e");
    expect((await screen.findByRole("alert")).textContent).toBe("a drop needs a note: why");
    expect(current()).toBe("A-0001");
  });
});

describe("the open item", () => {
  it("asks before a call carries the panel off text typed in it; Stay writes nothing, Leave writes", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await open("/ledger?open=A-0001");
    const panel = await screen.findByRole("complementary", { name: "A-0001, Send Ada the dates" });
    fireEvent.change(within(panel).getByLabelText("Add a line"), { target: { value: "a line not yet added" } });
    const claude = within(panel).getByRole("button", { name: /^Claude/ });
    fireEvent.click(claude);
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(writes()).toHaveLength(0);
    fireEvent.click(claude);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(writes()[0]).toMatchObject({ op: "triage", items: ["A-0001"], verdict: "claude", at: "A-0002" });
  });

  it("saves a field when it is left, keeping it unsaved with the writer's words when refused", async () => {
    answers["POST /api/web/ledger/save"] = () => json({ error: "who can't be cleared", alikes: [] }, 400);
    await open("/ledger?open=A-0001");
    const panel = await screen.findByRole("complementary", { name: "A-0001, Send Ada the dates" });
    const who = within(panel).getByLabelText("Who") as HTMLInputElement;
    fireEvent.change(who, { target: { value: "" } });
    expect(who.closest("[data-field]")?.hasAttribute("data-unsaved")).toBe(true);
    fireEvent.blur(who);
    expect((await within(panel).findByRole("alert")).textContent).toBe("who can't be cleared");
    const save = sent.find((s) => s.path === "/api/web/ledger/save");
    expect(save?.body).toEqual({ op: "set", items: ["A-0001"], fields: { who: "" } });
    expect(who.value).toBe("");
    expect(who.closest("[data-field]")?.hasAttribute("data-unsaved")).toBe(true);
  });

  it("refetches the page's reads after a save only when another write landed since the page read", async () => {
    const source = ROWS[0] as Row;
    const saved = (before: string, version: string) => () =>
      json({ row: source, panel: { ...item(source), who: "Ben Sample" }, before, version });
    const lists = () => sent.filter((s) => s.path === "/api/web/ledger").length;
    await open("/ledger?open=A-0001");
    const panel = await screen.findByRole("complementary", { name: "A-0001, Send Ada the dates" });
    const who = within(panel).getByLabelText("Who") as HTMLInputElement;
    const leave = async (value: string) => {
      const saves = sent.filter((s) => s.path === "/api/web/ledger/save").length;
      fireEvent.change(who, { target: { value } });
      fireEvent.blur(who);
      await waitFor(() => expect(sent.filter((s) => s.path === "/api/web/ledger/save")).toHaveLength(saves + 1));
      await waitFor(() => expect(who.closest("[data-field]")?.hasAttribute("data-unsaved")).toBe(false));
    };
    const read = lists();
    // The save's write started from the version the page read: its own write is all that changed.
    answers["POST /api/web/ledger/save"] = saved("v1", "v2");
    await leave("Ben Sample");
    await act(async () => await new Promise((done) => setTimeout(done, 50)));
    expect(lists()).toBe(read);
    // A write from elsewhere landed while the field held the poll: the save's write started past the page's v2.
    answers["POST /api/web/ledger/save"] = saved("v3", "v4");
    await leave("Cleo Example");
    await waitFor(() => expect(lists()).toBe(read + 1));
  });

  it("opens the drop form with its why focused on Delete, and drops with Enter there", async () => {
    const router = await open("/ledger");
    key("j");
    key("Delete");
    await waitFor(() => expect(router.state.location.search).toEqual({ open: "A-0001", drop: "1" }));
    const why = (await screen.findByLabelText("Why")) as HTMLInputElement;
    await waitFor(() => expect(document.activeElement?.id).toBe(why.id));
    // A key typed there is the why's, never a key's command.
    key("e", {}, why);
    expect(writes()).toHaveLength(0);
    fireEvent.change(why, { target: { value: "replied in the thread" } });
    fireEvent.keyDown(why, { key: "Enter" });
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "drop", items: ["A-0001"], note: "replied in the thread" });
  });
});

describe("the keys dialog", () => {
  it("lists the Ledger's keys before the ones that work everywhere", async () => {
    await open("/ledger");
    fireEvent.click(screen.getByRole("button", { name: "Keys (?)" }));
    const dialog = await screen.findByRole("dialog", { name: "Keys" });
    expect([...dialog.querySelectorAll("h3")].map((h) => h.textContent)).toEqual(["The Ledger", "Everywhere"]);
    expect(dialog.textContent).toContain("drop: say why");
  });

  it("gives the focus back to the selected row once it closes, so the keys carry on there", async () => {
    await open("/ledger");
    key("j");
    key("?");
    const dialog = await screen.findByRole("dialog", { name: "Keys" });
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement?.getAttribute("data-row")).toBe("A-0001"));
  });
});
