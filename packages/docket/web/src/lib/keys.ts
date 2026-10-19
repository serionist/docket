/**
 * The app's one key layer (DR-0711): a single keydown listener reads a registry the shell and each page fill, so a
 * key runs the command its button runs and the ? dialog lists exactly what the keys do. It ignores a held key's
 * repeats, keys typed in a field or with Alt or Meta, keys aimed at an open dialog or menu, and every key while the
 * keys switch is off (the docket_keys cookie, as data-keys on the document).
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

export interface KeyGroup {
  id: string;
  title: string;
  commands: KeyCommand[];
}

const groups = new Map<string, KeyGroup>();
const listeners = new Set<() => void>();
let snapshot: KeyGroup[] = [];

function changed(): void {
  snapshot = [...groups.values()];
  for (const listener of listeners) listener();
}

export function register(group: KeyGroup): () => void {
  groups.set(group.id, group);
  changed();
  return () => {
    if (groups.get(group.id) === group) {
      groups.delete(group.id);
      changed();
    }
  };
}

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
// What has its own keys: a dialog, a menu or a listbox gets its keys, never the page behind it.
const OWN_KEYS = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]';

export function keysOn(root: HTMLElement = document.documentElement): boolean {
  return root.dataset.keys !== "off";
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return FIELDS.has(target.tagName) || target.isContentEditable;
}

/** The command a keydown runs, or none; the order of registration decides between two that claim one key. */
export function commandFor(event: KeyboardEvent): KeyCommand | undefined {
  if (event.repeat || event.altKey || event.metaKey || event.isComposing) return undefined;
  if (!keysOn() || typing(event.target)) return undefined;
  if (event.target instanceof Element && event.target.closest(OWN_KEYS)) return undefined;
  for (const group of [...snapshot].reverse()) {
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

/** A component's keys, registered while it is mounted; the commands may change on each render. */
export function useKeys(id: string, title: string, commands: KeyCommand[]): void {
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
    return register({ id, title, commands: live });
    // The keys and their words are fixed for a component; the commands themselves are read through the ref.
  }, [id, title]);
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
