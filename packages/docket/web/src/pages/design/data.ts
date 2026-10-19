/**
 * The invented week the /design page shows: items as a day page lists them, people and companies made up. Nothing
 * here is read from a vault.
 */
import type { Verdict } from "@/components/stamp";

export type Kind = "reply" | "task" | "decision" | "invite" | "waiting";

export interface Item {
  ref: string;
  kind: Kind;
  title: string;
  who: string;
  due: string;
  late?: string;
  stream: string;
  verdict?: Verdict;
  source: string;
  notes: string;
  history: string[];
}

export interface Group {
  key: string;
  title: string;
  items: Item[];
}

function item(
  ref: string,
  kind: Kind,
  title: string,
  who: string,
  due: string,
  stream: string,
  extra: Partial<Item> = {},
): Item {
  return {
    ref,
    kind,
    title,
    who,
    due,
    stream,
    source: `r:mail:northwind-${ref.slice(2).toLowerCase()}`,
    notes: "",
    history: [`Mon 19 Oct 09:12  added by Claude from the sync`],
    ...extra,
  };
}

export const GROUPS: Group[] = [
  {
    key: "overdue",
    title: "Overdue",
    items: [
      item("A-0012", "reply", "Send Ada the venue dates for the pilot review", "Ada Example", "16 Oct", "pilot", {
        late: "4 days late",
        verdict: "discuss",
        source: "r:calls:rec-0100@01:50-03:20",
        notes:
          "Ada asked on Thursday's call which two days in November work for the review. The venue holds the 12th and the 19th until Friday.",
        history: [
          "Thu 15 Oct 14:31  added by Claude from the calls sync",
          "Fri 16 Oct 08:02  due set to 16 Oct by you",
          "Mon 19 Oct 09:12  call: discuss, by you",
        ],
      }),
      item("A-0015", "task", "Renew the fleet tracker licence before it lapses", "Ben Sample", "17 Oct", "fleet", {
        late: "3 days late",
        verdict: "me",
      }),
      item("A-0018", "reply", "Answer Cleo about the Northwind order form", "Cleo Example", "19 Oct", "northwind", {
        late: "1 day late",
      }),
    ],
  },
  {
    key: "today",
    title: "Due today",
    items: [
      item("A-0021", "decision", "Pick the transcription model for calls", "", "20 Oct", "pilot", {
        verdict: "claude",
      }),
      item("A-0022", "task", "Book the room for Thursday's fleet review", "Dora Example", "20 Oct", "fleet"),
      item("A-0024", "reply", "Thank Emil for the site photos", "Emil Sample", "20 Oct", "fleet", { verdict: "me" }),
    ],
  },
  {
    key: "week",
    title: "This week",
    items: [
      item("A-0026", "waiting", "Northwind's signed order form", "Ben Sample", "21 Oct", "northwind", {
        verdict: "chase",
      }),
      item("A-0027", "invite", "Fleet review, Thursday 14:00 to 15:00", "Dora Example", "22 Oct", "fleet"),
      item("A-0029", "task", "Draft the pilot's risk list for Ada", "Ada Example", "22 Oct", "pilot", {
        verdict: "claude",
      }),
      item("A-0030", "decision", "Keep or drop the Friday status mail", "", "23 Oct", "pilot"),
      item("A-0031", "reply", "Confirm the delivery window with the depot", "Emil Sample", "23 Oct", "fleet"),
      item("A-0033", "waiting", "Cleo's numbers for the quarterly plan", "Cleo Example", "23 Oct", "northwind", {
        verdict: "chase",
      }),
    ],
  },
  {
    key: "later",
    title: "Later",
    items: [
      item("A-0035", "task", "Move the archive to the new storage account", "", "28 Oct", "fleet"),
      item("A-0036", "decision", "Whether the pilot runs a second site", "Ada Example", "30 Oct", "pilot", {
        verdict: "discuss",
      }),
      item("A-0038", "invite", "Northwind quarterly, 3 Nov 10:00", "Cleo Example", "3 Nov", "northwind"),
      item("A-0040", "task", "Write up the depot visit for the team", "", "6 Nov", "fleet", { verdict: "me" }),
      item("A-0041", "reply", "Send Ben the revised price sheet", "Ben Sample", "9 Nov", "northwind"),
    ],
  },
  {
    key: "none",
    title: "No date",
    items: [
      item("A-0043", "task", "Tidy the pilot's shared folder", "", "", "pilot"),
      item("A-0044", "waiting", "Dora's feedback on the review agenda", "Dora Example", "", "fleet"),
    ],
  },
];

export const ITEMS: Item[] = GROUPS.flatMap((group) => group.items);
