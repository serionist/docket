---
title: Add an API provider for embeddings and reranks, opt-in in vault.yaml
issue: "#89"
status: draft
approved_by:
approved_at:
---

# Add an API provider for embeddings and reranks, opt-in in vault.yaml

## Goal

A machine without a GPU, or without time for CPU embedding, gets search by meaning and reranking from Voyage AI. It
sends a vault's text off the machine only when three things hold at once: the vault allows the provider in
`vault.yaml`, this machine chose it, and its key is in this machine's keychain. Each chunk goes only if it passes the
vault's settings in force at the moment it is sent.

| The issue's part | This plan |
|---|---|
| Opt-in | `api_provider: voyage` in `vault.yaml`, absent by default, a one-way door. It is a key added later to #134's settings in force, so it needs no special case: turning it on or off asks at the owner's console and applies once committed, and no page sets it |
| Key | in the system keychain through keyring, set at the owner's console; no file fallback, never in the vault or the user config |
| Gate | only chunks from the index, which never reads `staging/`; each screened again with the settings in force before it leaves: its sources still there and not private, the never-store lists, redaction; the query too |
| Provider | an in-process runner behind #67's `Runner`, chosen per machine as `provider: api` in the models block |
| Docs | the README's "What leaves this machine" section |

- Also in scope: the rerank contract (budget, pause, fallback), costs and a monthly token cap, rate limits, the fake
  provider for tests, and the decisions DR-0536 to DR-0539.
- Out of scope: a second provider, Voyage's batch API (asynchronous, 33% cheaper), and an EU-only endpoint (Voyage's
  docs name no region).
- Depends on #134 (the settings in force, `docket/console.py`, the question in `rewrite`). This is built after #134
  merges.

## Design

### The provider: Voyage AI

Three hosted APIs offer both multilingual embeddings and a reranker behind a plain key. OpenAI and Mistral have no
reranker, and Anthropic has no embeddings API and names Voyage instead.

| | Voyage AI | Cohere | Jina AI |
|---|---|---|---|
| Embeddings | voyage-4: multilingual, 32k tokens, dimensions 256 to 2048 | embed-multilingual-v3.0 lists Hungarian; v4 and v5 don't say | jina-embeddings-v3, 89 languages; v4 is non-commercial |
| Reranker | rerank-2.5 and -lite, multilingual | rerank-v3.5, multilingual | jina-reranker-v2-multilingual and later |
| Price | $0.06 per M tokens (voyage-4), $0.02 (rerank-2.5-lite); 200M free tokens per model | not in the official pages read | not in the official pages read |
| Limits, first paid tier | 2,000 RPM; 8M TPM (voyage-4), 4M (rerank-2.5-lite) | trial: 1,000 calls a month, 10 reranks a minute, not for production | 500 RPM, 2M TPM |
| Inputs and training | opt-out of training with "zero-day retention", for any organization with a payment method | logged 30 days; zero retention for approved enterprise customers only | not used to train; now under Elastic's terms |

Voyage, because:

- both models behind one bearer key;
- a published price and a free allowance that covers a vault's first embedding;
- zero-day retention available to any paying user;
- Anthropic's own docs name it.

Its gaps go in Risks: no language list names Hungarian, and the docs read name no processing region.

| Model | Use | Why |
|---|---|---|
| `voyage-4`, `output_dimension: 512`, `input_type` query or document | the embedder | 512 dimensions keep the vector table and the scan near today's 384 (DR-0337): 2 KB a chunk against 4 KB at 1024 |
| `rerank-2.5-lite`, 50 pairs | the reranker | the faster of the two, for DR-0454's 0.75 s; 50 pairs as on a GPU, since a pair costs nothing locally |

### Three locks, all needed

| Lock | Where | Set by | Off |
|---|---|---|---|
| the vault allows it | `api_provider: voyage` in `vault.yaml`, as in force (#134) | `docket models allow voyage` at the owner's console, or the owner's own commit of a hand edit | `docket models allow none`, the same way |
| this machine chose it | `provider: api` in the user config's models block (DR-0335) | `docket models install --api`, at the owner's console | `docket models remove`, at once |
| the key | the keychain, entry `docket` / `voyage-api-key` | `docket models key voyage`, a hidden prompt at the console | `docket models key voyage --forget`, at once |

- Every send checks all three. Any one missing makes the models `Off` with why: for example, "this vault doesn't
  allow sending its text to voyage; `docket models allow voyage`, at your console". Search goes on by keyword.
- Removing the machine's choice or the key stops sending at once, with no question, since neither touches the gate.
  The vault's key follows the gate's rule for a key added later.
- Each lock is turned on only at the owner's console, through #134's `console.problem()`, refused under `CLAUDECODE`
  and from a pipe: `models allow` through `rewrite`, `models install --api` and `models key` in themselves. So Claude
  turns none of them on, and an agent's own `git commit` of a hand edit, the gap #134 accepts, can put
  `api_provider` in force but still finds no machine choice and no key.
- In a vault used from several machines, each machine's choice and key are that machine's instant offs; the vault's
  key, once committed, stops them all.

### The vault.yaml key, under loosens() as a key added later

- `VaultConfig.api_provider: Literal["voyage"] | None = None`. `dump()` writes it only when set, as `SourceConfig`'s
  serializer drops `folder`; not with `exclude_none`, which would drop `mapper: null` too. So every vault that never
  opts in keeps its `vault.yaml` byte for byte, stays in Docket's form (`written_by_docket`), and opens on a machine
  not upgraded yet.
- A vault that opted in carries the key, and an older Docket refuses it as an unknown key (DR-0021). That fails safe:
  an older machine never sends what it doesn't know it may.
- No line in `loosens()` or `effective()`. #134's `effective(committed, file)` starts from HEAD's settings and puts in
  only the fields its table names, so `api_provider` stays HEAD's until committed, whichever way the file changed.
  `loosens(before, after)` is `effective(before, after) != after`, so any change of it loosens.
- So, with no new code in the gate:
  - `docket models allow` writes through `rewrite(via="cli")`, which asks at the console under #134 and shows
    `api_provider: none -> voyage`;
  - an uncommitted edit adding it never applies until committed;
  - a page can't set it, since DR-0506's list doesn't name it and `FOR_PAGES` has no writer for it.
- Turning it off asks too. A row in `effective()` could make "off" apply at once, as a source's `enabled` does, but it
  isn't needed: the machine's choice and the key are instant offs.

### The key

- keyring's system backend only: a read and write-back check, as the service token's store does (`service/token.py`),
  with no file fallback. A machine without a keychain (Linux without Secret Service) can't use the provider, and
  `docket models status` says so.
- `docket models key voyage` reads the key with `getpass` only at a console, through #134's `console.problem()`:
  refused under `CLAUDECODE` and from a pipe, so a key never passes through Claude's transcript or a shell history.
- The keychain keeps the key out of transcripts, files and the vault. It doesn't hide it from a process running as
  the user, which can ask the keychain as Docket does; the README says so.
- The key is read when the runner starts and again after a 401. It is never logged, never in an error, never in
  `models status`.

### The runner

`models/api.py` holds `VoyageRunner`, which implements `provider.Runner` in the process that uses it (the service, or
a command). There is no worker process, since DR-0330's reason, ONNX's environment, doesn't apply. `Models` around it
is #67's, unchanged: the budget, the pause and the counts.

| Part | What |
|---|---|
| `send(url, body, timeout)` | the one network path: `urllib.request` over HTTPS to `https://api.voyageai.com/v1/`, `Authorization: Bearer`, the OS trust store; proxies from the environment honoured, unlike the loopback client. Its opener has no redirect handler, so a 3xx is an error naming its status; a `Skip` reason or a log line carries the status and fixed words, never a response body or the request |
| `query(text)` | `/embeddings`, `input_type: query`, 2 s as the worker's `QUERY_SECONDS`; past it, None, and search skips its vector stage |
| `batch(texts)` | `/embeddings`, `input_type: document`, up to 128 inputs or 200,000 characters a request, inside Voyage's 1,000 texts and 320k tokens; 60 s |
| `rerank(query, texts, deadline)` | `/rerank` with the HTTP timeout at the deadline; past it, `Skip(budget=True)`; any error, `Skip(why)` |
| the catalogue | `ApiEmbedder` and `ApiReranker`, beside `Embedder` and `Reranker`, with no files; the table is `chunk_vec_voyage_4_d512` (DR-0336), so a local model's vectors and Voyage's never mix |
| `open_models(vault)` | takes the vault, since the vault's lock is per vault; the service's `ModelsKeeper` already holds a vault factory |

### What is sent, and the screen before it leaves

| What | When | Screened |
|---|---|---|
| a chunk's input (title and text) | once, by the embedding pass after an index update, and again only when its text changes | by the pass, before the request |
| the query | on each search with models on, for its vector | in the search, before the request |
| the query and its top 50 candidates' text | on each search's rerank | the query, as above; candidates only if the last screen passed them |

The screen is the vault's settings in force (#134), at the moment of sending:

- **Chunks come only from the index.** The index reads `raw/`, `processed/` and `entities/`, never `staging/` (DR-0115
  and `index/build.py`), so nothing staged is ever a chunk.
- **Sources in force.** A chunk is held back unless every record it comes from belongs to a source that is in the
  settings in force and isn't `private`. Raw is written once, so a source the owner made private after a bad recording
  slipped in as work still has that recording in the index. A removed source's records are held back too, since their
  sensitivity is unknown now. A source that is there but off is a pause, not a judgement, so its records may go. A
  doc's chunk is held to the sources of the records its doc covers.
- **Never-store, on the records.** A chunk is held back when any record it comes from matches the never-store lists as
  intake reads a record (`neverstore.match`), or its text says a listed keyword. A doc's chunk is checked on its text
  and the records its doc covers. The records are read through the raw lookup by place (#76).
- **Redaction.** A chunk is held back when the redactor of the settings in force would change its input, its title
  alone or its text alone, as intake redacts each field on its own: an anchored pattern (`^ACCT-\d+`) meets a text
  that starts with it, but not the joined input, where the title comes first. A rule turned on after the text was
  stored keeps it here rather than sending it unredacted. The input itself is never rewritten, so its hash and its
  vector stay stable (DR-0336).
- **The query.** It is held back when it holds a value from any never-store list, or the redactor would change it:
  - `listed()` on the whole query, for the people and keywords lists, and on each word and each pair of neighbouring
    words, for the people list;
  - `in_container()` on each word, for the containers list.

  Search then has no vector stage and no rerank through the API, and says why, never naming what matched.
- **The fingerprint.** The sha256 of the never-store lists, the redaction settings, and each source's presence,
  sensitivity and `enabled` in force. The vector table's meta row keeps the one its vectors were screened under.
- **Rerank candidates.** Fifty record reads don't fit in 0.75 s, so the pass's screen stands in for them:
  - a chunk gets an API vector only when the pass passed it;
  - a rerank sends only candidates with a vector, and only while the table's fingerprint is the current one; otherwise
    the whole rerank is skipped for that search, with the reason, as a budget overrun is;
  - candidates not sent (no vector yet, such as a record ingested a minute ago, or held back) keep their fused
    positions, and the sent ones are reranked among the positions they held, in DR-0452's two tiers, so a new record
    never sinks for want of a vector.
- **The pass, request by request.** Before each request the pass reads the fingerprint of the settings in force. When
  it has moved since the batch was screened, the pass screens the batch again before sending, and starts the full screen
  below.
- **When the settings change.** The fingerprint moves. The next pass screens every chunk again without sending
  anything: it drops the vector of each chunk now held back, then stores the new fingerprint.
- **Held-back chunks** are skipped for the rest of a pass, so it never loops on them. They are counted in
  `docket models status` ("held back by the vault's settings"), and the next pass after a settings change tries them
  again.

### The two-tier contract, as the local reranker has it

| Tier | What |
|---|---|
| on time | DR-0454's 0.75 s from when the search asks; the HTTP call's timeout is the deadline; DR-0452's and DR-0453's order of the top 50 |
| past it, or failing | the fused order stands and the result says why (DR-0459): past the budget is counted, three in a row pause the reranker for five minutes (`provider.Models`, unchanged); a 429, a 5xx, a network error or a refused key is a `Skip` with its reason, never retried inside a search |
| the query's vector | 2 s, then the vector stage is skipped with its reason, as with the worker |

### Costs and rate limits

| Use | Tokens | Cost at list price |
|---|---|---|
| a vault's first embedding: 100,000 chunks of about 1,500 characters | about 37M | about $2.25, inside the 200M free tokens |
| a search's rerank: the query paired with 50 candidates | about 25k | about $0.0005 |
| a query's vector | under 50 | nothing worth counting |

- **Usage.** Each answer's `usage.total_tokens` is added to this machine's count for the month, in the state folder
  and never in the vault. `docket models status` shows it, with the list price as an estimate.
- **The monthly cap.** `api_monthly_tokens` in the models block, 50M by default (about $3). Past it every send stops
  until the month turns or the cap is raised, and status and search say so. It guards against a loop that embeds again
  and again.
- **The pass's back-off.** A 429 or 5xx backs off 1, 2, 4 and so on up to 60 s, and gives up for that pass after 6
  tries. Voyage's docs name no `Retry-After`.
- **Searches never retry.**
- **A refused key.** A 401 or 403 stops every send, with "the API key was refused", until the key is set again.

### Tests fake the provider

- `FakeVoyage` in `tests/models_kit.py` stands in for `send`:
  - vectors are a deterministic hash of each input's words, so search by meaning works on the fixture;
  - rerank scores are word overlap;
  - it records every request body;
  - it can answer 429, 500, 401, or late past a deadline.
- An autouse fixture in `conftest.py` makes the real `send` raise, so a test that forgets the fake fails rather than
  reaching the network. No test has a real key or makes a real call.
- `MemoryKeyring`, a `keyring.backend.KeyringBackend` in memory, is set for the tests that need a key and put back to
  the failing backend after. The real keychain is never touched.
- A test per system checks that the trust store `send` uses holds certificates (`ssl` context `cert_store_stats`),
  with no request.

### Decided

Recorded as `proposed` for the maintainer's review.

| Decided | Not taken | Entry |
|---|---|---|
| Voyage AI: voyage-4 at 512 dimensions and rerank-2.5-lite, through an in-process `Runner` over HTTPS; `provider: api` in the models block; restates DR-0335 with api allowed and supersedes DR-0340 in step 1 | Cohere: zero retention only for approved enterprise customers, 30 days of logs; Jina: under Elastic's terms now, prices unpublished; Vertex: cloud IAM, not a key; a worker process: nothing to isolate | DR-0536 |
| `api_provider: voyage` in `vault.yaml`, written only when set, a one-way door; no line in `loosens()` or `effective()`: a key added later | a list of providers: one at a time is enough; a row in `effective()` for an instant off: the machine's choice and the key are instant offs; in the user config: the vault's text is the vault's to give (DR-0335) | DR-0537 |
| the screen: chunks from the index only, held back unless every record's source is still there and not private, on the records' never-store match, and on a change the redactor would make to the input, the title or the text, under the settings in force; the query held to every never-store list; reranks send only chunks passed under the current fingerprint, the rest keeping their places | redacting at send: the input and its hash would depend on the settings, and the vector with them; trusting what was stored: a value added to a never-store list later would still be sent | DR-0538 |
| the key in the keychain only, set at the console; usage counted per machine, a monthly token cap of 50M; the pass backs off, searches never retry | a file fallback, as the service token has: the key in a file that tools and transcripts reach by its path, where the keychain keeps it out of those, though not from a process running as the user; no cap: a loop could spend without a stop | DR-0539 |

## Doors and data

- **One-way door:** `vault.yaml` gains `api_provider` (DR-0021, DR-0537).
  - The PR names it in its title, AGENTS.md's door list gains it, and its decision is recorded as proposed for the
    maintainer's review.
  - A vault that never opts in is unchanged, byte for byte.
- **The user config's models block** gains `provider: api` and `api_monthly_tokens`. Not a door, as DR-0335 has it.
- **The index** gains `chunk_vec_voyage_4_d512` when the provider is chosen, and its meta row gains the fingerprint.
  No schema version: a table appears as a local model's does (DR-0336), and two rebuilds still match without models.
- **What leaves the machine:** the README's section says what goes, when, and to whom. It also points to Voyage's
  dashboard setting for zero-day retention.

## Steps

1. The catalogue and the choice:
   - `ApiEmbedder`, `ApiReranker` and the Voyage entries;
   - `provider: api` and `api_monthly_tokens` in `models/config.py`;
   - DR-0536 supersedes DR-0335 and DR-0340, and their citations move with it.

   Tests: the models block reads api, and refuses an unknown provider, as today.
2. The vault key: `VaultConfig.api_provider`, written only when set, and `docket models allow`. Tests:
   - a vault without it dumps byte for byte as today, a source with no mapper among its sources;
   - `allow voyage` asks at the console, and is refused under `CLAUDECODE` and from a pipe;
   - an uncommitted edit adding it is not in force;
   - no page writer sets it.
3. The key: keychain only, `docket models key`. Tests: `MemoryKeyring` round trip; refused with no keychain; refused
   under `CLAUDECODE` and from a pipe; the key in no log, error or status.
4. The runner, `models/api.py`: `send`, `query`, `batch`, `rerank`, the cap, the usage and the back-off. Tests: all
   against `FakeVoyage`, with the real `send` raising.
5. The screen: the pass's records check and redaction check, held-back chunks, the fingerprint, the query screen, and
   the rerank's fingerprint rule. Tests: the staging and never-store test below.
6. `open_models(vault)` and the three locks, in the service's keeper, `docket search`, `docket eval` and
   `docket index update`; `docket models install --api` asks at the console. Tests: each missing lock is `Off` with
   its reason; `install --api` and `key` are refused under `CLAUDECODE` and from a pipe.
7. The docs below, then `finish-work`: `uv run gate full`, a draft PR, `review-code` until clean.

## Tests

On temp vaults from the testkit, with temp config and state dirs, the failing keyring unless `MemoryKeyring` is set,
and `DOCKET_VAULT` unset; invented records only, and no network.

| Test | Asserts |
|---|---|
| `test_models_api.py`: `test_nothing_staged_or_never_store_reaches_the_provider` | a record staged and not promoted holds `zebrastaged`; a record in raw holds `zebrakept`, then `zebrakept` joins `never_store.keywords` and is committed; a record from `U0NEVER` is in raw, then `U0NEVER` joins the people list; a record holds a value a pattern turned on later would redact. After an index update, a full pass, and searches with rerank for `pallet`, `zebrakept` and `zebrastaged`: no request `FakeVoyage` saw holds any of those texts or that record; the `zebrakept` query sent nothing; `models status` counts three held back |
| `test_models_api.py`: `test_a_source_made_private_or_removed_keeps_its_records_here` | a record in raw whose source is then made `private`, and one whose source is then removed, each committed: nothing of either reaches `FakeVoyage`, in the pass or in a rerank; a source turned off but kept lets its records go; each change moves the fingerprint |
| `test_models_api.py`: `test_a_query_holding_a_never_store_value_stays_here` | queries naming a listed person (both word orders), a listed container and a listed keyword send nothing, and the search says why without the value |
| `test_models_api.py`: `test_an_anchored_pattern_holds_back_a_chunk_by_its_text_alone` | `^ACCT-\d+` turned on after a chunk whose text starts `ACCT-1234` under the title `Budget call` was indexed: the chunk is held back, though the joined input doesn't match |
| `test_models_api.py`: `test_candidates_not_sent_keep_their_places` | a chunk ingested after the last pass, in the top 50 with no vector: it keeps its fused position, and only the sent candidates move among their own positions |
| `test_models_api.py`: `test_the_pass_screens_a_batch_again_when_the_settings_move` | a keyword added between two requests of one pass: the second request carries none of the chunks it holds back |
| `test_models_api.py`: `test_a_redirect_or_an_error_says_only_its_status` | a 302 is an error, not followed; a 500's body and the request's text appear in no `Skip`, log line or `models status` |
| `test_models_api.py`: `test_a_changed_never_store_list_screens_again_before_any_rerank` | after a value is added, reranks are skipped with the reason until the pass screens again; the held chunk's vector is gone; the next rerank sends only passing chunks |
| `test_models_api.py`: `test_each_lock_missing_turns_the_provider_off_with_why` | no vault key, no machine choice, no key: each `Off`, with its own reason, and no request |
| `test_models_api.py`: `test_a_slow_or_failing_rerank_keeps_the_fused_order` | late past the deadline: counted, three pause it; 429, 500, 401: `Skip` with the reason, no retry inside a search |
| `test_models_api.py`: `test_the_pass_backs_off_and_stops_at_the_cap` | 429 then success; six 500s end the pass; usage past `api_monthly_tokens` stops sending, and status says so |
| `test_models_api.py`: `test_the_trust_store_holds_certificates` | `send`'s SSL context has CA certificates, on each system |
| `test_vault_settings.py`: `test_api_provider_is_written_only_when_set` | the fixture's `vault.yaml`, a source with no mapper among its sources (so `mapper: null` stays), dumps byte for byte without it; with it, it reads back; a provider it doesn't know is refused |
| `test_cli_models.py`: `test_allowing_the_provider_asks_at_the_console` | the question shows `api_provider: none -> voyage`; refused under `CLAUDECODE=1` and a pipe; the owner fixture's code commits it; `allow none` asks too |
| #134's `effective()` tests, one case more | `effective()` keeps HEAD's `api_provider` against a file that adds or drops it |
| `test_models_key.py` | the keychain round trip through `MemoryKeyring`, refused with the failing backend and at no console, the key in no output; `docket models install --api` refused under `CLAUDECODE=1` and from a pipe |

- The test in the first row fails without the screen. A variant run on a patch that sends straight from the index
  shows `zebrakept` in a request: its red run.
- Coverage stays at the `docket` floor of 100%.

## Docs

- DR-0536 to DR-0539 in `packages/docket/index.d/`, added with this plan; DR-0335 and DR-0340 superseded in step 1.
- The README:
  - a "What leaves this machine" section: the three locks, the table of what is sent and when, the screen, Voyage's
    zero-day retention setting, costs and the cap;
  - the models commands: `allow`, `key`, `install --api`, each at the owner's console;
  - that the keychain keeps the key out of transcripts and files, not from a process running as the user;
  - that on a vault used from several machines, each machine's choice and key are its own instant offs.
- `docs/design.md`: the models paragraph names the API provider and the screen.
- AGENTS.md's one-way doors gain `api_provider` (DR-0537).
- The setup skill offers the provider only as the owner's own commands, run at their console.
- `CHANGELOG.md`, Added: the API provider, opt-in per vault, and what leaves the machine.

## Risks

| Risk | Status |
|---|---|
| Hungarian isn't named in Voyage's docs; search quality there is unmeasured, and CI makes no real calls | open: before the maintainer turns it on, `docket eval` with his key on the question set's Hungarian kinds, against the local floors; Cohere's embed-multilingual-v3.0 lists Hungarian and is the fallback choice |
| Voyage's facts come from docs read on 17 Oct: prices, limits, the 200M free tokens, the training opt-out; no region, retention default or SOC 2 statement was found | open: checked again when built; the README sends the owner to Voyage's dashboard for zero-day retention |
| 0.75 s may be short for a rerank over the network | accepted: past it the fused order stands and the pause follows (#67's contract); `docket eval` reranks with no budget |
| uv's standalone Python on macOS may have no CA bundle of its own | open: the trust-store test runs on each system; `truststore` (MIT) is the fix if it fails, a dependency the PR names |
| A machine with no payment method on Voyage gets far lower limits than Tier 1 | accepted: the pass backs off and resumes; the README says the free tokens need a payment method on file |
| 512 dimensions lose some quality against 1024 | accepted: keeps the index and the scan near today's; the same eval run measures it |
| Turning the vault key off by a hand edit waits for the commit | accepted: the machine's choice and the key are instant offs |
| #134's accepted gap, an agent's own `git commit` of a hand edit, can put `api_provider` in force | accepted: sending still needs this machine's choice and key, each set only at the owner's console (V7) |
| A source a chunk can't be traced to (a doc covering no record, a legacy note) is screened on its text alone | accepted: such a doc was screened at its put, and its text by the never-store keywords and the redactor here |
| The record read in the screen slows the pass on a vault of 100,000 chunks | open: measured in the build against the pass on the perf vault; the reads come by place (#76) |
| Voyage is part of MongoDB now, and its endpoint may move (`ai.mongodb.com`) | accepted: the base URL is one constant |
| #134 hasn't merged | open: this is built after it; if its `effective()` changes shape, the key still falls under "a key added later" |

## Plan review

Round 1, 17 Oct 2026, a read-only review for the lead at 9598eb8: changes needed, one major and six minors. All seven
are folded in above; the reviewer checks V1 again.

| Finding | What | Fixed |
|---|---|---|
| V1, major | the screen ignored the sources in force: a source made private keeps its raw records in the index, and they were sent, and a sensitivity change didn't move the fingerprint | a chunk goes only when every record's source is in force and not private; a removed source's records stay; an off source's may go; each source's presence, sensitivity and `enabled` are in the fingerprint; a test for a source made private and one removed |
| V2, minor | the query was held to keywords only | held to every never-store list through `listed()` and `in_container()`, with a test |
| V3, minor | an anchored pattern misses a text that starts with it once the title is joined before it | the title alone and the text alone go through the redactor too, with a test |
| V4, minor | a redirect could be followed, and a reason could carry a body | no redirect handler; reasons and logs carry the status and fixed words only, with a test |
| V5, minor | a candidate with no vector yet would sink | unsent candidates keep their fused positions; the sent ones move among their own, with a test |
| V6, minor | the pass could send a batch screened before the settings moved | the fingerprint is read before each request, and the batch screened again, with a test |
| V7, minor | the machine's choice and the key weren't held to the console | `install --api` and `key` ask at the console; the Risks name #134's gap; the README and DR-0539 say what the keychain does and doesn't hide |

Also from the review's notes: "written only when set" follows `folder`'s serializer, not `exclude_none`; the
byte-for-byte fixture has a source with no mapper; the README names the instant offs per machine.
