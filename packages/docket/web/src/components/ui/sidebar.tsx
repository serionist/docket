/**
 * shadcn/ui's Sidebar (DR-0708), cut to what the shell uses and fitted to its widths: open at 224 px from 1280, folded
 * to its icons from 1024, a sheet from a menu button below that. Nothing of it is kept in the browser and it takes
 * no key of its own: the browser keeps only the theme and the keys switch (DR-0706), and the key layer is the app's
 * one listener (DR-0711).
 */
import { cva, type VariantProps } from "class-variance-authority";
import { PanelLeftIcon } from "lucide-react";
import { Slot } from "radix-ui";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { type Band, useBand } from "@/hooks/use-width";
import { cn } from "@/lib/utils";

interface SidebarState {
  band: Band;
  /** Open at full width, rather than folded to its icons. */
  open: boolean;
  setOpen: (open: boolean) => void;
  /** Below 1024 px: the sidebar is a sheet. */
  sheet: boolean;
  sheetOpen: boolean;
  setSheetOpen: (open: boolean) => void;
  toggle: () => void;
}

const SidebarContext = React.createContext<SidebarState | null>(null);

function useSidebar(): SidebarState {
  const found = React.useContext(SidebarContext);
  if (!found) throw new Error("useSidebar is used inside a SidebarProvider");
  return found;
}

function SidebarProvider({ className, children, ...props }: React.ComponentProps<"div">) {
  const band = useBand();
  const sheet = band === "small" || band === "narrow";
  // The width chooses; a toggle holds until the window crosses into another band.
  const [chosen, setChosen] = React.useState<{ band: Band; open: boolean } | null>(null);
  const open = chosen?.band === band ? chosen.open : band === "wide";
  const [sheetOpen, setSheetOpen] = React.useState(false);
  const setOpen = React.useCallback((next: boolean) => setChosen({ band, open: next }), [band]);
  const toggle = React.useCallback(() => {
    if (sheet) setSheetOpen((was) => !was);
    else setOpen(!open);
  }, [sheet, open, setOpen]);
  const value = React.useMemo<SidebarState>(
    () => ({ band, open, setOpen, sheet, sheetOpen, setSheetOpen, toggle }),
    [band, open, setOpen, sheet, sheetOpen, toggle],
  );
  return (
    <SidebarContext.Provider value={value}>
      <div
        data-slot="sidebar-wrapper"
        className={cn("group/sidebar-wrapper flex h-svh w-full overflow-hidden bg-paper", className)}
        {...props}
      >
        {children}
      </div>
    </SidebarContext.Provider>
  );
}

function Sidebar({ className, children, label, ...props }: React.ComponentProps<"div"> & { label: string }) {
  const { sheet, sheetOpen, setSheetOpen, open } = useSidebar();
  if (sheet) {
    return (
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent side="left" className="w-64 p-0" showClose={false} aria-describedby={undefined}>
          <SheetTitle className="sr-only">{label}</SheetTitle>
          <SheetDescription className="sr-only">The pages of Docket</SheetDescription>
          <nav
            aria-label={label}
            data-slot="sidebar"
            data-state="expanded"
            className="group flex h-full w-full flex-col"
          >
            {children}
          </nav>
        </SheetContent>
      </Sheet>
    );
  }
  return (
    <nav
      aria-label={label}
      data-slot="sidebar"
      data-state={open ? "expanded" : "collapsed"}
      className={cn(
        "group flex h-full shrink-0 flex-col border-r border-rule bg-paper text-ink transition-[width] duration-150 ease-out data-[state=collapsed]:w-12 data-[state=expanded]:w-56",
        className,
      )}
      {...props}
    >
      {children}
    </nav>
  );
}

function SidebarTrigger({ className, ...props }: React.ComponentProps<typeof Button>) {
  const { toggle, sheet, sheetOpen, open } = useSidebar();
  const expanded = sheet ? sheetOpen : open;
  return (
    <Button
      data-slot="sidebar-trigger"
      variant="ghost"
      size="icon-sm"
      aria-expanded={expanded}
      aria-label={sheet ? "Open the pages" : open ? "Fold the sidebar" : "Unfold the sidebar"}
      className={className}
      onClick={toggle}
      {...props}
    >
      <PanelLeftIcon />
    </Button>
  );
}

function SidebarHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="sidebar-header" className={cn("flex flex-col gap-2 p-2", className)} {...props} />;
}

function SidebarContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-content"
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-1 overflow-x-hidden overflow-y-auto p-2 group-data-[state=collapsed]:px-1.5",
        className,
      )}
      {...props}
    />
  );
}

function SidebarFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-footer"
      className={cn("flex flex-col gap-1 border-t border-rule p-2 group-data-[state=collapsed]:px-1.5", className)}
      {...props}
    />
  );
}

function SidebarSeparator({ className, ...props }: React.ComponentProps<"hr">) {
  return <hr data-slot="sidebar-separator" className={cn("mx-2 my-1 border-rule", className)} {...props} />;
}

function SidebarMenu({ className, ...props }: React.ComponentProps<"ul">) {
  return <ul data-slot="sidebar-menu" className={cn("flex w-full min-w-0 flex-col gap-px", className)} {...props} />;
}

function SidebarMenuItem({ className, ...props }: React.ComponentProps<"li">) {
  return <li data-slot="sidebar-menu-item" className={cn("relative", className)} {...props} />;
}

const menuButton = cva(
  "tap flex h-8 w-full min-w-0 items-center gap-2.5 rounded-sm px-2 text-left text-sm text-ink transition-colors outline-offset-[-2px] hover:bg-hover group-data-[state=collapsed]:size-9 group-data-[state=collapsed]:justify-center group-data-[state=collapsed]:px-0 [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-ink-muted aria-[current=page]:bg-sheet aria-[current=page]:font-semibold aria-[current=page]:shadow-[inset_0_0_0_1px_var(--rule)] aria-[current=page]:[&>svg]:text-green",
);

function SidebarMenuButton({
  asChild = false,
  tooltip,
  className,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean; tooltip?: string } & VariantProps<typeof menuButton>) {
  const Comp = asChild ? Slot.Root : "button";
  const { open, sheet } = useSidebar();
  const button = <Comp data-slot="sidebar-menu-button" className={cn(menuButton(), className)} {...props} />;
  if (!tooltip || open || sheet) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="right">{tooltip}</TooltipContent>
    </Tooltip>
  );
}

/** A count or a mark at an entry's right edge; folded, it sits on the icon's corner. */
function SidebarMenuBadge({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="sidebar-menu-badge"
      className={cn(
        "pointer-events-none ml-auto text-xs text-ink-muted tabular-nums group-data-[state=collapsed]:hidden",
        className,
      )}
      {...props}
    />
  );
}

/** The part of an entry that folds away with the sidebar: its words. */
function SidebarLabel({ className, ...props }: React.ComponentProps<"span">) {
  return <span className={cn("min-w-0 truncate group-data-[state=collapsed]:sr-only", className)} {...props} />;
}

export {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarSeparator,
  SidebarTrigger,
  useSidebar,
};
