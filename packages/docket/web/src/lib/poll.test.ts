import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetDrafts, setUnsaved, startSave } from "./drafts";
import { holding, POLL_MS, usePoll } from "./poll";

afterEach(() => {
  resetDrafts();
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe("the poll's hold", () => {
  it("holds for nothing on a quiet page", () => {
    expect(holding(0)).toBe(false);
  });

  it("holds while rows are ticked, text is unsaved or a save is in flight", () => {
    expect(holding(2)).toBe(true);
    setUnsaved("panel:A-1:notes", true);
    expect(holding(0)).toBe(true);
    resetDrafts();
    const done = startSave();
    expect(holding(0)).toBe(true);
    done();
    expect(holding(0)).toBe(false);
  });

  it("holds while a field has the focus, but not a button or a tick", () => {
    const field = document.createElement("textarea");
    const tick = document.createElement("button");
    tick.setAttribute("role", "checkbox");
    document.body.append(field, tick);
    field.focus();
    expect(holding(0)).toBe(true);
    tick.focus();
    expect(holding(0)).toBe(false);
  });

  it("holds while a dialog or a menu is open", () => {
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.append(dialog);
    expect(holding(0)).toBe(true);
    dialog.setAttribute("role", "listbox");
    expect(holding(0)).toBe(true);
  });

  it("holds while the tab is hidden", () => {
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    expect(holding(0)).toBe(true);
    hidden.mockRestore();
  });
});

describe("the poll", () => {
  beforeEach(() => vi.useFakeTimers());

  it("asks once as the page mounts, then every 5 s, and refetches on a change", async () => {
    const ask = vi.fn(async () => ({ changed: false }));
    const changed = vi.fn();
    renderHook(() => usePoll({ version: () => "v1", ask, hold: () => false, changed }));
    await vi.advanceTimersByTimeAsync(0);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask).toHaveBeenCalledWith("v1");
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(ask).toHaveBeenCalledTimes(2);
    expect(changed).not.toHaveBeenCalled();
    ask.mockResolvedValueOnce({ changed: true });
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("asks nothing before the page has read, or while it holds, and asks at the first tick after", async () => {
    const ask = vi.fn(async () => ({ changed: true }));
    const changed = vi.fn();
    const page: { version?: string } = {};
    let held = true;
    renderHook(() => usePoll({ version: () => page.version, ask, hold: () => held, changed }));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(ask).not.toHaveBeenCalled();
    page.version = "v1";
    await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    expect(ask).not.toHaveBeenCalled();
    held = false;
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("refetches a read the clock has put out of date without asking, once it doesn't hold", async () => {
    const ask = vi.fn(async () => ({ changed: false }));
    const changed = vi.fn();
    let due = false;
    let held = false;
    renderHook(() => usePoll({ version: () => "v1", ask, hold: () => held, changed, stale: () => due }));
    await vi.advanceTimersByTimeAsync(0);
    held = true;
    due = true;
    await vi.advanceTimersByTimeAsync(POLL_MS * 2);
    expect(changed).not.toHaveBeenCalled();
    held = false;
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(changed).toHaveBeenCalledTimes(1);
    expect(ask).toHaveBeenCalledTimes(1);
  });

  it("says nothing changed when an ask fails, asks again when the tab comes back, and stops once unmounted", async () => {
    const ask = vi.fn(async () => {
      throw new TypeError("network");
    });
    const changed = vi.fn();
    const shown = renderHook(() => usePoll({ version: () => "v1", ask, hold: () => false, changed }));
    await vi.advanceTimersByTimeAsync(0);
    expect(changed).not.toHaveBeenCalled();
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(ask).toHaveBeenCalledTimes(2);
    shown.unmount();
    await vi.advanceTimersByTimeAsync(POLL_MS * 2);
    expect(ask).toHaveBeenCalledTimes(2);
  });
});
