/**
 * A meeting's talking points (DR-0667), each named by its id, never its n: its mark ticks it answered or open again;
 * an open point is answered, edited, dropped or made an item; an answered or dropped one opens again; a rolled one
 * shows where it went and takes no button, since only an undo changes it. A point Claude proposed is drawn in pencil,
 * a dashed rule, until it is kept or removed, one at a time or all of them. Every button is one write with its notice
 * and Undo; an answer or a text being typed is unsaved until it is sent, so the guard asks before it goes.
 */
import { CheckIcon, CircleCheckIcon, CircleIcon, CircleSlashIcon, CopyIcon, CornerDownRightIcon } from "lucide-react";
import * as React from "react";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useUnsaved } from "@/lib/drafts";
import { cn } from "@/lib/utils";
import type { Body, MeetingPanel, PointLine } from "@/pages/day/api";
import { refusal, useActions } from "@/pages/day/context";
import { draftKey, leaveOnEscape } from "@/pages/day/field";
import { WhoField } from "@/pages/day/who-field";
import { InLink } from "./links";

type What = "answer" | "text" | "item";
interface Editing {
  point: string;
  what: What;
}
/** The writer's own choice for an item made from a point: "Name: what" waits on Name, anything else is a task. */
const DEFAULT = "default";

/** The open points as text, copied for an agenda (DR-0667). */
export function CopyAgenda({ agenda, className }: { agenda: string; className?: string }) {
  const [copied, setCopied] = React.useState(false);
  React.useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <Button
      variant="ghost"
      size="sm"
      className={cn("h-6 px-1.5 text-xs text-ink-muted", className)}
      disabled={!agenda}
      title={agenda ? "Copy the open points as an agenda" : "No open point to copy"}
      onClick={() => void navigator.clipboard?.writeText(agenda).then(() => setCopied(true))}
    >
      {copied ? <CheckIcon className="size-3.5 text-green" /> : <CopyIcon className="size-3.5" />}
      {copied ? "Copied" : "Copy agenda"}
      <span className="sr-only" aria-live="polite">
        {copied ? "The agenda is copied" : ""}
      </span>
    </Button>
  );
}

/** A point's mark: the tick that makes it answered or open again, or what it is when no tick changes it. */
function Mark({ point, disabled, onToggle }: { point: PointLine; disabled: boolean; onToggle: () => void }) {
  const box = "grid size-6 shrink-0 place-items-center rounded-sm pointer-coarse:size-11";
  if (point.state === "rolled" || point.state === "dropped") {
    const Icon = point.state === "rolled" ? CornerDownRightIcon : CircleSlashIcon;
    return (
      <span className={cn(box, "text-ink-muted")} title={point.state === "rolled" ? "rolled on" : "dropped"}>
        <Icon aria-hidden className="size-4" />
        <span className="sr-only">{point.state === "rolled" ? "rolled on" : "dropped"}</span>
      </span>
    );
  }
  const answered = point.state === "answered";
  return (
    <button
      type="button"
      aria-pressed={answered}
      aria-label={`Answered: ${point.text}`}
      title={answered ? "Answered; press to open it again" : "Mark it answered"}
      disabled={disabled}
      onClick={onToggle}
      className={cn(
        box,
        "tap cursor-default hover:bg-hover disabled:opacity-50",
        answered ? "text-green" : "text-ink-muted",
      )}
    >
      {answered ? <CircleCheckIcon aria-hidden className="size-4" /> : <CircleIcon aria-hidden className="size-4" />}
    </button>
  );
}

/** A quiet button on a point's line, its name saying which point it acts on to a screen reader. */
function Act({
  label,
  point,
  onClick,
  disabled,
  danger,
}: {
  label: string;
  point: PointLine;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={disabled}
      aria-label={`${label}: ${point.text}`}
      onClick={onClick}
      className={cn("h-6 px-1.5 text-xs text-ink-muted hover:text-ink", danger && "hover:bg-red-wash hover:text-red")}
    >
      {label}
    </Button>
  );
}

/** An answer or a point's text, typed and sent with Enter or Save; Esc lays it aside unsent. */
function TextEditor({
  meeting,
  point,
  what,
  onSend,
  onCancel,
}: {
  meeting: string;
  point: PointLine;
  what: "answer" | "text";
  onSend: (value: string) => Promise<boolean>;
  onCancel: () => void;
}) {
  const { pending } = useActions();
  const given = what === "answer" ? point.answer : point.text;
  const [value, setValue] = React.useState(given);
  useUnsaved(draftKey(meeting, `point:${point.id}:${what}`), value.trim() !== given.trim());
  const id = `point-${point.id}-${what}`;
  const send = () => void onSend(value);
  return (
    <form
      className="grid gap-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <label htmlFor={id} className="text-xs font-semibold text-ink-muted">
        {what === "answer" ? "The answer" : "The point"}
      </label>
      {what === "answer" ? (
        <Textarea
          id={id}
          autoFocus
          rows={2}
          dir="auto"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send();
            } else if (event.key === "Escape") {
              event.preventDefault();
              onCancel();
            }
          }}
          className="min-h-9 text-base pointer-coarse:min-h-11"
        />
      ) : (
        <Input
          id={id}
          autoFocus
          dir="auto"
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              onCancel();
            }
          }}
        />
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          {what === "answer" ? "Save the answer" : "Save the point"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** An item made from a point: the stream, the meeting's preselected, then a kind and a who, each left blank for the
 * writer's default (DR-0667). */
function ItemEditor({
  meeting,
  point,
  onSend,
  onCancel,
}: {
  meeting: MeetingPanel;
  point: PointLine;
  onSend: (fields: Record<string, string>) => Promise<boolean>;
  onCancel: () => void;
}) {
  const { pending, streams, kinds } = useActions();
  const [stream, setStream] = React.useState(meeting.stream || DEFAULT);
  const [kind, setKind] = React.useState(DEFAULT);
  const [who, setWho] = React.useState("");
  useUnsaved(draftKey(meeting.ref, `point:${point.id}:item`), who.trim() !== "");
  const id = `point-${point.id}-item`;
  const send = () =>
    void onSend({
      ...(stream !== DEFAULT ? { stream } : {}),
      ...(kind !== DEFAULT ? { kind } : {}),
      ...(who.trim() ? { who: who.trim() } : {}),
    });
  return (
    <form
      className="grid gap-2 rounded-sm border border-rule bg-paper p-2.5"
      aria-label={`Make an item of ${point.text}`}
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <div className="grid grid-cols-2 gap-2">
        <div className="grid gap-1">
          <span id={`${id}-stream`} className="text-xs font-semibold text-ink-muted">
            Stream
          </span>
          <Select value={stream} onValueChange={setStream}>
            <SelectTrigger aria-labelledby={`${id}-stream`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {!meeting.stream && <SelectItem value={DEFAULT}>pick one</SelectItem>}
              {streams.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1">
          <span id={`${id}-kind`} className="text-xs font-semibold text-ink-muted">
            Kind
          </span>
          <Select value={kind} onValueChange={setKind}>
            <SelectTrigger aria-labelledby={`${id}-kind`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={DEFAULT}>from the point</SelectItem>
              {kinds.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="grid gap-1">
        <label htmlFor={`${id}-who`} className="text-xs font-semibold text-ink-muted">
          Who
        </label>
        <WhoField
          id={`${id}-who`}
          value={who}
          placeholder="from the point"
          onChange={setWho}
          onPick={setWho}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              send();
            } else leaveOnEscape(event);
          }}
        />
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          Make the item
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Point({
  meeting,
  point,
  editing,
  problem,
  onEdit,
  onWrite,
}: {
  meeting: MeetingPanel;
  point: PointLine;
  editing: What | null;
  problem: string;
  onEdit: (what: What | null) => void;
  onWrite: (body: Partial<Body> & { op: string }) => Promise<boolean>;
}) {
  const { pending } = useActions();
  const live = !meeting.archived && point.state !== "rolled";
  const pencil = point.proposed && point.state !== "rolled";
  const set = (fields: Record<string, string>) => onWrite({ op: "point_set", point: point.id, fields });
  const sent = async (body: Partial<Body> & { op: string }) => {
    const ok = await onWrite(body);
    if (ok) onEdit(null);
    return ok;
  };
  return (
    <li
      data-point={point.id}
      data-state={point.state}
      className={cn(
        "grid gap-1 py-2",
        pencil ? "my-1 rounded-sm border border-dashed border-control px-1.5" : "border-t border-rule first:border-t-0",
      )}
    >
      <div className="flex items-start gap-2">
        <Mark
          point={point}
          disabled={pending || !live}
          onToggle={() => void set({ state: point.state === "answered" ? "open" : "answered" })}
        />
        <div className="grid min-w-0 flex-1 gap-1 pt-0.5 pointer-coarse:pt-3">
          <p
            className={cn(
              "text-sm break-words",
              point.state === "dropped" && "text-ink-muted line-through decoration-ink-muted",
              point.state === "rolled" && "text-ink-muted",
            )}
            dir="auto"
          >
            {point.text}
          </p>
          {point.answer && (
            <p className="border-l-2 border-green/60 pl-2 text-sm text-ink-muted break-words" dir="auto">
              <span className="sr-only">Answer: </span>
              {point.answer}
            </p>
          )}
          {(pencil || point.item || point.came_from || point.went_to) && (
            <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-muted">
              {pencil && <span>proposed, not kept yet</span>}
              {point.item && (
                <span>
                  about <InLink href={point.item.href}>{point.item.ref}</InLink>
                </span>
              )}
              {point.came_from && (
                <span>
                  carried from <InLink href={point.came_from.href}>{point.came_from.ref}</InLink>
                </span>
              )}
              {point.went_to && (
                <span>
                  rolled to <InLink href={point.went_to.href}>{point.went_to.ref}</InLink>
                </span>
              )}
            </p>
          )}
        </div>
      </div>
      {live && editing === null && (
        <div className="flex flex-wrap items-center gap-0.5 pl-7 pointer-coarse:pl-12">
          {pencil && (
            <Act
              label="Keep"
              point={point}
              disabled={pending}
              onClick={() => void onWrite({ op: "keep", point: point.id })}
            />
          )}
          {pencil && (
            <Act
              label="Remove"
              point={point}
              danger
              disabled={pending}
              onClick={() => void onWrite({ op: "remove", points: [point.id] })}
            />
          )}
          {point.state === "open" ? (
            <>
              <Act label="Answer" point={point} onClick={() => onEdit("answer")} />
              <Act label="Edit" point={point} onClick={() => onEdit("text")} />
              {!point.item && <Act label="Make an item" point={point} onClick={() => onEdit("item")} />}
              <Act
                label="Drop"
                point={point}
                danger
                disabled={pending}
                onClick={() => void set({ state: "dropped" })}
              />
            </>
          ) : (
            <>
              {point.state === "answered" && (
                <Act label="Edit the answer" point={point} onClick={() => onEdit("answer")} />
              )}
              <Act
                label="Open again"
                point={point}
                disabled={pending}
                onClick={() => void set(point.state === "dropped" ? { state: "open" } : { state: "open", answer: "" })}
              />
            </>
          )}
        </div>
      )}
      {live && editing !== null && (
        <div className="pl-7 pointer-coarse:pl-12">
          {editing === "item" ? (
            <ItemEditor
              meeting={meeting}
              point={point}
              onSend={(fields) => sent({ op: "point_item", point: point.id, fields })}
              onCancel={() => onEdit(null)}
            />
          ) : (
            <TextEditor
              meeting={meeting.ref}
              point={point}
              what={editing}
              onSend={(value) => sent({ op: "point_set", point: point.id, fields: { [editing]: value } })}
              onCancel={() => onEdit(null)}
            />
          )}
        </div>
      )}
      {problem && <Refusal className="ml-7">{problem}</Refusal>}
    </li>
  );
}

export function Points({ meeting }: { meeting: MeetingPanel }) {
  const { write, pending } = useActions();
  const [editing, setEditing] = React.useState<Editing | null>(null);
  const [problem, setProblem] = React.useState<{ point: string; words: string } | null>(null);
  const [text, setText] = React.useState("");
  useUnsaved(draftKey(meeting.ref, "point"), text.trim() !== "");
  const onWrite = (point: string) => async (body: Partial<Body> & { op: string }) => {
    const outcome = await write({ ...body, items: [meeting.ref] });
    setProblem(outcome.ok ? null : { point, words: refusal(outcome) });
    return outcome.ok;
  };
  const add = async () => {
    if (await onWrite("")({ op: "point", fields: { text } })) setText("");
  };
  const proposed = meeting.points.filter((p) => p.proposed && p.state !== "rolled").map((p) => p.id);
  const id = `points-${meeting.ref}`;
  return (
    <section aria-labelledby={id} className="grid gap-2">
      <div className="flex min-h-6 items-center gap-2">
        <h3 id={id} className="text-xs font-semibold text-ink-muted">
          Talking points
        </h3>
        <span className="text-xs text-ink-muted tabular-nums">{meeting.open_points} open</span>
        <CopyAgenda agenda={meeting.agenda} className="ml-auto" />
      </div>
      {meeting.points.length ? (
        <ol className="grid">
          {meeting.points.map((point) => (
            <Point
              key={point.id}
              meeting={meeting}
              point={point}
              editing={editing?.point === point.id ? editing.what : null}
              problem={problem?.point === point.id ? problem.words : ""}
              onEdit={(what) => setEditing(what ? { point: point.id, what } : null)}
              onWrite={onWrite(point.id)}
            />
          ))}
        </ol>
      ) : (
        <p className="text-sm text-ink-muted">No talking points yet: add one, or ask for the items worth raising.</p>
      )}
      {proposed.length > 1 && !meeting.archived && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          <span>{proposed.length} proposed, not kept yet</span>
          <Button size="sm" disabled={pending} onClick={() => void onWrite("")({ op: "keep" })}>
            Keep every proposed point
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-red hover:bg-red-wash"
            disabled={pending}
            onClick={() => void onWrite("")({ op: "remove", points: proposed })}
          >
            Remove them all
          </Button>
        </div>
      )}
      {!meeting.archived && (
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void add();
          }}
        >
          <div className="grid min-w-0 flex-1 gap-1">
            <label htmlFor={`point-${meeting.ref}`} className="text-xs font-semibold text-ink-muted">
              Add a point
            </label>
            <Input
              id={`point-${meeting.ref}`}
              value={text}
              dir="auto"
              autoComplete="off"
              onChange={(event) => setText(event.target.value)}
              onKeyDown={leaveOnEscape}
            />
          </div>
          <Button type="submit" disabled={pending}>
            Add
          </Button>
        </form>
      )}
      {problem?.point === "" && problem.words && <Refusal>{problem.words}</Refusal>}
    </section>
  );
}
