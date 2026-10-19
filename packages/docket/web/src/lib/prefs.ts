/**
 * The two things the browser keeps (DR-0706): the theme and the keys switch, in the cookies the Jinja pages read
 * too. The server writes docket_theme into the document as data-theme, so the first paint has the right theme; a
 * change here sets the cookie and the attribute at once.
 */
import { useSyncExternalStore } from "react";

export type Theme = "system" | "light" | "dark";
export const THEMES: Theme[] = ["system", "light", "dark"];
const YEAR = 60 * 60 * 24 * 365;

function setCookie(name: string, value: string): void {
  const age = value ? YEAR : 0;
  document.cookie = `${name}=${value}; Path=/; Max-Age=${age}; SameSite=Strict`;
}

function cookie(name: string): string | undefined {
  for (const part of document.cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return undefined;
}

const listeners = new Set<() => void>();
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
function emit(): void {
  for (const listener of listeners) listener();
}

export function theme(root: HTMLElement = document.documentElement): Theme {
  const found = root.dataset.theme;
  return found === "light" || found === "dark" ? found : "system";
}

export function setTheme(next: Theme, root: HTMLElement = document.documentElement): void {
  root.dataset.theme = next;
  setCookie("docket_theme", next === "system" ? "" : next);
  emit();
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, () => theme());
}

export function keysOff(root: HTMLElement = document.documentElement): boolean {
  return root.dataset.keys === "off";
}

export function setKeysOn(on: boolean, root: HTMLElement = document.documentElement): void {
  if (on) delete root.dataset.keys;
  else root.dataset.keys = "off";
  setCookie("docket_keys", on ? "" : "off");
  emit();
}

export function useKeysOn(): boolean {
  return useSyncExternalStore(subscribe, () => !keysOff());
}

/** The keys switch as its cookie left it, applied to the document at start. */
export function applyKeysCookie(root: HTMLElement = document.documentElement): void {
  if (cookie("docket_keys") === "off") root.dataset.keys = "off";
}
