/**
 * The Settings page's parts (DR-0510, DR-0511, DR-0601, DR-0789): the sources with their changes, stricter first and
 * a looser one marked; the never-store lists, their values only when the URL asks; the redaction rules, each turned
 * the other way by its button or, for credentials off, by the command the owner runs at their own console; and this
 * machine's models, with the commands that change them. Every word and line to paste is the server's.
 */
import { ChevronRightIcon } from "lucide-react";
import type * as React from "react";
import { AppLink } from "@/components/app-link";
import { Note } from "@/components/doc";
import { Paste, Tab, type Tone } from "@/components/paste";
import { Refusal } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { ModelsRead, NeverListRead, RuleRead, SettingsPage, SourceRead } from "./api";

/** What a write said beside the thing it was on: a refusal, or a change left to the owner's own edit. */
export interface Said {
  where: string;
  words: string;
  tone: "refused" | "warn";
}

export interface Acts {
  // The row a write is in flight on, by its id: only its controls wait, keeping their place and their focus.
  busy: string | null;
  said: Said | null;
  onCopy: (text: string) => void;
}

/** A control of the row `where` while a write is in flight on it: still focusable, said to be busy; the other rows'
 * stay as they are. */
function waiting(acts: Acts, where: string) {
  const here = acts.busy === where || undefined;
  return { "aria-disabled": here, "aria-busy": here };
}

function SaidHere({ said, where }: { said: Said | null; where: string }) {
  if (!said || said.where !== where) return null;
  return said.tone === "refused" ? <Refusal>{said.words}</Refusal> : <Note tone="warn">{said.words}</Note>;
}

/** A part of the page: its heading over a rule, and what it holds. */
export function Part({
  id,
  title,
  aside,
  children,
}: {
  id: string;
  title: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="border-t border-rule pt-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 pb-2 sm:px-4">
        <h2 id={id} className="text-base font-semibold">
          {title}
        </h2>
        {aside && <div className="ml-auto">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

/** A fold the owner opens: a change's buttons, an entry to change by hand, the commands. */
function Fold({ summary, open, children }: { summary: string; open?: boolean; children: React.ReactNode }) {
  return (
    <details className="group/fold" open={open}>
      <summary className="tap inline-flex min-h-8 cursor-default items-center gap-1 rounded-sm pr-2 text-sm text-ink-muted hover:text-ink">
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform group-open/fold:rotate-90" />
        {summary}
      </summary>
      <div className="grid gap-2 pt-1 pb-1 pl-4.5">{children}</div>
    </details>
  );
}

const SENSITIVITY_TONE: Record<string, Tone> = { work: "green", mixed: "me", private: "muted" };
const PATHS = new Set(["Mapper", "Folder", "Here", "Media folders"]);

function Facts({ facts }: { facts: { name: string; value: string }[] }) {
  return (
    <dl className="grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
      {facts.map(({ name, value }) => (
        <div key={name} className="grid min-w-0 gap-0.5">
          <dt className="text-xs text-ink-muted">{name}</dt>
          <dd
            dir="auto"
            className={cn("min-w-0 break-words", PATHS.has(name) ? "font-mono text-xs break-all" : "text-sm")}
          >
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Source({
  source,
  acts,
  onChange,
}: {
  source: SourceRead;
  acts: Acts;
  onChange: (source: string, act: string) => void;
}) {
  const id = `source-${source.name}`;
  return (
    <li aria-labelledby={`${id}-title`} id={id} className="grid gap-2 border-b border-rule px-3 py-3 sm:px-4">
      <h3 id={`${id}-title`} tabIndex={-1} className="flex flex-wrap items-center gap-x-2 gap-y-1 outline-none">
        <span className={cn("text-sm font-semibold", !source.enabled && "text-ink-muted")}>{source.name}</span>
        <Tab tone={SENSITIVITY_TONE[source.sensitivity] ?? "muted"}>{source.sensitivity}</Tab>
        {!source.enabled && <Tab tone="muted">off</Tab>}
        {source.folder && <Tab tone="muted">folder</Tab>}
      </h3>
      <Facts facts={source.facts} />
      {source.problem && <Note tone="warn">{source.problem}</Note>}
      <SaidHere said={acts.said} where={id} />
      <Fold summary={`Change ${source.name}`}>
        <ul className="grid gap-1.5">
          {source.changes.map((change) => (
            <li key={change.act} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <Button
                variant="outline"
                size="sm"
                {...waiting(acts, id)}
                onClick={() => onChange(source.name, change.act)}
                aria-label={`${change.what}${change.act === "private" ? "…" : ""}: ${source.name}`}
              >
                {change.what}
                {change.act === "private" && "…"}
              </Button>
              {change.looser && <Tab tone="me">lets more in</Tab>}
              <span className="min-w-0 flex-1 text-sm text-ink-muted">{change.effect}.</span>
            </li>
          ))}
        </ul>
        <p className="max-w-prose text-sm text-ink-muted">
          Its {source.folder ? "folder and exclude" : "tools, mapper and media folders"} change in its entry, by hand:
          that lets more in, or changes what the service runs or reads. Its entry as Docket writes it, to change under{" "}
          <code className="font-mono text-xs">sources</code>:
        </p>
        <pre className="rounded-sm border border-rule bg-paper px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap">
          {source.entry}
        </pre>
      </Fold>
    </li>
  );
}

export function Sources({
  page,
  acts,
  onChange,
}: {
  page: SettingsPage;
  acts: Acts;
  onChange: (source: string, act: string) => void;
}) {
  return (
    <Part id="sources-title" title="Sources">
      {page.folders_problem && (
        <div className="px-3 pb-2 sm:px-4">
          <Note tone="warn">{page.folders_problem}</Note>
        </div>
      )}
      {page.sources.length ? (
        <ul className="border-t border-rule">
          {page.sources.map((source) => (
            <Source key={source.name} source={source} acts={acts} onChange={onChange} />
          ))}
        </ul>
      ) : (
        <p className="px-3 pb-2 text-sm text-ink-muted sm:px-4">No source yet.</p>
      )}
      <p className="px-3 py-3 text-xs text-ink-muted sm:px-4">
        A new source, a folder source too, comes in with{" "}
        <code className="font-mono">docket source new &lt;name&gt;</code>.
      </p>
    </Part>
  );
}

function NeverList({
  list,
  shown,
  acts,
  typed,
  error,
  onType,
  onAdd,
  onTakeOff,
}: {
  list: NeverListRead;
  shown: boolean;
  acts: Acts;
  typed: string;
  error: string;
  onType: (value: string) => void;
  onAdd: () => void;
  onTakeOff: (value: string) => void;
}) {
  const id = `list-${list.name}`;
  const field = `add-${list.name}`;
  const errorId = `${field}-error`;
  return (
    <li id={id} aria-labelledby={`${id}-title`} className="grid gap-2 border-b border-rule px-3 py-3 sm:px-4">
      <h3 id={`${id}-title`} tabIndex={-1} className="flex flex-wrap items-center gap-x-2 outline-none">
        <span className="text-sm font-semibold">{list.name}</span>
        <span className="text-xs text-ink-muted tabular-nums">
          {list.count} {list.count === 1 ? "value" : "values"}
        </span>
      </h3>
      <p className="max-w-prose text-sm text-ink-muted">It {list.matches}.</p>
      {shown &&
        (list.values.length ? (
          <ul className="grid gap-1" aria-label={`The values on ${list.name}`}>
            {list.values.map((value) => (
              <li key={value.value} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span dir="auto" className="min-w-0 text-sm font-semibold break-words" data-value>
                  {value.value}
                </span>
                <code
                  dir="auto"
                  className="min-w-0 font-mono text-xs break-all text-ink-muted"
                  title="its line in vault.yaml"
                >
                  {value.line}
                </code>
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto"
                  {...waiting(acts, id)}
                  onClick={() => onTakeOff(value.value)}
                  aria-label={`Take it off: ${list.name}`}
                >
                  Take it off
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">Nothing on it.</p>
        ))}
      <SaidHere said={acts.said} where={id} />
      <Fold summary={`Add to ${list.name}`}>
        {/* The service says whether a value holds, on submit; the browser's own checks would say it first. */}
        <form
          noValidate
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (acts.busy === null) onAdd();
          }}
        >
          <div className="grid min-w-0 flex-1 basis-60 gap-1 text-sm">
            <label htmlFor={field}>{list.ask}</label>
            <Input
              id={field}
              value={typed}
              onChange={(event) => onType(event.target.value)}
              maxLength={200}
              autoComplete="off"
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              dir="auto"
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? errorId : undefined}
            />
            {error && (
              <p id={errorId} className="text-sm text-red">
                {error}
              </p>
            )}
          </div>
          <Button type="submit" variant="outline" {...waiting(acts, id)}>
            Add to {list.name}
          </Button>
        </form>
        <p className="text-xs text-ink-muted">Docket asks once more first, naming what it would drop.</p>
      </Fold>
    </li>
  );
}

export function NeverStore({
  page,
  acts,
  typed,
  errors,
  onType,
  onAdd,
  onTakeOff,
}: {
  page: SettingsPage;
  acts: Acts;
  typed: Record<string, string>;
  errors: Record<string, string>;
  onType: (name: string, value: string) => void;
  onAdd: (name: string) => void;
  onTakeOff: (name: string, value: string) => void;
}) {
  return (
    <Part
      id="lists-title"
      title="Never store"
      aside={
        <AppLink
          href={page.shown ? PATH_HIDE : PATH_SHOW}
          className="inline-flex min-h-8 items-center text-sm underline decoration-rule underline-offset-2 hover:decoration-ink pointer-coarse:min-h-11"
        >
          {page.shown ? "Hide the values" : "Show the values"}
        </AppLink>
      }
    >
      <ul className="border-t border-rule">
        {page.lists.map((list) => (
          <NeverList
            key={list.name}
            list={list}
            shown={page.shown}
            acts={acts}
            typed={typed[list.name] ?? ""}
            error={errors[list.name] ?? ""}
            onType={(value) => onType(list.name, value)}
            onAdd={() => onAdd(list.name)}
            onTakeOff={(value) => onTakeOff(list.name, value)}
          />
        ))}
      </ul>
      <p className="max-w-prose px-3 py-3 text-xs text-ink-muted sm:px-4">
        A value is committed in vault.yaml as you write it: it is in your repo's history, and goes wherever the vault is
        pushed. Taking one off lets in what it kept out, from the next sync: show the values to take one off.
      </p>
    </Part>
  );
}

const PATH_SHOW = "/settings?show=lists";
const PATH_HIDE = "/settings";

function Rule({ rule, acts, onTurn }: { rule: RuleRead; acts: Acts; onTurn: (rule: RuleRead) => void }) {
  const id = `rule-${rule.name}`;
  const turn = rule.on ? "Turn it off" : "Turn it on";
  return (
    <li id={id} aria-labelledby={`${id}-title`} className="grid gap-2 border-b border-rule px-3 py-3 sm:px-4">
      <h3 id={`${id}-title`} tabIndex={-1} className="flex flex-wrap items-center gap-x-2 gap-y-1 outline-none">
        <span className={cn("text-sm font-semibold", !rule.on && "text-ink-muted")} dir="auto">
          {rule.name}
        </span>
        <Tab tone={rule.on ? "green" : "muted"}>{rule.on ? "on" : "off"}</Tab>
        {rule.pattern && <Tab tone="muted">your pattern</Tab>}
      </h3>
      <p className="text-sm text-ink-muted">
        {rule.pattern ? (
          <code dir="ltr" className="font-mono text-xs break-all text-ink">
            {rule.what}
          </code>
        ) : (
          `Redacts ${rule.what}.`
        )}
      </p>
      <SaidHere said={acts.said} where={id} />
      {rule.button ? (
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Button
            variant="outline"
            size="sm"
            {...waiting(acts, id)}
            onClick={() => onTurn(rule)}
            aria-label={`${turn}: ${rule.name}`}
          >
            {turn}
          </Button>
          {rule.looser && <Tab tone="me">lets more in</Tab>}
          <span className="min-w-0 flex-1 text-sm text-ink-muted">{rule.note}.</span>
        </p>
      ) : (
        <div className="grid gap-1.5">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="font-semibold">{turn}</span>
            {rule.looser && <Tab tone="me">lets more in</Tab>}
            <span className="text-ink-muted">{rule.note}.</span>
          </p>
          {rule.command && (
            <Paste
              text={rule.command}
              label={`the command that turns ${rule.name} ${rule.on ? "off" : "on"}`}
              onCopy={acts.onCopy}
            />
          )}
          {rule.on && !rule.pattern && rule.name === "credentials" && (
            <p className="text-xs text-ink-muted">
              Turning credentials off is yours to confirm: run it in your own terminal, where it shows the change and
              asks you.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function Redaction({
  page,
  acts,
  onTurn,
}: {
  page: SettingsPage;
  acts: Acts;
  onTurn: (rule: RuleRead) => void;
}) {
  return (
    <Part id="redaction-title" title="Redaction">
      {page.credentials_off && (
        <div className="px-3 pb-2 sm:px-4">
          <Note tone="warn">Credentials are not redacted: {page.credentials_off}.</Note>
        </div>
      )}
      <ul className="border-t border-rule">
        {page.rules.map((rule) => (
          <Rule key={`${rule.pattern ? "pattern" : "rule"}-${rule.name}`} rule={rule} acts={acts} onTurn={onTurn} />
        ))}
      </ul>
      <div className="grid gap-2 px-3 py-3 sm:px-4">
        <p className="max-w-prose text-xs text-ink-muted">{page.names}.</p>
        <Fold summary="A pattern of your own">
          <p className="max-w-prose text-sm text-ink-muted">
            Add, change or remove one by hand, under <code className="font-mono text-xs">redaction:</code>{" "}
            <code className="font-mono text-xs">patterns:</code> in vault.yaml. It is committed as written, so write the
            shape, never the secret itself. Such as:
          </p>
          <pre className="rounded-sm border border-rule bg-paper px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap">
            {page.pattern_example}
          </pre>
        </Fold>
      </div>
    </Part>
  );
}

export function Models({
  models,
  machine,
  onCopy,
}: {
  models: ModelsRead;
  machine: string;
  onCopy: (text: string) => void;
}) {
  return (
    <Part id="models-title" title={`Models on ${machine}`}>
      <div className="grid gap-3 border-t border-rule px-3 py-3 sm:px-4">
        <dl className="grid gap-x-4 gap-y-1.5 sm:grid-cols-[minmax(9rem,auto)_minmax(0,1fr)]">
          {models.rows.map(({ name, value }, at) => (
            // Two environments or two vectors' rows may share a name; their order is the server's.
            <div key={`${name}-${at}`} className="contents">
              <dt className="text-xs text-ink-muted sm:pt-0.5">{name}</dt>
              <dd className="min-w-0 text-sm break-words">{value}</dd>
            </div>
          ))}
        </dl>
        {models.errors.length > 0 && (
          <Fold summary="What the service's worker wrote last">
            <pre className="max-h-64 overflow-auto rounded-sm border border-rule bg-paper px-3 py-2 font-mono text-xs whitespace-pre-wrap">
              {models.errors.join("\n")}
            </pre>
          </Fold>
        )}
        <Fold summary="Install, remove or turn them off" open={!models.on}>
          <ul className="grid gap-2">
            {models.commands.map((command) => (
              <li key={command.text} className="grid gap-1">
                <p className="text-sm">{command.what}:</p>
                <Paste text={command.text} label={command.what} onCopy={onCopy} />
              </li>
            ))}
            <li className="grid gap-1">
              <p className="text-sm">
                Off on this machine, the files kept: this line under <code className="font-mono text-xs">models:</code>{" "}
                in{" "}
                <code dir="ltr" className="font-mono text-xs break-all">
                  {models.config_path}
                </code>
                , then restart the service.
              </p>
              <Paste text={models.off_line} label="the line that turns models off here" onCopy={onCopy} />
            </li>
          </ul>
        </Fold>
      </div>
    </Part>
  );
}
