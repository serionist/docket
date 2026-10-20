/**
 * /queue: the open items to work through (DR-0666), the untriaged first, those whose snooze ended leading them, then
 * the items by their call, discuss, claude, me and chase, each by due: the day page over the queue's read, with the
 * Ledger's keys, act bar, panel and new item.
 */
import { DayPage } from "@/pages/day/page";

export function QueuePage() {
  return <DayPage page="queue" title="Queue" />;
}
