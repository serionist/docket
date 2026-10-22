/**
 * The Curate page on a fake service: the round with its cards, log and stoplist, the server's canonical URL written
 * back; a card opened with its snippet marked and its place on the Search page; Accept posting the round, card and
 * hash the card was shown with, then moving on to the next open card; Edit's form posting exactly what was typed, a
 * refusal keeping it; a card whose entity changed since read again; a round, an undo and a lift each posted as the
 * route takes it; markup in a name shown as text; and no key that decides. The service's own answers are tested in
 * Python (tests/test_web_curate.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import type { CardPanel, CardRow, CurateView } from "./api";

const NORTHWIND = "0bd52c";
const CLEO = "96d988";
const ANNA = "c28557";
const ROUND = "20261022T090000-ui-box";
const EVIL = '"><img src=x onerror=alert(1)>';

let decided: Record<string, CardRow["state"]> = {};
let moved = false;
let reads: string[] = [];
let posts: { path: string; body: Record<string, unknown> }[] = [];

function cardRow(id: string, n: number, title: string, kind: string): CardRow {
  return {
    id,
    n,
    state: decided[id] ?? "open",
    kind,
    title,
    spellings: [{ text: title, trust: "label", add: "the name", chunks: 3 }],
    missed: "search misses it in 3 chunks of 3 places",
    url: `/curate?open=${id}`,
  };
}

function view(): CurateView {
  return {
    lead: "Round of 22 Oct: 3 cards, 0 decided.",
    due: "The next round is due on 29 Oct.",
    minutes: "review 2 min in the last 1 week with a round",
    round: ROUND,
    cards: [
      cardRow(NORTHWIND, 1, "Nortwin", "Alias for Northwind"),
      cardRow(CLEO, 2, EVIL, "New person"),
      cardRow(ANNA, 3, "Anna", "New person"),
    ],
    left_out: "11 common words",
    applied: [],
    measures: null,
    dropped: [],
    log: [
      {
        id: "20261022T090100-ui-box-1",
        at: "22 Oct 2026 09:01",
        op: "create",
        entity: "dora",
        part: "term, Dóra",
        by: "user",
        via: "page",
        undo: true,
        undone_by: null,
        unfinished: false,
      },
    ],
    problems: [],
    skipped: [],
    tier1: null,
  };
}

function cardPanel(id: string, edit: boolean): CardPanel {
  const row = view().cards.find((c) => c.id === id) as CardRow;
  return {
    id,
    n: row.n,
    new: id !== NORTHWIND,
    editable: true,
    kind: row.kind,
    title: row.title,
    state: decided[id] ?? "open",
    type: id === NORTHWIND ? "customer" : "person",
    entity: id === NORTHWIND ? "northwind" : "cleo-test",
    spellings: row.spellings,
    missed: row.missed,
    newest: "07 Oct 2026",
    snippets: [
      {
        locator: "r:plaud:rec-0002/transaction@00:00-00:59",
        before: "the pallets for ",
        mark: "Nortwin",
        after: " today",
        search: "/search?hit=r:plaud:rec-0002/transaction@00:00-00:59",
      },
    ],
    round: ROUND,
    hash: moved ? "changed" : "none",
    close: "/curate",
    edit_url: `/curate?edit=${id}`,
    back: `/curate?open=${id}`,
    form: edit
      ? {
          new: true,
          types: ["person", "term", "customer"],
          type: "person",
          id: "cleo-test",
          names: [
            { code: "en", value: "Cleo Test" },
            { code: "hu", value: "" },
          ],
          spellings: [
            { i: 0, text: "Cleo", trust: "docs", checked: true, kind: "plain" },
            { i: 1, text: "Test Cleo", trust: "other order", checked: true, kind: "plain" },
          ],
          role: "",
        }
      : null,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const SHELL = { vault: "vault", machine: "ui-box", zone: "Europe/Budapest", index_line: "", untriaged: 0 };

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/api/web/shell") return json({ ...SHELL, staging: 0, sync_due: [], problem: null });
  if (request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    posts.push({ path: url.pathname, body });
    if (url.pathname === "/api/web/curate/decide") {
      if (moved) return json({ error: "This entity's file changed since the page showed the card." }, 409);
      const edit = body.edit as Record<string, unknown> | undefined;
      if (edit && edit.id === "Not An Id") return json({ error: "id: an id is lowercase words joined by -" }, 400);
      decided[String(body.card)] = body.act === "reject" ? "rejected" : "accepted";
      return json({ notice: `Card ${String(body.card)} accepted and logged; the log below undoes it.` });
    }
    if (url.pathname === "/api/web/curate/round") return json({ notice: "A new round: 3 cards." });
    if (url.pathname === "/api/web/curate/undo") return json({ notice: "Undone: 1 change taken back and logged." });
    return json({ notice: "Lifted: curation may offer that spelling again." });
  }
  reads.push(`${url.pathname}${url.search}`);
  if (url.pathname === "/api/web/curate/panel") {
    const query = url.searchParams;
    const id = query.get("edit") ?? query.get("open") ?? "";
    const canonical = query.get("edit") ? `/curate?edit=${id}` : `/curate?open=${id}`;
    return json({ canonical, panel: cardPanel(id, query.has("edit")), missing: null });
  }
  return json({
    canonical: "/curate",
    view: view(),
    lifts: [{ shown: "new:-:rita sample", id: "38bbd7c4af95d3ca", liftable: true }],
    notice: null,
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
  decided = {};
  moved = false;
  reads = [];
  posts = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the Curate page", () => {
  it("lists the round's cards, log and stoplist, a name's markup as text", async () => {
    await open("/curate", "[data-row]");
    expect(screen.getByText("Round of 22 Oct: 3 cards, 0 decided.")).toBeTruthy();
    expect(document.querySelectorAll("[data-row]")).toHaveLength(3);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getAllByText(EVIL).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Undo create 20261022T090100-ui-box-1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Lift new:-:rita sample" })).toBeTruthy();
  });

  it("opens a card with j and Enter, its snippet marked and linked to its place on Search", async () => {
    const router = await open("/curate", "[data-row]");
    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Enter" });
    await waitFor(() => expect(where(router)).toBe(`/curate?open=${NORTHWIND}`));
    await waitFor(() => expect(document.activeElement?.id).toBe("panel-title"));
    expect(within(panel()).getByText("Nortwin", { selector: "mark" })).toBeTruthy();
    const link = within(panel()).getByRole("link", { name: "r:plaud:rec-0002/transaction@00:00-00:59" });
    expect(link.getAttribute("href")).toBe("/search?hit=r:plaud:rec-0002/transaction@00:00-00:59");
  });

  it("accepts at the round and hash the card was shown with, then moves on to the next open card", async () => {
    const router = await open(`/curate?open=${NORTHWIND}`, "#panel-title");
    await waitFor(() => expect(within(panel()).getByRole("button", { name: "Accept" })).toBeTruthy());
    fireEvent.click(within(panel()).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      path: "/api/web/curate/decide",
      body: { act: "accept", round: ROUND, card: NORTHWIND, hash: "none" },
    });
    await waitFor(() => expect(where(router)).toBe(`/curate?open=${CLEO}`));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("accepted and logged"));
  });

  it("posts the edit as typed, and a refusal keeps it", async () => {
    await open(`/curate?edit=${CLEO}`, "#edit-id");
    const id = within(panel()).getByLabelText("Id") as HTMLInputElement;
    expect(id.value).toBe("cleo-test");
    fireEvent.change(id, { target: { value: "Not An Id" } });
    fireEvent.change(within(panel()).getByLabelText("Name in hu"), { target: { value: "Test Cleo" } });
    fireEvent.change(within(panel()).getByLabelText("Role"), { target: { value: "rights agent" } });
    fireEvent.click(within(panel()).getByRole("checkbox", { name: /Test Cleo/ }));
    fireEvent.click(within(panel()).getByRole("button", { name: "Accept as edited" }));
    await waitFor(() => expect(within(panel()).getByRole("alert").textContent).toContain("id:"));
    expect(posts[0]?.body).toEqual({
      act: "edit",
      round: ROUND,
      card: CLEO,
      hash: "none",
      edit: {
        keep: [0],
        kinds: { "0": "plain", "1": "plain" },
        type: "person",
        id: "Not An Id",
        names: { en: "Cleo Test", hu: "Test Cleo" },
        role: "rights agent",
      },
    });
    expect((within(panel()).getByLabelText("Id") as HTMLInputElement).value).toBe("Not An Id");
    expect((within(panel()).getByLabelText("Role") as HTMLInputElement).value).toBe("rights agent");
  });

  it("reads a card whose entity changed since again and says so", async () => {
    await open(`/curate?open=${ANNA}`, "#panel-title");
    await waitFor(() => expect(within(panel()).getByRole("button", { name: "Accept" })).toBeTruthy());
    moved = true;
    const before = reads.length;
    fireEvent.click(within(panel()).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(panel()).getByText(/changed since the page showed the card/)).toBeTruthy());
    await waitFor(() => expect(reads.length).toBeGreaterThan(before));
  });

  it("starts a round, undoes and lifts, each posted as its route takes it", async () => {
    await open("/curate", "[data-row]");
    fireEvent.click(screen.getByRole("button", { name: "Start a new round" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Undo create 20261022T090100-ui-box-1" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Lift new:-:rita sample" }));
    await waitFor(() => expect(posts).toHaveLength(3));
    expect(posts).toEqual([
      { path: "/api/web/curate/round", body: {} },
      { path: "/api/web/curate/undo", body: { op: "20261022T090100-ui-box-1" } },
      { path: "/api/web/curate/lift", body: { key: "38bbd7c4af95d3ca" } },
    ]);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Lifted"));
  });

  it("has no key that decides a card", async () => {
    await open(`/curate?open=${NORTHWIND}`, "#panel-title");
    await waitFor(() => expect(within(panel()).getByRole("button", { name: "Accept" })).toBeTruthy());
    for (const key of ["1", "2", "a", "e", "r", "Delete", "u"]) fireEvent.keyDown(document.body, { key });
    expect(posts).toEqual([]);
  });
});
