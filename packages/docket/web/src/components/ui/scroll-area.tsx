import { ScrollArea as ScrollAreaPrimitive } from "radix-ui";
import type * as React from "react";
import { useNonce } from "@/lib/nonce";
import { cn } from "@/lib/utils";

/**
 * A scrolling region with a quiet thumb. Its viewport carries the document's nonce: Radix styles it through a style
 * element. A labelled viewport takes the focus, so the keyboard scrolls it.
 */
function ScrollArea({
  className,
  children,
  viewportRef,
  viewportLabel,
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.Root> & {
  viewportRef?: React.Ref<HTMLDivElement>;
  viewportLabel?: string;
}) {
  const nonce = useNonce();
  return (
    <ScrollAreaPrimitive.Root data-slot="scroll-area" className={cn("relative overflow-hidden", className)} {...props}>
      <ScrollAreaPrimitive.Viewport
        ref={viewportRef}
        nonce={nonce}
        data-slot="scroll-area-viewport"
        aria-label={viewportLabel}
        role={viewportLabel ? "region" : undefined}
        tabIndex={viewportLabel ? 0 : undefined}
        className="size-full rounded-[inherit] focus-visible:outline-offset-[-2px]"
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollBar />
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
}

function ScrollBar({
  className,
  orientation = "vertical",
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>) {
  return (
    <ScrollAreaPrimitive.ScrollAreaScrollbar
      data-slot="scroll-area-scrollbar"
      orientation={orientation}
      className={cn(
        "flex touch-none p-px select-none data-horizontal:h-2 data-horizontal:flex-col data-vertical:h-full data-vertical:w-2",
        className,
      )}
      {...props}
    >
      <ScrollAreaPrimitive.ScrollAreaThumb className="relative flex-1 rounded-full bg-rule hover:bg-control" />
    </ScrollAreaPrimitive.ScrollAreaScrollbar>
  );
}

export { ScrollArea, ScrollBar };
