import { Switch as SwitchPrimitive } from "radix-ui";
import type * as React from "react";
import { cn } from "@/lib/utils";

/** A switch: a 32 x 20 track inside a target of 24 px high, 44 on a coarse pointer. */
function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer group/switch inline-flex h-6 w-9 shrink-0 cursor-default items-center justify-center rounded-full outline-offset-0 disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:size-11",
        className,
      )}
      {...props}
    >
      <span className="relative flex h-5 w-8 items-center rounded-full border border-control bg-sheet transition-colors group-data-checked/switch:border-green group-data-checked/switch:bg-green">
        <SwitchPrimitive.Thumb
          data-slot="switch-thumb"
          className="pointer-events-none block size-3.5 translate-x-0.5 rounded-full bg-control transition-transform data-checked:translate-x-[14px] data-checked:bg-on-green"
        />
      </span>
    </SwitchPrimitive.Root>
  );
}

export { Switch };
