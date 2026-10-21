/**
 * The briefs (DR-0780): a tab per entity type with its count, the decisions only the owner can make named first, in
 * the owner's ochre, then a group per type under a sticky head, orphans last, a ruled row per brief. A row reads at a
 * glance: the entity's name and, when it waits for a new rev, its stale mark; its rev and how long since; below, why
 * it is stale, as the stale report says it.
 */
import { AppLink } from "@/components/app-link";
import { Note } from "@/components/doc";
import { Empty, ListGroup } from "@/components/shell/page";
import { cn } from "@/lib/utils";
import type { BriefRow, BriefsView } from "./api";
import { StateBadge } from "./parts";

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Why a row is stale, each fact on its own: new records, flagged lines, the report's why, an orphan's survivor. */
function facts(row: BriefRow): string[] {
  const out: string[] = [];
  if (row.new) out.push(plural(row.new, "new record", "new records"));
  if (row.flagged) out.push(plural(row.flagged, "flagged line", "flagged lines"));
  if (row.why) out.push(row.why);
  if (row.orphan) out.push(row.orphan);
  return out;
}

function Row({ row }: { row: BriefRow }) {
  const said = facts(row);
  return (
    <li className="border-b border-rule">
      <AppLink
        href={row.url}
        data-row={row.id}
        data-state={row.state}
        className="group/row relative grid gap-0.5 bg-sheet px-3 py-1.5 text-ink outline-offset-[-2px] hover:bg-hover focus-visible:bg-selection pointer-coarse:min-h-11 sm:px-4"
      >
        <span aria-hidden className="absolute inset-y-0 left-0 hidden w-0.5 bg-green group-focus-visible/row:block" />
        <span className="flex min-h-5 min-w-0 items-center gap-2">
          <span dir="auto" className="min-w-0 truncate text-sm font-semibold">
            {row.name}
          </span>
          <StateBadge state={row.state} />
          <span className="ml-auto shrink-0 text-xs text-ink-muted tabular-nums">
            rev {row.rev}
            {row.age && `, ${row.age}`}
          </span>
        </span>
        {said.length > 0 && (
          <span className="flex flex-wrap gap-x-3 text-xs text-ink-muted">
            {said.map((fact) => (
              <span key={fact} dir="auto">
                {fact}
              </span>
            ))}
          </span>
        )}
      </AppLink>
    </li>
  );
}

/** The tabs narrowing the list to one entity type, links so each is a URL of its own, and what the list holds. */
function Types({ view }: { view: BriefsView }) {
  if (!view.types.length) return null;
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-rule px-2 py-1.5 sm:px-3">
      <nav aria-label="Entity types" className="max-w-full overflow-x-auto">
        <ul className="flex gap-0.5">
          {view.types.map((type) => (
            <li key={type.name}>
              <AppLink
                href={type.url}
                aria-current={type.on ? "page" : undefined}
                className={cn(
                  "tap flex h-7 items-center gap-1.5 rounded-sm px-2 text-sm whitespace-nowrap text-ink-muted hover:bg-hover hover:text-ink",
                  type.on && "bg-hover font-semibold text-ink",
                )}
              >
                {type.name}
                <span className="text-xs font-normal tabular-nums">{type.count}</span>
              </AppLink>
            </li>
          ))}
        </ul>
      </nav>
      {view.summary && <p className="px-1 text-xs text-ink-muted">{view.summary}</p>}
    </div>
  );
}

/** The entities with decisions waiting: only the owner's pick on the Briefs page settles one. */
function ToDecide({ view }: { view: BriefsView }) {
  if (!view.to_decide.length) return null;
  return (
    <section
      aria-labelledby="to-decide"
      className="flex shrink-0 flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-rule border-l-2 border-l-me bg-me-wash px-3 py-2 sm:px-4"
    >
      <h2 id="to-decide" className="text-xs font-semibold text-me">
        Decisions waiting for you
      </h2>
      <ul className="flex flex-wrap gap-x-3">
        {view.to_decide.map((link) => (
          <li key={link.url}>
            <AppLink
              href={link.url}
              dir="auto"
              className="inline-flex min-h-6 items-center text-sm text-ink underline underline-offset-4 hover:text-me pointer-coarse:min-h-11"
            >
              {link.text}
            </AppLink>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function BriefList({ view }: { view: BriefsView }) {
  return (
    <>
      <Types view={view} />
      <div className="min-h-0 flex-1 overflow-y-auto" role="region" aria-label="Briefs" tabIndex={-1}>
        <ToDecide view={view} />
        {view.hidden_note && <Note className="m-3 sm:mx-4">{view.hidden_note}</Note>}
        {view.empty ? (
          <Empty title={view.empty}>{view.empty_how && <p>{view.empty_how}</p>}</Empty>
        ) : (
          view.groups.map((group) => (
            <ListGroup key={group.type} id={`group-${group.type}`} title={group.type} count={group.rows.length}>
              {group.rows.map((row) => (
                <Row key={row.id} row={row} />
              ))}
            </ListGroup>
          ))
        )}
      </div>
    </>
  );
}
