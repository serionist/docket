# Design reference: what a judge cites

Where the judge's ground truth lives. Each line points at its source; the source wins when they differ, and a fact
that is in neither is not a rule. Look a decision up with `uv run docs show <id>`.

## The text of record

- `docs/plans/2026-10-20-252-react-app.md`: the stack, the shell and the tokens, the API, the day pages' bar, the
  screenshot loop, and each page's "Built" notes and Review sections: the kept behaviours and the traps already paid.
- `docs/design.md`, its web app section: what each page is for, a line each.
- The page's decisions (`uv run docs list decisions --path packages/docket/web`), among them DR-0703 to DR-0715 for the
  app as a whole and the page's own since.
- What Claude links to: the plugin's skills (`plugin/skills/tasks` opens `/queue`, `plugin/skills/decision` links
  `/briefs?brief=brief:<subject>`) and the MCP server's instructions; those links open the same state byte for byte.
- `docs/design.md`'s architecture: the app reads and writes through `/api/web` only, so a judge knows what a page can
  and cannot know.

## The decided floor

- The stack and the look: shadcn/ui on Radix and Tailwind CSS 4 (DR-0703, DR-0713), the tokens in
  `src/styles/tokens.css` as light-dark() pairs in Atkinson Hyperlegible Next and Mono, 32 px rows, 44 on a coarse
  pointer (DR-0707), the shell's sidebar, list and 480 px panel folding at 1024 and 768 (DR-0708). A hand-made
  lookalike of a shadcn component is a major finding; a colour that is not a token is a finding.
- `build-ui`'s bar: the states, writes, typed text, busy, focus, keys, widths, the CSP, what a read sends, and what no
  page does.
- Light and dark both: a dark-only breakage means a colour escaped the tokens.
- `/design` shows every shared part on invented data: a page that differs from it without a reason is a finding.

## Behaviour every page owes

The passing conditions of each lens in `reality-lenses.md`: the empty flavours, the validation display contract, honest
loading, a failure in the writer's words with a way to ask again, a write's notice with Undo, the poll that holds while
the owner types, a busy control that stays focusable, focus in dialogs, the keys and the `?` list, Enter and Esc.

## Professional quality: the aim above the floor

The floor keeps the pages consistent; it does not make them designed. Under `redesign: improve` or `overhaul`, judge
every non-trivial page against these and propose the concrete better design, with its cost to the owner:

- **Space is composed.** The list uses the width with intent beside the panel; nothing stranded in a narrow column
  beside empty space.
- **Hierarchy shows at a glance.** One clear anchor; weight, size and ink separate title, group, row and meta; groups
  have a head and a rhythm.
- **Surfaces are intentional.** Rules and panels earn their borders; related actions sit together; the stamps are the
  one bold thing.
- **Chrome is legible.** Every icon-only control has a label; an unexplained count or mark is a recall burden.
- **A start page has a purpose.** Today answers "what is late, what is next, did the syncs run" at a glance.
- **The owner's context.** One person at a desk or on a phone between meetings, working fast with the keys: the next
  call to make reads at a glance, and the destructive act is never the easy one by accident.
