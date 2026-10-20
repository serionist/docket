/**
 * The new item form, which n opens (DR-0710). What is typed stays in the page while it is closed, as the old form's
 * did, and through a refusal. An add the writer refuses as a look-alike names the open items it looks like, each a
 * link to its panel, beside Add anyway, which adds it again with force. Who suggests the names already in use
 * (DR-0714). An item added here gets the source page:<day>T<hh:mm> unless one is given.
 */
import * as React from "react";
import { DatePicker } from "@/components/date-picker";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useUnsaved } from "@/lib/drafts";
import { returnFocus } from "@/lib/focus";
import type { Row } from "./api";
import { useActions } from "./context";
import { WhoField } from "./who-field";

export interface NewDraft {
  title: string;
  kind: string;
  who: string;
  stream: string;
  due: string;
  description: string;
  source: string;
}

export function blankDraft(stream = ""): NewDraft {
  return { title: "", kind: "task", who: "", stream, due: "", description: "", source: "" };
}

function typedIn(draft: NewDraft): boolean {
  return Boolean(draft.title || draft.who || draft.due || draft.description || draft.source);
}

export function NewItem({
  open,
  onOpenChange,
  draft,
  onDraft,
  onAdded,
  onShow,
  rowHref,
  returnTo,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  draft: NewDraft;
  onDraft: (draft: NewDraft) => void;
  /** The item added: the form empties and the item opens. */
  onAdded: (ref: string) => void;
  /** An item it looks like, opened with the form's text kept for n to bring back. */
  onShow: (ref: string) => void;
  rowHref: (ref: string) => string;
  /** What gets the focus back once the form closes. */
  returnTo: React.RefObject<Element | null>;
}) {
  const { write, pending, kinds, today } = useActions();
  const [problem, setProblem] = React.useState("");
  const [alikes, setAlikes] = React.useState<Row[]>([]);
  useUnsaved("new:item", typedIn(draft));
  const set = (key: keyof NewDraft) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    onDraft({ ...draft, [key]: event.target.value });
  const add = async (force: boolean) => {
    const fields = Object.fromEntries(Object.entries(draft).filter(([, value]) => value.trim() !== ""));
    const outcome = await write({ op: "add", fields, force });
    if (outcome.ok) {
      setProblem("");
      setAlikes([]);
      onAdded(outcome.answer.added ?? "");
      return;
    }
    setProblem(outcome.error);
    setAlikes(outcome.alikes);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="top-4 max-h-[calc(100dvh-2rem)] max-w-xl overflow-y-auto sm:top-[min(20vh,160px)]"
        onCloseAutoFocus={(event) => returnFocus(event, returnTo.current)}
      >
        <DialogHeader>
          <DialogTitle>New item</DialogTitle>
          <DialogDescription>
            Something you owe or await. Fields left empty take the writer's defaults.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void add(false);
          }}
        >
          <div className="grid gap-1 sm:col-span-2">
            <label htmlFor="new-title" className="text-xs font-semibold text-ink-muted">
              Title
            </label>
            <Input id="new-title" value={draft.title} onChange={set("title")} autoComplete="off" dir="auto" />
          </div>
          <div className="grid gap-1">
            <label htmlFor="new-kind" className="text-xs font-semibold text-ink-muted">
              Kind
            </label>
            <Select value={draft.kind} onValueChange={(kind) => onDraft({ ...draft, kind })}>
              <SelectTrigger id="new-kind" className="w-full">
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
          </div>
          <div className="grid gap-1">
            <label htmlFor="new-who" className="text-xs font-semibold text-ink-muted">
              Who
            </label>
            <WhoField
              id="new-who"
              value={draft.who}
              onChange={(who) => onDraft({ ...draft, who })}
              onPick={(who) => onDraft({ ...draft, who })}
            />
          </div>
          <div className="grid gap-1">
            <label htmlFor="new-stream" className="text-xs font-semibold text-ink-muted">
              Stream
            </label>
            <Input
              id="new-stream"
              value={draft.stream}
              onChange={set("stream")}
              list="ledger-streams"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="grid gap-1">
            <label htmlFor="new-due" className="text-xs font-semibold text-ink-muted">
              Due
            </label>
            <DatePicker
              id="new-due"
              value={draft.due}
              today={today}
              label="Due"
              clear="No due date"
              onPick={(day) => onDraft({ ...draft, due: day })}
              className="w-full"
            />
          </div>
          <div className="grid gap-1 sm:col-span-2">
            <label htmlFor="new-description" className="text-xs font-semibold text-ink-muted">
              Description
            </label>
            <Textarea
              id="new-description"
              rows={2}
              value={draft.description}
              onChange={set("description")}
              dir="auto"
            />
          </div>
          <div className="grid gap-1 sm:col-span-2">
            <label htmlFor="new-source" className="text-xs font-semibold text-ink-muted">
              Source, a link or a locator (optional)
            </label>
            <Input
              id="new-source"
              value={draft.source}
              onChange={set("source")}
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-xs"
            />
          </div>
          {problem && <Refusal className="sm:col-span-2">{problem}</Refusal>}
          {alikes.length > 0 && (
            <div className="grid gap-2 sm:col-span-2" data-slot="alikes">
              <p className="text-sm">It looks like {alikes.length === 1 ? "this open item" : "these open items"}:</p>
              <ul className="grid gap-1">
                {alikes.map((row) => (
                  <li key={row.id} className="flex min-w-0 items-baseline gap-2 text-sm">
                    <a
                      href={rowHref(row.ref)}
                      onClick={(event) => {
                        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
                        event.preventDefault();
                        onShow(row.ref);
                      }}
                      className="inline-flex min-h-6 min-w-0 items-center gap-1 truncate pointer-coarse:min-h-11 text-green underline-offset-4 hover:underline"
                    >
                      <span className="font-mono text-xs">{row.ref}</span> <span dir="auto">{row.title}</span>
                    </a>
                    <span className="shrink-0 text-xs text-ink-muted" dir="auto">
                      {row.who}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <DialogFooter className="sm:col-span-2">
            {alikes.length > 0 && (
              <Button type="button" disabled={pending} onClick={() => void add(true)}>
                Add anyway
              </Button>
            )}
            <Button type="submit" variant="default" disabled={pending}>
              Add
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
