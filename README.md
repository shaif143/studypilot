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
| Assignments worked out by several models | ✅ Claude, ChatGPT, Gemini and a local model (whichever are set up, two at least) work a brief out together in two steps: they agree a step-by-step guide, then complete the assignment by following it. Each step is a discussion: every model drafts alone, they review each other's drafts with a reason and a reference for each issue, an author accepts an issue or defends the draft, a defended issue is ruled on by a model that took neither side, the best-voted draft becomes one agreed version, and everyone votes on it. An objection in the vote is argued and ruled on the same way, so one model objecting wrongly is overruled rather than obeyed. The result says how it ended (all agreed, agreed by ruling, or not agreed, with what is still objected to) and the whole discussion is kept beside it. The student picks which files and folders the models may use as references |
| Folders | ✅ Folders inside a course, among its lectures and among its assignments: create, rename, delete (the files stay), file a lecture or a brief in one, upload straight into one, and tick a whole folder as references |
| Image and audio briefs | ✅ Read by a vision model (Anthropic or Gemini, with consent) and by a local Whisper model or Gemini for audio — the document records which, and the upload says to check the transcript |
| Scanned PDFs | ✅ A PDF with no text layer is read page by page by the vision model (with the same consent, up to 40 pages); indexed, searchable and cited like any other, and the upload says it was read from images |
| Claude / Gemini on the Mac | ✅ Paste the key in Settings; it is kept in the macOS Keychain, never a file, and only sent to that provider. Pick the model per provider (Opus 5.5 by default; Fable 5.2, Sonnet 5.5, Haiku 5.5, or any id) |
| Folders on this Mac | ✅ Link a folder (native picker) to a course and it is indexed **in place** — nothing copied, the reader opens the original file. The whole folder is listed at once; documents are read first (seconds each) and images and recordings last, with progress shown; Rescan follows new, changed and deleted files; notes are saved back into a `StudyPilot Notes` subfolder. Desktop only |
| Folder upload | ✅ Drop or choose a whole folder (subfolders included); every supported file is indexed, each with its own result, and anything the app cannot take is listed by name |
| Old PowerPoint (.ppt) | ✅ A pure-Python reader of the 1997–2003 binary format (`olefile` + the record walker in `ingestion/ppt.py`): slide order, titles, bullets, inline text, pictures, groups, speaker notes, incremental saves. Windows-metafile drawings are played back as vector drawing (see *Old metafile drawings*) |
| .zip / .rar archives | ✅ Uploaded whole; every readable member becomes its own document, named by its path, and the rest are listed with a reason. Images (PNG/JPEG/WebP/GIF/BMP/TIFF) and recordings inside go to the same consent-gated model as a single upload — the dialog asks once per batch — or are skipped with one clear reason. RAR is read with the `bsdtar` every Mac ships |
| Google Drive | ✅ Like mounting Drive in Colab: one click to connect (the build ships the app's own OAuth client, so nothing is typed), then link Drive folders to courses. **Files are not copied**: each is read once for its index and opened from Drive; "Sync now" follows the folder; notes are saved back into a `StudyPilot Notes` subfolder. Google Docs and Slides come through Google's PDF export |
| Privacy controls | ✅ Export, delete-everything, per-feature consent, all off by default |
| macOS packaging | ✅ ARM64 sidecar + `.app` / `.dmg` |
| Hosted deployment | ✅ PostgreSQL + pgvector, S3 storage, accounts — see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| Account authentication | ✅ Argon2id, session cookies, per-account isolation |
| PPTX import | ✅ python-pptx reads the deck, PyMuPDF lays each slide out as a PDF page at the shapes' real positions (text, tables, pictures, speaker notes); the reader, highlights and topics then work exactly as for a PDF |
| Concept map | ✅ Per course: topics as a graph (stated prerequisites + lecture order), coloured by your quiz results, with "start here" — built by code, drawn with Mermaid in strict mode |
| Check my attempt | ✅ Paste your work on a plan step; the coach names the error, cites the slide, gives one hint — level rises each time you ask, decided by code — and never writes the answer |
| Diagrams, graphs and tables in answers | ✅ The tutor may add a short Mermaid flowchart or a plotted curve (a ` ```plot ` block of number pairs); both are rendered as pictures from data, never executed. Markdown tables are drawn, with maths and citations working inside cells |
| Slide study view | ✅ My Courses → **Open** lists every lecture's slides as thumbnails; a slide opens with a written explanation on two thirds of the screen — one or more teaching slides with the equations unpacked, a diagram or graph where it helps, a worked example, and lecture-video searches — and the lecturer's original on the other third, with an **Ask** prompt along the bottom. Written once per slide (the model is shown the slide itself) and kept; anything not on the slide is labelled *Beyond the slide* |
| Dashboard | ✅ Courses fold into one accordion row; *What to do next* is colour-coded by how pressing each step is (urgent, soon, routine, on track), with the label as well as the colour |
| Sidebar | ✅ A button at the top of the sidebar hides it and brings it back (also ⌘\\ or Ctrl+\\), and the choice is remembered. The page area fits exactly under the title strip, so a full-height screen such as the slide view keeps its bottom bar on screen |
| Explain a whole lecture | ✅ **Explain all slides** in a lecture's header writes every slide not yet explained, three at a time in the background, with progress and a **Stop**; what was written before a stop is kept |
| Class transcripts | ✅ A lecture takes the transcript of the class it was shown in (`.txt`, `.md`, `.vtt`, `.srt`, `.docx`, `.pdf`); the part about each slide is found by its words and its place in the lecture and put in front of the model, which then teaches the slide the way the lecturer did and marks what came *From the class* |
| Interactive explanations | ✅ Beside diagrams and plots: a graph the student drives with sliders (` ```explore `, expressions in a small arithmetic language parsed as data and never run as code), a derivation revealed step by step (` ```steps `), self-check questions with hidden answers (` ```quiz `), and callouts for the key idea, the common mistake and the exam tip |
| Notes on a slide | ✅ A note box under the original slide, saved as you type and kept with that slide; all of them are gathered on *My Notes* |
| Past exam papers | ✅ A course's past papers and solutions are analysed into the paper's format, its mix of question forms and a ranked forecast of topics, each with the question references behind it and the likely next variation. Likelihoods are clamped to what the number of papers supports and never reach certainty. Quizzes, mock exams and flashcards then follow it: the real exam's forms and marks, likeliest topics first, different questions each time, written from this year's lectures |
| Stopping work in progress | ✅ A folder scan and an upload can be stopped. The file being read is finished or discarded, nothing is left half-added, and a cancelled upload is not kept |
| Old metafile drawings | ✅ WMF and EMF pictures in PowerPoint decks (diagrams, MathType equations, pasted screenshots) are played back onto the slide as vector drawing by a built-in renderer; nothing on a Mac draws them otherwise |
| Notebooks and HTML pages | ✅ A Jupyter notebook (`.ipynb`, including Colab's) and an HTML page are laid out as pages and then read like any lecture: Markdown as text, code as code, the outputs and figures saved in the notebook, pictures embedded in the file. A top-level heading starts a page, so a page is a section. Nothing is run, scripts are discarded, and a picture that would have to be fetched from the web is left out and reported |
| Claude chat (website only) | ✅ **Claude** in the sidebar opens a chat that runs on the Claude API key each account saves: Claude Fable 5.2 or Claude Opus 5.5, replies streamed as they are written, files and photos attached, pasted or dropped, chats kept in a list and renamed or deleted, and **projects** whose instructions and files every chat in them shares. It can also act on your courses when asked: list them, make and rename folders, move files, add an attachment to a course, search and read your material, save a note. It cannot delete a file or a course. The key is checked with Anthropic, stored encrypted and never shown again |
| Connectors and plugins in the Claude chat | ✅ **Connectors**: add a remote MCP server by its address, with an access token if it needs one; Anthropic reaches it, **Test** lists the tools it offers, and each can be switched off. **Sign in** takes you to the connector's own sign-in page and back (the standard MCP sign-in: the app finds the connector's authorisation server, registers itself, and keeps the token encrypted and renewed); a pasted token still works for a server that hands them out. **Plugins**: add a `.zip`, `.plugin` or `.skill` archive; its skills are loaded by Claude when a task calls for them, its commands run by typing `/name`, and its remote connectors are added switched off. Nothing in a plugin is run, and a chat keeps working after a connector it used is removed |
| Resizable slide view | ✅ A bar between the explanation and the original slide is dragged to give either more room (or moved with the arrow keys; double-click resets). The slide redraws sharp at its new size and the choice is remembered |
| Terms and symbols first | ✅ A slide's explanation opens with a **Terms and symbols** card: one table defining every technical term and abbreviation the slide uses, and one giving each symbol, its name as it is said aloud, and what it stands for with its unit. The explanation that follows uses only what those tables define |
| Fits any window | ✅ Every screen lays itself out by the room the page actually has beside the sidebar, not by the width of the window, so nothing spills out of its card at the Mac window's minimum size or anywhere between. Rows of buttons wrap, long names are cut with an ellipsis, and on a phone-width browser the sidebar slides over the page and closes when a destination is chosen |
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

Runs the TypeScript typecheck, ESLint, 170 frontend unit tests, Ruff, and 441
backend tests. The 15 PostgreSQL integration tests need a database:

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
