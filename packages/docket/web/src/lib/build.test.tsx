import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { freshPage, PageLoadError, sameBuild, StaleBuild } from "./build";

function stamp(inputs: string, status = 200): Response {
  return new Response(JSON.stringify({ inputs }), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("a page loaded on its first opening", () => {
  it("loads while the service serves this build, and asks nothing outside a build", async () => {
    const fetched = vi.fn(async () => stamp("abc"));
    vi.stubGlobal("fetch", fetched);
    const load = vi.fn(async () => ({ Page: "the page" }));
    await expect(freshPage(load, "abc")()).resolves.toEqual({ Page: "the page" });
    expect(fetched).toHaveBeenCalledWith("/static/app/build.json", { cache: "no-cache", credentials: "same-origin" });
    expect(await sameBuild("")).toBe(true);
    expect(fetched).toHaveBeenCalledTimes(1);
  });

  it("isn't fetched once the service serves another build, and the page then loads whole", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => stamp("def")),
    );
    const load = vi.fn(async () => ({}));
    await expect(freshPage(load, "abc")()).rejects.toBeInstanceOf(StaleBuild);
    expect(load).not.toHaveBeenCalled();
    const reload = vi.fn();
    render(<PageLoadError error={new StaleBuild()} reload={reload} />);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status").textContent).toBe("Docket was updated: loading its new version.");
  });

  it("goes ahead when the stamp can't be read, and says another failure without reloading", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("offline");
      }),
    );
    expect(await sameBuild("abc")).toBe(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => stamp("", 404)),
    );
    expect(await sameBuild("abc")).toBe(true);
    const reload = vi.fn();
    render(<PageLoadError error={new SyntaxError("missing export")} reload={reload} />);
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toBe("This page didn't load. Reload it to try again.");
  });
});
