import type * as React from "react";
import { cn } from "@/lib/utils";

/** A part still loading, in the place and the size it will take; hidden from assistive technology, whose words say
 * what is loading instead. */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div data-slot="skeleton" aria-hidden className={cn("animate-pulse rounded-md bg-muted", className)} {...props} />
  );
}

export { Skeleton };
