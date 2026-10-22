/**
 * The Settings page on a fake service: the values only with show=lists; an add asked first, its question in a dialog
 * with the way back focused, the value shown from the page and sent only in the writes' bodies; a refusal of the value
 * on its field, which keeps it and takes the focus; a file changed since the question asked again with why; a private
 * source's question; a rule turned on busy on its own row; credentials off with no button but its command; and the
 * line an add Docket can't place written by the page as the Python tests read it. The service's answers are tested in
 * Python (tests/test_web_settings*.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageFailed } from "@/components/shell/shell";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import lines from "@/test/never-store-lines.json";
import { neverStoreLine, type SettingsPage } from "./api";

const VALUE = "Ada Example";

function pageOf(shown: boolean): SettingsPage {
  return {
    canonical: shown ? "/settings?show=lists" : "/settings",
    token: "t-1",
    lead: "2 sources, 2 on; 2 never-store values; credentials redacted, phone numbers not; models off on ui-box.",
    form: "vault.yaml is as Docket writes it.",
    machine: "ui-box",
    primary: "ui-box",
    notice: null,
    waiting: null,
    unfilled: [],
    folders_problem: null,
    sources: [
      {
        name: "memo",
        folder: false,
        sensitivity: "work",
        enabled: true,
        facts: [{ name: "Tools", value: "get_memo" }],
        problem: null,
        changes: [
          { act: "private", what: "Make it private", effect: "nothing from it is stored", looser: false },
          { act: "off", what: "Turn it off", effect: "its syncs stop", looser: false },
        ],
        entry: "  memo:\n    tools: [get_memo]",
      },
    ],
    shown,
    lists: [
      {
        name: "people",
        count: 1,
        matches: "keeps out a record that names them",
        values: shown ? [{ value: "Cleo Test", line: "- Cleo Test" }] : [],
        ask: "A name, as a mail writes it",
      },
    ],
    rules: [
      {
        name: "credentials",
        pattern: false,
        on: true,
        what: "passwords and keys",
        note: "lets in what it redacts",
        looser: true,
        button: false,
        command: 'docket redaction set credentials off --vault "/v"',
      },
      {
        name: "phone",
        pattern: false,
        on: false,
        what: "phone numbers",
        note: "markers already stored stay",
        looser: false,
        button: true,
        command: null,
      },
    ],
    credentials_off: null,
    names: "Names are never redacted",
    pattern_example: "patterns:\n  - name: ticket-id",
    models: {
      on: false,
      rows: [{ name: "Models", value: "off" }],
      errors: [],
      commands: [],
      config_path: "/c",
      off_line: "provider: none",
    },
  };
}

let posts: { path: string; body: Record<string, unknown> }[] = [];
let answers: Record<string, () => Response> = {};
let holding: (() => void) | null = null;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/api/web/shell") {
    return json({
      vault: "v",
      machine: "m",
      zone: "UTC",
      index_line: "",
      untriaged: 0,
      staging: 0,
      sync_due: [],
      problem: null,
    });
  }
  if (request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    posts.push({ path: url.pathname, body });
    const key = `${url.pathname} ${String(body.act ?? body.state ?? "")}`;
    if (holding === null && key.endsWith("redaction on")) {
      await new Promise<void>((done) => (holding = done));
    }
    const answer = answers[key];
    return answer ? answer() : json({ notice: "done", warning: null });
  }
  if (url.pathname !== "/api/web/settings") return json({ error: "not here" }, 404);
  return json(pageOf(url.searchParams.get("show") === "lists"));
}

const QUESTION = {
  question: {
    name: "people",
    lines: ["It drops 1 record waiting in staging now."],
    broad: null,
    commits: "It is committed.",
    token: "t-2",
  },
  notice: null,
  warning: null,
  by_hand: false,
};

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
  await waitFor(() => expect(document.querySelector("[data-lead]")).toBeTruthy());
  return router;
}

function addField(): HTMLInputElement {
  return screen.getByLabelText("A name, as a mail writes it");
}

beforeEach(() => {
  posts = [];
  answers = {};
  holding = null;
});

afterEach(() => vi.unstubAllGlobals());

describe("the Settings page", () => {
  it("holds the values only when its URL asks for them", async () => {
    await open("/settings");
    expect(screen.queryByText("Cleo Test")).toBeNull();
    expect(screen.getByRole("link", { name: "Show the values" }).getAttribute("href")).toBe("/settings?show=lists");
    fireEvent.click(screen.getByRole("link", { name: "Show the values" }));
    expect(await screen.findByText("Cleo Test")).toBeTruthy();
  });

  it("asks before an add, sending the value only in the writes' bodies, and adds on the answer", async () => {
    await open("/settings");
    fireEvent.change(addField(), { target: { value: VALUE } });
    answers["/api/web/settings/never-store ask"] = () => json(QUESTION);
    answers["/api/web/settings/never-store add"] = () =>
      json({ notice: "Added one value to people.", warning: null, question: null, by_hand: false });
    fireEvent.submit(addField().closest("form") as HTMLFormElement);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(VALUE)).toBeTruthy();
    expect(within(dialog).getByText("It drops 1 record waiting in staging now.")).toBeTruthy();
    // The way back is focused: the add drops what the value names, for good.
    await waitFor(() => expect(document.activeElement?.textContent).toBe("Back, adding nothing"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add it to people" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(await screen.findByText("Added one value to people.")).toBeTruthy();
    expect(posts.map((p) => [p.body.act, p.body.value, p.body.token])).toEqual([
      ["ask", VALUE, "t-1"],
      ["add", VALUE, "t-2"],
    ]);
    expect(addField().value).toBe("");
  });

  it("puts a refusal of the value on its field, keeping what was typed, with the focus there", async () => {
    await open("/settings");
    fireEvent.change(addField(), { target: { value: "  " } });
    answers["/api/web/settings/never-store ask"] = () => json({ error: "a never-store value is never empty" }, 400);
    fireEvent.submit(addField().closest("form") as HTMLFormElement);
    await waitFor(() => expect(addField().getAttribute("aria-invalid")).toBe("true"));
    const said = document.getElementById(addField().getAttribute("aria-describedby") ?? "");
    expect(said?.textContent).toBe("a never-store value is never empty");
    expect(document.activeElement).toBe(addField());
    expect(addField().value).toBe("  ");
    fireEvent.change(addField(), { target: { value: VALUE } });
    expect(addField().getAttribute("aria-invalid")).toBeNull();
  });

  it("asks again with why when the file changed since the question", async () => {
    await open("/settings");
    fireEvent.change(addField(), { target: { value: VALUE } });
    answers["/api/web/settings/never-store ask"] = () => json(QUESTION);
    answers["/api/web/settings/never-store add"] = () => json({ error: "vault.yaml changed since you saw this" }, 409);
    fireEvent.submit(addField().closest("form") as HTMLFormElement);
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Add it to people" }));
    await waitFor(() =>
      expect(within(screen.getByRole("alertdialog")).getByText("vault.yaml changed since you saw this")).toBeTruthy(),
    );
    expect(posts.map((p) => p.body.act)).toEqual(["ask", "add", "ask"]);
  });

  it("asks once more before a source is made private", async () => {
    await open("/settings");
    answers["/api/web/settings/source private"] = () =>
      json({
        question: { source: "memo", lines: ["It drops 2 records of memo."], token: "t-3" },
        notice: null,
        warning: null,
      });
    fireEvent.click(screen.getByRole("button", { name: "Make it private…: memo" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("It drops 2 records of memo.")).toBeTruthy();
    answers["/api/web/settings/source private"] = () =>
      json({ notice: "memo is private.", warning: null, question: null });
    fireEvent.click(within(dialog).getByRole("button", { name: "Make memo private" }));
    expect(await screen.findByText("memo is private.")).toBeTruthy();
    expect(posts.map((p) => [p.body.confirmed, p.body.token])).toEqual([
      [false, "t-1"],
      [true, "t-3"],
    ]);
  });

  it("keeps a busy rule's button where it is, focusable and said busy, on its own row only", async () => {
    await open("/settings");
    const turn = screen.getByRole("button", { name: "Turn it on: phone" });
    turn.focus();
    fireEvent.click(turn);
    await waitFor(() => expect(turn.getAttribute("aria-busy")).toBe("true"));
    expect(turn.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(turn);
    expect(screen.getByRole("button", { name: "Turn it off: memo" }).getAttribute("aria-disabled")).toBeNull();
    fireEvent.click(turn);
    act(() => holding?.());
    await waitFor(() => expect(turn.getAttribute("aria-busy")).toBeNull());
    expect(posts.filter((p) => p.path.endsWith("/redaction"))).toHaveLength(1);
  });

  it("gives credentials off no button, only its command", async () => {
    await open("/settings");
    expect(screen.queryByRole("button", { name: /^Turn it (on|off): credentials/ })).toBeNull();
    expect(screen.getByText('docket redaction set credentials off --vault "/v"')).toBeTruthy();
    expect(screen.getByText(/Turning credentials off is yours to confirm/)).toBeTruthy();
  });

  it("writes an add's line as the Python tests read it", () => {
    for (const { value, line } of lines) expect(neverStoreLine(value)).toBe(line);
  });
});

describe("a page that failed as it drew", () => {
  it("says so with no detail and reloads on its button", () => {
    const reload = vi.fn();
    render(<PageFailed reload={reload} />);
    expect(screen.getByRole("heading").textContent).toBe("Something failed in Docket");
    fireEvent.click(screen.getByRole("button", { name: "Reload the page" }));
    expect(reload).toHaveBeenCalledOnce();
  });
});
