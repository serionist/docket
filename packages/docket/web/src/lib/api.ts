/**
 * The calls to the service's own JSON routes, /api/web (DR-0709), typed from its OpenAPI document: `npm run types`
 * writes src/api/schema.d.ts from openapi.json, which `uv run devkit web-schema` writes from the routes' models.
 * The session cookie goes with each call; a 401 marks the session lapsed, and any other failure is an HttpError.
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "@/api/schema";
import { HttpError } from "@/lib/query";
import { markLapsed } from "@/lib/session";

export type Shell = components["schemas"]["Shell"];

const failures: Middleware = {
  async onResponse({ response }) {
    if (response.ok) return undefined;
    if (response.status === 401) markLapsed();
    let words = response.statusText;
    try {
      const body: unknown = await response.clone().json();
      if (body && typeof body === "object" && "error" in body && typeof body.error === "string") words = body.error;
    } catch {
      // Not JSON: the status line says enough.
    }
    throw new HttpError(response.status, words || `HTTP ${response.status}`);
  },
};

// The page's own origin, and fetch looked up at each call, so a test can stand in for it.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});
api.use(failures);

export async function readShell(): Promise<Shell> {
  const { data } = await api.GET("/api/web/shell");
  if (data === undefined) throw new HttpError(500, "the shell's read answered nothing");
  return data;
}
