/**
 * A link to a URL the server wrote (DR-0792): a plain click on one of the app's own pages opens it in place, through
 * the router, which writes the same bytes back (lib/search.ts); a click with a key held, or a link to one of the
 * server's own pages, is the browser's. `replace` keeps the history entry, as a step within a page does. A link to
 * the URL the page is on, such as the anchor of the stretch the Recording page marks, brings its hash into view, as
 * a browser's link to its own page does; a page scrolls to a new URL's hash itself once its read answers.
 */
import { useRouter } from "@tanstack/react-router";
import type * as React from "react";
import routes from "@/routes.json";

const IN_APP = new Set<string>(routes);

/** Whether a path, its query and hash left off, is one of the app's routes. */
export function inApp(href: string): boolean {
  return IN_APP.has(href.split(/[?#]/, 1)[0] ?? "");
}

/** Whether a click is a plain one, which the page takes; any other opens as the browser opens a link. */
export function plainClick(event: React.MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

export function AppLink({
  href,
  replace,
  onClick,
  ...props
}: React.ComponentProps<"a"> & {
  href: string;
  replace?: boolean;
}) {
  const router = useRouter();
  return (
    <a
      href={href}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented || !plainClick(event) || !inApp(href)) return;
        event.preventDefault();
        const [path, hash] = href.split("#", 2);
        const { pathname, searchStr } = router.state.location;
        if (hash && path === `${pathname}${searchStr}`) {
          document.getElementById(hash)?.scrollIntoView({ block: "center" });
          return;
        }
        void router.navigate({ href, replace });
      }}
      {...props}
    />
  );
}
