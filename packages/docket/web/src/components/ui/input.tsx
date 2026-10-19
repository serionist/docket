import type * as React from "react";
import { cn } from "@/lib/utils";

const field =
  "w-full min-w-0 rounded-sm border border-control bg-sheet px-2 text-sm text-ink placeholder:text-ink-muted transition-colors focus-visible:border-green disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-red";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return <input type={type} data-slot="input" className={cn(field, "tap h-8", className)} {...props} />;
}

export { field, Input };
