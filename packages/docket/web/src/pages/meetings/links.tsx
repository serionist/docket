/**
 * A link to another page of the app, or to a panel on this one, by the URL the server wrote: a plain click goes
 * through the router, so the leave guard asks about unsaved text first and the page keeps its reads; a click with a
 * key held, or a middle click, opens the URL as the browser would.
 */
import { useNavigate } from "@tanstack/react-router";
import type * as React from "react";
import { cn } from "@/lib/utils";

export function InLink({ href, className, children }: { href: string; className?: string; children: React.ReactNode }) {
  const navigate = useNavigate();
  return (
    <a
      href={href}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        void navigate({ href });
      }}
      className={cn("text-green underline-offset-4 hover:underline", className)}
    >
      {children}
    </a>
  );
}
