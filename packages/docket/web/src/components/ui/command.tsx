/**
 * shadcn/ui's Command on cmdk, as `npx shadcn@4.21.4 add command` writes it, kept to the parts the app uses: the
 * root, the list, an item and the empty line, on the app's tokens. The who fields' combobox (DR-0714) puts its own
 * input inside the root, so cmdk's keys move through the list from the field, and the list in a popover.
 */
import { Command as CommandPrimitive } from "cmdk";
import type * as React from "react";
import { cn } from "@/lib/utils";

function Command({ className, ...props }: React.ComponentProps<typeof CommandPrimitive>) {
  return <CommandPrimitive data-slot="command" className={cn("text-ink", className)} {...props} />;
}

function CommandList({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      data-slot="command-list"
      className={cn(
        "max-h-64 scroll-py-1 overflow-x-hidden overflow-y-auto overscroll-contain outline-none",
        className,
      )}
      {...props}
    />
  );
}

function CommandEmpty({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className={cn("px-2 py-3 text-sm text-ink-muted", className)}
      {...props}
    />
  );
}

function CommandItem({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        "relative flex min-h-8 cursor-default items-center gap-2 rounded-sm px-2 py-1 text-sm pointer-coarse:min-h-11 outline-hidden select-none data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-selection [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    />
  );
}

export { Command, CommandEmpty, CommandItem, CommandList };
