import type * as React from "react";
import { field } from "@/components/ui/input";
import { cn } from "@/lib/utils";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(field, "field-sizing-content min-h-16 resize-none py-1.5", className)}
      {...props}
    />
  );
}

export { Textarea };
