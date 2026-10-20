import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installKeys, register } from "@/lib/keys";
import { DatePicker, type DatePickerProps } from "./date-picker";

function picker(props: Partial<DatePickerProps> = {}) {
  const onPick = vi.fn();
  render(
    <DatePicker
      id="due"
      value="2026-10-26"
      today="2026-10-20"
      label="Due"
      clear="No due date"
      onPick={onPick}
      {...props}
    />,
  );
  return onPick;
}

async function opened(name: RegExp = /^Due/): Promise<HTMLElement> {
  fireEvent.click(screen.getByRole("button", { name }));
  return screen.findByRole("dialog");
}

function key(name: string, target: Element) {
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }));
  });
}

/** The window at a width, as the shell's bands read it. */
function at(width: number) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: width >= Number(/min-width: (\d+)px/.exec(query)?.[1] ?? 0),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

beforeAll(() => installKeys());

beforeEach(() => at(1440));

let unregister: (() => void) | undefined;
afterEach(() => {
  unregister?.();
  vi.unstubAllGlobals();
});

describe("the date picker", () => {
  it("shows its day as the app writes it, whatever the browser's language, and says it in its name", () => {
    picker();
    const button = screen.getByRole("button", { name: "Due, Mon 26 Oct" });
    expect(button.textContent).toBe("Mon 26 Oct");
  });

  it("says what it holds with no day", () => {
    picker({ value: "", empty: "Another day", label: "Snooze to a day" });
    expect(screen.getByRole("button", { name: "Snooze to a day, Another day" })).toBeTruthy();
  });

  it("opens a month of weeks from Monday on its day, focused, and a day picked is its change", async () => {
    const onPick = picker();
    const dialog = await opened();
    expect(dialog.getAttribute("aria-label")).toBe("Due");
    const heads = [...dialog.querySelectorAll("thead th")].map((th) => th.getAttribute("aria-label"));
    expect(heads).toEqual(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]);
    await waitFor(() =>
      expect(document.activeElement?.closest("[data-day]")?.getAttribute("data-day")).toBe("2026-10-26"),
    );
    expect(dialog.querySelector('[data-today="true"]')?.getAttribute("data-day")).toBe("2026-10-20");
    expect(within(dialog).getByRole("button", { name: "Monday 26 October 2026, picked" })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Tuesday 20 October 2026, today" })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Friday 23 October 2026" }));
    expect(onPick).toHaveBeenCalledWith("2026-10-23");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("takes a day typed as ISO writes it, and says why it refuses one", async () => {
    const onPick = picker({ value: "", min: "2026-10-21" });
    const dialog = await opened();
    const box = within(dialog).getByLabelText("Or type the day");
    fireEvent.change(box, { target: { value: "23/10/2026" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(within(dialog).getByRole("alert").textContent).toBe("Write the day as 2026-10-23.");
    fireEvent.change(box, { target: { value: "2026-10-20" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Set" }));
    expect(within(dialog).getByRole("alert").textContent).toBe("The first day it takes is Wed 21 Oct.");
    expect(onPick).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "2026-10-23" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith("2026-10-23");
  });

  it("keeps the days before its first one from being picked, and clears its day when it may", async () => {
    const onPick = picker({ min: "2026-10-21" });
    const dialog = await opened();
    expect(
      (within(dialog).getByRole("button", { name: /^Tuesday 20 October 2026/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "No due date" }));
    expect(onPick).toHaveBeenCalledWith("");
  });

  it("opens its month in a popover beside it, and below 768 px in a sheet from the screen's foot", async () => {
    picker();
    const popover = await opened();
    expect(popover.getAttribute("data-slot")).toBe("popover-content");
    expect(popover.className).toContain("max-h-(--radix-popover-content-available-height)");
    fireEvent.keyDown(popover, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    cleanup();
    at(390);
    const onPick = picker();
    const sheet = await opened();
    expect(sheet.getAttribute("data-slot")).toBe("sheet-content");
    expect(sheet.getAttribute("data-side")).toBe("bottom");
    expect(within(sheet).getByRole("heading", { name: "Due" })).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Due" })).toBe(sheet);
    await waitFor(() =>
      expect(document.activeElement?.closest("[data-day]")?.getAttribute("data-day")).toBe("2026-10-26"),
    );
    fireEvent.click(within(sheet).getByRole("button", { name: "Friday 23 October 2026" }));
    expect(onPick).toHaveBeenCalledWith("2026-10-23");
  });

  it.each([1440, 390])("keeps every key pressed in it from the page's keys, at %i px", async (width) => {
    at(width);
    const ran = vi.fn();
    unregister = register({
      id: "page",
      title: "The page",
      commands: [{ keys: ["e", "j", "1", "s", "u", "Escape", "Delete"], what: "anything", run: ran }],
    });
    picker();
    // Outside the picker the page's keys run, so the test can tell they would.
    key("e", document.body);
    expect(ran).toHaveBeenCalledTimes(1);
    const dialog = await opened();
    await waitFor(() => expect(document.activeElement?.closest("[data-day]")).toBeTruthy());
    const day = document.activeElement as Element;
    for (const name of ["e", "j", "1", "s", "u", "Delete"]) key(name, day);
    for (const name of ["e", "1"]) key(name, within(dialog).getByLabelText("Or type the day"));
    key("Escape", day);
    expect(ran).toHaveBeenCalledTimes(1);
  });
});
