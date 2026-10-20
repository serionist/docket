/**
 * A day as the URLs and the server write it, ISO's 2026-10-23, and as the date picker's button shows it, "Fri 23 Oct",
 * by day_view.day_text's rule: the year added when it isn't today's. Every other date the app shows comes from the
 * server ready to show (DR-0709); the picker writes only the day just picked, before any read answers. Names are
 * English, as the server's are, whatever the browser's language.
 */

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

/** The day an ISO string names, at local midnight, or undefined for anything else, 2026-02-30 included. */
export function parseDay(text: string): Date | undefined {
  const found = ISO.exec(text.trim());
  if (!found) return undefined;
  const [year, month, day] = [Number(found[1]), Number(found[2]), Number(found[3])];
  const date = new Date(2000, 0, 1);
  // setFullYear, since the constructor reads a year under 100 as 1900 and on.
  date.setFullYear(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : undefined;
}

const two = (n: number) => String(n).padStart(2, "0");

/** A day as ISO writes it, from its local date. */
export function isoOf(date: Date): string {
  return `${String(date.getFullYear()).padStart(4, "0")}-${two(date.getMonth() + 1)}-${two(date.getDate())}`;
}

/** "Fri 23 Oct", or "Mon 4 Jan 2027" when the year isn't today's; an ISO string that doesn't parse, as it is. */
export function dayText(iso: string, today: string): string {
  const day = parseDay(iso);
  if (!day) return iso;
  const text = `${WEEKDAYS[day.getDay()]?.slice(0, 3)} ${day.getDate()} ${MONTHS[day.getMonth()]?.slice(0, 3)}`;
  const year = parseDay(today)?.getFullYear() ?? new Date().getFullYear();
  return day.getFullYear() === year ? text : `${text} ${day.getFullYear()}`;
}

/** A day said in full, "Friday 23 October 2026", for a day's button in the month: its number a word of its name. */
export function dayName(date: Date): string {
  return `${WEEKDAYS[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}
