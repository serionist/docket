/**
 * TanStack Query as the day pages need it (DR-0711): reads live in memory only, nothing refetches on focus or on
 * reconnect, a read stays fresh until something says otherwise (the poll, or a page's own write), and a refusal
 * (any 4xx) is never retried, since asking again gets the same answer. A read the cache holds is kept 5 minutes.
 */
import { QueryClient } from "@tanstack/react-query";

/** An answer that wasn't 2xx: its status, for a refusal the writer's words, and the body as it came. */
export class HttpError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.body = body;
  }

  get refused(): boolean {
    return this.status >= 400 && this.status < 500;
  }
}

// Network failures and 5xx get this many tries in all.
export const TRIES = 3;
export const KEPT_MS = 5 * 60 * 1000;

export function retry(failures: number, error: unknown): boolean {
  if (error instanceof HttpError && error.refused) return false;
  return failures < TRIES - 1;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        refetchOnMount: false,
        staleTime: Infinity,
        gcTime: KEPT_MS,
        retry,
      },
      mutations: { retry: false },
    },
  });
}
