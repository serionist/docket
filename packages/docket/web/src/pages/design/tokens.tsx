/** The tokens (DR-0707), each shown as it renders in the theme in use, its value read from the page itself. */
import * as React from "react";
import { KINDS, KindTab } from "@/components/kind";
import { MEANING, Stamp, VERDICTS } from "@/components/stamp";
import { cn } from "@/lib/utils";

const COLOURS: [string, string][] = [
  ["paper", "the ground, and the sidebar"],
  ["sheet", "the list, the panel, menus and dialogs"],
  ["ink", "text"],
  ["ink-muted", "meta: refs, kinds, who, dates"],
  ["rule", "the line between rows"],
  ["control", "a field's or a button's edge"],
  ["hover", "a row or a control under the pointer"],
  ["selection", "the selected row"],
  ["green", "selection, focus, the main button"],
  ["red", "overdue, drop, errors"],
  ["red-wash", "behind a refusal"],
];

function hex(rgb: string): string {
  const parts =
    rgb
      .match(/\d+(\.\d+)?/g)
      ?.slice(0, 3)
      .map(Number) ?? [];
  return parts.length === 3 ? `#${parts.map((n) => Math.round(n).toString(16).padStart(2, "0")).join("")}` : rgb;
}

function Swatch({ name, role }: { name: string; role: string }) {
  const ref = React.useRef<HTMLSpanElement>(null);
  const [value, setValue] = React.useState("");
  React.useEffect(() => {
    const read = () => ref.current && setValue(hex(getComputedStyle(ref.current).backgroundColor));
    read();
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", read);
    return () => {
      observer.disconnect();
      scheme.removeEventListener("change", read);
    };
  }, []);
  return (
    <li className="flex items-center gap-3 border-b border-rule py-2">
      <span ref={ref} aria-hidden className={cn("size-8 shrink-0 rounded-sm border border-rule", `bg-${name}`)} />
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">{name}</span>
        <span className="block truncate text-xs text-ink-muted">{role}</span>
      </span>
      <code className="font-mono text-xs text-ink-muted">{value}</code>
    </li>
  );
}

// Tailwind finds class names in the source as written, so each swatch's class is spelt out once here.
export const SWATCH_CLASSES =
  "bg-paper bg-sheet bg-ink bg-ink-muted bg-rule bg-control bg-hover bg-selection bg-green bg-red bg-red-wash";

// What each kind holds, as the design names them.
const KIND_MEANING: Record<(typeof KINDS)[number], string> = {
  reply: "a message you owe an answer",
  decision: "a call you have to make",
  task: "something to do",
  invite: "a meeting to answer",
  waiting: "on someone else, to chase",
};

const SCALE: [string, string, string][] = [
  ["text-xl font-semibold", "21 / 28", "A page's title"],
  ["text-lg font-semibold", "18 / 24", "The panel's title"],
  ["text-base", "16 / 24", "The panel's text, a note or a summary you read through"],
  ["text-sm", "14 / 20", "The interface: rows, buttons, fields, the sidebar"],
  ["text-xs", "12 / 16", "Meta: who, when, a ref's kind, a group's count"],
];

function Section({ title, id, children }: { title: string; id: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="grid content-start gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function DesignTokens() {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto" role="region" aria-label="Tokens" tabIndex={0}>
      <div className="grid max-w-5xl gap-10 px-4 py-6 sm:px-6 lg:grid-cols-2">
        <Section title="Colour" id="colour">
          <ul className="border-t border-rule">
            {COLOURS.map(([name, role]) => (
              <Swatch key={name} name={name} role={role} />
            ))}
          </ul>
        </Section>
        <div className="grid content-start gap-10">
          <Section title="The calls" id="calls">
            <ul className="border-t border-rule">
              {VERDICTS.map((verdict) => (
                <li key={verdict} className="flex items-center gap-3 border-b border-rule py-2">
                  <span className="w-20">
                    <Stamp verdict={verdict} />
                  </span>
                  <span className="text-sm">{MEANING[verdict]}</span>
                </li>
              ))}
            </ul>
          </Section>
          <Section title="The kinds" id="kinds">
            <ul className="border-t border-rule">
              {KINDS.map((kind) => (
                <li key={kind} className="flex items-center gap-3 border-b border-rule py-2">
                  <span className="w-20">
                    <KindTab kind={kind} />
                  </span>
                  <span className="text-sm">{KIND_MEANING[kind]}</span>
                </li>
              ))}
            </ul>
          </Section>
          <Section title="Type" id="type">
            <ul className="border-t border-rule">
              {SCALE.map(([classes, size, what]) => (
                <li key={size} className="grid grid-cols-[4rem_1fr] items-baseline gap-3 border-b border-rule py-2">
                  <span className="text-xs text-ink-muted tabular-nums">{size}</span>
                  <span className={classes}>{what}</span>
                </li>
              ))}
              <li className="grid grid-cols-[4rem_1fr] items-baseline gap-3 border-b border-rule py-2">
                <span className="text-xs text-ink-muted">Mono</span>
                <span className="grid gap-0.5 font-mono text-xs">
                  <span>r:calls:rec-0100@01:50-03:20</span>
                  <span>projects/pilot/STATUS.md</span>
                </span>
              </li>
              <li className="grid grid-cols-[4rem_1fr] items-baseline gap-3 border-b border-rule py-2">
                <span className="text-xs text-ink-muted">Accents</span>
                <span>Árvíztűrő tükörfúrógép, Łódź, Ærøskøbing, Ştefan</span>
              </li>
              <li className="grid grid-cols-[4rem_1fr] items-baseline gap-3 border-b border-rule py-2">
                <span className="text-xs text-ink-muted">Figures</span>
                <span className="grid grid-cols-3 gap-2 text-right tabular-nums">
                  <span>1,204</span>
                  <span>98</span>
                  <span>11.50</span>
                  <span>36</span>
                  <span>7,310</span>
                  <span>0.25</span>
                </span>
              </li>
            </ul>
          </Section>
          <Section title="Space and shape" id="shape">
            <div className="grid grid-cols-2 items-end gap-x-4 gap-y-5 text-xs text-ink-muted sm:grid-cols-4">
              <figure className="col-span-2 grid gap-1 sm:col-span-1">
                <div className="flex h-8 items-center border-y border-rule bg-sheet px-2 text-sm text-ink">
                  A row, 32 px
                </div>
                <figcaption>44 px on a touch screen</figcaption>
              </figure>
              {(
                [
                  ["rounded-sm", "4 px: controls"],
                  ["rounded-lg", "8 px: menus"],
                  ["rounded-xl", "12 px: dialogs"],
                ] as const
              ).map(([radius, what]) => (
                <figure key={radius} className="grid gap-1">
                  <div className={cn("h-10 w-16 border border-control bg-sheet", radius)} />
                  <figcaption>{what}</figcaption>
                </figure>
              ))}
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
