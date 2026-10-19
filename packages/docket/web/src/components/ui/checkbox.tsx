import { CheckIcon, MinusIcon } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import type * as React from "react";
import { cn } from "@/lib/utils";

/** A tick box: 16 px drawn inside a target of 24 px, 44 on a coarse pointer. */
function Checkbox({ className, ...props }: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "peer group/check grid size-6 shrink-0 cursor-default place-items-center rounded-sm outline-offset-0 disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:size-11",
        className,
      )}
      {...props}
    >
      <span className="grid size-4 place-items-center rounded-sm border border-control bg-sheet text-on-green transition-colors group-data-checked/check:border-green group-data-checked/check:bg-green group-data-[state=indeterminate]/check:border-green group-data-[state=indeterminate]/check:bg-green">
        <CheckboxPrimitive.Indicator data-slot="checkbox-indicator" className="grid place-content-center">
          {props.checked === "indeterminate" ? (
            <MinusIcon className="size-3.5" strokeWidth={3} />
          ) : (
            <CheckIcon className="size-3.5" strokeWidth={3} />
          )}
        </CheckboxPrimitive.Indicator>
      </span>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
