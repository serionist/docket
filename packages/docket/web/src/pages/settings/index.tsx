/**
 * /settings (DR-0789, DR-0790): what vault.yaml says about the sources, the never-store lists and redaction, and this
 * machine's models, each change both ways with its button (DR-0601). A never-store value is added after its question
 * and taken off in one write; a rule turns on or off; a source changes sensitivity or turns on or off, private only
 * after its question. Turning credentials redaction off has no button: the page names its command, for the owner's own
 * console. The value typed to add stays in the page: the question, the add and every answer leave it out, and the
 * address bar never holds it. Each write carries the read's token, so a page older than the file writes nothing and
 * reads itself again. A write is busy on its own row only; its refusal lands beside what it was on, an add's on its
 * field, which takes the focus and keeps what was typed.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useRouterState } from "@tanstack/react-router";
import { KeyboardIcon } from "lucide-react";
import * as React from "react";
import { Note } from "@/components/doc";
import { COPIED_MS, Paste } from "@/components/paste";
import { SHELL_KEY } from "@/components/shell/app-sidebar";
import { Loading, NoticeBar, Page, PageHeader, ReadFailed, Refusal } from "@/components/shell/page";
import { useShowKeys, useTitle } from "@/components/shell/shell";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { returnFocus } from "@/lib/focus";
import { HttpError } from "@/lib/query";
import { writeBack } from "@/lib/search";
import { wordsOf } from "@/pages/day/api";
import {
  type AddQuestion,
  neverStore,
  neverStoreLine,
  PATH,
  type PrivateQuestion,
  readSettings,
  redaction,
  type RuleRead,
  source,
} from "./api";
import { type Acts, Models, NeverStore, Redaction, type Said, Sources } from "./parts";

export const SETTINGS = ["settings"] as const;

/** The add's question as the page holds it: the value typed, and the service's question, which never holds it. */
interface Adding {
  name: string;
  value: string;
  question: AddQuestion;
  refused: string;
  stale: string;
  byHand: string;
}

interface Privating {
  question: PrivateQuestion;
  refused: string;
}

const moved = (error: unknown) => error instanceof HttpError && error.status === 409;

/** The heading of what a write was on, which takes the focus when the control that sent it is gone. */
const headingOf = (where: string) => document.getElementById(`${where}-title`);

export function SettingsPage() {
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const router = useRouter();
  const client = useQueryClient();
  const showKeys = useShowKeys();
  useTitle("Settings");

  const read = useQuery({
    queryKey: [...SETTINGS, searchStr],
    queryFn: () => readSettings(searchStr),
    placeholderData: (previous) => previous,
    refetchOnMount: "always",
  });
  const data = read.data;

  React.useEffect(() => {
    const history = { location: router.history.location, replace: (href: string) => router.history.replace(href) };
    if (data && data.asked === searchStr) writeBack(history, data.canonical);
  }, [data, searchStr, router]);

  // What a write did, or a URL from before the app that still names it, said once at the page's foot.
  const [notice, setNotice] = React.useState<string | null>(null);
  const [seenNotice, setSeenNotice] = React.useState<string | null>(null);
  if (data?.notice && data.asked === searchStr && seenNotice !== data.asked) {
    setSeenNotice(data.asked);
    setNotice(data.notice);
  }
  const [said, setSaid] = React.useState<Said | null>(null);
  // The row a write is in flight on: only its controls wait.
  const [busy, setBusy] = React.useState<string | null>(null);
  const [typed, setTyped] = React.useState<Record<string, string>>({});
  const [fieldError, setFieldError] = React.useState<Record<string, string>>({});
  const [adding, setAdding] = React.useState<Adding | null>(null);
  const [privating, setPrivating] = React.useState<Privating | null>(null);
  const opener = React.useRef<Element | null>(null);

  // A write's control the read again took away (a value taken off, a sensitivity that no longer offers itself):
  // the focus goes to the heading of what it was on, never to the page's body.
  const refocus = React.useRef<string | null>(null);
  React.useEffect(() => {
    const where = refocus.current;
    if (where === null || read.isFetching) return;
    refocus.current = null;
    const active = document.activeElement;
    if (!active || active === document.body || !active.isConnected) headingOf(where)?.focus({ preventScroll: true });
  });

  const [copied, setCopied] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (copied === null) return;
    const timer = setTimeout(() => setCopied(null), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = (text: string) => void navigator.clipboard?.writeText(text).then(() => setCopied(text));

  // A write read again: the page, and the sidebar, whose marks a source's change can move.
  // It answers once the page's read is back, with the token for the file the write left.
  const settle = (where: string) => {
    refocus.current = where;
    void client.invalidateQueries({ queryKey: SHELL_KEY });
    return client.invalidateQueries({ queryKey: SETTINGS });
  };

  /** One write on a row: busy there while it goes, its notice at the foot, a warning or a refusal beside what it was
   * on, and a page older than the file read again. */
  const run = <T extends { notice?: string | null; warning?: string | null }>(
    where: string,
    write: () => Promise<T>,
  ) => {
    if (busy !== null) return;
    setBusy(where);
    setSaid(null);
    write()
      .then(
        (answer) => {
          if (answer.notice) setNotice(answer.notice);
          if (answer.warning) setSaid({ where, words: answer.warning, tone: "warn" });
          // The row stays busy until the page holds the new file's token, so the next write isn't refused as stale.
          return settle(where);
        },
        (error: unknown) => {
          setSaid({ where, words: wordsOf(error), tone: "refused" });
          return moved(error) ? settle(where) : undefined;
        },
      )
      .finally(() => setBusy(null));
  };

  const token = data?.token ?? "";

  const ask = (name: string, value: string, stale = "") => {
    const where = `list-${name}`;
    if (busy !== null && busy !== where) return;
    setBusy(where);
    setFieldError((all) => ({ ...all, [name]: "" }));
    neverStore({ act: "ask", name, value, token })
      .then(
        (answer) => {
          if (answer.question) {
            if (!adding) opener.current = document.activeElement;
            setAdding({ name, value, question: answer.question, refused: "", stale, byHand: "" });
            return;
          }
          setAdding(null);
          if (answer.notice) setNotice(answer.notice);
          setTyped((all) => ({ ...all, [name]: "" }));
        },
        (error: unknown) => {
          // A refusal of the value lands on its field, which keeps it and takes the focus.
          setAdding(null);
          setFieldError((all) => ({ ...all, [name]: wordsOf(error) }));
          document.getElementById(`add-${name}`)?.focus();
        },
      )
      .finally(() => setBusy(null));
  };

  const add = () => {
    if (!adding || busy !== null) return;
    const { name, value, question } = adding;
    const where = `list-${name}`;
    setBusy(where);
    neverStore({ act: "add", name, value, token: question.token }).then(
      (answer) => {
        if (answer.by_hand) {
          setBusy(null);
          setAdding({ ...adding, byHand: answer.warning ?? "", refused: "" });
          return;
        }
        setAdding(null);
        if (answer.notice) setNotice(answer.notice);
        setTyped((all) => ({ ...all, [name]: "" }));
        setBusy(where);
        void settle(where).finally(() => setBusy(null));
      },
      (error: unknown) => {
        // The file changed since the question: asked again, counted as it stands now; the row stays busy.
        if (moved(error)) {
          ask(name, value, wordsOf(error));
          return;
        }
        setBusy(null);
        setAdding({ ...adding, refused: wordsOf(error) });
      },
    );
  };

  const askPrivate = (name: string, refusedBefore = "") => {
    const where = `source-${name}`;
    setBusy(where);
    setSaid(null);
    source({ source: name, act: "private", token, confirmed: false })
      .then(
        (answer) => {
          if (!answer.question) return;
          if (!privating) opener.current = document.activeElement;
          setPrivating({ question: answer.question, refused: refusedBefore });
        },
        (error: unknown) => {
          setPrivating(null);
          setSaid({ where, words: wordsOf(error), tone: "refused" });
        },
      )
      .finally(() => setBusy(null));
  };

  const makePrivate = (question: PrivateQuestion) => {
    if (busy !== null) return;
    const where = `source-${question.source}`;
    setBusy(where);
    source({ source: question.source, act: "private", token: question.token, confirmed: true }).then(
      (answer) => {
        setPrivating(null);
        if (answer.notice) setNotice(answer.notice);
        if (answer.warning) setSaid({ where, words: answer.warning, tone: "warn" });
        void settle(where).finally(() => setBusy(null));
      },
      (error: unknown) => {
        if (moved(error)) {
          askPrivate(question.source, wordsOf(error));
          return;
        }
        setBusy(null);
        setPrivating({ question, refused: wordsOf(error) });
      },
    );
  };

  const change = (name: string, act: string) => {
    if (busy !== null) return;
    if (act === "private") askPrivate(name);
    else run(`source-${name}`, () => source({ source: name, act, token, confirmed: false }));
  };

  const turn = (rule: RuleRead) =>
    run(`rule-${rule.name}`, () => redaction({ rule: rule.name, state: rule.on ? "off" : "on", token }));

  const takeOff = (name: string, value: string) =>
    run(`list-${name}`, () => neverStore({ act: "off", name, value, token }));

  const acts: Acts = { busy, said, onCopy: copy };
  const foot = (notice || copied) && (
    <NoticeBar
      text={copied ? "Copied" : (notice ?? "")}
      lines={copied ? [copied] : []}
      onDismiss={() => (copied ? setCopied(null) : setNotice(null))}
    />
  );

  let body: React.ReactNode;
  if (read.error && !data) {
    body = (
      <ReadFailed
        error={read.error}
        onRetry={() => void read.refetch()}
        whole={() => void router.navigate({ href: PATH })}
      />
    );
  } else if (!data) {
    body = <Loading what="Reading vault.yaml and this machine's models." rows={8} />;
  } else {
    body = (
      <div className="grid w-full max-w-4xl gap-4 pb-6">
        <div className="grid gap-2 px-3 pt-3 sm:px-4">
          <p className="text-xs text-ink-muted">
            {data.machine},{" "}
            {data.machine === data.primary ? "the primary machine" : `not the primary machine, ${data.primary}`}
          </p>
          <p data-lead className="text-base">
            {data.lead}
          </p>
          {data.waiting && <Note tone="warn">{data.waiting}</Note>}
          <Note>{data.form}</Note>
          {data.unfilled.length > 0 && (
            <p className="text-xs text-ink-muted">
              Put these in by hand where a command holds their placeholder:{" "}
              {data.unfilled.map(({ name, value }, at) => (
                <React.Fragment key={`${name}-${value}`}>
                  <code className="font-mono">&lt;{name}&gt;</code> is{" "}
                  <code dir="auto" className="font-mono break-all">
                    {value}
                  </code>
                  {at < data.unfilled.length - 1 ? "; " : "."}
                </React.Fragment>
              ))}
            </p>
          )}
        </div>
        <Sources page={data} acts={acts} onChange={change} />
        <NeverStore
          page={data}
          acts={acts}
          typed={typed}
          errors={fieldError}
          onType={(name, value) => {
            setTyped((all) => ({ ...all, [name]: value }));
            // A refusal clears once the value changes: the next submit says whether it holds.
            setFieldError((all) => (all[name] ? { ...all, [name]: "" } : all));
          }}
          onAdd={(name) => {
            setSaid(null);
            ask(name, typed[name] ?? "");
          }}
          onTakeOff={takeOff}
        />
        <Redaction page={data} acts={acts} onTurn={turn} />
        <Models models={data.models} machine={data.machine} onCopy={copy} />
      </div>
    );
  }

  return (
    <Page>
      <PageHeader title="Settings">
        <Button variant="ghost" size="icon-sm" aria-label="Keys (?)" onClick={showKeys}>
          <KeyboardIcon />
        </Button>
      </PageHeader>
      <div className="flex min-h-0 flex-1 flex-col">
        <div
          className="min-h-0 flex-1 overflow-y-auto"
          role="region"
          aria-label="What vault.yaml says"
          aria-busy={read.isFetching || undefined}
          tabIndex={-1}
        >
          {body}
        </div>
        {foot}
      </div>
      <AddDialog
        adding={adding}
        busy={busy !== null}
        onAdd={add}
        onCopy={copy}
        onClose={() => setAdding(null)}
        opener={opener}
      />
      <PrivateDialog
        privating={privating}
        busy={busy !== null}
        onConfirm={makePrivate}
        onClose={() => setPrivating(null)}
        opener={opener}
      />
    </Page>
  );
}

function AddDialog({
  adding,
  busy,
  onAdd,
  onCopy,
  onClose,
  opener,
}: {
  adding: Adding | null;
  busy: boolean;
  onAdd: () => void;
  onCopy: (text: string) => void;
  onClose: () => void;
  opener: React.RefObject<Element | null>;
}) {
  const question = adding?.question;
  return (
    <AlertDialog open={adding !== null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent data-question="add" onCloseAutoFocus={(event) => returnFocus(event, opener.current)}>
        {adding && question && (
          <>
            <AlertDialogHeader>
              <AlertDialogTitle>Add to {adding.name}?</AlertDialogTitle>
              <AlertDialogDescription dir="auto" className="text-base font-semibold break-words text-ink" data-value>
                {adding.value.trim()}
              </AlertDialogDescription>
            </AlertDialogHeader>
            {adding.stale && <Note tone="warn">{adding.stale}</Note>}
            <ul className="grid list-disc gap-1 pl-5 marker:text-ink-muted">
              {question.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            {question.broad && <Note tone="warn">{question.broad}</Note>}
            <p className="text-xs text-ink-muted">{question.commits}</p>
            {adding.refused && <Refusal>{adding.refused}</Refusal>}
            {adding.byHand && (
              <div className="grid gap-2">
                <Note tone="warn">{adding.byHand}</Note>
                <Paste text={neverStoreLine(adding.value)} label="the line that adds it" onCopy={onCopy} />
              </div>
            )}
            <AlertDialogFooter>
              {/* The way back takes the focus as the question opens: the add drops what it names, for good. */}
              <AlertDialogCancel>{adding.byHand ? "Back to Settings" : "Back, adding nothing"}</AlertDialogCancel>
              {!adding.byHand && (
                <Button
                  variant="default"
                  aria-disabled={busy || undefined}
                  aria-busy={busy || undefined}
                  onClick={onAdd}
                >
                  Add it to {adding.name}
                </Button>
              )}
            </AlertDialogFooter>
          </>
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}

function PrivateDialog({
  privating,
  busy,
  onConfirm,
  onClose,
  opener,
}: {
  privating: Privating | null;
  busy: boolean;
  onConfirm: (question: PrivateQuestion) => void;
  onClose: () => void;
  opener: React.RefObject<Element | null>;
}) {
  const question = privating?.question;
  return (
    <AlertDialog open={privating !== null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent data-question="private" onCloseAutoFocus={(event) => returnFocus(event, opener.current)}>
        {privating && question && (
          <>
            <AlertDialogHeader>
              <AlertDialogTitle>Make {question.source} private?</AlertDialogTitle>
              <AlertDialogDescription>
                Nothing from it is stored after this, and what waits from it in staging goes.
              </AlertDialogDescription>
            </AlertDialogHeader>
            {privating.refused && <Refusal>{privating.refused}</Refusal>}
            <ul className="grid list-disc gap-1 pl-5 marker:text-ink-muted">
              {question.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <AlertDialogFooter>
              <AlertDialogCancel>Back, changing nothing</AlertDialogCancel>
              <Button
                variant="default"
                aria-disabled={busy || undefined}
                aria-busy={busy || undefined}
                onClick={() => onConfirm(question)}
              >
                Make {question.source} private
              </Button>
            </AlertDialogFooter>
          </>
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
