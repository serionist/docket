/**
 * The Staging page on a fake service: the list by source with the ready bar and the held commands, the server's
 * canonical URL written back; j and Enter opening a record with the panel's heading taking the focus; ticks the page's
 * own until Preview writes them into the URL; Work posting exactly what is ticked and typed with what the panel
 * showed, then the panel closed, the notice said once and the sidebar's count read again; a refusal kept beside the
 * ticks; a record changed since read again with its own ticks; private through its question; promote; and no key that
 * writes. The service's own answers are tested in Python (tests/test_web_staging.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import type { StagedRow, StagingList, StagingPanel } from "./api";
import { chosenHref, listQuery } from "./api";

const CALL = "d59ac38cdfc692cec0989d036a6644d0";
const OFFER = "f3a5a31858c897f5aa86450669a9f32e";
const HELD = "0f4aed28df6f942726b0f672403021e4";

function row(key: string, title: string, fields: Partial<StagedRow> = {}): StagedRow {
  return {
    key,
    short: key.slice(0, 12),
    rid: `calls:${title}`,
    source: "calls",
    kind: "transcript",
    icon: "recording",
    title,
    when: "7 Oct 2026 10:00",
    waited: "2 days",
    state: "pending",
    why: "mixed",
    line: "a mixed source: work or private?",
    on_hold: false,
    url: `/staging?open=${key}`,
    ...fields,
  };
}

function listView(): StagingList {
  return {
    lead: "3 records wait at the gate: 2 for you to classify, 1 ready to promote, 1 on hold",
    source_chips: [
      { label: "All sources", url: "/staging", on: true },
      { label: "calls", url: "/staging?source=calls", on: false },
    ],
    state_chips: [{ label: "Pending and ready", url: "/staging", on: true }],
    ready: [{ source: "memo", n: 1 }],
    groups: [
      {
        source: "calls",
        counts: "2 pending",
        rows: [
          row(HELD, "", { on_hold: true, why: "on-hold", rid: "calls:rec-7/transcript", line: "on hold: it stops" }),
          row(CALL, "Supplier call"),
          row(OFFER, "The offer", { kind: "mail", icon: "thread" }),
        ],
      },
    ],
    more: null,
    unreadable: [],
    narrowed: false,
  };
}

let changed = false;
let reads: string[] = [];
let posts: { path: string; body: Record<string, unknown> }[] = [];
let shellReads = 0;

function panelOf(query: URLSearchParams): StagingPanel {
  const key = query.get("open") ?? "";
  const u = changed ? [5] : query.getAll("u").map(Number);
  const confirm = query.get("confirm") === "private";
  return {
    key,
    rid: "calls:rec-1/transcript",
    kind: key === OFFER ? "mail" : "transcript",
    title: key === OFFER ? "The offer" : "Supplier call",
    source: "calls",
    when: "7 Oct 2026 10:00",
    staged_at: "20 Oct 2026 09:00",
    state: changed ? "ready" : "pending",
    by: changed ? "you" : "nobody yet",
    cuts: [],
    why_line: changed ? "classified work by you, with 1 cut" : "a mixed source: work or private?",
    on_hold: false,
    utterances: [1, 2, 3, 4, 5].map((n) => ({
      n,
      span: `00:0${n}-00:0${n + 1}`,
      speaker: "Ada Example",
      text: `Line ${n}.`,
      checked: u.includes(n),
      going: u.includes(n),
      was_cut: false,
      released: n === 4,
    })),
    lines: null,
    text: "",
    facts: [{ name: "author", value: "ada@example.invalid" }],
    native: "",
    cut_before: null,
    notes: [],
    problem: null,
    preview: u.length ? { summary: `Removes ${u.length} utterances.`, text: null } : null,
    typed: "",
    work_label: u.length ? `Work, with ${u.length} cut${u.length === 1 ? "" : "s"}` : "Work, whole",
    can_work: true,
    confirm,
    seen: { state: changed ? "ready" : "pending", by: changed ? "user" : "", cuts: changed ? "ms:5000-6000" : "" },
    close: "/staging",
    confirm_url: `/staging?open=${key}&confirm=private`,
    back: `/staging?open=${key}`,
  };
}

const SHELL = {
  vault: "vault",
  machine: "ui-box",
  zone: "Europe/Budapest",
  index_line: "",
  untriaged: 0,
  staging: 3,
  sync_due: [],
  problem: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/api/web/shell") {
    shellReads += 1;
    return json(SHELL);
  }
  if (request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    posts.push({ path: url.pathname, body });
    if (url.pathname === "/api/web/staging/promote") {
      return json({ written: 1, held: 0, notice: "Promoted: 1 record written to raw and committed." });
    }
    if ((body.cut as string[]).includes("12:30")) return json({ error: "cut 12:30: a cut is <start>-<end>" }, 400);
    if (changed) return json({ error: "This record changed since you opened it: nothing was written." }, 409);
    const key = String(body.key);
    const notice =
      body.label === "work"
        ? `${key.slice(0, 12)} classified work: it goes to raw at the next promotion.`
        : `${key.slice(0, 12)} dropped as private: a later pull of the same version is skipped.`;
    return json({ key, label: body.label, notice });
  }
  reads.push(`${url.pathname}${url.search}`);
  if (url.pathname === "/api/web/staging/panel") {
    const query = url.searchParams;
    const canonical = `/staging?open=${query.get("open")}${query
      .getAll("u")
      .map((n) => `&u=${n}`)
      .join("")}${query.get("confirm") ? "&confirm=private" : ""}`;
    return json({ canonical, panel: panelOf(query), missing: null });
  }
  if (url.pathname !== "/api/web/staging") return json({ error: "not here" }, 404);
  if (url.searchParams.get("k") === "0") return json({ error: "k is a whole number from 1 to 1000, not '0'" }, 400);
  const notice = url.searchParams.get("done") ? `${CALL.slice(0, 12)} classified work.` : null;
  return json({ canonical: "/staging", view: listView(), held: heldOf(), notice });
}

function heldOf() {
  return [
    {
      source: "calls",
      ids: [
        {
          id: "rec-cut-01",
          again: null,
          commands: [
            { label: "read it", text: 'docket hold show calls "rec-cut-01" --from "<folder>" --vault "/v"' },
            { label: "release it", text: 'docket hold release calls "rec-cut-01" --from "<folder>" --vault "/v"' },
          ],
        },
      ],
      on_hold: 1,
      unfilled: [{ name: "folder", value: "/srv/$HOME" }],
      paste: "Go through my held calls recordings with /docket:cuts.",
    },
  ];
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
  const { pathname, search } = router.history.location;
  return `${pathname}${search}`;
}

function panel(): HTMLElement {
  return screen.getByRole("complementary");
}

beforeAll(() => installKeys());

beforeEach(() => {
  changed = false;
  reads = [];
  posts = [];
  shellReads = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the Staging page's URL", () => {
  it("reads the list without the panel's parameters and builds a preview's URL in the server's order", () => {
    expect(listQuery({ open: CALL, u: ["2"], state: "pending", source: "calls", k: "100" })).toBe(
      "?source=calls&state=pending&k=100",
    );
    expect(listQuery({ done: "work-d59ac38cdfc6" })).toBe("?done=work-d59ac38cdfc6");
    expect(chosenHref({ state: "pending", open: "x", u: "9" }, CALL, [3, 1], [], " 0:10-0:20 ")).toBe(
      `/staging?state=pending&open=${CALL}&u=1&u=3&cut=0:10-0:20`,
    );
    expect(chosenHref({}, CALL, [], [], "")).toBe(`/staging?open=${CALL}&picked=1`);
  });
});

describe("the Staging page", () => {
  it("lists what waits with the ready bar and the held commands, and a held record doesn't open", async () => {
    const router = await open("/staging?k=50", "[data-row]");
    expect(screen.getByText(/3 records wait at the gate/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Promote memo" })).toBeTruthy();
    const held = document.querySelector(`[data-row="${HELD}"]`) as HTMLElement;
    expect(held.querySelector("a")).toBeNull();
    expect(held.textContent).toContain("calls:rec-7/transcript");
    expect(screen.getByText('docket hold show calls "rec-cut-01" --from "<folder>" --vault "/v"')).toBeTruthy();
    expect(screen.getByText("/srv/$HOME")).toBeTruthy();
    // The server's canonical URL, written back as it came.
    expect(reads[0]).toBe("/api/web/staging?k=50");
    await waitFor(() => expect(where(router)).toBe("/staging"));
  });

  it("opens a record with j and Enter, keeps its ticks the page's own until Preview, then reads them", async () => {
    const router = await open("/staging", "[data-row]");
    fireEvent.keyDown(document.body, { key: "j" });
    expect((document.activeElement as HTMLElement).dataset.row).toBe(CALL);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Enter" });
    await waitFor(() => expect(where(router)).toBe(`/staging?open=${CALL}`));
    await waitFor(() => expect(document.activeElement?.id).toBe("panel-title"));
    expect(screen.getByText("Supplier call", { selector: "#panel-title" })).toBeTruthy();
    // What a release cuts has no tick.
    expect(within(panel()).queryByRole("checkbox", { name: "Cut 00:04-00:05" })).toBeNull();
    const before = reads.length;
    fireEvent.click(within(panel()).getByRole("checkbox", { name: "Cut 00:02-00:03" }));
    expect(reads.length).toBe(before);
    expect(within(panel()).getByRole("button", { name: "Work, with these cuts" })).toBeTruthy();
    fireEvent.click(within(panel()).getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(where(router)).toBe(`/staging?open=${CALL}&u=2`));
    await waitFor(() => expect(within(panel()).getByText("Removes 1 utterances.")).toBeTruthy());
    expect(within(panel()).getByRole("button", { name: "Work, with 1 cut" })).toBeTruthy();
    expect(within(panel()).getByRole("checkbox", { name: "Cut 00:02-00:03" }).getAttribute("aria-checked")).toBe(
      "true",
    );
  });

  it("posts exactly what is ticked and typed with what the panel showed, then closes and reads again", async () => {
    const router = await open(`/staging?open=${CALL}&u=2`, "#panel-title");
    await waitFor(() => expect(within(panel()).getByRole("button", { name: "Work, with 1 cut" })).toBeTruthy());
    const shells = shellReads;
    fireEvent.click(within(panel()).getByRole("button", { name: "Work, with 1 cut" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      path: "/api/web/staging/classify",
      body: { key: CALL, label: "work", u: ["2"], line: [], cut: [], seen: { state: "pending", by: "", cuts: "" } },
    });
    await waitFor(() => expect(where(router)).toBe("/staging"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("classified work"));
    await waitFor(() => expect(shellReads).toBeGreaterThan(shells));
    await waitFor(() => expect((document.activeElement as HTMLElement).dataset.row).toBe(CALL));
  });

  it("keeps the ticks beside a refusal and reads nothing again", async () => {
    await open(`/staging?open=${CALL}`, "#panel-title");
    await waitFor(() => expect(within(panel()).getAllByRole("checkbox").length).toBe(4));
    fireEvent.click(within(panel()).getByRole("checkbox", { name: "Cut 00:03-00:04" }));
    fireEvent.change(within(panel()).getByPlaceholderText("12:30-14:05"), { target: { value: "12:30" } });
    const before = reads.length;
    fireEvent.click(within(panel()).getByRole("button", { name: "Work, with these cuts" }));
    await waitFor(() => expect(within(panel()).getByRole("alert").textContent).toContain("cut 12:30"));
    expect(reads.length).toBe(before);
    expect(within(panel()).getByRole("checkbox", { name: "Cut 00:03-00:04" }).getAttribute("aria-checked")).toBe(
      "true",
    );
    expect((within(panel()).getByPlaceholderText("12:30-14:05") as HTMLInputElement).value).toBe("12:30");
  });

  it("reads a record that changed since again, with its own ticks, and says so", async () => {
    await open(`/staging?open=${CALL}`, "#panel-title");
    await waitFor(() => expect(within(panel()).getAllByRole("checkbox").length).toBe(4));
    fireEvent.click(within(panel()).getByRole("checkbox", { name: "Cut 00:01-00:02" }));
    changed = true;
    fireEvent.click(within(panel()).getByRole("button", { name: "Work, with these cuts" }));
    await waitFor(() => expect(within(panel()).getByText(/This record changed since you opened it/)).toBeTruthy());
    await waitFor(() =>
      expect(within(panel()).getByRole("checkbox", { name: "Cut 00:05-00:06" }).getAttribute("aria-checked")).toBe(
        "true",
      ),
    );
    expect(within(panel()).getByRole("checkbox", { name: "Cut 00:01-00:02" }).getAttribute("aria-checked")).toBe(
      "false",
    );
  });

  it("asks once more before private and posts no cuts with it", async () => {
    const router = await open(`/staging?open=${OFFER}`, "#panel-title");
    await waitFor(() => expect(within(panel()).getByRole("button", { name: "Private: drop it" })).toBeTruthy());
    fireEvent.click(within(panel()).getByRole("button", { name: "Private: drop it" }));
    await waitFor(() => expect(where(router)).toBe(`/staging?open=${OFFER}&confirm=private`));
    await waitFor(() => expect(within(panel()).getByText(/Drop this mail for good\?/)).toBeTruthy());
    expect(posts).toEqual([]);
    fireEvent.click(within(panel()).getByRole("button", { name: "Drop it as private" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toEqual({
      key: OFFER,
      label: "private",
      u: [],
      line: [],
      cut: [],
      seen: { state: "pending", by: "", cuts: "" },
    });
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("dropped as private"));
  });

  it("promotes one source and says what it wrote", async () => {
    await open("/staging", "[data-row]");
    fireEvent.click(screen.getByRole("button", { name: "Promote memo" }));
    await waitFor(() => expect(posts).toEqual([{ path: "/api/web/staging/promote", body: { source: "memo" } }]));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Promoted: 1 record"));
  });

  it("says a URL's done once and writes the canonical URL back without it", async () => {
    const router = await open("/staging?done=work-d59ac38cdfc6", "[data-row]");
    await waitFor(() => expect(where(router)).toBe("/staging"));
    expect(screen.getByRole("status").textContent).toContain("classified work");
  });

  it("says a value the URL can't hold in the server's words", async () => {
    await open("/staging?k=0", "[role=alert]");
    expect(screen.getByRole("alert").textContent).toBe("k is a whole number from 1 to 1000, not '0'");
  });

  it("has no key that writes", async () => {
    await open(`/staging?open=${CALL}`, "#panel-title");
    await waitFor(() => expect(within(panel()).getAllByRole("checkbox").length).toBe(4));
    for (const key of ["1", "2", "3", "4", "0", "e", "w", "p", "Delete", "u"]) {
      fireEvent.keyDown(document.body, { key });
    }
    expect(posts).toEqual([]);
  });
});
