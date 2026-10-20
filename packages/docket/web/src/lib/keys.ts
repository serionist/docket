/**
 * The app's one key layer (DR-0711): a single keydown listener reads a registry the shell and each page fill, so a
 * key runs the command its button runs and the ? dialog lists exactly what the keys do. It ignores a held key's
 * repeats, keys typed in a field or a combobox or with Alt or Meta, Enter and Space on a focused link or control,
 * keys aimed at an open dialog or menu, a key some part of the page already took (its default prevented, as a
 * popover's or dialog's Esc that closed it), and every key while the keys switch is off (the docket_keys cookie, as
 * data-keys on the document). A page's keys come before the shell's, whichever registered first.
 */
import { useEffect, useLayoutEffect, useRef, useSyncExternalStore } from "react";

export interface KeyCommand {
  /** `KeyboardEvent.key` values, such as "j", "Enter", "?" or " ". */
  keys: string[];
  /** What the command does, as the ? dialog lists it. */
  what: string;
  run: (event: KeyboardEvent) => void;
  /** Ctrl held, as Ctrl+Z; otherwise a key with Ctrl is left to the browser. */
  ctrl?: boolean;
  /** Whether the command applies now; a key whose command doesn't apply does nothing and isn't taken. */
  when?: () => boolean;
}

/** Where a group stands: a page's keys come before the shell's. */
export type KeyLevel = "shell" | "page";
const RANK: Record<KeyLevel, number> = { shell: 0, page: 1 };

export interface KeyGroup {
  id: string;
  title: string;
  commands: KeyCommand[];
  /** "page" when left out. */
  level?: KeyLevel;
}

// Each group with when it registered: within a level, the one registered last comes first.
const groups = new Map<string, { group: KeyGroup; at: number }>();
const listeners = new Set<() => void>();
let snapshot: KeyGroup[] = [];
let registrations = 0;

function rank(group: KeyGroup): number {
  return RANK[group.level ?? "page"];
}

function changed(): void {
  snapshot = [...groups.values()]
    .sort((a, b) => rank(b.group) - rank(a.group) || b.at - a.at)
    .map((entry) => entry.group);
  for (const listener of listeners) listener();
}

export function register(group: KeyGroup): () => void {
  groups.set(group.id, { group, at: ++registrations });
  changed();
  return () => {
    if (groups.get(group.id)?.group === group) {
      groups.delete(group.id);
      changed();
    }
  };
}

/** Every group now registered, in the order a key is matched: pages before the shell, the newest first. */
export function registered(): KeyGroup[] {
  return snapshot;
}

/** Every group now registered, for the ? dialog. */
export function useRegistered(): KeyGroup[] {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, registered);
}

const FIELDS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
// A combobox, such as a Select's trigger, takes letters for its typeahead, as a field takes them.
const TYPED_IN = '[role="combobox"]';
// What has its own keys: a dialog, a menu or a listbox gets its keys, never the page behind it.
const OWN_KEYS = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]';
// What Enter and Space press when it has the focus: a link, a button, a tick, a switch, a menu item or a tab.
const PRESSED = [
  "a[href]",
  "button",
  "summary",
  '[role="button"]',
  '[role="checkbox"]',
  '[role="switch"]',
  '[role="menuitem"]',
  '[role="tab"]',
].join(", ");
const PRESSING = new Set(["Enter", " "]);

export function keysOn(root: HTMLElement = document.documentElement): boolean {
  return root.dataset.keys !== "off";
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return FIELDS.has(target.tagName) || target.isContentEditable || target.closest(TYPED_IN) !== null;
}

/** The command a keydown runs, or none; a page's group comes before the shell's, then the newest group first. */
export function commandFor(event: KeyboardEvent): KeyCommand | undefined {
  // A layer that closes on Esc takes it at the capture phase and may be gone from the page by now, so the dialog
  // around the key's target can no longer tell; the key it took is never the page's too.
  if (event.defaultPrevented || event.repeat || event.altKey || event.metaKey || event.isComposing) return undefined;
  if (!keysOn() || typing(event.target)) return undefined;
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest(OWN_KEYS)) return undefined;
  if (PRESSING.has(event.key) && target?.closest(PRESSED)) return undefined;
  for (const group of snapshot) {
    for (const command of group.commands) {
      if (!command.keys.includes(event.key)) continue;
      if (Boolean(command.ctrl) !== event.ctrlKey) continue;
      if (command.when && !command.when()) continue;
      return command;
    }
  }
  return undefined;
}

function onKeydown(event: KeyboardEvent): void {
  const command = commandFor(event);
  if (command === undefined) return;
  event.preventDefault();
  command.run(event);
}

let installed = false;

/** The one listener, added once for the app's life. */
export function installKeys(target: Document = document): void {
  if (installed) return;
  installed = true;
  target.addEventListener("keydown", onKeydown);
}

/**
 * A component's keys, registered while it is mounted; the commands may change on each render. The shell's are at
 * "shell", so a page's keys win a key both claim, whether the page mounted with the shell or after it.
 */
export function useKeys(id: string, title: string, commands: KeyCommand[], level: KeyLevel = "page"): void {
  const latest = useRef(commands);
  useLayoutEffect(() => {
    latest.current = commands;
  });
  useEffect(() => {
    const live = latest.current.map((command, at) => ({
      ...command,
      run: (event: KeyboardEvent) => latest.current[at]?.run(event),
      when: () => latest.current[at]?.when?.() ?? true,
    }));
    return register({ id, title, commands: live, level });
    // The keys and their words are fixed for a component; the commands themselves are read through the ref.
  }, [id, title, level]);
}

/** The keys as the ? dialog and the hints write them; a letter with Ctrl as the keyboard prints it. */
export function keyName(key: string, ctrl = false): string {
  if (ctrl && key.length === 1) return key.toUpperCase();
  const names: Record<string, string> = {
    " ": "Space",
    Escape: "Esc",
    ArrowUp: "Up",
    ArrowDown: "Down",
    Backspace: "Backspace",
    Delete: "Delete",
  };
  return names[key] ?? key;
}
