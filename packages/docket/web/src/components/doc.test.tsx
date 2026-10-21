/**
 * The raw a panel shows: two lines that start in the same second share a locator, as the server writes it to the
 * second, and the panel read again wider or narrower keeps showing each line once.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { components } from "@/api/schema";
import { RawSpan } from "./doc";

type SpanView = components["schemas"]["SpanView"];

function span(lines: [string, string][]): SpanView {
  return {
    rid: "calls:rec-0100",
    anchor: "[r:calls:rec-0100@00:10]",
    head: "recording - 7 Oct 2026 10:00",
    versions: "",
    problem: null,
    segments: lines.map(([at, text]) => ({
      at,
      locator: `r:calls:rec-0100@${at}`,
      speaker: "Ada Example",
      text,
      mark: at === "00:10",
    })),
    paragraphs: [],
    mark: false,
    note: null,
  };
}

describe("a raw span's lines", () => {
  it("show each line once when two start in the same second and the span grows at both ends", () => {
    const narrow = span([
      ["00:09", "line a"],
      ["00:10", "line b"],
      ["00:10", "line c"],
      ["00:11", "line d"],
    ]);
    const { rerender } = render(<RawSpan span={narrow} onCopy={() => undefined} />);
    const wider = span([
      ["00:08", "line z"],
      ["00:09", "line a"],
      ["00:10", "line b"],
      ["00:10", "line c"],
      ["00:11", "line d"],
      ["00:12", "line e"],
    ]);
    rerender(<RawSpan span={wider} onCopy={() => undefined} />);
    const shown = screen.getAllByRole("listitem").map((li) => li.textContent?.replace(/^.*?Ada Example/, ""));
    expect(shown).toEqual(["line z", "line a", "line b", "line c", "line d", "line e"]);
    rerender(<RawSpan span={narrow} onCopy={() => undefined} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });
});
