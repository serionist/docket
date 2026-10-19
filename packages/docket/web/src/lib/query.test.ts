import { describe, expect, it } from "vitest";
import { createQueryClient, HttpError, KEPT_MS, retry, TRIES } from "./query";

describe("the QueryClient's defaults", () => {
  const defaults = createQueryClient().getDefaultOptions().queries ?? {};

  it("refetch on nothing but the poll: not on focus, not on reconnect, not on mount, never stale", () => {
    expect(defaults.refetchOnWindowFocus).toBe(false);
    expect(defaults.refetchOnReconnect).toBe(false);
    expect(defaults.refetchOnMount).toBe(false);
    expect(defaults.staleTime).toBe(Infinity);
  });

  it("keep a read 5 minutes", () => {
    expect(defaults.gcTime).toBe(KEPT_MS);
    expect(KEPT_MS).toBe(300_000);
  });

  it("never retry a refusal, and retry a failure up to three tries in all", () => {
    expect(retry(0, new HttpError(400, "the writer's words"))).toBe(false);
    expect(retry(0, new HttpError(401, "session"))).toBe(false);
    expect(retry(0, new HttpError(500, "failed"))).toBe(true);
    expect(retry(0, new TypeError("network"))).toBe(true);
    expect(retry(TRIES - 1, new HttpError(500, "failed"))).toBe(false);
  });

  it("never retry a write", () => {
    expect(createQueryClient().getDefaultOptions().mutations?.retry).toBe(false);
  });
});
