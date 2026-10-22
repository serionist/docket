/**
 * The open card (DR-0468, DR-0786): its state, the entity or id it writes, what search misses, its spellings, and up
 * to three snippets cut from the index's chunks now, each with the spelling under the highlighter and its place on
 * the Search page. An open card takes Accept, Edit or Reject; Edit opens its form in the panel: for a new card its
 * type, id, a name per language and role, and for any card the spellings to keep, each plain or asr; the ones left out
 * are rejected as its aliases. A decision carries the round and the entity file's hash the card was shown with.
 */
import { AppLink } from "@/components/app-link";
import { Note } from "@/components/doc";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { CardPanel, Edited, EditForm } from "./api";
import { CardState } from "./round";

/** The edit form as typed: the owner's own until Accept as edited sends it. */
export interface Draft {
  type: string;
  id: string;
  names: Record<string, string>;
  role: string;
  keep: number[];
  kinds: Record<string, string>;
}

export function draftOf(form: EditForm): Draft {
  return {
    type: form.type,
    id: form.id,
    names: Object.fromEntries(form.names.map((name) => [name.code, name.value])),
    role: form.role,
    keep: form.spellings.filter((s) => s.checked).map((s) => s.i),
    kinds: Object.fromEntries(form.spellings.map((s) => [String(s.i), s.kind])),
  };
}

/** What a decision posts for the form: the spellings by their place, and for a new card its entity. */
export function editedOf(form: EditForm, draft: Draft): Edited {
  const kept = { keep: [...draft.keep].sort((a, b) => a - b), kinds: draft.kinds };
  if (!form.new) return { ...kept, type: "", id: "", names: {}, role: "" };
  return { ...kept, type: draft.type, id: draft.id, names: draft.names, role: draft.role };
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function Form({ form, draft, onDraft }: { form: EditForm; draft: Draft; onDraft: (draft: Draft) => void }) {
  return (
    <form
      className="grid gap-3 border-t border-rule px-4 pt-3"
      aria-labelledby="edit-title"
      onSubmit={(event) => event.preventDefault()}
    >
      <h3 id="edit-title" className="text-xs font-semibold text-ink-muted">
        As you edit it
      </h3>
      {form.new && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="grid gap-1">
            <label htmlFor="edit-type" className="text-xs text-ink-muted">
              Type
            </label>
            <Select value={draft.type} onValueChange={(type) => onDraft({ ...draft, type })}>
              <SelectTrigger id="edit-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {form.types.map((type) => (
                  <SelectItem key={type} value={type}>
                    {type}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="grid gap-1">
            <span className="text-xs text-ink-muted">Id</span>
            <Input
              id="edit-id"
              value={draft.id}
              spellCheck={false}
              autoComplete="off"
              className="font-mono"
              onChange={(event) => onDraft({ ...draft, id: event.target.value })}
            />
          </label>
          {form.names.map(({ code }) => (
            <label key={code} className="grid gap-1">
              <span className="text-xs text-ink-muted">Name in {code}</span>
              <Input
                id={`edit-name-${code}`}
                value={draft.names[code] ?? ""}
                dir="auto"
                autoComplete="off"
                onChange={(event) => onDraft({ ...draft, names: { ...draft.names, [code]: event.target.value } })}
              />
            </label>
          ))}
          <label className="grid gap-1">
            <span className="text-xs text-ink-muted">Role</span>
            <Input
              id="edit-role"
              value={draft.role}
              dir="auto"
              autoComplete="off"
              onChange={(event) => onDraft({ ...draft, role: event.target.value })}
            />
          </label>
        </div>
      )}
      <fieldset className="grid min-w-0 gap-1">
        <legend className="mb-1 text-xs text-ink-muted">
          Spellings to keep as aliases; the rest are rejected as its aliases
        </legend>
        {form.spellings.length ? (
          form.spellings.map((s) => {
            const id = `keep-${s.i}`;
            const kept = draft.keep.includes(s.i);
            return (
              <div key={s.i} className="flex min-h-8 flex-wrap items-center gap-x-2 gap-y-1" data-spelling={s.i}>
                <Checkbox
                  id={id}
                  checked={kept}
                  onCheckedChange={(on) =>
                    onDraft({
                      ...draft,
                      keep: on === true ? [...new Set([...draft.keep, s.i])] : draft.keep.filter((i) => i !== s.i),
                    })
                  }
                />
                <label htmlFor={id} className="min-w-0 flex-1 cursor-default text-sm">
                  <span dir="auto">{s.text}</span> <span className="text-xs text-ink-muted">({s.trust})</span>
                </label>
                <Select
                  value={draft.kinds[String(s.i)] ?? "plain"}
                  onValueChange={(kind) => onDraft({ ...draft, kinds: { ...draft.kinds, [String(s.i)]: kind } })}
                >
                  <SelectTrigger className="w-24" aria-label={`${s.text} as`} disabled={!kept}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="plain">plain</SelectItem>
                    <SelectItem value="asr">asr</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            );
          })
        ) : (
          <p className="text-sm text-ink-muted">Nothing to add but the name.</p>
        )}
      </fieldset>
    </form>
  );
}

export function CardBody({
  card,
  draft,
  changed,
  refused,
  onDraft,
}: {
  card: CardPanel;
  draft: Draft | null;
  /** Why the last decision wrote nothing because the entity file moved, said above the card as it stands now. */
  changed: string;
  refused: string;
  onDraft: (draft: Draft) => void;
}) {
  return (
    <div className="grid gap-3 pb-4">
      <div className="grid gap-3 px-4 pt-3">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
          <div className="grid gap-0.5">
            <dt className="text-xs text-ink-muted">state</dt>
            <dd>
              <CardState state={card.state} />
            </dd>
          </div>
          <div className="grid min-w-0 gap-0.5">
            <dt className="text-xs text-ink-muted">{card.new ? "its id" : "entity"}</dt>
            <dd>
              <code className="font-mono text-xs break-all">{card.entity}</code>
            </dd>
          </div>
          <div className="grid gap-0.5">
            <dt className="text-xs text-ink-muted">type</dt>
            <dd className="text-sm">{card.type}</dd>
          </div>
          {card.newest && (
            <div className="grid gap-0.5">
              <dt className="text-xs text-ink-muted">newest</dt>
              <dd className="text-sm tabular-nums">{card.newest}</dd>
            </div>
          )}
        </dl>
        <p className="text-sm">{card.missed.charAt(0).toUpperCase() + card.missed.slice(1)}.</p>
        {changed && <Note tone="warn">{changed}</Note>}
        {refused && <Refusal>{refused}</Refusal>}
        {card.state !== "open" && <Note>Decided: {card.state}. The log undoes a decision.</Note>}
      </div>
      <section aria-labelledby="spellings-title" className="grid gap-1 px-4">
        <h3 id="spellings-title" className="text-xs font-semibold text-ink-muted">
          Spellings
        </h3>
        <ul className="grid gap-1">
          {card.spellings.map((s, at) => (
            <li key={at} className="flex flex-wrap items-baseline gap-x-2 text-sm">
              <span dir="auto" className="font-semibold">
                {s.text}
              </span>
              <span className="text-xs text-ink-muted">
                {s.trust}; {s.add}; in {plural(s.chunks, "chunk", "chunks")}
              </span>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="where-title" className="grid gap-2 px-4">
        <h3 id="where-title" className="text-xs font-semibold text-ink-muted">
          Where it is
        </h3>
        {card.snippets.length ? (
          card.snippets.map((s) => (
            <figure key={s.locator} className="grid gap-1 border-l-2 border-rule pl-3" data-snippet>
              <blockquote dir="auto" className="text-sm break-words">
                {s.before}
                {s.mark && <mark className="rounded-[2px] bg-mark px-px text-ink">{s.mark}</mark>}
                {s.after}
              </blockquote>
              <figcaption>
                <AppLink
                  href={s.search}
                  title="Open it in Search"
                  className="inline-flex min-h-6 items-center font-mono text-xs break-all text-green underline-offset-4 hover:underline pointer-coarse:min-h-11"
                >
                  {s.locator}
                </AppLink>
              </figcaption>
            </figure>
          ))
        ) : (
          <p className="text-sm text-ink-muted">No snippet to show: the index doesn't hold its chunks now.</p>
        )}
      </section>
      {card.form && draft && <Form form={card.form} draft={draft} onDraft={onDraft} />}
    </div>
  );
}

/** The panel's foot: an open card's Accept, Edit and Reject, or the form's Accept as edited and Cancel. */
export function CardActions({
  card,
  pending,
  onAccept,
  onEdit,
  onReject,
  onSaveEdit,
  onCancel,
}: {
  card: CardPanel;
  pending: boolean;
  onAccept: () => void;
  onEdit: () => void;
  onReject: () => void;
  onSaveEdit: () => void;
  onCancel: () => void;
}) {
  if (card.state !== "open") return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5 border-t border-rule bg-paper px-3 py-2")} data-actions>
      {card.form ? (
        <>
          <Button variant="default" disabled={pending} onClick={onSaveEdit}>
            Accept as edited
          </Button>
          <Button disabled={pending} onClick={onCancel}>
            Cancel
          </Button>
        </>
      ) : (
        <>
          <Button variant="default" disabled={pending} onClick={onAccept}>
            Accept
          </Button>
          {card.editable && (
            <Button disabled={pending} onClick={onEdit}>
              Edit
            </Button>
          )}
          <Button variant="destructive" className="ml-auto" disabled={pending} onClick={onReject}>
            Reject
          </Button>
        </>
      )}
    </div>
  );
}
