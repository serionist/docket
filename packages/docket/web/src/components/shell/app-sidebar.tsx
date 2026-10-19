import { useQuery } from "@tanstack/react-query";
import { KeyboardIcon, MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { Switch } from "@/components/ui/switch";
import { readShell, type Shell } from "@/lib/api";
import { setKeysOn, setTheme, type Theme, useKeysOn, useTheme } from "@/lib/prefs";
import { DAY, type NavPage, REST, SETTINGS } from "./nav";

export const SHELL_KEY = ["shell"] as const;

export function useShell() {
  return useQuery({ queryKey: SHELL_KEY, queryFn: readShell });
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The count or mark an entry carries, and the words a folded sidebar's tooltip and a screen reader get. */
function markOf(page: NavPage, shell: Shell | undefined): { shown?: string; words?: string; due?: boolean } {
  if (!shell || !page.mark) return {};
  if (page.mark === "staging" && shell.staging) {
    return { shown: String(shell.staging), words: `${plural(shell.staging, "record waits", "records wait")}` };
  }
  if (page.mark === "sync" && shell.sync_due.length) {
    return { due: true, words: `due: ${shell.sync_due.join(", ")}` };
  }
  return {};
}

function Entry({ page, shell, current }: { page: NavPage; shell: Shell | undefined; current?: string }) {
  const Icon = page.icon;
  const mark = markOf(page, shell);
  const label = mark.words ? `${page.title}, ${mark.words}` : page.title;
  return (
    <SidebarMenuItem>
      <SidebarMenuButton asChild tooltip={label}>
        <a href={page.href} aria-current={current === page.key ? "page" : undefined}>
          <Icon aria-hidden />
          <SidebarLabel>{page.title}</SidebarLabel>
          {mark.words && <span className="sr-only">, {mark.words}</span>}
          {mark.shown && <SidebarMenuBadge aria-hidden>{mark.shown}</SidebarMenuBadge>}
          {mark.due && (
            <SidebarMenuBadge aria-hidden className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-green" />
              due
            </SidebarMenuBadge>
          )}
        </a>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

const THEME_ICON = { system: MonitorIcon, light: SunIcon, dark: MoonIcon } satisfies Record<Theme, unknown>;
const THEME_WORDS: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

function ThemeMenu() {
  const theme = useTheme();
  const { open, sheet } = useSidebar();
  const Icon = THEME_ICON[theme];
  const folded = !open && !sheet;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size={folded ? "icon" : "sm"}
          className={folded ? "size-9" : "h-8 justify-start gap-2.5 px-2"}
          aria-label={folded ? `Theme: ${THEME_WORDS[theme]}` : undefined}
        >
          <Icon className="text-ink-muted" />
          {!folded && (
            <>
              <span className="flex-1 text-left">Theme</span>
              <span className="text-xs text-ink-muted">{THEME_WORDS[theme]}</span>
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as Theme)}>
          {(Object.keys(THEME_WORDS) as Theme[]).map((key) => {
            const ItemIcon = THEME_ICON[key];
            return (
              <DropdownMenuRadioItem key={key} value={key}>
                <ItemIcon />
                {THEME_WORDS[key]}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function KeysSwitch() {
  const on = useKeysOn();
  const { open, sheet } = useSidebar();
  if (!open && !sheet) return null;
  return (
    <label className="tap flex h-8 cursor-default items-center gap-2.5 rounded-sm pr-0.5 pl-2 text-sm hover:bg-hover">
      <KeyboardIcon className="size-4 text-ink-muted" aria-hidden />
      <span className="flex-1">Keys</span>
      <Switch checked={on} onCheckedChange={setKeysOn} aria-label="Keys on" />
    </label>
  );
}

function Where({ shell }: { shell: Shell | undefined }) {
  if (!shell) return null;
  const where = shell.problem ?? `${shell.vault ?? "no vault"} on ${shell.machine ?? "this machine"}`;
  return (
    <div className="grid gap-0.5 px-2 pt-1 pb-0.5 text-xs text-ink-muted group-data-[state=collapsed]:hidden">
      <p className="truncate" title={where}>
        {where}
      </p>
      {/* Two lines at 224 px, so its time shows. */}
      <p className="text-pretty">{shell.index_line}</p>
    </div>
  );
}

/** The shell's sidebar: the pages, their counts from /api/web/shell, and at its foot Settings and the switches. */
export function AppSidebar({ current }: { current?: string }) {
  const { data: shell } = useShell();
  const { sheet } = useSidebar();
  return (
    <Sidebar label="Pages">
      <SidebarHeader className="flex-row items-center justify-between gap-1 group-data-[state=collapsed]:flex-col group-data-[state=collapsed]:px-1.5">
        <a
          href="/search"
          className="tap flex h-8 min-w-0 items-center gap-2 rounded-sm px-2 text-sm font-semibold group-data-[state=collapsed]:sr-only"
        >
          <span aria-hidden className="grid size-5 place-items-center rounded-sm bg-green text-xs text-on-green">
            D
          </span>
          Docket
        </a>
        {!sheet && <SidebarTrigger />}
      </SidebarHeader>
      <SidebarContent>
        <SidebarMenu>
          {DAY.map((page) => (
            <Entry key={page.key} page={page} shell={shell} current={current} />
          ))}
        </SidebarMenu>
        <SidebarSeparator />
        <SidebarMenu>
          {REST.map((page) => (
            <Entry key={page.key} page={page} shell={shell} current={current} />
          ))}
        </SidebarMenu>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <Entry page={SETTINGS} shell={shell} current={current} />
        </SidebarMenu>
        <ThemeMenu />
        <KeysSwitch />
        <Where shell={shell} />
      </SidebarFooter>
    </Sidebar>
  );
}
