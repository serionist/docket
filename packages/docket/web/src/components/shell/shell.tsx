import { Outlet, useRouterState } from "@tanstack/react-router";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useKeys } from "@/lib/keys";
import { AppSidebar } from "./app-sidebar";
import { KeysDialog } from "./keys-dialog";
import { DAY, REST, SETTINGS } from "./nav";
import { SessionDialog } from "./session-dialog";

/** The window's title for a page: its name, then Docket's, as the server's pages write it. */
export function useTitle(title: string): void {
  React.useEffect(() => {
    document.title = `${title} - Docket`;
  }, [title]);
}

const ShowKeys = React.createContext<() => void>(() => undefined);

/** What opens the ? dialog, for a page's own button: it opens with the keys off too, and says so. */
export function useShowKeys(): () => void {
  return React.useContext(ShowKeys);
}

/** Every page's frame: the skip link, the sidebar, the page, and the dialogs any page can raise. */
export function Shell() {
  const path = useRouterState({ select: (state) => state.location.pathname });
  const current = [...DAY, ...REST, SETTINGS].find((page) => page.href === path)?.key;
  const [keysOpen, setKeysOpen] = React.useState(false);
  // What had the focus when the dialog opened, which gets it back when the dialog closes: the dialog has no trigger
  // of its own, as a key or a page's button opens it.
  const opener = React.useRef<Element | null>(null);
  const showKeys = React.useCallback(() => {
    opener.current = document.activeElement;
    setKeysOpen(true);
  }, []);
  useKeys("shell", "Everywhere", [{ keys: ["?"], what: "these keys", run: showKeys }], "shell");
  return (
    <TooltipProvider>
      <a
        href="#main"
        className="sr-only z-50 rounded-sm border border-rule bg-sheet px-3 py-2 text-sm font-semibold focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to the page
      </a>
      <ShowKeys.Provider value={showKeys}>
        <SidebarProvider>
          <AppSidebar current={current} />
          <Outlet />
        </SidebarProvider>
      </ShowKeys.Provider>
      <KeysDialog open={keysOpen} onOpenChange={setKeysOpen} returnTo={opener} />
      <SessionDialog />
    </TooltipProvider>
  );
}

/** A path the app has no page for. */
export function NotFound() {
  useTitle("Not found");
  return (
    <main id="main" className="flex flex-1 flex-col items-start gap-2 p-8">
      <h1 className="text-xl font-semibold">Not found</h1>
      <p className="text-ink-muted">There is no page here. The sidebar lists every page.</p>
    </main>
  );
}

/** What a page that failed as it drew shows (DR-0792): that something failed and where its words are, never what they
 * are, a path or a trace, with the way to try again. */
export function PageFailed({ reload = () => window.location.reload() }: { reload?: () => void }) {
  useTitle("Something failed");
  return (
    <main id="main" className="flex flex-1 flex-col items-start gap-2 p-8">
      <h1 className="text-xl font-semibold">Something failed in Docket</h1>
      <p className="max-w-prose text-ink-muted">
        The service log says what, and never holds your words; docket service status names it.
      </p>
      <Button size="sm" onClick={reload}>
        Reload the page
      </Button>
    </main>
  );
}
