/**
 * Meetings on a fake service: the days in the read's order with the earlier ones after their line, the one selection
 * moving across the days, no item key ever writing on a meeting, each point button's body naming its point by id, the
 * pencilled points removed all at once, an answer typed and laid aside or guarded, the suggestions asked for in the
 * URL, and the outcome saved on leaving with the panel's suggestions kept. The service's own answers are tested in
 * Python (tests/test_web_meetings.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { resetDrafts } from "@/lib/drafts";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import type { MeetingLine, MeetingPanel, Meetings, PointLine } from "@/pages/day/api";
import { createAppRouter } from "@/router";

function line(ref: string, subject: string, time: string, extra: Partial<MeetingLine> = {}): MeetingLine {
  return {
    ref,
    id: `01ID${ref}`,
    subject,
    time,
    when: `Wed 21 Oct ${time}`,
    start: "2026-10-21T10:00:00+02:00",
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

const LIST: Meetings = {
  canonical: "/meetings",
  version: "v1",
  today: "2026-10-21",
  list: {
    days: [
      {
        key: "2026-10-21",
        label: "Today, Wed 21 Oct",
        ahead: true,
        meetings: [
          line("M-0002", "Daily stand-up", "08:00-08:15", { over: true, dim: true, points: 0, points_open: 0 }),
          line("M-0003", "Budget review", "10:00-12:00", { now: true, response: "tentative" }),
        ],
      },
      {
        key: "2026-10-22",
        label: "Tomorrow, Thu 22 Oct",
        ahead: true,
        meetings: [line("M-0001", "Pilot sync", "10:00-10:30")],
      },
      {
        key: "2026-10-20",
        label: "Yesterday, Tue 20 Oct",
        ahead: false,
        meetings: [line("M-0004", "Supplier call", "15:00-16:00")],
      },
    ],
    counts: "4 meetings from Wed 7 Oct to Wed 4 Nov; 3 points open",
    shown: 4,
    empty: "",
    streams: ["ops", "pilot"],
  },
  filters: { q: "", stream: "", kind: "", who: "", k: 100, state: "open", group: "due", archived: false },
  kinds: ["reply", "decision", "task", "invite", "waiting"],
  notice: null,
};

function point(id: string, text: string, extra: Partial<PointLine> = {}): PointLine {
  return {
    id,
    n: 1,
    ref: "M-0001#1",
    state: "open",
    text,
    answer: "",
    proposed: false,
    item: null,
    came_from: null,
    went_to: null,
    ...extra,
  };
}

const PANEL: MeetingPanel = {
  ref: "M-0001",
  id: "01IDM-0001",
  subject: "Pilot sync",
  when: "Thu 22 Oct 10:00-10:30",
  start: "2026-10-22T10:00:00+02:00",
  end: "2026-10-22T10:30:00+02:00",
  organiser: { name: "Ben Sample", href: "/briefs?brief=brief:ben-sample" },
  where: "",
  response: null,
  response_words: "not answered yet",
  stream: "pilot",
  keyless: false,
  archived: false,
  over: false,
  rolls: "",
  attendees: [{ name: "Ada Example", href: "" }],
  recordings: [],
  points: [
    point("01POINTOPEN", "The venue's dates"),
    point("01POINTANSWERED", "Who signs off", { state: "answered", answer: "Cleo" }),
    point("01POINTPENCIL1", "Ask about parking", { proposed: true }),
    point("01POINTPENCIL2", "Ask about the menu", { proposed: true }),
    point("01POINTROLLED", "The old offer", {
      state: "rolled",
      proposed: true,
      went_to: { ref: "M-0009", href: "/meetings?open=M-0009" },
    }),
  ],
  open_points: 3,
  proposed: 2,
  agenda: "Pilot sync\n- The venue's dates",
  outcome: "",
  notes: "",
  items: [],
  suggestions: null,
  materials: [],
  history: [],
  facts: "added by claude",
};

const SUGGESTED = [
  {
    row: {
      ref: "A-0002",
      id: "01IDA-0002",
      kind: "task",
      title: "Book the room",
      who: "Ben Sample",
      stream: "pilot",
      meeting: "M-0001",
      state: "open",
      triage: null,
      woke: false,
      archived: false,
      due: { text: "today", title: "", tone: "today" as const },
      age: 0,
      callable: true,
      quick: [],
    },
    href: "/ledger?open=A-0002",
    why: "linked to the meeting",
  },
];

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
  body: Record<string, unknown> | null;
}

let sent: Sent[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const WROTE = {
  notice: {
    kind: "info",
    text: "Saved.",
    lines: ["M-0001: point kept"],
    button: "Undo",
    batch: "01BATCH",
    then: "undo",
  },
  done: "01BATCH",
  skipped: [],
  at: null,
  added: null,
  version: "v2",
};

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const body = request.method === "POST" ? ((await request.clone().json()) as Record<string, unknown>) : null;
  sent.push({ path: url.pathname, query: url.search, body });
  if (url.pathname === "/api/web/shell") return json(SHELL);
  if (url.pathname === "/api/web/meetings") return json(LIST);
  if (url.pathname === "/api/web/meetings/panel") {
    const open = url.searchParams.get("open") ?? "";
    const asked = url.searchParams.get("suggest") === "1";
    const panel = { ...PANEL, ref: open, id: `01ID${open}`, suggestions: asked ? SUGGESTED : null };
    return json({ canonical: `/meetings${url.search}`, panel, missing: "" });
  }
  if (url.pathname === "/api/web/ledger/changed") return json({ changed: false, version: "v1" });
  if (url.pathname === "/api/web/ledger/write") return json(WROTE);
  if (url.pathname === "/api/web/ledger/save") {
    return json({
      line: LIST.list.days[1]?.meetings[0],
      panel: { ...PANEL, outcome: "Agreed" },
      before: "v1",
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
  await waitFor(() => expect(document.querySelector('[data-row="M-0003"]')).toBeTruthy());
  return router;
}

async function panel() {
  return screen.findByRole("complementary", { name: /^M-0001, Pilot sync/ });
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
  vi.restoreAllMocks();
  resetDrafts();
});

describe("the meetings list", () => {
  it("lists the days in the read's order, the earlier ones after their line, and joins the sidebar after the Ledger", async () => {
    await open("/meetings");
    expect(document.title).toBe("Meetings - Docket");
    const heads = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(heads).toEqual([
      expect.stringMatching(/^Today, Wed 21 Oct2/),
      expect.stringMatching(/^Tomorrow/),
      expect.stringMatching(/^Yesterday/),
    ]);
    const divider = screen.getByText("Earlier days, newest first");
    const yesterday = screen.getByRole("heading", { name: /^Yesterday/ });
    expect(divider.compareDocumentPosition(yesterday) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.querySelector('[data-row="M-0002"]')?.getAttribute("data-dim")).toBe("1");
    const pages = screen.getByRole("navigation", { name: "Pages" });
    const links = within(pages).getAllByRole("link");
    expect(links.slice(1, 4).map((l) => l.getAttribute("href"))).toEqual(["/queue", "/ledger", "/meetings"]);
    expect(links[3]?.getAttribute("aria-current")).toBe("page");
    expect(sent.some((s) => s.path === "/api/web/meetings")).toBe(true);
  });

  it("moves across the days with j and k, opens with Enter, and no item key ever writes on a meeting", async () => {
    const router = await open("/meetings");
    key("j");
    key("j");
    key("j");
    expect(current()).toBe("M-0001");
    key("k");
    key("j");
    for (const name of ["1", "2", "0", "e", "x", "s", "Delete", "n"]) key(name);
    await act(async () => await new Promise((done) => setTimeout(done, 20)));
    expect(writes()).toHaveLength(0);
    key("Enter");
    await panel();
    expect(router.state.location.search).toEqual({ open: "M-0001" });
    expect(sent.some((s) => s.path === "/api/web/meetings/panel" && s.query === "?open=M-0001")).toBe(true);
  });
});

describe("the open meeting's points", () => {
  it("names each point by its id: the mark answers it, open again clears the answer, keep inks one in", async () => {
    await open("/meetings?open=M-0001");
    const side = await panel();
    fireEvent.click(within(side).getByRole("button", { name: "Answered: The venue's dates" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    fireEvent.click(within(side).getByRole("button", { name: "Open again: Who signs off" }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    fireEvent.click(within(side).getByRole("button", { name: "Keep: Ask about parking" }));
    await waitFor(() => expect(writes()).toHaveLength(3));
    expect(writes()).toEqual([
      {
        op: "point_set",
        point: "01POINTOPEN",
        fields: { state: "answered" },
        items: ["M-0001"],
        page: "meetings",
        at: null,
        bulk: false,
      },
      {
        op: "point_set",
        point: "01POINTANSWERED",
        fields: { state: "open", answer: "" },
        items: ["M-0001"],
        page: "meetings",
        at: null,
        bulk: false,
      },
      { op: "keep", point: "01POINTPENCIL1", items: ["M-0001"], page: "meetings", at: null, bulk: false },
    ]);
    // A rolled point takes no button: only an undo changes it.
    expect(within(side).queryByRole("button", { name: /The old offer$/ })).toBeNull();
    expect(await screen.findByRole("status")).toBeTruthy();
  });

  it("removes every pencilled point at once, leaving a rolled one alone", async () => {
    await open("/meetings?open=M-0001");
    const side = await panel();
    fireEvent.click(within(side).getByRole("button", { name: "Remove them all" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      op: "remove",
      items: ["M-0001"],
      points: ["01POINTPENCIL1", "01POINTPENCIL2"],
    });
  });

  it("sends a typed answer with Enter, lays it aside with Esc, and guards it when the panel would move on", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await open("/meetings?open=M-0001");
    const side = await panel();
    fireEvent.click(within(side).getByRole("button", { name: "Answer: The venue's dates" }));
    const answer = within(side).getByLabelText("The answer");
    fireEvent.change(answer, { target: { value: "the 12th" } });
    // j would carry the panel off the typed answer: the guard asks, and Stay keeps it.
    key("j", document.body);
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(within(side).getByLabelText("The answer")).toBeTruthy();
    fireEvent.keyDown(answer, { key: "Escape" });
    expect(within(side).queryByLabelText("The answer")).toBeNull();
    expect(writes()).toHaveLength(0);
    fireEvent.click(within(side).getByRole("button", { name: "Answer: The venue's dates" }));
    const again = within(side).getByLabelText("The answer");
    fireEvent.change(again, { target: { value: "the 19th" } });
    fireEvent.keyDown(again, { key: "Enter" });
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "point_set", point: "01POINTOPEN", fields: { answer: "the 19th" } });
  });

  it("asks for the suggestions in the URL and adds one as a point by its item", async () => {
    const router = await open("/meetings?open=M-0001");
    const side = await panel();
    fireEvent.click(within(side).getByRole("button", { name: "Suggest items" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ open: "M-0001", suggest: "1" }));
    fireEvent.click(await within(side).findByRole("button", { name: "Add as a point: A-0002" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ op: "point", items: ["M-0001"], fields: { item: "A-0002" } });
  });

  it("keeps a point being typed when the suggestions are asked for, the panel staying as it was until they come", async () => {
    await open("/meetings?open=M-0001");
    const side = await panel();
    fireEvent.change(within(side).getByLabelText("Add a point"), { target: { value: "budget for Q3" } });
    fireEvent.click(within(side).getByRole("button", { name: "Suggest items" }));
    await within(side).findByRole("button", { name: "Add as a point: A-0002" });
    expect((within(side).getByLabelText("Add a point") as HTMLInputElement).value).toBe("budget for Q3");
  });

  it("asks before another point's editor takes the place of an answer being typed", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await open("/meetings?open=M-0001");
    const side = await panel();
    fireEvent.click(within(side).getByRole("button", { name: "Answer: The venue's dates" }));
    fireEvent.change(within(side).getByLabelText("The answer"), { target: { value: "the 12th" } });
    fireEvent.click(within(side).getByRole("button", { name: "Answer: Ask about parking" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect((within(side).getByLabelText("The answer") as HTMLTextAreaElement).value).toBe("the 12th");
    fireEvent.click(within(side).getByRole("button", { name: "Answer: Ask about parking" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect((within(side).getByLabelText("The answer") as HTMLTextAreaElement).value).toBe("");
  });

  it("saves the outcome on leaving it as a set on the meeting, the suggestions shown kept", async () => {
    await open("/meetings?open=M-0001&suggest=1");
    const side = await panel();
    await within(side).findByRole("button", { name: "Add as a point: A-0002" });
    const outcome = within(side).getByLabelText("Outcome") as HTMLTextAreaElement;
    fireEvent.change(outcome, { target: { value: "Agreed" } });
    fireEvent.blur(outcome);
    await waitFor(() => expect(sent.some((s) => s.path === "/api/web/ledger/save")).toBe(true));
    expect(sent.find((s) => s.path === "/api/web/ledger/save")?.body).toEqual({
      op: "set",
      items: ["M-0001"],
      fields: { outcome: "Agreed" },
      page: "meetings",
    });
    await waitFor(() => expect(outcome.closest("[data-field]")?.hasAttribute("data-unsaved")).toBe(false));
    expect(within(side).getByRole("button", { name: "Add as a point: A-0002" })).toBeTruthy();
  });
});
