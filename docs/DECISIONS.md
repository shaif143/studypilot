# Decision records

Each entry records what was decided, why, and what it costs.

---

## 1. Python 3.13, not 3.14

**Context.** The machine's default `python3` is 3.14.

**Decision.** Pin to 3.13 (`requires-python = ">=3.11,<3.14"`).

**Why.** Several dependencies have no 3.14 wheels and would build from source
or fail outright. 3.13 resolves the whole set cleanly.

**Cost.** One more thing for a contributor to install. `scripts/setup.sh` pins
it explicitly so this is not a trap.

---

## 2. One application package, thin hosts

**Context.** The requirement forbids duplicating logic between web and desktop.

**Decision.** `packages/app` exports a single `<StudyPilotApp platform={…}>`
containing every screen, route and data hook. The hosts contain an HTML file, a
`main.tsx`, a router choice and a ~30-line platform adapter.

**Why.** Shared *components* still allow two divergent applications. A shared
*application* cannot diverge: a screen cannot be added to one host and
forgotten in the other, because neither host contains screens.

**Cost.** Anything genuinely host-specific must be expressed through the
`Platform` interface rather than branched inline. So far that is the API
address, the credential, and opening external links.

---

## 3. Internal packages consumed as source

**Decision.** Vite aliases and `tsconfig` paths point at `src/index.ts`; the
packages have no build step.

**Why.** No build-order orchestration, cross-package hot reload, and one
`tsc --build` typechecks everything.

**Cost.** The packages are not independently publishable. They are not intended
to be.

---

## 4. The default provider performs no generation

**Context.** The app must work without a language model, and must never
present something as tutoring that is not.

**Decision.** The default `extractive` provider ranks sentences from retrieved
material by overlap with the question and quotes them verbatim with citations.
It declares `generative: false`, and every surface — badges, notices, the
answer text itself — says the material was quoted, not explained.

**Why.** It makes the entire pipeline — ingestion, retrieval, citation,
assessment, progress — real and testable with no API key and no network, while
being structurally incapable of inventing a fact. The alternative, shipping a
model-shaped placeholder, is exactly the fabrication the brief prohibits.

**Cost.** Out of the box the product is a very good search-and-recall tool
rather than a tutor. That is stated plainly rather than hidden.

---

## 5. Hashed embeddings as the zero-install default

**Decision.** Default to a deterministic hashed bag-of-n-grams embedder;
`model2vec` and `sentence-transformers` are optional extras.

**Why.** First run must work offline with no download. Hashing is weak
semantically, but hybrid retrieval leans on BM25, which carries lexical queries
well — and lexical is what most "what is X" questions are. It is also
deterministic, which makes retrieval tests reproducible.

**Cost.** Paraphrased questions retrieve worse until a real embedding model is
installed. Settings says so, with the install command.

---

## 6. Sufficiency measured by term coverage, not rank score

**Context.** The first implementation gated on fused rank score. It passed
almost everything: *"What was the population of Lisbon in 1850?"* was judged
sufficiently supported by a deck about convolutional networks.

**Two causes.** The BM25 query included stop words, so `is`/`the`/`and` matched
nearly every chunk; and RRF scores are scale-free, so the top result of a
hopeless search scores about the same as the top result of a perfect one.

**Decision.** Strip stop words from the sparse query, and gate on the fraction
of the question's content words that appear in the retrieved passages.

**Why.** Coverage is a signal about *this* question against *this* evidence.
Rank position is not. A model's own confidence was never a candidate — it is
not a measure of correctness.

**Cost.** A question phrased entirely in synonyms of the material's vocabulary
can be judged insufficient. The failure direction is the safe one: the tutor
says it cannot answer rather than answering from general knowledge.

---

## 7. The sparse index runs inside the caller's transaction

**Context.** `SparseIndex` originally opened its own connection.

**Symptom.** `database is locked` during ingestion and deletion. SQLite allows
one writer at a time, and the second connection deadlocked against the
request's own open write transaction. Because index failures are non-fatal by
design, this happened **silently**: documents were indexed for dense search
only, and the test suite took 52 s instead of 4 s waiting on busy timeouts.

**Decision.** Every `SparseIndex` method takes the caller's `Session`.

**Lesson recorded here deliberately.** Non-fatal error handling turned a loud
failure into a quiet capability loss. The warning was logged and ignored.

---

## 8. One-file PyInstaller bundle

**Context.** Tauri's `externalBin` copies a single named executable into
`Contents/MacOS`. A PyInstaller one-directory build also needs its `_internal`
folder beside that executable, which the bundler would place in
`Contents/Resources` instead — leaving the sidecar unable to find its own
libraries.

**Decision.** Build one file.

**Cost.** ~110 MB and about six seconds of self-extraction on cold launch. The
shell therefore starts the backend on a background thread and the window paints
immediately with a "Starting the engine…" state.

**Also.** `onnxruntime` is excluded: `qdrant-client` pulls it for the optional
`fastembed` feature, which local mode never uses. That alone saved 18 MB.

---

## 9. Backend startup is asynchronous, and the token is written eagerly

**Two related findings during first run.**

The token file was created lazily on the first authenticated request, but the
Vite dev proxy and the Tauri shell both read it as soon as the port opens —
a chicken-and-egg that 401'd every request with no diagnosable cause. The token
is now materialised in `RuntimeContext.create`.

Starting the sidecar synchronously in Tauri's `setup` hook left an
unresponsive window for the duration of self-extraction. Startup now runs on a
background thread and the webview's first `backend_info` call awaits readiness.

---

## 10. LangGraph, but a small graph

**Decision.** Use LangGraph for the supervisor, with a fixed state machine, no
tool calling, and at most one corrective retrieval pass.

**Why.** The typed state and checkpointing fit resumable study sessions. An
autonomous loop does not: over one student's documents it buys nothing and
costs tokens, latency and predictability. With no tools exposed, text injected
into an uploaded PDF has nothing to invoke.

**Cost.** Capabilities needing genuine multi-step tool use would require
revisiting this. None of the current features do.

---

## 11. Deterministic code owns everything high-stakes

**Decision.** Scope and permissions, retrieval, the sufficiency judgement,
citation verification, MCQ and numeric marking, spaced-repetition scheduling,
mastery updates and the recommended next action are all plain code. The model
owns explanation text, and — when configured — question drafting and rubric
application, both validated afterwards.

**Why.** A student's progress record and revision schedule are high-stakes for
their time. They must be reproducible and explainable.

**Cost.** Deterministic question generation produces cloze-deletion items,
which test recall rather than understanding. Labelled honestly as
`source_derived` — "From your slides" — so it is never mistaken for more.

---

## 12. Question provenance is a first-class field

**Decision.** Every question and flashcard carries `source_derived`,
`ai_generated`, `past_paper`, `past_paper_variation` or `user`, and the UI
always shows it. Nothing in the generation path can emit `past_paper`; that
value exists only for questions extracted from a document the student uploaded
and labelled as a past paper.

**Why.** "Never invent examination history" is a hard requirement, and the
difference between a real past question and a model's imitation is exactly what
a student revising needs to know.

---

## 13. Cloud use requires consent twice

**Decision.** A provider that transmits content off-device is refused unless
*both* the stored user preference and an explicit per-request flag are set.
Refusal degrades to offline quoting with a visible warning rather than failing.

**Why.** A single global toggle, once flipped, silently applies to every future
action. Per-request consent keeps the egress decision attached to the action
that causes it.

---

## 14. Vitest on happy-dom; `legacy-peer-deps` in `.npmrc`

Two environment workarounds, recorded so they can be removed later.

- **happy-dom instead of jsdom.** jsdom 30 pulls `undici`, whose decompress
  interceptor needs Node's zstd bindings (Node 24+). This machine runs Node
  23.5, where the worker crashes on start.
- **`legacy-peer-deps=true`.** npm 10.9.2's resolver crashes with a `TypeError`
  while walking vitest's optional browser-runner peers on a from-scratch
  install. It is an npm bug, not a real conflict; installs from the committed
  lockfile work without it.

---

## 15. `react-hooks/preserve-manual-memoization` disabled

**Decision.** Off; the other rules in `eslint-plugin-react-hooks` v7 stay as
errors.

**Why.** It reports that the React Compiler could not preserve a `useCallback`.
The compiler is not enabled in this build, so the diagnostic has no runtime
consequence. The correctness rules — `set-state-in-effect`, `immutability`,
`exhaustive-deps` — were fixed properly rather than silenced: state seeded from
server data is now adjusted during render (React's documented pattern), and
effect bodies no longer call setState synchronously.

---

## 16. Neon Auth sits beside the built-in accounts, not on top of them

**Decision.** `STUDYPILOT_AUTH_PROVIDER` selects one of two identity
providers at runtime. The built-in Argon2id accounts stay; Neon Auth is the
other option, used by the hosted deployment.

**Why.** Replacing the built-in provider would have broken the desktop
application, which has no browser to run a JavaScript auth client in and must
work offline against a local profile. Neon's own guidance is also to keep a
working identity provider unless migration is asked for, and this one is
tested and in use.

**What it costs.** Two code paths through sign-in, and a `provider` field on
`/api/auth/status` so the browser knows which one it is talking to. The
sign-in form itself is shared: only the mutation behind it changes.

**What it buys.** The backend never handles a Neon password — it verifies an
EdDSA token against the published JWKS and nothing more. Account rows are
keyed `neon:<sub>` on the immutable subject, so a user changing their email
does not orphan their material, and a Neon subject can never collide with a
built-in account's email handle.

**Boundary.** `@neon/auth` is imported in exactly one file,
`apps/web/src/neon-auth.ts`, behind a dynamic `import()`. `packages/app`
depends on a structural interface in `packages/shared`, so the shared
application — and therefore the desktop build — carries no dependency on it.

---

## 17. The planner's grounding decision belongs to code

**Decision.** A step in an assignment guide is "grounded in your slides" only
when deterministic code says so: real citation markers, the model did not
flag it as beyond the material, and the step's content terms are mostly
present in the cited passages. The model's flag can demote a step, never
promote one. The disclosure on a beyond-the-slides step is a fixed template,
not model text.

**Why.** The feature exists to tell a student which parts of their plan their
course actually taught. The expensive error is a false "grounded"; a model
asked to self-report grounding will sometimes say yes to be helpful. Making
the decision asymmetric and mechanical means a prompt cannot soften it.

**What it costs.** Some genuinely covered steps are held for approval because
their wording does not overlap the slides — a false "beyond". That is the
cheap error: the student reads the disclosure, sees it is covered, and
approves it.

---

## 18. Gemini replaces OpenAI

**Decision.** The second cloud provider is Gemini. OpenAI remains
constructible for anyone who pinned it but is no longer offered.

**Why.** One provider that reads text, images and audio through a single
endpoint is what makes image and audio briefs possible on a hosted instance
with no local speech model, and the owner asked for it. Audio goes to the
dedicated transcription model.

---

## 19. Tests never read `.env.local`

**Decision.** `Settings` reads only `.env`; the dev script exports
`.env.local` into its own environment.

**Why.** `.env.local` holds the hosted deployment's real connection string.
Letting `Settings` read it — which was briefly done so API keys would be
picked up — would have pointed every `Settings()` in the test suite, whose
fixtures drop and recreate the schema, at the production database. The
permission classifier refused to run the suite, which is how it was caught.

---

## 20. The planner is a LangGraph graph, with memory in the state

**Decision.** The assignment planner runs as a `StateGraph`
(`app/assignments/graph.py`) with one bounded corrective pass, and the
student's earlier decisions are part of its state, applied by code.

**Why.** It was first written as a linear pipeline, which was honest but not
what the product calls for: the tutor already runs on LangGraph, the
corrective retrieve-and-redraft is a cycle, and "remember what I declined"
needs state that outlives one model call. Making the planner a graph keeps
one orchestration model across the agents and makes the loop bound explicit
(`corrective_done`). Memory lives in the persisted plans, not in the model,
so it is inspectable and deletable with the rest of the student's data.

**Not chosen.** A LangGraph checkpointer. The plan is small and already
persisted whole in `assignment_plans`; a second store of partial graph state
would be a second thing to migrate and delete for no resumability a student
would notice (a plan takes a minute).

---

## 21. PowerPoint is rendered to PDF, not parsed into a second pipeline

**Decision.** `app/ingestion/pptx.py` reads a deck with python-pptx and draws
each slide as a PDF page with PyMuPDF at the shapes' real positions; the PDF
pipeline, reader, highlights and topics then run unchanged. Speaker notes go in
a grey strip under the slide so they are searchable without moving anything.

**Why.** A second extractor would have meant a second bounding-box model, a
second preview path and a reader that cannot show the file. One rendered PDF
removes all three. What is lost — theme, fonts, SmartArt, animations — is not
what a citation needs. Docling reads PPTX too but pulls PyTorch, which an 8 GB
M1 cannot spare; python-pptx is pure Python. FiraGO (`pymupdf-fonts`) draws the
text because the Base-14 fonts turn bullets and Greek letters into "?".

---

## 22. Diagrams are pictures: Mermaid in strict mode, maps built by code

**Decision.** The concept map is built on the server from stored topics,
prerequisites and mastery rows — no model — and rendered client-side with
Mermaid under `securityLevel: 'strict'`. The tutor may add a Mermaid
flowchart to an answer; it goes through the same renderer, and one that does
not parse is shown as text.

**Why.** The blueprint's rule was never to execute model-authored JavaScript.
Mermaid text is data; strict mode disables click handlers and HTML labels, so
the worst a hostile lecture title can do is look odd — and topic names are
stripped of Mermaid syntax before they reach a label anyway. Plotly and React
Flow were not added: Mermaid covers maps and flowcharts, and the heavier
libraries wait for a feature that needs them.

---

## 23. The attempt reviewer's hint level is decided by code

**Decision.** "Check my attempt" tells the model which rung of the hint
ladder to give (concept → rule → approach → analogous example → line by line)
from how many reviews the step already has, and the prompt forbids writing
the solution.

**Why.** If the model chose the level it would skip to the end on the first
ask, which is the assignment-solving behaviour the product exists to avoid.
Counting earlier reviews is deterministic, visible to the student ("next hint:
the rule or formula to look at"), and the reviews persist so the ladder
survives a restart.

---

## 24. Deferred deliberately

| Deferred | Reason |
| --- | --- |
| Neo4j / Graph RAG | Prerequisites are a list column; a graph store earns nothing yet |
| Cache-augmented generation | Premature before retrieval quality is settled |
| Cross-encoder reranking | RRF over two retrievers is enough at this corpus size |
| Docling | Pulls PyTorch; PyMuPDF covers text, tables, equations and boxes, and python-pptx covers decks |
| Plotly / React Flow | Mermaid covers maps and flowcharts; nothing yet needs plots or an interactive canvas |
| Federated training | Needs a threat model and measurable controls first |
| Camera gaze estimation | Interface and simulated data only; see `docs/PRIVACY.md` |
