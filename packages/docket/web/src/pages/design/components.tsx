/** Every shared component, in each of its states, on invented data. */
import { CalendarClockIcon, CopyIcon, EllipsisIcon, ListFilterIcon } from "lucide-react";
import * as React from "react";
import { Empty, NoticeBar, Refusal } from "@/components/shell/page";
import { Stamp } from "@/components/stamp";
import { WhoBox } from "@/components/who-box";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { markLapsed } from "@/lib/session";

function Block({ title, id, children }: { title: string; id: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="grid content-start gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Specimen({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-2 border-t border-rule pt-3">
      <p className="text-xs text-ink-muted">{name}</p>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

const SNOOZE = ["Tomorrow, Wed 21 Oct", "Next working day, Wed 21 Oct", "Next week, Mon 26 Oct", "In a month, 20 Nov"];

// The names a who field suggests, as the server orders them: the most used first, a vault's person with none.
const PEOPLE = [
  { name: "Ada Example", uses: 12, also: ["Ada E."] },
  { name: "Ben Sample", uses: 7, also: [] },
  { name: "Cleo Example", uses: 3, also: [] },
  { name: "Dóra Minta", uses: 0, also: ["Dora"] },
];

export function DesignComponents() {
  const [group, setGroup] = React.useState("due");
  const [keys, setKeys] = React.useState(true);
  const [ticked, setTicked] = React.useState<boolean | "indeterminate">("indeterminate");
  const [who, setWho] = React.useState("Ada Example");
  return (
    <div className="min-h-0 flex-1 overflow-y-auto" role="region" aria-label="Components" tabIndex={-1}>
      <div className="grid max-w-5xl gap-10 px-4 py-6 sm:px-6 lg:grid-cols-2">
        <Block title="Buttons" id="buttons">
          <Specimen name="By weight: the one main action, the rest, the quiet, the one that drops">
            <Button variant="default">Save</Button>
            <Button>Snooze</Button>
            <Button variant="ghost">Cancel</Button>
            <Button variant="destructive">Drop</Button>
            <Button variant="link">Open the source</Button>
          </Specimen>
          <Specimen name="Small, with a key, an icon, and while a write is in flight">
            <Button size="sm">
              Done <Kbd className="h-4 min-w-4 shadow-none">e</Kbd>
            </Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button size="icon-sm" variant="ghost" aria-label="Copy the locator">
                  <CopyIcon />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                Copy the locator <Kbd className="h-4 border-0 bg-transparent text-sheet shadow-none">c</Kbd>
              </TooltipContent>
            </Tooltip>
            <Button size="sm" disabled>
              Saving
            </Button>
          </Specimen>
        </Block>

        <Block title="Fields" id="fields">
          <div className="grid gap-3 border-t border-rule pt-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="who">Who</Label>
              <WhoBox id="who" value={who} onChange={setWho} onPick={setWho} people={PEOPLE} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="stream">Stream</Label>
              <Select defaultValue="pilot">
                <SelectTrigger id="stream" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pilot">pilot</SelectItem>
                  <SelectItem value="fleet">fleet</SelectItem>
                  <SelectItem value="northwind">northwind</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5 sm:col-span-2">
              <Label htmlFor="notes">Notes</Label>
              <Textarea id="notes" defaultValue="The venue holds the 12th and the 19th until Friday." />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="due">Due</Label>
              <Input id="due" aria-invalid="true" defaultValue="the 31st of June" />
              <p className="text-xs text-red">A date, such as 2026-10-23</p>
            </div>
            <div className="grid content-start gap-3 pt-6">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={ticked} onCheckedChange={setTicked} />
                Select all in Overdue
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={keys} onCheckedChange={setKeys} />
                Keys {keys ? "on" : "off"}
              </label>
            </div>
          </div>
        </Block>

        <Block title="Menus and popovers" id="menus">
          <Specimen name="A menu with a choice, a popover with the snooze choices, a row's more">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm">
                  <ListFilterIcon />
                  Group by {group}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>Group by</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={group} onValueChange={setGroup}>
                  {["due", "stream", "kind", "who", "meeting"].map((g) => (
                    <DropdownMenuRadioItem key={g} value={g}>
                      {g}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            <Popover>
              <PopoverTrigger asChild>
                <Button size="sm">
                  <CalendarClockIcon />
                  Snooze
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-64 p-1">
                <ul>
                  {SNOOZE.map((choice) => (
                    <li key={choice}>
                      <button
                        type="button"
                        className="flex h-8 w-full cursor-default items-center rounded-sm px-2 text-left text-sm hover:bg-hover pointer-coarse:h-11"
                      >
                        {choice}
                      </button>
                    </li>
                  ))}
                </ul>
              </PopoverContent>
            </Popover>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="icon-sm" variant="ghost" aria-label="More for A-0012">
                  <EllipsisIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem>
                  Open the source <DropdownMenuShortcut>o</DropdownMenuShortcut>
                </DropdownMenuItem>
                <DropdownMenuItem>
                  Copy the ref <DropdownMenuShortcut>c</DropdownMenuShortcut>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive">Drop</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </Specimen>
        </Block>

        <Block title="Dialogs" id="dialogs">
          <Specimen name="Saying why, asking before leaving, a lapsed session">
            <Dialog>
              <DialogTrigger asChild>
                <Button size="sm" variant="destructive">
                  Drop A-0012
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Drop A-0012</DialogTitle>
                  <DialogDescription>Send Ada the venue dates for the pilot review</DialogDescription>
                </DialogHeader>
                <div className="grid gap-1.5">
                  <Label htmlFor="why">Why</Label>
                  <Textarea id="why" placeholder="Ada booked the venue herself" />
                </div>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="ghost">Cancel</Button>
                  </DialogClose>
                  <Button variant="destructive">Drop</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm">Leave the page</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Leave with unsaved notes?</AlertDialogTitle>
                  <AlertDialogDescription>
                    A-0012's notes haven't saved. Leaving drops what you typed there.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Stay</AlertDialogCancel>
                  <AlertDialogAction>Leave</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button size="sm" variant="ghost" onClick={markLapsed}>
              End the session here
            </Button>
          </Specimen>
        </Block>

        <Block title="Keys and calls" id="keys">
          <Specimen name="A key, a pair, and the four calls">
            <Kbd>?</Kbd>
            <KbdGroup>
              <Kbd>j</Kbd>
              <Kbd>k</Kbd>
            </KbdGroup>
            <KbdGroup>
              <Kbd>Ctrl+Z</Kbd>
            </KbdGroup>
            <span className="mx-1 h-5 w-px bg-rule" aria-hidden />
            <Stamp verdict="discuss" />
            <Stamp verdict="claude" />
            <Stamp verdict="me" />
            <Stamp verdict="chase" />
          </Specimen>
        </Block>

        <Block title="What a list says" id="says">
          <div className="grid gap-3 border-t border-rule pt-3">
            <Refusal>A-0012 is closed: reopen it before you set its call.</Refusal>
            <div className="overflow-hidden rounded-sm border border-rule">
              <NoticeBar
                lines={["A-0012, A-0015 call: discuss"]}
                action="Undo"
                onAction={() => undefined}
                onDismiss={() => undefined}
              />
            </div>
            <div className="rounded-sm border border-rule">
              <Empty title="Nothing waits at the gate">Records land here when a sync brings them in.</Empty>
            </div>
            <ScrollArea className="h-32 rounded-sm border border-rule" viewportLabel="The panel's history">
              <ol className="grid gap-1 p-3 text-xs">
                {Array.from({ length: 12 }, (_, n) => (
                  <li key={n} className="grid grid-cols-[7.5rem_1fr] gap-2">
                    <span className="text-ink-muted tabular-nums">{`Mon 19 Oct 09:${String(10 + n).padStart(2, "0")}`}</span>
                    <span>{n % 2 ? "notes saved, by you" : "call: discuss, by you"}</span>
                  </li>
                ))}
              </ol>
            </ScrollArea>
          </div>
        </Block>
      </div>
    </div>
  );
}
