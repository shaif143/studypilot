# StudyPilot

A privacy-first learning companion for university students. Upload your lecture
PDFs; StudyPilot indexes them locally, answers questions **only** from your own
material with clickable citations, builds quizzes and flashcards from what you
have read, and tracks what you actually know.

It runs as a native macOS application on Apple Silicon and as a web application,
sharing one frontend codebase.

---

## What works today

This is an honest list. Everything marked ✅ was built, run and tested; see
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the full milestone checklist.

| Capability | State |
| --- | --- |
| Course management | ✅ Create, edit, archive, delete |
| PDF import | ✅ Text, headings, tables, equations, figures, bounding boxes, page previews |
| Topic detection | ✅ Deterministic, from the document's own heading structure |
| Hybrid retrieval | ✅ Dense (Qdrant) + BM25 (SQLite FTS5) + reciprocal rank fusion |
| Access control | ✅ Enforced in the repository layer, before retrieval |
| Grounded answers with citations | ✅ Markers verified against retrieved passages; fabricated ones stripped |
| Evidence sufficiency / abstention | ✅ The tutor declines rather than guessing |
| Corrective retrieval | ✅ Deterministic query reformulation over authorised material only |
| Interactive reader | ✅ Real PDF rendering, citation highlighting, resumable position |
| Quizzes & mock exams | ✅ Generated from indexed material, deterministic marking |
| Flashcards | ✅ SM-2 style scheduling |
| Personalised notes | ✅ Concept / exam-focused / weakness modes, editable and exportable |
| Knowledge tracing | ✅ Bayesian Knowledge Tracing with explicit uncertainty |
| Socratic assignment coaching | ✅ Progressive hints, per-course integrity policy |
| Assignment guide | ✅ Upload a brief (PDF, image or audio) → step-by-step plan; every step is either grounded in the course's lectures with citations, or held for your approval with a disclosure saying what the slides do not cover |
| Image and audio briefs | ✅ Read by a vision model (Anthropic or Gemini, with consent) and by a local Whisper model or Gemini for audio — the document records which, and the upload says to check the transcript |
| Claude / Gemini on the Mac | ✅ Paste the key in Settings; it is kept in the macOS Keychain, never a file, and only sent to that provider. Pick the model per provider (Opus 5.5 by default; Fable 5.1, Sonnet 5.5, Haiku 5.5, or any id) |
| Folder upload | ✅ Drop or choose a whole folder (subfolders included); every supported file is indexed, each with its own result, and anything the app cannot take is listed by name |
| Google Drive | ✅ Connect a Google account (read-only, PKCE) and link a Drive folder to a course. **Files are not copied**: each is read once to build its search index, opened from Drive when you read it, and "Sync now" follows the folder. Google Docs and Slides come through Google's PDF export |
| Privacy controls | ✅ Export, delete-everything, per-feature consent, all off by default |
| macOS packaging | ✅ ARM64 sidecar + `.app` / `.dmg` |
| Hosted deployment | ✅ PostgreSQL + pgvector, S3 storage, accounts — see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| Account authentication | ✅ Argon2id, session cookies, per-account isolation |
| PPTX import | ✅ python-pptx reads the deck, PyMuPDF lays each slide out as a PDF page at the shapes' real positions (text, tables, pictures, speaker notes); the reader, highlights and topics then work exactly as for a PDF |
| Concept map | ✅ Per course: topics as a graph (stated prerequisites + lecture order), coloured by your quiz results, with "start here" — built by code, drawn with Mermaid in strict mode |
| Check my attempt | ✅ Paste your work on a plan step; the coach names the error, cites the slide, gives one hint — level rises each time you ask, decided by code — and never writes the answer |
| Diagrams in answers | ✅ The tutor may add a short Mermaid flowchart; rendered as a picture, never executed |
| Camera-based attention tracking | ❌ Interface and simulated data only — see [`docs/PRIVACY.md`](docs/PRIVACY.md) |
| Federated learning | ❌ Design only — see [`research/federated_learning/`](research/federated_learning/) |
| Knowledge graph / Graph RAG | ❌ Schema supports prerequisites; no graph store |
| Cross-device sync | ❌ Not started — and not implied anywhere in the UI |
| Notarised distribution | ❌ Needs signing credentials |

### About the default "no AI model" mode

StudyPilot ships with **no language model configured**, and does not pretend
otherwise. In that state:

- ingestion, search, citations, quizzes, flashcards, scheduling and progress
  tracking all work completely;
- "explanations" are **quoted passages from your own documents**, not written
  prose, and every surface says so.

Configure a provider (local Ollama, or Anthropic / Gemini with explicit consent)
to get real explanations and a drafted assignment guide. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#model-providers).

---

## Requirements

- **macOS on Apple Silicon** (M1 or later). Intel is not supported.
- **Node.js 20+** (developed against 23.5).
- **Python 3.11–3.13** (3.13 recommended; 3.14 lacks wheels for several
  dependencies).
- **[uv](https://docs.astral.sh/uv/)** for the Python environment.
- **[Rust](https://rustup.rs)** — only for building the desktop app.

Designed to run comfortably on a baseline 8 GB M1.

## Quick start

```bash
./scripts/setup.sh
```

That creates the Python environment, installs JavaScript dependencies and
generates the test fixtures. Then, in two terminals:

```bash
npm run dev:api
```

```bash
npm run dev
```

Open <http://127.0.0.1:5173>.

Then: create a course → upload `fixtures/sample-lecture-cnn.pdf` → open the
reader → ask *"Why is padding necessary?"* → click a citation.

### Desktop app

```bash
npm run dev:desktop
```

To build an installable `.app` and `.dmg`:

```bash
./scripts/build-sidecar.sh
```

```bash
npm run build:desktop
```

The result is in `apps/desktop/src-tauri/target/release/bundle/`. The build is
unsigned; macOS will require right-click → Open on first launch until signing
credentials are configured.

## Checks

```bash
npm run check
```

Runs the TypeScript typecheck, ESLint, 104 frontend unit tests, Ruff, and 319
backend tests. The 11 PostgreSQL integration tests need a database:

```bash
npm run pg:up && npm run api:test:pg
```

The browser tests drive the real website against the real API in Chromium
(both are started for you, with a throw-away data directory and no model):

```bash
npx playwright install chromium
```

```bash
npm run test:e2e
```

Individually:

```bash
npm run typecheck && npm run lint && npm test && npm run api:lint && npm run api:test
```

---

## Repository layout

```text
studypilot/
├── apps/
│   ├── web/                    Website host (Vite + BrowserRouter)
│   └── desktop/                macOS host (Vite + HashRouter)
│       └── src-tauri/          Rust shell: supervises the Python sidecar
├── packages/
│   ├── shared/                 Types, platform adapter interface, formatters
│   ├── api-client/             Typed HTTP client and error translation
│   ├── ui/                     Design system (Tailwind 4, CSS-first theme)
│   └── app/                    The entire application: screens, routing, state
├── services/api/
│   └── app/
│       ├── api/                FastAPI routes, schemas, dependencies
│       ├── agents/             LangGraph learning supervisor and prompts
│       ├── rag/                Retrieval: vector store, BM25, fusion
│       ├── ingestion/          PDF extraction, chunking, topic detection
│       ├── learning/           Knowledge tracing, scheduling, notes
│       ├── assessment/         Question generation and marking
│       ├── models/             Embedding and LLM provider adapters
│       └── storage/            ORM models, repositories, blob store
├── research/federated_learning/  Design notes and threat model (no training)
├── fixtures/                   Reproducible sample documents
├── scripts/                    Setup, dev, packaging
└── docs/                       Architecture, decisions, roadmap, privacy, testing
```

Both hosts render the same `<StudyPilotApp>` from `packages/app`. The only
thing that differs is a small [platform adapter](packages/shared/src/platform.ts)
supplying the API address and credential.

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the system fits together, and why |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decision records with trade-offs |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Milestone checklist: done, experimental, planned |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | Data flows, what leaves the device, what does not |
| [`docs/TESTING.md`](docs/TESTING.md) | What is tested, what is not, how to run it |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Hosting the website and API, with a security checklist |

## Licence

Not yet chosen.
