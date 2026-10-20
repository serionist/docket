import { render } from "@testing-library/react";
import type * as React from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { commandFor, installKeys, keyName, register, registered, useKeys } from "./keys";

function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = document.body): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

const unregister: (() => void)[] = [];
afterEach(() => {
  while (unregister.length) unregister.pop()?.();
  document.body.replaceChildren();
});

beforeAll(() => installKeys());

describe("the key layer", () => {
  it("runs the command a key names, and takes the key from the browser", () => {
    const run = vi.fn();
    unregister.push(register({ id: "t", title: "Test", commands: [{ keys: ["j"], what: "next", run }] }));
    const event = press("j");
    expect(run).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(true);
  });

  it("ignores a held key's repeats and keys with Alt or Meta", () => {
    const run = vi.fn();
    unregister.push(register({ id: "t", title: "Test", commands: [{ keys: ["j"], what: "next", run }] }));
    press("j", { repeat: true });
    press("j", { altKey: true });
    press("j", { metaKey: true });
    expect(run).not.toHaveBeenCalled();
  });

  it("leaves a key typed in a field to the field", () => {
    const run = vi.fn();
    unregister.push(register({ id: "t", title: "Test", commands: [{ keys: ["j"], what: "next", run }] }));
    const field = document.createElement("input");
    document.body.append(field);
    const event = press("j", {}, field);
    expect(run).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it("leaves a dialog's keys to the dialog", () => {
    const run = vi.fn();
    unregister.push(register({ id: "t", title: "Test", commands: [{ keys: ["Escape"], what: "close", run }] }));
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const button = document.createElement("button");
    dialog.append(button);
    document.body.append(dialog);
    press("Escape", {}, button);
    expect(run).not.toHaveBeenCalled();
  });

  it("leaves a key some part of the page already took, as a popover's Esc that closed it and took it away", () => {
    const run = vi.fn();
    unregister.push(register({ id: "t", title: "Test", commands: [{ keys: ["Escape"], what: "close", run }] }));
    // A layer that closes on Esc at the capture phase, its content gone from the page before the key reaches us.
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const day = document.createElement("button");
    dialog.append(day);
    document.body.append(dialog);
    const closes = (event: KeyboardEvent) => {
      event.preventDefault();
      day.remove();
    };
    document.addEventListener("keydown", closes, { capture: true });
    try {
      press("Escape", {}, day);
    } finally {
      document.removeEventListener("keydown", closes, { capture: true });
    }
    expect(run).not.toHaveBeenCalled();
    press("Escape");
    expect(run).toHaveBeenCalledOnce();
  });

  it("does nothing while the keys switch is off", () => {
    const run = vi.fn();
    unregister.push(register({ id: "t", title: "Test", commands: [{ keys: ["j"], what: "next", run }] }));
    document.documentElement.dataset.keys = "off";
    press("j");
    expect(run).not.toHaveBeenCalled();
  });

  it("tells Ctrl+Z from z, and skips a command that doesn't apply now", () => {
    const plain = vi.fn();
    const ctrl = vi.fn();
    unregister.push(
      register({
        id: "t",
        title: "Test",
        commands: [
          { keys: ["z"], what: "z", run: plain, when: () => false },
          { keys: ["z"], ctrl: true, what: "undo", run: ctrl },
        ],
      }),
    );
    press("z");
    press("z", { ctrlKey: true });
    expect(plain).not.toHaveBeenCalled();
    expect(ctrl).toHaveBeenCalledOnce();
    expect(commandFor(new KeyboardEvent("keydown", { key: "z" }))).toBeUndefined();
  });

  it("lets the group registered last win a key two of one level claim, and drops a group when it goes", () => {
    const first = vi.fn();
    const second = vi.fn();
    unregister.push(register({ id: "first", title: "First", commands: [{ keys: ["n"], what: "new", run: first }] }));
    const off = register({ id: "second", title: "Second", commands: [{ keys: ["n"], what: "new", run: second }] });
    expect(registered().map((g) => g.id)).toEqual(["second", "first"]);
    press("n");
    off();
    press("n");
    expect(second).toHaveBeenCalledOnce();
    expect(first).toHaveBeenCalledOnce();
    expect(registered().map((g) => g.id)).toEqual(["first"]);
  });

  it("lets a page's group win a key the shell's claims too, whichever registered first", () => {
    const shell = vi.fn();
    const page = vi.fn();
    const pageGroup = { id: "page", title: "Page", commands: [{ keys: ["?"], what: "page keys", run: page }] };
    const shellGroup = {
      id: "shell",
      title: "Everywhere",
      level: "shell" as const,
      commands: [{ keys: ["?"], what: "keys", run: shell }],
    };
    // A full load mounts the page's effects before the shell's; a client navigation mounts the page after.
    for (const order of [
      [pageGroup, shellGroup],
      [shellGroup, pageGroup],
    ]) {
      const offs = order.map((group) => register(group));
      expect(registered().map((g) => g.id)).toEqual(["page", "shell"]);
      press("?");
      for (const off of offs) off();
    }
    expect(page).toHaveBeenCalledTimes(2);
    expect(shell).not.toHaveBeenCalled();
  });

  it("puts a page's keys before the shell's under React, on a full load and after a navigation", () => {
    const shell = vi.fn();
    const page = vi.fn();
    function Page() {
      useKeys("page", "This page", [{ keys: ["?"], what: "page keys", run: page }]);
      return null;
    }
    function Shell({ children }: { children?: React.ReactNode }) {
      useKeys("shell", "Everywhere", [{ keys: ["?"], what: "these keys", run: shell }], "shell");
      return children;
    }
    // A full load: the child page's effect registers before its parent shell's.
    const load = render(
      <Shell>
        <Page />
      </Shell>,
    );
    press("?");
    load.unmount();
    // A navigation: the shell stands, and the page mounts under it.
    const nav = render(<Shell />);
    nav.rerender(
      <Shell>
        <Page />
      </Shell>,
    );
    press("?");
    expect(registered().map((g) => g.title)).toEqual(["This page", "Everywhere"]);
    nav.unmount();
    expect(page).toHaveBeenCalledTimes(2);
    expect(shell).not.toHaveBeenCalled();
  });

  it("leaves Enter and Space to the focused link or control, and takes its other keys", () => {
    const run = vi.fn();
    unregister.push(
      register({
        id: "t",
        title: "Test",
        commands: [
          { keys: ["Enter", "o"], what: "open", run },
          { keys: ["x", " "], what: "tick", run },
        ],
      }),
    );
    const link = document.createElement("a");
    link.href = "/ledger";
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    details.append(summary);
    const button = document.createElement("button");
    const inner = document.createElement("span");
    button.append(inner);
    const controls = [link, summary, button, inner];
    for (const role of ["button", "checkbox", "switch", "menuitem", "tab"]) {
      const el = document.createElement("div");
      el.setAttribute("role", role);
      el.tabIndex = 0;
      controls.push(el);
    }
    document.body.append(link, details, button, ...controls.slice(4));
    for (const control of controls) {
      for (const key of ["Enter", " "]) {
        expect(press(key, {}, control).defaultPrevented, `${key} on ${control.outerHTML}`).toBe(false);
      }
    }
    expect(run).not.toHaveBeenCalled();
    // A letter on a button is the page's still: only the keys that press it are the button's.
    expect(press("x", {}, button).defaultPrevented).toBe(true);
    expect(run).toHaveBeenCalledOnce();
  });

  it("leaves a combobox's typeahead letters to it, as a field's", () => {
    const run = vi.fn();
    unregister.push(register({ id: "t", title: "Test", commands: [{ keys: ["j", "k"], what: "move", run }] }));
    const trigger = document.createElement("button");
    trigger.setAttribute("role", "combobox");
    const value = document.createElement("span");
    trigger.append(value);
    document.body.append(trigger);
    expect(press("j", {}, trigger).defaultPrevented).toBe(false);
    expect(press("k", {}, value).defaultPrevented).toBe(false);
    expect(run).not.toHaveBeenCalled();
  });

  it("registers a component's keys while it is mounted, each running its latest command", () => {
    const first = vi.fn();
    const second = vi.fn();
    function Page({ run }: { run: () => void }) {
      useKeys("page", "Page", [{ keys: ["j"], what: "next", run }]);
      return null;
    }
    const view = render(<Page run={first} />);
    view.rerender(<Page run={second} />);
    press("j");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    view.unmount();
    expect(registered().some((g) => g.id === "page")).toBe(false);
  });

  it("names keys as the keyboard prints them", () => {
    expect(keyName(" ")).toBe("Space");
    expect(keyName("Escape")).toBe("Esc");
    expect(keyName("z", true)).toBe("Z");
    expect(keyName("j")).toBe("j");
  });
});
