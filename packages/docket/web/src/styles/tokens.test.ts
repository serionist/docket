import { describe, expect, it } from "vitest";
import css from "./tokens.css?raw";

// The floors DR-0707 holds: text 4.5:1; a control's edge, the focus ring and icons 3:1 (WCAG 1.4.3, 1.4.11).
const TEXT = 4.5;
const EDGE = 3;
const GROUNDS = ["paper", "sheet", "selection", "hover"];
const INKS = ["ink", "ink-muted", "green", "red", "discuss", "claude", "me", "chase"];

type Pair = [string, string];

function tokens(): Map<string, Pair> {
  const found = new Map<string, Pair>();
  for (const m of css.matchAll(/--([a-z-]+):\s*light-dark\((#[0-9a-f]{6}),\s*(#[0-9a-f]{6})\)/g)) {
    found.set(m[1] as string, [m[2] as string, m[3] as string]);
  }
  return found;
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((at) => {
    const c = parseInt(hex.slice(at, at + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const checks: [string, string, number][] = [
  ...INKS.flatMap((ink) => GROUNDS.map((ground): [string, string, number] => [ink, ground, TEXT])),
  ...["discuss", "claude", "me", "chase"].map((v): [string, string, number] => [v, `${v}-wash`, TEXT]),
  ["on-green", "green", TEXT],
  ["ink", "red-wash", TEXT],
  ["red", "red-wash", TEXT],
  ["sheet", "ink", TEXT],
  ...["control", "green"].flatMap((edge) => GROUNDS.map((ground): [string, string, number] => [edge, ground, EDGE])),
];

describe("the tokens' contrast", () => {
  const found = tokens();

  it("has every token the checks name, each a light-dark() pair of hex colours", () => {
    const named = new Set(checks.flatMap(([a, b]) => [a, b]));
    expect([...named].filter((name) => !found.has(name))).toEqual([]);
  });

  it.each(checks)("%s on %s holds %s:1 in both themes", (fg, bg, floor) => {
    const [fgLight, fgDark] = found.get(fg) as Pair;
    const [bgLight, bgDark] = found.get(bg) as Pair;
    expect(ratio(fgLight, bgLight)).toBeGreaterThanOrEqual(floor);
    expect(ratio(fgDark, bgDark)).toBeGreaterThanOrEqual(floor);
  });

  it("computes WCAG's ratio", () => {
    expect(ratio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(ratio("#777777", "#777777")).toBe(1);
  });
});
