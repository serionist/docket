import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { anyUnsaved, LEAVE, mayLeave, resetDrafts, saves, setUnsaved, settled, startSave, useUnsaved } from "./drafts";

afterEach(() => resetDrafts());

function Typed({ name, on }: { name: string; on: boolean }) {
  useUnsaved(name, on);
  return null;
}

describe("text typed and not yet saved", () => {
  it("is asked about by its place, leaving out what a write itself sends", () => {
    setUnsaved("panel:A-1:notes", true);
    setUnsaved("act:why", true);
    expect(anyUnsaved()).toBe(true);
    expect(anyUnsaved("panel:")).toBe(true);
    expect(anyUnsaved("panel:", ["panel:A-1:notes"])).toBe(false);
    setUnsaved("panel:A-1:notes", false);
    expect(anyUnsaved("panel:")).toBe(false);
  });

  it("is a component's while it is mounted and on", () => {
    const shown = render(<Typed name="panel:A-1:log" on />);
    expect(anyUnsaved("panel:A-1")).toBe(true);
    shown.rerender(<Typed name="panel:A-1:log" on={false} />);
    expect(anyUnsaved()).toBe(false);
    shown.rerender(<Typed name="panel:A-1:log" on />);
    shown.unmount();
    expect(anyUnsaved()).toBe(false);
  });

  it("is left only once the owner says so", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    expect(mayLeave()).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
    setUnsaved("panel:A-1:notes", true);
    expect(mayLeave("panel:")).toBe(false);
    expect(mayLeave("panel:")).toBe(true);
    expect(confirm).toHaveBeenCalledWith(LEAVE);
    expect(mayLeave("act:")).toBe(true);
  });
});

describe("the saves in flight", () => {
  it("count up and down, each ended once, and settle when none is left", async () => {
    const first = startSave();
    const second = startSave();
    expect(saves()).toBe(2);
    let done = false;
    const waited = settled().then(() => {
      done = true;
    });
    first();
    first();
    expect(saves()).toBe(1);
    await Promise.resolve();
    expect(done).toBe(false);
    second();
    await waited;
    expect(done).toBe(true);
    await expect(settled()).resolves.toBeUndefined();
  });
});
