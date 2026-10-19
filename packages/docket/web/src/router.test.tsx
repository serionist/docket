import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import routes from "@/routes.json";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { clearLapsed } from "@/lib/session";
import { createAppRouter, routeTree } from "@/router";

const SHELL = {
  vault: "vault",
  machine: "ui-box",
  zone: "Europe/Budapest",
  index_line: "12 chunks from 3 records and 1 docs, updated 09:30",
  untriaged: 4,
  staging: 3,
  sync_due: ["chat"],
  problem: null,
};

function answer(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
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

async function open(url: string, fetched: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => fetched.clone()),
  );
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
  return router;
}

beforeAll(() => installKeys());

afterEach(() => {
  vi.unstubAllGlobals();
  clearLapsed();
});

describe("the router", () => {
  it("has the routes routes.json gives the service, and no others", () => {
    const paths = Object.keys(createAppRouter(createMemoryHistory()).routesByPath)
      .filter((path) => path !== "/")
      .sort();
    expect(paths).toEqual([...routes].sort());
    expect(routeTree.id).toBe("__root__");
  });

  it("keeps a page's state as a plain query string, repeated keys repeated", async () => {
    const router = await open("/design?part=tokens", answer(200, SHELL));
    await act(async () => {
      await router.navigate({ to: "/design", search: { part: "components", open: ["A-0012", "A-0015"] } });
    });
    expect(router.state.location.href).toBe("/design?part=components&open=A-0012&open=A-0015");
    expect(router.state.location.search).toEqual({ part: "components", open: ["A-0012", "A-0015"] });
  });
});

describe("the shell", () => {
  it("lists the built pages with the counts /api/web/shell gives", async () => {
    await open("/design", answer(200, SHELL));
    expect(await screen.findByRole("link", { name: "Staging, 3 records wait" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sync, due: chat" })).toBeTruthy();
    expect(screen.getByText("vault on ui-box")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Design" })).toBeTruthy();
    expect(document.title).toBe("Design - Docket");
  });

  it("lists this page's keys before the shell's in the ? dialog", async () => {
    await open("/design", answer(200, SHELL));
    await screen.findByRole("link", { name: "Staging, 3 records wait" });
    act(() => {
      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "?", bubbles: true, cancelable: true }));
    });
    const dialog = await screen.findByRole("dialog", { name: "Keys" });
    const groups = [...dialog.querySelectorAll("h3")].map((h) => h.textContent);
    expect(groups).toEqual(["This list", "Everywhere"]);
  });

  it("opens the ? dialog from the header's button with the keys off, and says they are off", async () => {
    document.documentElement.dataset.keys = "off";
    await open("/design", answer(200, SHELL));
    act(() => {
      screen.getByRole("button", { name: "Keys (?)" }).click();
    });
    const dialog = await screen.findByRole("dialog", { name: "Keys" });
    expect(dialog.textContent).toContain("The keys are off.");
  });

  it("says how to sign in again when the session has lapsed, with a command nothing in the URL reaches", async () => {
    // A crafted link: quotes, a command substitution and backquotes, which a shell would run if they reached it.
    const hostile = "/design?part=components&x=%22%3B+calc+%24%28whoami%29+%60id%60+%27&y=$(touch+pwned)";
    await open(hostile, answer(401, { error: "a bearer token is needed" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Your session has ended");
    const command = dialog.querySelector("code")?.textContent;
    expect(command).toBe("docket open");
    for (const piece of ["whoami", "calc", "pwned", "`", "$(", '"', "/design"]) {
      expect(dialog.textContent).not.toContain(piece);
    }
  });
});
