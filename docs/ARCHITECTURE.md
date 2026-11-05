# Architecture

## Shape of the system

```text
┌──────────────────────┐   ┌──────────────────────────────────────┐
│  apps/web            │   │  apps/desktop                        │
│  Vite + BrowserRouter│   │  Vite + HashRouter                   │
│                      │   │  ┌────────────────────────────────┐  │
│                      │   │  │ src-tauri (Rust)               │  │
│                      │   │  │  • generates a per-launch token│  │
│                      │   │  │  • reserves a loopback port    │  │
│                      │   │  │  • spawns + supervises sidecar │  │
│                      │   │  └────────────────────────────────┘  │
└──────────┬───────────┘   └──────────────────┬───────────────────┘
           │        both render the same       │
           └──────────►  packages/app  ◄───────┘
                             │
                   packages/api-client  (one HTTP boundary)
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  services/api  — FastAPI, loopback, token-authenticated          │
│                                                                   │
│   api/ ──► agents/ (LangGraph) ──► rag/ ──► storage/              │
│              │                      │         │                   │
│              └─► models/ ◄──────────┘         ├─ SQLite + FTS5    │
│                  (LLM + embeddings)           ├─ Qdrant (local)   │
│                                               └─ blob store       │
└──────────────────────────────────────────────────────────────────┘
```

Everything above the HTTP boundary is shared between the website and the macOS
application. The one thing that differs is the
[platform adapter](../packages/shared/src/platform.ts): three methods supplying
the API address, the credential, and how to open an external link.

## Why one application package

The requirement was to avoid duplicating logic between web and desktop. Rather
than two apps that import shared components, there is **one** application —
`packages/app` exports a single `<StudyPilotApp platform={…}>` — and the hosts
are thin: an HTML file, a `main.tsx`, a router choice, and a platform adapter.
A screen cannot be added to one host and forgotten in the other, because
neither host contains screens.

Internal packages are consumed **as TypeScript source** through Vite aliases and
`tsconfig` paths. There is no package build step to orchestrate, edits
hot-reload across package boundaries, and `tsc --build` typechecks everything at
once.

## Local backend security

A loopback port is not a trust boundary: any process on the machine can connect
to it. The backend is therefore treated as a real service.

- **Every route except `/healthz` requires a shared secret.** `/healthz` is
  open because the Rust shell needs it to detect that the sidecar is up, and it
  reveals nothing but a version string.
- **The desktop generates a token per launch**, passes it to the sidecar in its
  environment, and hands it to the webview through the `backend_info` command.
  It never touches disk.
- **The website never sees a token.** In development the Vite proxy reads the
  backend's token file and attaches the header server-side as it forwards each
  request; in production a reverse proxy does the same. No credential is ever
  present in a browser bundle.
- **The token file is 0600** and the data directory is `rwx------`.
- **CORS** allows only the dev server and the Tauri webview origins, with
  `allow_credentials=False`.
- **The port is ephemeral** on desktop: the shell asks the OS for a free one, so
  two copies never collide and nothing is reachable off the machine.

## Request path for a tutor turn

1. `POST /api/tutor/ask` — authenticated, validated by Pydantic.
2. `run_turn` resolves the session and checks **ownership** of the course and
   document through the repository layer.
3. The model provider is selected, passing through the **two-part consent
   gate**: a cloud provider requires both the stored preference *and* an
   explicit flag on this request. Refusal degrades to offline quoting with a
   warning, rather than failing the turn.
4. The LangGraph supervisor runs:
   `classify → retrieve → (abstain | generate → verify) → recommend`.
5. `retrieve` resolves the permitted chunk ids **first**, then searches only
   within them.
6. `verify` resolves every `[n]` marker against the passages that were actually
   supplied. Markers with no match are **stripped** and recorded as a warning.
7. The turn is persisted with its citations, so reopening the session restores
   it exactly.

### Why the graph is small

There is no autonomous agent loop and no tool calling. The supervisor is a
fixed state machine with at most one corrective retrieval pass. An unbounded
loop over a single student's documents buys nothing and costs tokens, latency
and predictability — and with no tools exposed, text injected into an uploaded
PDF has nothing to invoke.

Deterministic code owns scope and permissions, retrieval, the sufficiency
judgement, citation verification, marking, scheduling, and the recommended next
action. The model owns only explanation text.

## Retrieval

```text
question
   │
   ├─► classify (definition | formula | explanation | general)
   │         └─ sets the dense/sparse weighting
   │
   ├─► resolve permitted chunk ids  ← the access-control boundary
   │
   ├─► dense (Qdrant, cosine)   ─┐
   ├─► sparse (SQLite FTS5 BM25) ┴─► reciprocal rank fusion
   │
   ├─► expand each child hit to its page-level parent
   │
   └─► sufficiency check
            ├─ sufficient → generate a grounded answer
            └─ insufficient → reformulate once, then abstain
```

**Parent/child chunking.** Child chunks are small and are what gets embedded
and matched; parent chunks are page-sized and are what the model receives. A
precise match therefore arrives with enough context to be explainable.

**Why BM25 matters here.** Students ask about named concepts — "what is
stride". Exact term matching is what finds the slide that *defines* the term,
and it carries most of the weight when the built-in embedder is in use.

**Why RRF.** Cosine similarity and BM25 are on incomparable scales. Fusing on
rank alone avoids having to normalise them, which is what keeps the result
sensible when one retriever is much weaker than the other.

**Sufficiency is measured by term coverage**, not by rank score. RRF scores are
scale-free: the top result of a hopeless search scores about the same as the
top result of a perfect one. Coverage — how many of the question's content words
appear in the retrieved passages — is what distinguishes *"my slides discuss
this"* from *"my slides contain the word 'the'"*. A model's own confidence is
never used; it is not a measure of correctness.

## Model providers

All inference sits behind two interfaces in `app/models/base.py`. Each provider
declares whether it `sends_data_off_device`, and whether it is `generative` —
the privacy UI and the consent gate read those flags rather than hard-coding
provider names.

| Provider | Generative | Leaves device | Notes |
| --- | --- | --- | --- |
| `extractive` *(default)* | **No** | No | Ranks and quotes sentences from the student's own material. Cannot hallucinate; cannot teach. |
| `ollama` | Yes | No | Local runtime, optional install. Fine for tutor turns; too slow for a plan with `llama3.1:8b` on an 8 GB Mac (13 minutes for one call). Text only — it refuses image and audio attachments rather than ignoring them. |
| `anthropic` | Yes | **Yes** | Requires consent and `ANTHROPIC_API_KEY`. Reads images. The default for assignment guides when a key is present. No `temperature` is sent — Claude 5.x rejects it. |
| `gemini` | Yes | **Yes** | Requires consent and `GEMINI_API_KEY`. Reads images and audio through the general model (Google's dedicated transcribe model rejects system instructions and answered a bare request with nothing, so it is not used). Replaced OpenAI. |

`GenerationRequest` carries optional `attachments` (image or audio bytes). A
provider that cannot read them raises rather than answering without them —
answering about a file it did not see would be answering a different
question. `ProviderInfo.accepts_images` / `accepts_audio` are what the
ingestion path checks before handing a brief to a model.

Embeddings follow the same pattern: `hash` (no download, offline, weak),
`model2vec` (~30 MB, CPU-only, recommended), `sentence-transformers` (best
local quality, ~1 GB). Every indexed chunk records the model that produced its
vector, and a dimension change invalidates the collection rather than silently
mixing incompatible vectors.

Configure with environment variables on the backend, never in the frontend:

```bash
STUDYPILOT_LLM_PROVIDER=ollama
STUDYPILOT_EMBEDDING_PROVIDER=model2vec
```

## Storage

SQLAlchemy 2.0 models with no SQLite-only column types, so the same schema
backs a hosted PostgreSQL deployment. SQLite-specific physical structures —
the FTS5 index — live behind `app.rag.sparse.SparseIndex`.

**The repository layer is the authorisation boundary.** Every method that
touches private content takes an explicit `user_id` and filters on it. Route
handlers never build queries. `NotFoundError` is returned identically whether a
row is absent or owned by someone else, so the API cannot be used to probe for
another user's documents.

A subtlety worth recording: `SparseIndex` takes the caller's `Session` and runs
through it. SQLite allows one writer at a time, so opening a second connection
deadlocked against the request's own open transaction — and because index
failures are non-fatal by design, it did so *silently*, leaving documents
indexed for dense search only.

Uploaded files are content-addressed by SHA-256, which gives free
de-duplication and makes it impossible for a row to point at a file whose
contents have changed.

## Desktop execution model

```text
StudyPilot.app launches
   │
   ├─ window paints immediately (shows "Starting the engine…")
   │
   └─ background thread:
        ├─ reserve an ephemeral 127.0.0.1 port
        ├─ generate a 32-byte token
        ├─ spawn the bundled sidecar with STUDYPILOT_{PORT,API_TOKEN,DATA_DIR}
        ├─ poll /healthz until ready (45 s budget)
        └─ publish the address + token

   webview's first `backend_info` call awaits readiness
   on quit → the sidecar is killed (idempotent, covers both exit paths)
```

The backend is bundled as a **single self-contained ARM64 executable** built by
PyInstaller, so the installed app depends on no Python interpreter, no virtual
environment, no Docker, and nothing on the user's `PATH`. Data lives in
`~/Library/Application Support/StudyPilot/`, resolved by Tauri rather than
hard-coded.

Startup is asynchronous because a one-file PyInstaller bundle self-extracts on
cold launch (about six seconds). Blocking the setup hook would show an
unresponsive window for that time.

## Ingestion

For PDFs, deterministic end to end — no model is involved, so the output is
identical on every run and works with nothing installed:

```text
validate (magic bytes, size) → extract (PyMuPDF) → detect topics
  → parent/child chunks → embed → index (dense + sparse) → persist provenance
```

Partial failure is a first-class outcome. A deck where half the slides are
images still gets indexed, and the document is marked `partial` with the reason
attached rather than reported as a success.

Every indexed item retains: owner, course, document, version, page number,
section, element type, bounding box, source text, and the embedding model
version. The bounding boxes are what make a citation clickable — the reader
draws them over the rendered PDF.

### Image and audio briefs

A photographed brief or a recording cannot be read without a model, so
`app/ingestion/media.py` is the one place a model enters ingestion. It
produces the same `ExtractedDocument` the PDF path does — one page holding
the transcript — and everything downstream is unchanged. The rules that
follow from a model being involved:

- the file type comes from the bytes, never the name or the declared type;
- the document records what read it (`extraction_meta.extractor`:
  `vision:anthropic`, `whisper-small`, `transcribe:gemini`) and the upload
  response warns the student to check the transcript against the original;
- images need a provider with `accepts_images`; audio tries a local
  `faster-whisper` model first (optional `[audio]` extra — nothing leaves the
  machine; PCM WAV is decoded in-process so the common case does not depend
  on PyAV, whose API has changed across versions) and falls back to a
  provider with `accepts_audio` under the same consent gate as the tutor,
  controlled by `STUDYPILOT_AUDIO_TRANSCRIBER`;
- a provider that cannot read the file refuses with a message naming what is
  needed, and the document is marked `failed` rather than indexed empty.

## Assignment guides

`app/assignments/planner.py` turns an assignment brief into a step-by-step
plan grounded in the course's lecture material:

```text
brief ─▶ requirements ─▶ retrieve lecture passages per requirement
      ─▶ draft steps ─▶ ground each step ─▶ plan (ready | needs_approval)
```

Two model calls, both returning JSON that is parsed and validated rather
than trusted: one reduces the brief to its requirements (deliverables,
constraints, criteria, tools), one drafts steps against those requirements
and the retrieved passages. Output budgets are generous (4,000 and 8,000
tokens) because the first real run was cut off mid-array; a response that
is still cut off is salvaged object by object, and the plan says so. The
request's database transaction is released before each model call: a plan
takes a model a minute, and an 8B model on a laptop a quarter of an hour,
and holding a transaction that long blocks every writer on SQLite and pins a
pooled connection on PostgreSQL. On-device models get a 15-minute timeout,
cloud ones a few minutes. Retrieval is scoped to documents of kind
`lecture` and `textbook` only — citing another brief as evidence that the
slides cover something would be circular.

**The grounding rule is code, not the model.** A step is `slides` only when
it cites at least one real passage marker, the model did not itself flag it
as beyond the material, *and* the step's content terms are mostly present in
the passages it cited (the same term-coverage test the tutor uses). Anything
else is `beyond`: its approval state is `pending`, and it carries a
disclosure — written by code, so it reads the same every time — naming the
terms the lecture material does not cover. The model's own flag can hold a
step back but never promote one. The expensive mistake is telling a student
their slides cover something they do not.

The plan never contains the work. The system prompt forbids writing code,
analysis or report text; a step says what to do, why, and where the lecture
material shows how. The course's integrity policy is passed through.

Without a generative provider the planner still answers, honestly: the
brief's numbered lines become requirements, each gets the passages that
match it, and the plan says in its warnings that no model was used.

Plans persist as JSON in `assignment_plans`; approving or declining a step is
a repository operation that refuses to change a grounded step, so the record
of which steps ever needed a decision stays intact.

## Prompt-injection posture

Uploaded lecture text is untrusted input and is treated as such:

- it is delimited and explicitly labelled as data in every system prompt;
- the model is told that instructions found inside it must be reported, not
  obeyed;
- the graph exposes **no tools**, so there is nothing for injected text to
  invoke;
- citation markers are verified against the supplied evidence after generation,
  so a model persuaded to invent a source produces a marker that gets stripped;
- generated assessment questions are validated field by field and dropped
  unless they name a chunk that was actually supplied;
- FTS5 query syntax typed by a student is quoted before it reaches the index.

## Known limitations

- **Highlight geometry** assumes a page origin at (0, 0) and no rotation.
  Cropped or rotated pages fall back to no highlight rather than a wrong one.
- **Upload is synchronous.** Fine for a lecture deck (sub-second locally); a
  500-page textbook would hold the request open.
- **Single local profile** on desktop. The schema and repositories are
  multi-user already; only the `get_current_user` dependency needs replacing for
  a hosted deployment.
- **The built-in `hash` embedder matches wording, not meaning.** Hybrid
  retrieval compensates, but installing `model2vec` is a real improvement.
