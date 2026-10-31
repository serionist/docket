# Tech stack

Docket is a Python server with a React web app, a runner on each of your machines, and a Claude Code plugin. This page
says what each part is built with and why. How the parts talk is the next doc, architecture.

## The parts

| Part | Runs on | Built with |
|---|---|---|
| Server | Linux, in Docker; Mac and Windows for development | Python, FastAPI, SQLite |
| GPU worker | next to the server or on any machine with an NVIDIA GPU; on the CPU when there is none | Python, PyTorch, CTranslate2, ONNX Runtime |
| Web app | the browser, served by the server | React, shadcn/ui, Tailwind CSS |
| Runner | each Mac and Windows machine | Python, Playwright |
| Plugin | each Claude Code | skills, and MCP to the server over HTTPS |
| Connectors | the server or a runner, as each one's manifest allows | Python, and instructions for Claude |

## Server

| Choice | Why | Not chosen |
|---|---|---|
| Python, the newest version every ML library ships wheels for | the audio and search models are Python; one language for the server, the runner and connectors | a TypeScript server with a Python worker: two runtimes to ship |
| uv | one lock file, installs Python itself, fast | pip, Poetry |
| FastAPI and Pydantic | typed routes, and an OpenAPI file the web app's client is generated from | Django: more than an API needs |
| SQLAlchemy models, with migrations generated from them (Alembic) | code first: the models are the truth; Postgres stays possible | migrations written by hand |
| Jobs in a database table, worked by the server | a sync, a reconcile, a transcription survive a restart, with no extra service | Redis, a queue server |
| A WebSocket per browser tab, per runner and per GPU worker | the wizard and the pages update live; the server pushes work to runners and workers | v1's 5-second poll |
| MCP from the official Python SDK, over HTTPS | Claude reaches the same server the web app uses | a local MCP server on each machine |

## Data

| Choice | Why |
|---|---|
| One SQLite database for everything: accounts, vaults, people, records, tasks, what reconcile writes, the change log | only the server writes, so SQLite is fast enough; a person shared between vaults is one link row |
| A vault id on every table that belongs to a vault | the app holds several vaults and switches between them; export, import and delete a vault are app features |
| The change log, append-only | history and undo, with no git |
| Per vault: a search index file, and a folder for audio and documents, each file named by its hash | the index is big and rebuilt from the database, so backups skip it; large files stay out of the database |
| No git and no backup built in | backing up is the owner's business: copy the data folder |
| A SaaS later: one database per account | the split follows accounts, not vaults |

The wizard never holds a write open: its drafts are rows marked with its session. Finish makes them real in one short
transaction; Cancel deletes them.

## Search

| Choice | Why |
|---|---|
| SQLite full-text search for words | tuned in v1 for Hungarian word forms and typos, and held by quality floors |
| Vectors in SQLite (sqlite-vec), made by `multilingual-e5-small` | meaning, found locally, in the same file |
| A reranker: `bge-reranker-v2-m3` on the GPU, `mmarco-minilm` on the CPU | in v1 it lifted paraphrased questions from 0.44 to 0.61 found in the top 10 |
| Reconcile's tags filter first (project, people, topic, period); words, vectors and the reranker rank inside | the tags are the graph, so there is no second pass of Claude over every record, which full GraphRAG would need |

## Audio

| Choice | Why |
|---|---|
| Whisper large-v3 through faster-whisper, on the GPU | the best open transcription, fast on the home server's RTX 4060 |
| pyannote for who spoke when, and its speaker embeddings to match voices to people | it runs locally, so voice samples stay on your server |
| The likely attendees and the project's terms as Whisper's prompt, then Claude corrects with the meeting's context | names and terms come out right; which Sam it was comes from the calendar and the voice |

## GPU worker

The worker holds the audio and search models and nothing else: no database, no data folder. It connects to the server
like a runner, and every job carries what it needs.

| Job | The server sends | The worker sends back |
|---|---|---|
| Transcribe | a link to the audio, fetched over HTTPS; the likely attendees' names and the project's terms for Whisper's prompt | the segments, with times |
| Who spoke when | the audio link | the speaker turns, and a voice embedding per speaker |
| Embed | the texts | their vectors |
| Rerank | the question and up to 50 candidate passages | a score for each |

| Choice | Why |
|---|---|
| The server matches voice embeddings to people | the worker never knows who is who |
| Its only storage is its model cache | nothing to back up, nothing to leak |
| Paired like a runner, with a machine token | a second worker, or a laptop with a GPU, can connect too |
| With no worker connected, audio jobs wait and search answers by words alone | the server never depends on the GPU being up |

## Claude

| Choice | Why |
|---|---|
| The server runs Claude through the Agent SDK for Python | it ships Claude Code inside, so no Node; it does reconcile and the server's connector steps |
| A token list in priority order: subscription OAuth tokens and API keys | on a limit the next token takes over, and the limited one is skipped until its reset time, or for an hour |
| Each credential in a process of its own, with only that credential set | an API key left in the environment would win over a token |
| A SaaS takes API keys only | Anthropic's terms: products don't run on their users' subscriptions |
| On a runner, Claude-driven steps use that machine's own Claude login | no token leaves the server |
| In a Claude session, Claude does the steps itself | the same connector instructions, started from chat |

## Web app

| Choice | Why |
|---|---|
| React, Vite, TanStack Router and Query | v1's set; its pages turned out well |
| shadcn/ui on Radix, Tailwind CSS 4 | accessible parts, styled our way |
| A client typed from the server's OpenAPI file | a changed route breaks the build, not the page |
| Served by the server, on the same address | one address, one sign-in |

## Sign-in

| Who | How |
|---|---|
| You | a local account, or any OIDC provider: Google, Microsoft, your home server's |
| A runner | paired once: it shows a code, you approve it in the web app, it keeps a machine token |
| The plugin | paired the same way; its token goes in the MCP settings as a bearer header |

Browser sessions are cookies that page scripts can't read; machine tokens are stored hashed.

## Runner and connectors

| Choice | Why |
|---|---|
| The runner: a Python app installed by one command your Docket serves, `curl ... \| sh` on Mac and `irm ... \| iex` on Windows | it installs uv if missing, downloads the runner from your server, and shows its pairing code |
| A connector: a zip with a manifest, Python code and instructions for Claude, uploaded to the server or put in its connectors folder | community connectors later; runners download what they run |
| The manifest: where it may run, its settings as a JSON schema, what it reaches (hosts, folders, Claude, a browser), the contract version it was built for | the web app draws its settings form; installing it shows what it reaches |
| Each connector's code in its own environment, built by uv from the zip | one connector's packages never break another's |
| The zip's hash pinned at install; a runner refuses a changed one; an update asks again | a connector is code you trust, like a browser extension |
| Playwright for connectors that drive a browser, with a browser profile per connector | you sign in to Outlook once, and the session stays on that machine |

## Hosting

| Choice | Why |
|---|---|
| Two Docker images: the server with the web app, and the GPU worker | the server image stays small and changes often; the worker's CUDA image is several GB and changes rarely; a host without a GPU runs the worker on the CPU |
| The worker's models download into its own cache volume on first start | the image carries no model files |
| Built by CI and published to GitHub's container registry | the home server pulls a tested image |
| On the home server: behind Caddy, deployed by its deployer, the dev compose first as the test environment | its own doc, later |
| One data folder: the database, the index files, the file folders | mounted into the containers; your backups take the folder |

## Repo and tooling

| Choice | Why |
|---|---|
| One repo: `server/`, `runner/`, `contract/` (the shapes both sides share), `web/`, `plugin/`, `connectors/`, `docs/` | one change moves the contract and both sides together |
| ruff, pyright and pytest; Vitest and Playwright for the web app | v1's tools |
| CI: Linux for everything; Mac and Windows for the runner and the contract | they are what runs on those systems |
| The build skill: code first, then settle, then tests | carried over from v1 |

## Not chosen

| Option | Why not |
|---|---|
| Git as the store | the server is the only writer, so there is nothing to merge |
| Postgres now | one writer and one owner; SQLAlchemy keeps the move open for a SaaS |
| A desktop app | a browser and a server you can reach from anywhere do the same |
| Full GraphRAG | a second pass of Claude over every record; reconcile's tags give the graph |
| Outlook and Teams through an Azure app registration | Playwright driven by Claude was chosen; the registration stays a fallback |

## To prove first

| Question | Spike |
|---|---|
| Does voice ID tell your coworkers apart on Plaud audio, and how fast is it on the 4060? | five real recordings |
| Does the Agent SDK run on a subscription token, and how do the session and weekly limits show? | one scheduled job |
| Can Claude read Outlook and Teams on the web through Playwright, every time? | one week of mail |
| Which Python do all the ML libraries support? | the GPU worker image |
