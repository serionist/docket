/**
 * The Recording page on a fake service: the summary beside the transcript, the marked lines and the line pointing at
 * them; the server's canonical URL written back with the #at a link carries; a line's time playing the stored audio
 * from there, the URL kept on the line with no new history entry and the player never made again; a time without a
 * player a link to the line; a cut stretch silent; a held recording only that it is held, its link the server's own
 * page; a refusal in the server's words with the start's box. The service's own answers are tested in Python
 * (tests/test_web_recording.py, test_web_audio.py); here they are fixed.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installKeys } from "@/lib/keys";
import { createQueryClient } from "@/lib/query";
import { createAppRouter } from "@/router";
import type { RecordingView, TranscriptLine } from "./api";

const RID = "plaud:rec-tour";
const AUDIO = `/api/web/recording/audio?rid=${RID}`;

function line(at: string, seconds: number, text: string, extra: Partial<TranscriptLine> = {}): TranscriptLine {
  return {
    at,
    url: `/recording?rid=${RID}&at=${at}#at`,
    speaker: "Ben Sample",
    text,
    mark: false,
    target: false,
    start: seconds,
    cut: false,
    ...extra,
  };
}

function viewOf(at: string | null, player: string | null): RecordingView {
  const lines = [
    line("00:00", 0, "Welcome to the yard tour.", { speaker: "Ada Example" }),
    line("00:20", 20, "The new gate opens on Friday.", { mark: at === "00:20", target: at === "00:20" }),
    line("00:40", 40, "[cut]", { speaker: "", start: null, cut: true }),
  ];
  return {
    mode: "recording",
    rid: RID,
    title: "Yard tour",
    meta: ["plaud", "14 Oct 2026 10:30", "01:19 long"],
    locator: at ? `r:${RID}@${at}` : `r:${RID}`,
    message: "",
    link: null,
    link_label: "",
    audio: {
      why: player ? "stored" : "not-fetched",
      message: player ? "A line's time plays the audio from there." : "No audio stored yet.",
      speech: null,
    },
    summary: {
      id: "recording-note:2026-10-14-yard-tour",
      type: "recording-note",
      rev: 1,
      title: "Yard tour",
      lines: [
        {
          n: 15,
          body: "The new gate opens on Thursday.",
          style: "bullet",
          anchors: [{ text: "@00:20", url: `/recording?rid=${RID}&at=00:20#at`, title: "" }],
          cited: at === "00:20",
        },
      ],
    },
    no_summary: "",
    note: "",
    docs: [],
    transcripts: [{ rid: `${RID}/transaction_polish`, meta: `${RID}/transaction_polish, 3 lines`, lines, problem: null }],
    others: [],
    no_transcript: "",
    problems: [],
    player,
    pair: null,
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

let stored = true;
let reply: ((query: URLSearchParams) => Response) | null = null;
let reads: string[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function serve(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/api/web/shell") return json(SHELL);
  if (url.pathname === "/api/web/recording") {
    reads.push(url.search);
    if (reply) return reply(url.searchParams);
    const at = url.searchParams.get("at");
    const player = stored ? (at ? `${AUDIO}#t=${at === "00:20" ? "20.000" : "0.000"}` : AUDIO) : null;
    const canonical = `/recording?rid=${url.searchParams.get("rid")}${at ? `&at=${at}` : ""}`;
    return json({ canonical, view: viewOf(at, player), waiting: "" });
  }
  return json({ error: "not here" }, 404);
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

async function open(url: string, settled = "main ol li") {
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

beforeAll(() => installKeys());

beforeEach(() => {
  stored = true;
  reply = null;
  reads = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a recording", () => {
  it("shows its summary beside its transcript, the marked lines and the line pointing at them", async () => {
    const router = await open(`/recording?at=00:20&rid=${RID}#at`, "#at");
    await waitFor(() => expect(where(router)).toBe(`/recording?rid=${RID}&at=00:20#at`));
    expect(document.title).toBe("Yard tour - Recording - Docket");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Yard tour");
    expect(document.getElementById("at")?.textContent).toContain("The new gate opens on Friday.");
    const cited = document.querySelector("[data-cited]") as HTMLElement;
    expect(cited.textContent).toContain("points at the marked lines");
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Transcript" })).toBeTruthy();
  });

  it("plays from a line's time, keeps the URL on the line with no new entry, and never makes the player again", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const router = await open(`/recording?rid=${RID}`);
    const player = document.querySelector("audio") as HTMLAudioElement;
    expect(player.getAttribute("src")).toBe(AUDIO);
    expect(player.getAttribute("preload")).toBe("none");
    const before = router.history.length;
    fireEvent.click(screen.getByRole("link", { name: "00:20" }));
    expect(play).toHaveBeenCalledTimes(1);
    expect(player.currentTime).toBe(20);
    await waitFor(() => expect(where(router)).toBe(`/recording?rid=${RID}&at=00:20#at`));
    expect(router.history.length).toBe(before);
    await waitFor(() => expect(document.querySelector("[data-playing]")?.textContent).toContain("00:20"));
    // The read for the line marks it; the player is the same element, its source as it was.
    await waitFor(() => expect(document.querySelector("[data-cited]")).toBeTruthy());
    expect(document.querySelector("audio")).toBe(player);
    expect(player.getAttribute("src")).toBe(AUDIO);
  });

  it("links a line's time to the line when no audio plays, and a cut stretch never plays", async () => {
    stored = false;
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const router = await open(`/recording?rid=${RID}`);
    expect(document.querySelector("audio")).toBeNull();
    expect(screen.getByText("No audio stored yet.")).toBeTruthy();
    const cut = document.querySelector("[data-cut]") as HTMLElement;
    expect(cut.textContent).toContain("a stretch left out, never played");
    const before = router.history.length;
    fireEvent.click(within(cut).getByRole("link"));
    await waitFor(() => expect(where(router)).toBe(`/recording?rid=${RID}&at=00:40#at`));
    expect(router.history.length).toBe(before + 1);
    expect(play).not.toHaveBeenCalled();
  });
});

describe("no recording to show", () => {
  it("says a held recording is held, linking the server's own Staging page", async () => {
    reply = () =>
      json({
        canonical: "/recording?rid=plaud:rec-held",
        view: {
          mode: "held",
          rid: "plaud:rec-held",
          title: "plaud:rec-held",
          message: "plaud:rec-held is held: it has stretches you left out, so nothing of it is stored yet.",
          link: "/staging?source=plaud",
          link_label: "Held plaud recordings on the Staging page",
        },
        waiting: "",
      });
    await open("/recording?rid=plaud:rec-held", "a[href^='/staging']");
    expect(screen.getByText(/is held: it has stretches you left out/)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Recording");
    expect(document.querySelector("main")?.querySelector("audio, [data-mark], li")).toBeNull();
  });

  it("says a refusal in the server's words, beside the box that opens a rid or a locator", async () => {
    reply = () => json({ error: "rid is a recording's rid or its locator, not 'not a rid'" }, 400);
    const router = await open("/recording?rid=not+a+rid", '[role="alert"]');
    expect(screen.getByRole("alert").textContent).toContain("rid is a recording's rid or its locator");
    reply = null;
    const box = screen.getByLabelText("Rid or locator");
    fireEvent.change(box, { target: { value: "r:plaud:rec-tour@00:20" } });
    fireEvent.submit(box.closest("form") as HTMLFormElement);
    await waitFor(() => expect(where(router)).toBe("/recording?rid=r:plaud:rec-tour@00:20"));
  });
});
