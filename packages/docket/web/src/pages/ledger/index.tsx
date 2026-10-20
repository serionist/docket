/**
 * /ledger: every item, open by default, grouped by due or as the filters say (DR-0666): the day page over the
 * Ledger's own read, with the states and the grouping only it has.
 */
import { DayPage } from "@/pages/day/page";

export function LedgerPage() {
  return <DayPage page="ledger" title="Ledger" />;
}
