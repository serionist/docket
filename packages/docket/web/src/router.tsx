/**
 * The app's routes, in code. Each page's state is its URL as a plain query string, read and written by our own
 * parseSearch and stringifySearch (DR-0792), never TanStack Router's JSON. The paths the service answers with the
 * app's document are routes.json's, which a test holds to this tree.
 */
import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  type RouterHistory,
} from "@tanstack/react-router";
import { NotFound, PageFailed, Shell } from "@/components/shell/shell";
import { freshPage, PageLoadError } from "@/lib/build";
import { parseSearch, type Search, stringifySearch } from "@/lib/search";
import { BriefsPage } from "@/pages/briefs";
import { DesignPage } from "@/pages/design";
import { LedgerPage } from "@/pages/ledger";
import { MeetingsPage } from "@/pages/meetings";
import { QueuePage } from "@/pages/queue";
import { RecordingPage } from "@/pages/recording";
import { SearchPage } from "@/pages/search";
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

export const searchRoute = createRoute({
  getParentRoute: () => root,
  path: "/search",
  component: SearchPage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const recordingRoute = createRoute({
  getParentRoute: () => root,
  path: "/recording",
  component: RecordingPage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const briefsRoute = createRoute({
  getParentRoute: () => root,
  path: "/briefs",
  component: BriefsPage,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const stagingRoute = createRoute({
  getParentRoute: () => root,
  path: "/staging",
  // Staging, Curate, Sync and Settings load when first opened: the first page's script keeps to its budget (DR-0712).
  component: lazyRouteComponent(
    freshPage(() => import("@/pages/staging")),
    "StagingPage",
  ),
  errorComponent: PageLoadError,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const curateRoute = createRoute({
  getParentRoute: () => root,
  path: "/curate",
  component: lazyRouteComponent(
    freshPage(() => import("@/pages/curate")),
    "CuratePage",
  ),
  errorComponent: PageLoadError,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const syncRoute = createRoute({
  getParentRoute: () => root,
  path: "/sync",
  component: lazyRouteComponent(
    freshPage(() => import("@/pages/sync")),
    "SyncPage",
  ),
  errorComponent: PageLoadError,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const settingsRoute = createRoute({
  getParentRoute: () => root,
  path: "/settings",
  component: lazyRouteComponent(
    freshPage(() => import("@/pages/settings")),
    "SettingsPage",
  ),
  errorComponent: PageLoadError,
  validateSearch: (search: Record<string, unknown>): Search => search as Search,
});

export const routeTree = root.addChildren([
  designRoute,
  todayRoute,
  queueRoute,
  ledgerRoute,
  meetingsRoute,
  searchRoute,
  recordingRoute,
  briefsRoute,
  stagingRoute,
  curateRoute,
  syncRoute,
  settingsRoute,
]);

export function createAppRouter(history?: RouterHistory) {
  return createRouter({
    routeTree,
    history,
    parseSearch,
    stringifySearch,
    defaultPreload: false,
    // A page that fails as it draws says so and nothing more: never the error's words, which may quote a record.
    defaultErrorComponent: PageFailed,
    scrollRestoration: false,
    // A page scrolls to its own hash once its read answers, as the Recording page does to #at; the router's scroll
    // came on render, before the read, and found the line the last read had marked.
    defaultHashScrollIntoView: false,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
