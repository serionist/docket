/**
 * A who field with its names (DR-0714): shadcn's combobox, a Popover with a Command list, under a text field that
 * stays a text field. Typing narrows the names and opens the list, the first name ready, the name typed first when
 * there is one; the arrows move through it, Enter takes the name marked, and the text as typed is always the list's
 * last choice, so a new name is never refused. With no text nothing is marked, so Enter is the field's own and
 * clears it as it did. Esc closes the list alone, its default taken, so neither the field's Esc nor the page's acts
 * on it. With the list closed the field's own keys are its: Enter saves or submits as it did, Esc leaves it.
 */
import { ChevronsUpDownIcon } from "lucide-react";
import * as React from "react";
import { Command, CommandItem, CommandList } from "@/components/ui/command";
import { field } from "@/components/ui/input";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { type Named, named, suggest } from "@/lib/who";

const TYPED = "typed";
// The mark when none is wanted: a value no choice has, which cmdk keeps rather than marking its first.
const NONE = "none";
// A name's choice goes by the name, so a refetch that reorders the names keeps the mark on the same one.
const choiceOf = (name: string) => `name:${name}`;
// Keys the list takes while it is open; Home and End stay the field's, to move in the text.
const LIST_KEYS = new Set(["ArrowDown", "ArrowUp", "Enter"]);

export interface WhoBoxProps {
  id?: string;
  value: string;
  /** The text as it is typed. */
  onChange: (value: string) => void;
  /** A name taken from the list, or the text as typed taken from it: the field's change at once. */
  onPick: (name: string) => void;
  /** The names, the most used first; none while they are read. */
  people: readonly Named[];
  /** Whether the names are being read and none are here yet. */
  loading?: boolean;
  /** The list opened or closed, so the names are read only while someone looks at them. */
  onOpenChange?: (open: boolean) => void;
  /** The field's own keys while the list is closed. */
  onKeyDown?: (event: React.KeyboardEvent<HTMLInputElement>) => void;
  onBlur?: (event: React.FocusEvent<HTMLInputElement>) => void;
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

export function WhoBox({
  id: given,
  value,
  onChange,
  onPick,
  people,
  loading = false,
  onOpenChange,
  onKeyDown,
  onBlur,
  placeholder,
  className,
  ...aria
}: WhoBoxProps) {
  const fallback = React.useId();
  const id = given ?? fallback;
  const [open, setOpenState] = React.useState(false);
  // The choice the arrows or the pointer marked since the text last changed; until then the first is marked, or
  // none while there is no text.
  const [moved, setMoved] = React.useState<string | null>(null);
  // The list once shown, which cmdk names, so the field can say it controls it.
  const [list, setList] = React.useState<HTMLDivElement | null>(null);
  // The same list for a handler to scroll, which a value held in state isn't to be changed through.
  const shownList = React.useRef<HTMLDivElement | null>(null);
  const listed = React.useCallback((node: HTMLDivElement | null) => {
    shownList.current = node;
    setList(node);
  }, []);
  const wrap = React.useRef<HTMLDivElement>(null);
  const input = React.useRef<HTMLInputElement>(null);

  const shown = React.useMemo(() => suggest(people, value), [people, value]);
  const typed = value.trim();
  const asTyped = typed !== "" && !named(shown, typed);
  const choices = shown.length + (asTyped ? 1 : 0);
  const showing = open && (choices > 0 || loading);
  const first = typed === "" ? NONE : shown[0] ? choiceOf(shown[0].name) : asTyped ? TYPED : NONE;
  const active = moved ?? first;

  const setOpen = (next: boolean) => {
    if (next === open) return;
    setOpenState(next);
    setMoved(null);
    onOpenChange?.(next);
  };

  // The marked choice, as a screen reader follows it from the field. cmdk marks it on its items, at times after this
  // component renders, so the field follows the marks as they change.
  React.useEffect(() => {
    const field_ = input.current;
    if (!list || !field_) return;
    const follow = () => {
      const marked = list.querySelector<HTMLElement>('[cmdk-item][data-selected="true"]');
      // cmdk's list names its own marked item too, but keeps the old one's id when the field set the mark, so both
      // name the one marked now: an id no element holds is no name at all.
      for (const element of [field_, list]) {
        // Set only on a change, since setting it at all is a change the observer hears.
        const now = element.getAttribute("aria-activedescendant");
        if (marked && now !== marked.id) element.setAttribute("aria-activedescendant", marked.id);
        else if (!marked && now !== null) element.removeAttribute("aria-activedescendant");
      }
    };
    follow();
    const watch = new MutationObserver(follow);
    watch.observe(list, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-selected", "aria-activedescendant"],
    });
    return () => {
      watch.disconnect();
      field_.removeAttribute("aria-activedescendant");
    };
  }, [list]);

  const pick = (name: string) => {
    setOpen(false);
    onPick(name);
  };

  return (
    <Command
      shouldFilter={false}
      vimBindings={false}
      value={active}
      onValueChange={setMoved}
      label="Names"
      className="relative"
      ref={wrap}
    >
      <Popover open={showing} onOpenChange={setOpen}>
        <PopoverAnchor asChild>
          <div className="relative">
            <input
              ref={input}
              id={id}
              type="text"
              role="combobox"
              autoComplete="off"
              dir="auto"
              aria-autocomplete="list"
              aria-expanded={showing}
              aria-controls={showing ? list?.id : undefined}
              placeholder={placeholder}
              value={value}
              data-slot="input"
              className={cn(field, "tap h-8 pr-8 pointer-coarse:pr-11", className)}
              onChange={(event) => {
                onChange(event.target.value);
                setOpenState(true);
                onOpenChange?.(true);
                setMoved(null);
                if (shownList.current) shownList.current.scrollTop = 0;
              }}
              onClick={() => setOpen(true)}
              onBlur={onBlur}
              onKeyDown={(event) => {
                // Enter with nothing marked is the field's, as with the list closed: it clears or saves the text.
                const unmarked = event.key === "Enter" && active === NONE;
                if (showing && LIST_KEYS.has(event.key) && !unmarked) return;
                if (showing && (event.key === "Home" || event.key === "End")) {
                  event.stopPropagation();
                  return;
                }
                if (!showing && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
                  event.preventDefault();
                  event.stopPropagation();
                  setOpen(true);
                  return;
                }
                // The list is closed, or the key isn't its: the field's own, and never cmdk's, which takes Enter's
                // default away even with no list, and so a form's submit.
                event.stopPropagation();
                if (event.key === "Tab" || unmarked) setOpen(false);
                onKeyDown?.(event);
              }}
              {...aria}
            />
            <button
              type="button"
              tabIndex={-1}
              aria-label={showing ? "Hide the names" : "Show the names"}
              className="tap absolute top-1/2 right-1 grid size-6 -translate-y-1/2 cursor-default place-items-center rounded-sm text-ink-muted hover:bg-hover hover:text-ink pointer-coarse:right-0"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                document.getElementById(id)?.focus();
                setOpen(!showing);
              }}
            >
              <ChevronsUpDownIcon className="size-3.5" aria-hidden />
            </button>
          </div>
        </PopoverAnchor>
        <PopoverContent
          aria-label="Names"
          className="w-(--radix-popover-trigger-width) min-w-56 p-1"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => {
            // The field and its button are outside the list, and keep it open.
            if (event.target instanceof Node && wrap.current?.contains(event.target)) event.preventDefault();
          }}
        >
          <CommandList
            ref={listed}
            label="Names"
            className="max-h-[min(16rem,var(--radix-popover-content-available-height))]"
            // A press in the list keeps the focus in the field, so leaving it never saves the text half typed.
            onMouseDown={(event) => event.preventDefault()}
          >
            {loading && !shown.length && <p className="px-2 py-1.5 text-sm text-ink-muted">Reading the names.</p>}
            {shown.map((person) => (
              <CommandItem key={person.name} value={choiceOf(person.name)} onSelect={() => pick(person.name)}>
                <span className="min-w-0 flex-1 truncate" dir="auto">
                  {person.name}
                  {person.via && <span className="ml-1.5 text-xs text-ink-muted">{person.via}</span>}
                </span>
                {person.uses > 0 && (
                  <span className="shrink-0 text-xs text-ink-muted tabular-nums">
                    {person.uses} {person.uses === 1 ? "item" : "items"}
                  </span>
                )}
              </CommandItem>
            ))}
            {asTyped && (
              <CommandItem value={TYPED} onSelect={() => pick(typed)} className="border-t border-rule">
                <span className="min-w-0 flex-1 truncate">
                  Use <span dir="auto">“{typed}”</span>
                </span>
                <span className="shrink-0 text-xs text-ink-muted">as typed</span>
              </CommandItem>
            )}
          </CommandList>
        </PopoverContent>
      </Popover>
    </Command>
  );
}
