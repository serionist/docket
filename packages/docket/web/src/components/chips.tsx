/**
 * A row of chips that narrow a page, each a link to the page's URL with it on, as the server wrote it: Staging's
 * sources and states, Sync's sources. The one that is on is the current page.
 */
import { AppLink } from "@/components/app-link";
import { cn } from "@/lib/utils";

export interface Chip {
  label: string;
  url: string;
  on: boolean;
}

export function Chips({ label, chips }: { label: string; chips: Chip[] }) {
  return (
    <nav aria-label={label} className="shrink-0">
      <ul className="flex gap-0.5">
        {chips.map((chip) => (
          <li key={chip.url}>
            <AppLink
              href={chip.url}
              aria-current={chip.on ? "page" : undefined}
              className={cn(
                "tap flex h-7 items-center rounded-sm px-2 text-sm whitespace-nowrap text-ink-muted hover:bg-hover hover:text-ink",
                chip.on && "bg-hover font-semibold text-ink",
              )}
            >
              {chip.label}
            </AppLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
