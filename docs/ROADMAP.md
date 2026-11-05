# Roadmap and milestone checklist

Legend: ✅ implemented and tested · 🧪 experimental or interface-only ·
❌ not started · ⏸ deliberately deferred ([why](DECISIONS.md#16-deferred-deliberately))

---

## Phase 0 — Architecture and repository setup ✅

- ✅ Environment inspected; Python 3.13, Node 23.5, Rust toolchain installed
- ✅ Monorepo with npm workspaces
- ✅ TypeScript, Python (Ruff) and Rust tooling configured
- ✅ Shared frontend consumed by both hosts
- ✅ Tauri 2 shell with a supervised Python sidecar
- ✅ FastAPI backend with typed settings and structured logging
- ✅ Development commands (`scripts/*.sh`, npm scripts)
- ✅ Application shell, navigation and design system
- ✅ Automated tests on both sides
- ✅ Repository structure documented

## Phase 1 — Functional document learning MVP ✅

- ✅ Course management (create, edit, archive, delete, cascade)
- ✅ PDF upload with magic-byte validation, size limits and de-duplication
- ✅ Reader: real pdf.js rendering, zoom, page navigation, keyboard shortcuts
- ✅ Extraction: text, headings, tables, equations, figures, bounding boxes
- ✅ Page previews rendered server-side as a fallback
- ✅ Content-addressed blob storage; SQLite with WAL
- ✅ Local indexing: dense (Qdrant) + sparse (FTS5 BM25)
- ✅ Hybrid retrieval with reciprocal rank fusion and query classification
- ✅ Access control enforced before retrieval
- ✅ Evidence-sufficiency check and abstention
- ✅ Corrective retrieval (deterministic reformulation, authorised material only)
- ✅ Model-provider abstraction (extractive, Ollama, Anthropic, OpenAI)
- ✅ Grounded answers with verified citations
- ✅ Citation navigation: click a marker, jump to the page, highlight the region
- ✅ Chat interface with mode switching
- ✅ Session persistence and resume

## Phase 2 — Study and assessment ✅

- ✅ Flashcards with SM-2 style scheduling and honest provenance
- ✅ Quiz generation (deterministic and model-driven, both validated)
- ✅ Deterministic marking; rubric marking with partial credit
- ✅ Notes in concept / exam / weakness modes, editable and exportable
- ✅ Topic mapping from the document's heading structure
- ✅ Progress tracking with Bayesian Knowledge Tracing
- ✅ Rule-based, explainable recommendations
- ✅ Mock examination workflow with blueprint, time limit and review
- ✅ Dashboard reporting only real, queried numbers

## Phase 3 — Advanced educational functionality — partial

- ✅ Socratic assignment guidance with six hint levels and a per-course
  integrity policy
- ✅ **Assignment guides**: a brief (PDF, image or audio) becomes a
  step-by-step plan; each step is grounded in the course's lectures with
  citations or held for the student's approval with a disclosure saying
  what the slides do not cover. Drafted by Anthropic by default; works
  without a model as a requirements-with-passages list.
- ✅ Image briefs read by a vision model; audio briefs transcribed locally
  (faster-whisper, optional) or by Gemini
- ✅ Gemini provider (replaces OpenAI); Ollama set up for on-device generation
  (usable for tutor turns; `llama3.1:8b` is too slow to draft a plan on an
  8 GB Mac — see `docs/TESTING.md`)
- ✅ LangGraph orchestration (deliberately a bounded state machine)
- ✅ Corrective retrieval
- ✅ KaTeX mathematics rendering throughout
- ✅ **Concept map** per course: topics as a graph from stated prerequisites
  and lecture order, coloured by mastery, with "start here" (a weak topic whose
  predecessors are not weak). Built by code on the server, drawn with Mermaid
  in strict mode. Tutor answers may include a short Mermaid flowchart,
  rendered as a picture; an unparseable one falls back to its text.
- ✅ **PPTX import**: python-pptx + PyMuPDF render the deck to a PDF the
  existing pipeline and reader handle unchanged (speaker notes included).
- ✅ **Check my attempt** on a plan step: what is wrong, why (cited), one hint;
  the hint level climbs per ask, set by code, and the answer is never written.
- ❌ Plots and algorithm walkthroughs (Plotly / React Flow): not started.
  Mermaid covers maps and flowcharts; the heavier libraries wait for a feature
  that needs them.
- ⏸ **Multimodal understanding of diagrams inside lecture PDFs.** Figures are
  detected and their bounding boxes stored, but no vision model is called on
  them. The vision path now exists (it reads image *briefs*); pointing it at
  figures is the next step.
- ⏸ Knowledge graph / Graph RAG — prerequisites are a list column on `Topic`;
  no graph store.
- ⏸ Open Knowledge Format interchange layer.
- ⏸ Cache-augmented generation.
- ⏸ Cross-encoder reranking.

## Phase 4 — Advanced privacy and experimental features — interface only

- 🧪 **Attention module** — API, storage, aggregation and UI exist and accept
  only per-element dwell aggregates. **No camera code ships.** `webcam`
  samples are refused: `403` without consent, `501` with it. Only `simulated`
  data is accepted. See [`PRIVACY.md`](PRIVACY.md#attention-tracking--not-implemented).
- ❌ **Federated learning** — design notes and a threat model only; no
  training, no aggregation, nothing transmitted. See
  [`research/federated_learning/`](../research/federated_learning/).
- ❌ Secure aggregation, differential privacy — prerequisites for the above,
  not yet designed in detail.
- ❌ Local learning-model adaptation.

## Phase 5 — Community, scalability, distribution — partial

- ✅ **ARM64 macOS packaging** — a self-contained 110 MB sidecar and a working
  `.app`, validated early rather than postponed.
- ✅ Disk image via `scripts/make-dmg.sh`.
- ✅ Structured local logging with redaction.
- ❌ Code signing and notarisation — the build accepts the standard Tauri
  environment variables; no credentials are available here, so it is unsigned
  and macOS requires right-click → Open.
- ❌ Hosted backend deployment, production authentication.
- ❌ Optional secure synchronisation. **No cross-device sync exists, and none
  is implied anywhere in the UI.**
- ❌ Peer assistance, community routing, moderation.
- ❌ Production observability and security hardening beyond the local posture.

---

## What I would do next, in order

1. **Ship `model2vec` as the default embedder.** The hashed fallback matches
   wording rather than meaning, and it is the clearest quality ceiling in the
   product today. A ~30 MB download at first run would lift retrieval
   noticeably.
2. **Background ingestion with progress.** Upload is synchronous. Fine for a
   lecture deck, not for a textbook.
3. **More end-to-end coverage.** Two Playwright specs cover the MVP loop and the
   new features; drag-to-resize, keyboard navigation and the desktop shell are
   still hand-verified.
4. **Vision for diagrams.** Slides whose content is a figure currently yield no
   text, and the reader labels them honestly. This is the largest remaining
   gap in extraction coverage.
5. **Signing and notarisation**, once credentials exist.

## Known limitations

- Citation highlights assume a page origin at (0, 0) and no rotation; cropped
  or rotated pages fall back to no highlight rather than a wrong one.
- One local profile per desktop install. The schema and repositories are
  multi-user already; only `get_current_user` needs replacing.
- Image-only PDFs index nothing. The document is marked `partial` with the
  reason attached, and the reader says so per page. No OCR.
- Deterministic question generation produces cloze-deletion items, which test
  recall rather than understanding — labelled "From your slides" so they are
  never mistaken for more.
