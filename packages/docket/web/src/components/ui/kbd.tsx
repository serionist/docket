import type * as React from "react";
import { cn } from "@/lib/utils";

/** A key as it reads on the keyboard, in the ? dialog and beside what it presses. */
function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-sm border border-rule bg-sheet px-1 font-sans text-xs text-ink-muted shadow-[0_1px_0_var(--rule)] select-none",
        className,
      )}
      {...props}
    />
  );
}

function KbdGroup({ className, ...props }: React.ComponentProps<"span">) {
  return <span data-slot="kbd-group" className={cn("inline-flex items-center gap-1", className)} {...props} />;
}

export { Kbd, KbdGroup };
