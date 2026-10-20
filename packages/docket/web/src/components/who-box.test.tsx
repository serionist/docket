/**
 * The who field's combobox (DR-0714) in jsdom: typing opens the names with the first marked, the arrows move the
 * mark and Enter takes it, the text as typed is the last choice, Esc closes the list alone, and with the list closed
 * the field's own Enter and Esc are its; no key typed in it reaches the page's keys.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { installKeys, register } from "@/lib/keys";
import { WhoBox } from "./who-box";

const PEOPLE = [
  { name: "Ben Sample", uses: 7, also: [] },
  { name: "Ada Example", uses: 5, also: ["Ada E."] },
  { name: "Cleo Example", uses: 0, also: [] },
];

function Field({ onPick, onKeyDown }: { onPick: (name: string) => void; onKeyDown?: (key: string) => void }) {
  const [value, setValue] = React.useState("");
  return (
    <>
      <label htmlFor="who">Who</label>
      <WhoBox
        id="who"
        value={value}
        onChange={setValue}
        onPick={(name) => {
          setValue(name);
          onPick(name);
        }}
        onKeyDown={(event) => onKeyDown?.(event.key)}
        people={PEOPLE}
      />
    </>
  );
}

function press(target: Element, key: string) {
  act(() => {
    fireEvent.keyDown(target, { key });
  });
}

function marked(input: HTMLElement): string | undefined {
  const id = input.getAttribute("aria-activedescendant");
  return id ? (document.getElementById(id)?.textContent ?? undefined) : undefined;
}

beforeAll(() => installKeys());
afterEach(() => vi.restoreAllMocks());

describe("the who field's names", () => {
  it("opens on typing with the first name marked, moves with the arrows and takes the mark with Enter", async () => {
    const picked = vi.fn();
    render(<Field onPick={picked} />);
    const input = screen.getByLabelText("Who");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    fireEvent.change(input, { target: { value: "ex" } });
    expect(input.getAttribute("aria-expanded")).toBe("true");
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Ada Example5 items", "Cleo Example", "Use “ex”as typed"]);
    await waitFor(() => expect(input.getAttribute("aria-controls")).toBe(screen.getByRole("listbox").id));
    await waitFor(() => expect(marked(input)).toBe("Ada Example5 items"));
    press(input, "ArrowDown");
    await waitFor(() => expect(marked(input)).toBe("Cleo Example"));
    press(input, "Enter");
    expect(picked).toHaveBeenCalledWith("Cleo Example");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect((input as HTMLInputElement).value).toBe("Cleo Example");
  });

  it("keeps a new name as typed, the list's last choice", () => {
    const picked = vi.fn();
    render(<Field onPick={picked} />);
    const input = screen.getByLabelText("Who");
    fireEvent.change(input, { target: { value: "Dora New" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Use “Dora New”as typed"]);
    press(input, "Enter");
    expect(picked).toHaveBeenCalledWith("Dora New");
  });

  it("closes on Esc without the field's own Esc, and gives the field its keys while closed", () => {
    const keys = vi.fn();
    render(<Field onPick={vi.fn()} onKeyDown={keys} />);
    const input = screen.getByLabelText("Who");
    fireEvent.change(input, { target: { value: "a" } });
    expect(input.getAttribute("aria-expanded")).toBe("true");
    press(input, "Escape");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(keys).toHaveBeenCalledTimes(1);
    expect(keys).toHaveBeenLastCalledWith("Escape");
    // The Esc that closed the list had its default taken, so the field's handler, which looks, does nothing.
    press(input, "Enter");
    expect(keys).toHaveBeenLastCalledWith("Enter");
    press(input, "ArrowDown");
    expect(input.getAttribute("aria-expanded")).toBe("true");
  });

  it("leaves the page's keys alone while typing", () => {
    const run = vi.fn();
    const off = register({ id: "page", title: "A page", commands: [{ keys: ["j", "Escape"], what: "", run }] });
    render(<Field onPick={vi.fn()} />);
    const input = screen.getByLabelText("Who");
    input.focus();
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "j", bubbles: true, cancelable: true }));
    });
    fireEvent.change(input, { target: { value: "j" } });
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    });
    expect(run).not.toHaveBeenCalled();
    off();
  });
});
