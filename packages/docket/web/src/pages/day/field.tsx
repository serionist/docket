/**
 * A field of the open item, saved on change (DR-0710): a text field when it is left or Enter is pressed in it, a
 * select or a day as it changes, and who as a text field that also saves a name taken from its list (DR-0714). It is
 * unsaved from its first change until its save answers, and a refused one stays unsaved with the writer's words
 * beside it, its text as typed. A newer value read from the server replaces only text the owner hasn't changed.
 */
import * as React from "react";
import { DatePicker } from "@/components/date-picker";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { setUnsaved, startSave, useUnsaved } from "@/lib/drafts";
import { cn } from "@/lib/utils";
import type { Body, ItemPanel } from "./api";
import { refusal, useActions } from "./context";
import { WhoField } from "./who-field";

export type FieldName = "title" | "who" | "due" | "stream" | "kind" | "description" | "notes" | "outcome";
type Shape = "line" | "person" | "date" | "area" | "select" | "title";

const SHAPES: Record<FieldName, Shape> = {
  title: "title",
  who: "person",
  due: "date",
  stream: "line",
  kind: "select",
  description: "area",
  notes: "area",
  outcome: "area",
};

/** A field's place in the drafts: the panel's, by item and name. */
export function draftKey(item: string, name: string): string {
  return `panel:${item}:${name}`;
}

function bodyOf(item: string, name: FieldName, value: string): Body {
  return name === "notes"
    ? { op: "notes", items: [item], notes: value }
    : { op: "set", items: [item], fields: { [name]: value } };
}

/** Escape leaves a field and keeps what it holds, as the page's other fields do. */
export function leaveOnEscape(event: React.KeyboardEvent<HTMLElement>): void {
  if (event.key === "Escape" && !event.defaultPrevented) {
    event.preventDefault();
    event.currentTarget.blur();
  }
}

export function Field({
  item,
  name,
  label,
  value,
  className,
  rows,
}: {
  item: string;
  name: FieldName;
  label: string;
  value: string;
  className?: string;
  rows?: number;
}) {
  const { save, kinds, today } = useActions();
  const shape = SHAPES[name];
  const id = `f-${item}-${name}`;
  const [draft, setDraft] = React.useState(value);
  const [seen, setSeen] = React.useState(value);
  const [problem, setProblem] = React.useState("");
  if (value !== seen) {
    setSeen(value);
    if (draft === seen) setDraft(value);
  }
  const dirty = draft !== value;
  const key = draftKey(item, name);
  useUnsaved(key, dirty);
  // What is typed now, for a save that answers after more was typed.
  const typed = React.useRef(draft);
  React.useLayoutEffect(() => {
    typed.current = draft;
  });
  // The value a save in flight sends, so leaving the field just after a name was taken doesn't send it twice.
  const sending = React.useRef<string | null>(null);

  const commit = async (next: string) => {
    if (next === value) {
      setProblem("");
      return;
    }
    if (next === sending.current) return;
    sending.current = next;
    const done = startSave();
    const outcome = await save(bodyOf(item, name, next));
    sending.current = null;
    setProblem(refusal(outcome));
    if (outcome.ok && typed.current === next) {
      // What the writer kept, which may differ from what was typed, such as a title's spaces. The drafts hear of it
      // before the save ends, so a panel waiting on the save never asks about text just saved.
      const kept = String(outcome.answer.panel[name as keyof ItemPanel] ?? "");
      setDraft(kept);
      setSeen(kept);
      setUnsaved(key, false);
    }
    done();
  };

  const common = {
    id,
    "aria-invalid": problem ? true : undefined,
    "aria-describedby": problem ? `${id}-problem` : undefined,
  };
  let control: React.ReactNode;
  if (shape === "select") {
    control = (
      <Select
        value={draft}
        onValueChange={(next) => {
          setDraft(next);
          void commit(next);
        }}
      >
        <SelectTrigger {...common} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {kinds.map((kind) => (
            <SelectItem key={kind} value={kind}>
              {kind}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  } else if (shape === "date") {
    control = (
      <DatePicker
        id={id}
        value={draft}
        today={today}
        label={label}
        clear="No due date"
        problem={problem ? `${id}-problem` : undefined}
        onPick={(next) => {
          setDraft(next);
          void commit(next);
        }}
        className="w-full"
      />
    );
  } else if (shape === "area" || shape === "title") {
    control = (
      <Textarea
        {...common}
        dir="auto"
        rows={rows ?? 2}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => void commit(draft)}
        onKeyDown={(event) => {
          // The title is one line: Enter saves it, as leaving it does.
          if (shape === "title" && event.key === "Enter") {
            event.preventDefault();
            event.currentTarget.blur();
            return;
          }
          leaveOnEscape(event);
        }}
        className={cn(
          shape === "title" &&
            "min-h-0 border-transparent bg-transparent px-1 py-0.5 text-lg font-semibold text-pretty hover:border-control focus-visible:bg-sheet pointer-coarse:min-h-11",
          shape === "area" && "min-h-9 text-base pointer-coarse:min-h-11",
        )}
      />
    );
  } else if (shape === "person") {
    control = (
      <WhoField
        {...common}
        value={draft}
        onChange={setDraft}
        onPick={(next) => {
          setDraft(next);
          void commit(next);
        }}
        onBlur={() => void commit(draft)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            event.currentTarget.blur();
            return;
          }
          leaveOnEscape(event);
        }}
      />
    );
  } else {
    control = (
      <Input
        {...common}
        dir="auto"
        autoComplete="off"
        spellCheck={name === "stream" ? false : undefined}
        list={name === "stream" ? "ledger-streams" : undefined}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => void commit(draft)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            event.currentTarget.blur();
            return;
          }
          leaveOnEscape(event);
        }}
      />
    );
  }
  return (
    <div className={cn("grid content-start gap-1", className)} data-field={name} data-unsaved={dirty ? "" : undefined}>
      <div className={cn("flex min-h-5 items-center gap-2", shape === "title" && "sr-only")}>
        <label htmlFor={id} className="text-xs font-semibold text-ink-muted">
          {label}
        </label>
        {dirty && (
          <span className="rounded-sm bg-me-wash px-1 text-xs text-me" data-slot="unsaved">
            unsaved
          </span>
        )}
      </div>
      {control}
      {shape === "title" && dirty && (
        <span className="justify-self-start rounded-sm bg-me-wash px-1 text-xs text-me" data-slot="unsaved">
          unsaved
        </span>
      )}
      {problem && (
        <p id={`${id}-problem`} role="alert" className="text-xs text-red">
          {problem}
        </p>
      )}
    </div>
  );
}
