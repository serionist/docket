/**
 * The sidebar's pages in the maintainer's order (DR-0708): the day pages, then the rest, Settings at the foot. It
 * lists built pages only, each joining in the PR that builds or moves it; a page still on the server's own templates
 * is a plain link that loads it. Today, Queue and Meetings join with their pages, Queue with the untriaged count.
 */
import {
  BookOpenTextIcon,
  InboxIcon,
  ListChecksIcon,
  type LucideIcon,
  NotebookTextIcon,
  RefreshCwIcon,
  SearchIcon,
  Settings2Icon,
} from "lucide-react";

export type Mark = "staging" | "sync";

export interface NavPage {
  key: string;
  title: string;
  href: string;
  icon: LucideIcon;
  /** Moved into the app: a router link; otherwise the link loads the server's own page. */
  inApp: boolean;
  mark?: Mark;
}

export const DAY: NavPage[] = [
  { key: "ledger", title: "Ledger", href: "/ledger", icon: BookOpenTextIcon, inApp: true },
];

export const REST: NavPage[] = [
  { key: "search", title: "Search", href: "/search", icon: SearchIcon, inApp: false },
  { key: "briefs", title: "Briefs", href: "/briefs", icon: NotebookTextIcon, inApp: false },
  { key: "staging", title: "Staging", href: "/staging", icon: InboxIcon, inApp: false, mark: "staging" },
  { key: "curate", title: "Curate", href: "/curate", icon: ListChecksIcon, inApp: false },
  { key: "sync", title: "Sync", href: "/sync", icon: RefreshCwIcon, inApp: false, mark: "sync" },
];

export const SETTINGS: NavPage = {
  key: "settings",
  title: "Settings",
  href: "/settings",
  icon: Settings2Icon,
  inApp: false,
};
