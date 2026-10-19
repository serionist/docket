import { render } from "@testing-library/react";
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

  it("lets the group registered last win a key both claim, and drops a group when it goes", () => {
    const shell = vi.fn();
    const page = vi.fn();
    unregister.push(
      register({ id: "shell", title: "Everywhere", commands: [{ keys: ["?"], what: "keys", run: shell }] }),
    );
    const off = register({ id: "page", title: "Page", commands: [{ keys: ["?"], what: "page keys", run: page }] });
    press("?");
    off();
    press("?");
    expect(page).toHaveBeenCalledOnce();
    expect(shell).toHaveBeenCalledOnce();
    expect(registered().map((g) => g.id)).toEqual(["shell"]);
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
