/**
 * Today on a fake service: the day in the morning's order, the next meeting first, Today first in the sidebar and the
 * syncs' marks in its bar; one selection from the next meeting down into the items, where no item key ever writes on
 * a meeting and a call writes as page today; either panel from one read; a tick and the act bar; a saved item's row put
 * back wherever the day shows it, and a meeting's outcome saved as a meeting's; and an empty day. The service's own
 * answers are tested in Python (tests/test_web_today.py); here they are fixed, so each test reads what the page sent.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { resetDrafts } from "@/lib/drafts";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import type {
  Button,
  DayMeetingLine,
  ItemPanel,
  MeetingLine,
  MeetingPanel,
  Row,
  Today,
  TodayList,
} from "@/pages/day/api";
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

function row(ref: string, title: string, extra: Partial<Row> = {}): Row {
  return {
    ref,
    id: `01ID${ref}`,
    kind: "task",
    title,
    who: "Ada Example",
    stream: "pilot",
    meeting: null,
    state: "open",
    triage: null,
    woke: false,
    archived: false,
    due: { text: "today", title: "", tone: "today" },
    age: 1,
    callable: true,
    quick: buttons(null),
    ...extra,
  };
}

function line(ref: string, subject: string, time: string, extra: Partial<MeetingLine> = {}): MeetingLine {
  return {
    ref,
    id: `01ID${ref}`,
    subject,
    time,
    when: `Wed 21 Oct ${time}`,
    start: "2026-10-21T11:00:00+02:00",
    organiser: "",
    stream: "pilot",
    response: null,
    dim: false,
    over: false,
    now: false,
    points: 1,
    points_open: 1,
    items: 0,
    ...extra,
  };
}

function meeting(source: MeetingLine, extra: Partial<DayMeetingLine> = {}): DayMeetingLine {
  return { line: source, where: "", people: "", points: [], more_points: 0, items: [], more_items: 0, ...extra };
}

const LATE = row("A-0001", "Send Ada the dates", {
  triage: "chase",
  due: { text: "2d late", title: "", tone: "late" },
});
const ROOM = row("A-0002", "Book the room", { meeting: "M-0003" });
const DONE = row("A-0006", "Send the minutes", {
  state: "done",
  callable: false,
  quick: [],
  due: { text: "done 21 Oct", title: "", tone: "" },
});

const DAY: TodayList = {
  day: "Wednesday 21 October",
  lead: {
    label: "Now, until 12:30",
    meeting: meeting(line("M-0003", "Budget review", "11:00-12:30", { now: true, response: "tentative" }), {
      where: "Room 2",
      people: "Ben Sample and Ada Example",
      points: [
        { id: "01POINT1", text: "Ben: the spring numbers", answer: "", proposed: false },
        { id: "01POINT2", text: "Ask about the printer", answer: "", proposed: true },
      ],
      items: [ROOM],
    }),
  },
  due: [
    { key: "overdue", label: "Overdue", tone: "late", rows: [LATE], total: 1, selectable: true },
    { key: "today", label: "Due today", tone: "today", rows: [ROOM], total: 1, selectable: true },
  ],
  meetings: [
    {
      key: "2026-10-21",
      label: "Later today",
      meetings: [meeting(line("M-0009", "Supplier follow-up", "15:00-15:30"))],
    },
    {
      key: "2026-10-22",
      label: "Tomorrow, Thu 22 Oct",
      meetings: [meeting(line("M-0001", "Pilot sync", "10:00-10:30"))],
    },
  ],
  closed: { key: "closed", label: "Closed today", tone: "", rows: [DONE], total: 1, selectable: false },
  syncs: [
    {
      source: "chat",
      said: "synced 11:20",
      title: "Synced on ui-box",
      due: false,
      running: false,
      href: "/sync?source=chat",
    },
    { source: "mail", said: "never synced", title: "Never", due: true, running: false, href: "/sync?source=mail" },
  ],
  sync_problem: "",
  counts: "1 overdue, 1 due today; 2 meetings left today, 1 tomorrow",
  empty: "",
  streams: ["ops", "pilot"],
  until: "2099-01-01T00:00:00+00:00",
};

const TODAY: Today = {
  canonical: "/today",
  version: "v1",
  today: "2026-10-21",
  list: DAY,
  kinds: ["reply", "decision", "task", "invite", "waiting"],
  verdicts: buttons(null),
  snooze: [{ label: "Thu", until: "2026-10-22", title: "Snooze to the next working day" }],
  tomorrow: "2026-10-22",
  notice: null,
};

const EMPTY: Today = {
  ...TODAY,
  list: { ...DAY, lead: null, due: [], meetings: [], closed: null, counts: "nothing due", empty: "Nothing on today" },
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
    due: "2026-10-21",
    state_line: "open, no call yet",
    facts: "opened 2026-10-18 by claude; open 3 days",
    description: "",
    notes: "",
    outcome: "",
    source: { ref: "chat:C0TEST/1", label: "chat:C0TEST/1", kind: "record", href: "/search?hit=r:chat:C0TEST/1" },
    meeting: null,
    materials: [],
    history: [],
    verdicts: buttons(source.triage),
    snooze: TODAY.snooze,
    tomorrow: "2026-10-22",
    callable: true,
    closed: false,
  };
}

const MEETING: MeetingPanel = {
  ref: "M-0003",
  id: "01IDM-0003",
  subject: "Budget review",
  when: "Wed 21 Oct 11:00-12:30",
  start: "2026-10-21T11:00:00+02:00",
  end: "2026-10-21T12:30:00+02:00",
  organiser: null,
  where: "Room 2",
  response: "tentative",
  response_words: "tentative",
  stream: "ops",
  keyless: false,
  archived: false,
  over: false,
  rolls: "",
  attendees: [],
  recordings: [],
  points: [],
  open_points: 0,
  proposed: 0,
  agenda: "",
  outcome: "",
  notes: "",
  items: [],
  suggestions: null,
  materials: [],
  history: [],
  facts: "added by claude",
};

const SHELL = {
  vault: "vault",
  machine: "ui-box",
  zone: "Europe/Budapest",
  index_line: "",
  untriaged: 1,
  staging: 0,
  sync_due: ["mail"],
  problem: null,
};

const WROTE = {
  notice: {
    kind: "info",
    text: "Saved.",
    lines: ["A-0001: triage: claude"],
    button: "Undo",
    batch: "01B",
    then: "undo",
  },
  done: "01B",
  skipped: [],
  at: "A-0002",
  added: null,
  version: "v2",
};

interface Sent {
  path: string;
  query: string;
  body: Record<string, unknown> | null;
}

let sent: Sent[] = [];
let day: Today = TODAY;
let answers: Record<string, (body: Record<string, unknown> | null) => Response> = {};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const body = request.method === "POST" ? ((await request.clone().json()) as Record<string, unknown>) : null;
  sent.push({ path: url.pathname, query: url.search, body });
  const answer = answers[`${request.method} ${url.pathname}`];
  if (answer) return answer(body);
  if (url.pathname === "/api/web/shell") return json(SHELL);
  if (url.pathname === "/api/web/today") return json(day);
  if (url.pathname === "/api/web/today/panel") {
    const open = url.searchParams.get("open") ?? "";
    const shown = [LATE, ROOM, DONE].find((r) => r.ref === open);
    return json({
      canonical: `/today${url.search}`,
      item: shown ? item(shown) : null,
      meeting: open.startsWith("M-") ? { ...MEETING, ref: open, id: `01ID${open}` } : null,
      missing: "",
    });
  }
  if (url.pathname === "/api/web/ledger/changed") return json({ changed: false, version: "v1" });
  if (url.pathname === "/api/web/ledger/write") return json(WROTE);
  return json({ error: "not here" }, 404);
}

function writes(): Record<string, unknown>[] {
  return sent.filter((s) => s.path === "/api/web/ledger/write").map((s) => s.body ?? {});
}

function reads(): number {
  return sent.filter((s) => s.path === "/api/web/today").length;
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

async function open(url: string, waitFor_ = '[data-row="M-0003"]') {
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
  await waitFor(() => expect(document.querySelector(waitFor_)).toBeTruthy());
  return router;
}

function current(): string | undefined {
  return document.querySelector('li[aria-current="true"]')?.getAttribute("data-row") ?? undefined;
}

function rows(): string[] {
  return [...document.querySelectorAll("[data-row]")].map((r) => r.getAttribute("data-row") ?? "");
}

beforeAll(() => installKeys());

beforeEach(() => {
  sent = [];
  day = TODAY;
  answers = {};
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetDrafts();
});

describe("the day", () => {
  it("reads in the morning's order, the next meeting first, Today first in the sidebar, the syncs in its bar", async () => {
    await open("/today");
    expect(document.title).toBe("Today - Docket");
    const heads = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(heads).toEqual([
      "Now, until 12:30",
      "Overdue1Select all in Overdue",
      "Due today1Select all in Due today",
      "Later today1",
      "Tomorrow, Thu 22 Oct1",
      "Closed today1",
    ]);
    expect(rows()).toEqual(["M-0003", "A-0001", "A-0002", "M-0009", "M-0001", "A-0006"]);
    const lead = document.querySelector('[data-lead="1"]') as HTMLElement;
    expect(within(lead).getByText("Room 2, with Ben Sample and Ada Example")).toBeTruthy();
    expect(within(lead).getByRole("list", { name: "Open points on M-0003" }).textContent).toContain("proposed");
    expect(within(lead).getByRole("link", { name: "A-0002" }).getAttribute("href")).toBe("/today?open=A-0002");
    const pages = screen.getByRole("navigation", { name: "Pages" });
    const links = within(pages).getAllByRole("link");
    expect(links[0]?.getAttribute("href")).toBe("/");
    expect(links[1]?.getAttribute("href")).toBe("/today");
    expect(links[1]?.getAttribute("aria-current")).toBe("page");
    const syncs = screen.getByRole("list", { name: "Last sync per source" });
    expect(within(syncs).getByRole("link", { name: /chat/ }).getAttribute("href")).toBe("/sync?source=chat");
    expect(within(syncs).getByRole("link", { name: /mail/ }).getAttribute("data-due")).toBe("1");
    expect(screen.getByText("Ask Claude to sync it.")).toBeTruthy();
    // Today's poll asks with its page, so the cursor files and the runs count too (DR-0711).
    // A tab shown again asks at once rather than at the next tick.
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const polled = () => sent.filter((s) => s.path === "/api/web/ledger/changed").map((s) => s.query);
    await waitFor(() => expect(polled()).toContain("?v=v1&page=today"));
  });

  it("moves from the next meeting into the items; no item key writes on a meeting, and a call writes as today", async () => {
    await open("/today");
    key("j");
    expect(current()).toBe("M-0003");
    for (const name of ["1", "2", "0", "e", "x", "s", "Delete"]) key(name);
    await act(async () => await new Promise((done) => setTimeout(done, 20)));
    expect(writes()).toHaveLength(0);
    key("j");
    expect(current()).toBe("A-0001");
    key("2");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      op: "triage",
      items: ["A-0001"],
      verdict: "claude",
      page: "today",
      at: "A-0002",
    });
    await waitFor(() => expect(current()).toBe("A-0002"));
    expect((await screen.findByRole("status")).textContent).toContain("A-0001: triage: claude");
  });

  it("opens a meeting's panel or an item's from the one panel read, the panel following j", async () => {
    const router = await open("/today");
    key("j");
    key("Enter");
    await screen.findByRole("complementary", { name: "M-0003, Budget review" });
    expect(sent.some((s) => s.path === "/api/web/today/panel" && s.query === "?open=M-0003")).toBe(true);
    key("j", document.body);
    await screen.findByRole("complementary", { name: "A-0001, Send Ada the dates" });
    expect(router.state.location.search).toEqual({ open: "A-0001" });
  });

  it("ticks a due row and calls on it from the act bar, through a dry run", async () => {
    await open("/today");
    fireEvent.click(screen.getByRole("checkbox", { name: "Tick A-0001" }));
    const bar = await screen.findByRole("region", { name: "The ticked rows" });
    expect(within(bar).getByText("1 ticked")).toBeTruthy();
    fireEvent.click(within(bar).getByRole("button", { name: /^Done/ }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "done", items: ["A-0001"], bulk: true, page: "today" });
  });
});

describe("saves from Today's panels", () => {
  it("puts a saved item's row back wherever the day shows it, with no new read", async () => {
    answers["POST /api/web/ledger/save"] = () =>
      json({
        row: { ...ROOM, title: "Book the long hall" },
        panel: { ...item(ROOM), title: "Book the long hall" },
        before: "v1",
        version: "v2",
      });
    await open("/today?open=A-0002");
    const panel = await screen.findByRole("complementary", { name: "A-0002, Book the room" });
    const read = reads();
    const title = within(panel).getByLabelText("Title") as HTMLInputElement;
    fireEvent.change(title, { target: { value: "Book the long hall" } });
    fireEvent.blur(title);
    await waitFor(() => expect(sent.filter((s) => s.path === "/api/web/ledger/save")).toHaveLength(1));
    expect(sent.find((s) => s.path === "/api/web/ledger/save")?.body).toEqual({
      op: "set",
      items: ["A-0002"],
      fields: { title: "Book the long hall" },
      page: "today",
    });
    const lead = document.querySelector('[data-lead="1"]') as HTMLElement;
    await waitFor(() => expect(within(lead).getByText("Book the long hall")).toBeTruthy());
    const due = document.querySelector('[data-row="A-0002"]') as HTMLElement;
    expect(due.textContent).toContain("Book the long hall");
    await act(async () => await new Promise((done) => setTimeout(done, 50)));
    expect(reads()).toBe(read);
  });

  it("saves a meeting's outcome from its panel as a meeting's, its line put back", async () => {
    answers["POST /api/web/ledger/save"] = () =>
      json({
        line: { ...(DAY.lead?.meeting.line as MeetingLine), subject: "Budget review, moved" },
        panel: { ...MEETING, outcome: "Numbers by Friday" },
        before: "v1",
        version: "v2",
      });
    await open("/today?open=M-0003");
    const panel = await screen.findByRole("complementary", { name: "M-0003, Budget review" });
    const outcome = within(panel).getByLabelText("Outcome") as HTMLTextAreaElement;
    fireEvent.change(outcome, { target: { value: "Numbers by Friday" } });
    fireEvent.blur(outcome);
    await waitFor(() => expect(sent.filter((s) => s.path === "/api/web/ledger/save")).toHaveLength(1));
    expect(sent.find((s) => s.path === "/api/web/ledger/save")?.body).toEqual({
      op: "set",
      items: ["M-0003"],
      fields: { outcome: "Numbers by Friday" },
      page: "today",
    });
    const lead = document.querySelector('[data-lead="1"]') as HTMLElement;
    await waitFor(() => expect(within(lead).getByText("Budget review, moved")).toBeTruthy());
  });
});

describe("an empty day", () => {
  it("says so, keeps its syncs and points to the Queue", async () => {
    day = EMPTY;
    await open("/today", "#main h1");
    expect(await screen.findByText("Nothing on today")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open the Queue/ }).getAttribute("href")).toBe("/queue");
    expect(screen.getByRole("list", { name: "Last sync per source" })).toBeTruthy();
    expect(rows()).toEqual([]);
  });
});
