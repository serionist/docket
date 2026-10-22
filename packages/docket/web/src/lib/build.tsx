/**
 * Which build this script is, and a page loaded on its first opening that checks it is still the service's. A tab open
 * across an upgrade keeps the old app.js in memory, while the build's files have fixed names (DR-0705): a page's own
 * script fetched now would be the new build's, linked against the old one. So before a page's script is fetched, the
 * build's stamp, build.json, is asked; a stamp that differs loads the page whole, the new build with it.
 */
import * as React from "react";

// Written by the build as the hash build.json holds; absent outside a build, where nothing is checked.
declare const __DOCKET_BUILD__: string | undefined;

export const BUILT: string = typeof __DOCKET_BUILD__ === "string" ? __DOCKET_BUILD__ : "";
const STAMP = "/static/app/build.json";

/** The service now serves another build than this tab runs. */
export class StaleBuild extends Error {
  constructor() {
    super("Docket was updated since this page loaded");
    this.name = "StaleBuild";
  }
}

/** Whether the service still serves this build; a stamp that can't be read says yes, and the load goes ahead. */
export async function sameBuild(built: string = BUILT): Promise<boolean> {
  if (!built) return true;
  try {
    const answer = await globalThis.fetch(STAMP, { cache: "no-cache", credentials: "same-origin" });
    if (!answer.ok) return true;
    const stamp = (await answer.json()) as { inputs?: unknown };
    return stamp.inputs === built;
  } catch {
    return true;
  }
}

/** A page's script, fetched only while the service serves this build. */
export function freshPage<T>(load: () => Promise<T>, built: string = BUILT): () => Promise<T> {
  return async () => {
    if (!(await sameBuild(built))) throw new StaleBuild();
    return load();
  };
}

const reloadPage = () => window.location.reload();

/** What a page whose script didn't load shows: after an upgrade, the page loaded whole; else why, with a reload. */
export function PageLoadError({ error, reload = reloadPage }: { error: unknown; reload?: () => void }) {
  const stale = error instanceof StaleBuild;
  React.useEffect(() => {
    if (stale) reload();
  }, [stale, reload]);
  return (
    <main id="main" className="flex flex-1 flex-col items-start gap-2 p-8">
      <p role="status" className="text-sm">
        {stale ? "Docket was updated: loading its new version." : "This page didn't load. Reload it to try again."}
      </p>
    </main>
  );
}
