import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { keyName, useRegistered } from "@/lib/keys";
import { useKeysOn } from "@/lib/prefs";

/** The ? dialog: what each key does here, read from the same registry the keys run from (DR-0711). */
export function KeysDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const groups = useRegistered();
  const on = useKeysOn();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Keys</DialogTitle>
          <DialogDescription>
            {on
              ? "A key does what its button does. Keys typed in a field stay in the field, and Enter or Space presses the focused button or link."
              : "The keys are off. Turn them on with the switch at the sidebar's foot."}
          </DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[60vh] gap-4 overflow-y-auto">
          {/* This page's keys first, then the ones that work everywhere: the order a key is matched in. */}
          {groups.map((group) => (
            <section key={group.id} aria-labelledby={`keys-${group.id}`}>
              <h3 id={`keys-${group.id}`} className="mb-1 text-xs font-semibold text-ink-muted">
                {group.title}
              </h3>
              <dl className="grid grid-cols-[9rem_1fr]">
                {group.commands.map((command) => (
                  <div key={command.what} className="contents">
                    <dt className="flex min-h-8 items-center border-b border-rule py-1.5">
                      <KbdGroup>
                        {command.keys.map((key) => (
                          <Kbd key={key}>{(command.ctrl ? "Ctrl+" : "") + keyName(key, command.ctrl)}</Kbd>
                        ))}
                      </KbdGroup>
                    </dt>
                    <dd className="flex min-h-8 items-center border-b border-rule py-1.5 text-sm">{command.what}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
