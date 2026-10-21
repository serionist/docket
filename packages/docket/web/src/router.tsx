/**
 * The app's routes, in code. Each page's state is its URL as a plain query string, read and written by our own
 * parseSearch and stringifySearch (DR-0704), never TanStack Router's JSON. The paths the service answers with the
 * app's document are routes.json's, which a test holds to this tree.
 */
import { createRootRoute, createRoute, createRouter, type RouterHistory } from "@tanstack/react-router";
import { NotFound, Shell } from "@/components/shell/shell";
import { parseSearch, type Search, stringifySearch } from "@/lib/search";
import { DesignPage } from "@/pages/design";
import { LedgerPage } from "@/pages/ledger";
import { MeetingsPage } from "@/pages/meetings";
import { QueuePage } from "@/pages/queue";
import { TodayPage } from "@/pages/today";

const root = createRootRoute({ component: Shell, notFoundComponent: NotFound });

export const designRoute = createRoute({
  getParentRoute: () => root,
  path: "/design",
  component: DesignPage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const todayRoute = createRoute({
  getParentRoute: () => root,
  path: "/today",
  component: TodayPage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const ledgerRoute = createRoute({
  getParentRoute: () => root,
  path: "/ledger",
  component: LedgerPage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const queueRoute = createRoute({
  getParentRoute: () => root,
  path: "/queue",
  component: QueuePage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const meetingsRoute = createRoute({
  getParentRoute: () => root,
  path: "/meetings",
  component: MeetingsPage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const routeTree = root.addChildren([designRoute, todayRoute, queueRoute, ledgerRoute, meetingsRoute]);

export function createAppRouter(history?: RouterHistory) {
  return createRouter({
    routeTree,
    history,
    parseSearch,
    stringifySearch,
    defaultPreload: false,
    scrollRestoration: false,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
