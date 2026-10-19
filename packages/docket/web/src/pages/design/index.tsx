/**
 * /design: the tokens and every shared component in the shell, on invented data, off the sidebar (DR-0707). It is
 * where the shell lives until a page moves, and the screenshot loop's target (DR-0712). Its state is its URL:
 * `part` (list, tokens, components) and `open`, the item in the panel.
 */
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { KeyboardIcon } from "lucide-react";
import { Page, PageHeader } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import { Button } from "@/components/ui/button";
import { one, type Search } from "@/lib/search";
import { cn } from "@/lib/utils";
import { DesignComponents } from "./components";
import { DesignList } from "./list";
import { DesignTokens } from "./tokens";

const PARTS = [
  ["list", "List and panel"],
  ["tokens", "Tokens"],
  ["components", "Components"],
] as const;
type Part = (typeof PARTS)[number][0];

function partOf(search: Search): Part {
  const given = one(search, "part");
  return PARTS.some(([key]) => key === given) ? (given as Part) : "list";
}

export function DesignPage() {
  useTitle("Design");
  const search: Search = useSearch({ from: "/design" });
  const navigate = useNavigate();
  const showKeys = useShowKeys();
  const part = partOf(search);
  const open = part === "list" ? one(search, "open") : undefined;
  const setOpen = (ref: string | undefined) =>
    void navigate({
      to: "/design",
      search: ref ? { open: ref } : {},
      replace: ref !== undefined && open !== undefined,
    });
  return (
    <Page>
      <PageHeader title="Design">
        <nav aria-label="Parts" className="hidden items-center gap-0.5 sm:flex">
          {PARTS.map(([key, title]) => (
            <Link
              key={key}
              to="/design"
              search={key === "list" ? {} : { part: key }}
              aria-current={part === key ? "page" : undefined}
              className={cn(
                "tap flex h-8 items-center rounded-sm px-2.5 text-sm text-ink-muted hover:bg-hover hover:text-ink",
                part === key && "bg-hover font-semibold text-ink",
              )}
            >
              {title}
            </Link>
          ))}
        </nav>
        <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
          <KeyboardIcon />
        </Button>
      </PageHeader>
      <nav aria-label="Parts" className="flex gap-0.5 border-b border-rule px-3 py-1 sm:hidden">
        {PARTS.map(([key, title]) => (
          <Link
            key={key}
            to="/design"
            search={key === "list" ? {} : { part: key }}
            aria-current={part === key ? "page" : undefined}
            className={cn(
              "tap flex h-8 items-center rounded-sm px-2 text-sm text-ink-muted",
              part === key && "bg-hover font-semibold text-ink",
            )}
          >
            {title}
          </Link>
        ))}
      </nav>
      {part === "list" && <DesignList open={open} onOpen={setOpen} />}
      {part === "tokens" && <DesignTokens />}
      {part === "components" && <DesignComponents />}
    </Page>
  );
}
