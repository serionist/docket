/**
 * The bar under Today's header (DR-0773): the day in words and its counts in one line, then each live source's last
 * sync, one click from its runs on the Sync page (DR-0669). A source due a sync carries the sidebar's own due mark,
 * and the bar says what to do about it; one with a run open says it is syncing.
 */
import { cn } from "@/lib/utils";
import type { SyncLine, TodayList } from "@/pages/day/api";

function Source({ sync }: { sync: SyncLine }) {
  return (
    <li>
      <a
        href={sync.href}
        title={sync.title}
        data-due={sync.due ? "1" : undefined}
        className="tap inline-flex h-6 items-center gap-1.5 rounded-sm px-1 text-xs underline-offset-4 hover:bg-hover hover:underline"
      >
        <span className="font-semibold text-ink">{sync.source}</span>
        <span className={cn(sync.running ? "text-green" : "text-ink-muted")}>{sync.said}</span>
        {sync.due && (
          <span className="flex items-center gap-1 text-ink">
            <span aria-hidden className="size-1.5 rounded-full bg-green" />
            due
          </span>
        )}
      </a>
    </li>
  );
}

export function DayBar({ list }: { list: TodayList }) {
  const due = list.syncs.filter((s) => s.due).length;
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule bg-paper px-3 py-1.5 sm:px-4">
      <p className="min-w-0 text-xs">
        <span className="font-semibold text-ink">{list.day}</span>
        <span className="text-ink-muted">: {list.counts}</span>
      </p>
      {list.sync_problem ? (
        <p className="text-xs text-red sm:ml-auto">{list.sync_problem}</p>
      ) : list.syncs.length > 0 ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 sm:ml-auto">
          <ul aria-label="Last sync per source" className="flex flex-wrap items-center gap-x-1 gap-y-1">
            {list.syncs.map((sync) => (
              <Source key={sync.source} sync={sync} />
            ))}
          </ul>
          {due > 0 && <p className="text-xs text-ink-muted">Ask Claude to sync {due === 1 ? "it" : "them"}.</p>}
        </div>
      ) : null}
    </div>
  );
}
