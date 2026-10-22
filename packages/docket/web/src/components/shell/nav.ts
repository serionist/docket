/**
 * The sidebar's pages in the maintainer's order (DR-0708): the day pages, then the rest, Settings at the foot. It
 * lists built pages only, each in the app. Today leads, the start page (DR-0669); Queue carries the untriaged count,
 * Staging what waits at the gate and Sync its due mark.
 */
import {
  BookOpenTextIcon,
  CalendarDaysIcon,
  InboxIcon,
  ListChecksIcon,
  ListTodoIcon,
  type LucideIcon,
  NotebookTextIcon,
  RefreshCwIcon,
  SearchIcon,
  Settings2Icon,
  SunriseIcon,
} from "lucide-react";

export type Mark = "queue" | "staging" | "sync";

export interface NavPage {
  key: string;
  title: string;
  href: string;
  icon: LucideIcon;
  mark?: Mark;
}

export const DAY: NavPage[] = [
  { key: "today", title: "Today", href: "/today", icon: SunriseIcon },
  { key: "queue", title: "Queue", href: "/queue", icon: ListTodoIcon, mark: "queue" },
  { key: "ledger", title: "Ledger", href: "/ledger", icon: BookOpenTextIcon },
  { key: "meetings", title: "Meetings", href: "/meetings", icon: CalendarDaysIcon },
];

export const REST: NavPage[] = [
  { key: "search", title: "Search", href: "/search", icon: SearchIcon },
  { key: "briefs", title: "Briefs", href: "/briefs", icon: NotebookTextIcon },
  { key: "staging", title: "Staging", href: "/staging", icon: InboxIcon, mark: "staging" },
  { key: "curate", title: "Curate", href: "/curate", icon: ListChecksIcon },
  { key: "sync", title: "Sync", href: "/sync", icon: RefreshCwIcon, mark: "sync" },
];

export const SETTINGS: NavPage = {
  key: "settings",
  title: "Settings",
  href: "/settings",
  icon: Settings2Icon,
};
