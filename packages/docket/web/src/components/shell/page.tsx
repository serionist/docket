/**
 * A page of the shell (DR-0708): its header, the list filling the middle, and the open item's panel on the right,
 * 480 px. From 1024 px down the panel slides over the list, and below 768 it takes the screen with a back button.
 */
import { ArrowLeftIcon, XIcon } from "lucide-react";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { useBand } from "@/hooks/use-width";
import { cn } from "@/lib/utils";

export function Page({ className, ...props }: React.ComponentProps<"main">) {
  return (
    <main
      id="main"
      tabIndex={-1}
      className={cn(
        "relative flex min-w-0 flex-1 flex-col overflow-hidden bg-sheet outline-none lg:my-2 lg:mr-2 lg:rounded-lg lg:border lg:border-rule",
        className,
      )}
      {...props}
    />
  );
}

/** The page's title and what acts on the whole page; on a sheet-sized window, the button that opens the pages. */
export function PageHeader({
  title,
  children,
  className,
}: {
  title: string;
  children?: React.ReactNode;
  className?: string;
}) {
  const { sheet } = useSidebar();
  return (
    <header className={cn("flex min-h-12 shrink-0 items-center gap-2 border-b border-rule px-3 sm:px-4", className)}>
      {sheet && <SidebarTrigger className="-ml-1" />}
      <h1 className="truncate text-xl font-semibold">{title}</h1>
      <div className="ml-auto flex items-center gap-1">{children}</div>
    </header>
  );
}

/** The list and, beside or over it, the panel. */
export function Workspace({ list, panel }: { list: React.ReactNode; panel?: React.ReactNode }) {
  const band = useBand();
  const narrow = band === "narrow";
  return (
    <div className="relative flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col" hidden={narrow && Boolean(panel)}>
        {list}
      </div>
      {panel}
    </div>
  );
}

/**
 * The open item's panel. It follows the selection; Esc closes it (the page's keys say so). Beside the list from
 * 1024 px, over it below, the whole screen below 768 with a back button.
 */
export function Panel({
  label,
  onClose,
  header,
  children,
  footer,
}: {
  label: string;
  onClose: () => void;
  header?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const band = useBand();
  const narrow = band === "narrow";
  const over = band === "small";
  const ref = React.useRef<HTMLElement>(null);
  return (
    <aside
      ref={ref}
      aria-label={label}
      data-slot="panel"
      className={cn(
        "flex min-h-0 flex-col bg-sheet",
        narrow ? "absolute inset-0 z-20" : "w-[480px] shrink-0 border-l border-rule",
        over && "absolute inset-y-0 right-0 z-20 max-w-full shadow-float",
      )}
    >
      <div className="flex min-h-12 shrink-0 items-center gap-2 border-b border-rule px-3">
        {narrow ? (
          <Button variant="ghost" size="sm" onClick={onClose} className="-ml-1">
            <ArrowLeftIcon />
            Back to the list
          </Button>
        ) : null}
        <div className="flex min-w-0 flex-1 items-center gap-2">{header}</div>
        {!narrow && (
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close the panel (Esc)">
            <XIcon />
          </Button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      {footer && <div className="shrink-0 border-t border-rule px-4 py-2">{footer}</div>}
    </aside>
  );
}

/** A group of rows under its heading: its name and how many it holds, in the list's own words. */
export function ListGroup({
  title,
  count,
  children,
  id,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
  id: string;
}) {
  return (
    <section aria-labelledby={id}>
      <h2
        id={id}
        className="sticky top-0 z-10 flex h-8 items-center gap-2 border-b border-rule bg-paper px-3 text-xs font-semibold text-ink-muted sm:px-4"
      >
        {title}
        <span className="font-normal tabular-nums">{count}</span>
      </h2>
      <ul>{children}</ul>
    </section>
  );
}

/** What a page says when its list is empty: what that means, and what to do. */
export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mx-auto flex max-w-sm flex-col items-start gap-2 px-6 py-10">
      <p className="text-base font-semibold">{title}</p>
      {children && <div className="text-sm text-ink-muted">{children}</div>}
    </div>
  );
}

/** A refusal or a failure, in the writer's own words, beside what it concerns. */
export function Refusal({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      role="alert"
      className={cn("rounded-sm border-l-2 border-red bg-red-wash px-3 py-2 text-sm text-ink", className)}
    >
      {children}
    </p>
  );
}

/** The notice at the list's foot after a write: what it did and its lines, with Undo, then Redo, which u presses. */
export function NoticeBar({
  text,
  lines,
  action,
  onAction,
  onDismiss,
  disabled,
}: {
  text?: string;
  lines: string[];
  action?: "Undo" | "Redo" | null;
  onAction?: () => void;
  onDismiss: () => void;
  disabled?: boolean;
}) {
  const said = lines.join("; ");
  return (
    <div
      role="status"
      className="flex min-h-10 shrink-0 items-center gap-3 border-t border-rule bg-paper px-3 text-sm sm:px-4"
    >
      <p className="min-w-0 flex-1 truncate" title={[text, said].filter(Boolean).join(" ")}>
        {text && <span className="font-semibold">{text}</span>}
        {text && said ? " " : ""}
        {said}
      </p>
      {action && onAction && (
        <Button variant="outline" size="sm" onClick={onAction} disabled={disabled}>
          {action}
          <kbd className="font-sans text-xs text-ink-muted pointer-coarse:hidden">u</kbd>
        </Button>
      )}
      <Button variant="ghost" size="icon-sm" onClick={onDismiss} aria-label="Dismiss the notice">
        <XIcon />
      </Button>
    </div>
  );
}
