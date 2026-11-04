# Testing

## Running

```bash
npm run check
```

Runs everything: TypeScript, ESLint, frontend unit tests, Ruff (lint and
format), and the backend suite. Individually:

```bash
npm run api:test        # pytest — backend
```

```bash
npm test                # vitest — frontend
```

```bash
npm run typecheck && npm run lint && npm run api:lint
```

Nothing in the suite needs the network, a model download, or a running server.

---

## Current state

| Suite | Tests | Status |
| --- | --- | --- |
| Backend (`services/api/tests`) | 100 | ✅ all passing |
| Frontend (`tests/frontend`) | 79 | ✅ all passing |
| ESLint | — | ✅ clean |
| TypeScript | — | ✅ clean |
| Ruff lint + format | — | ✅ clean |
| Playwright end-to-end | 0 | ❌ **not written** — see gaps below |

---

## What the backend suite covers

### `test_end_to_end.py` — the MVP workflow, for real

The headline test drives the whole required journey against a genuine PDF:

> import → view slides → index → ask a grounded question → receive a cited
> explanation → generate a quiz → submit answers → receive feedback → save
> progress → reopen successfully

Nothing is stubbed except the absence of a cloud model. The offline provider
quotes the student's own material, so grounding, citation, assessment and
progress are all exercised end to end. It asserts, among other things, that:

- the uploaded fixture yields 9 pages, real chunks and 5+ detected topics;
- page 4's heading is `Padding` and its text contains the real sentence;
- page elements carry bounding boxes, and previews render as images;
- the answer to *"Why is padding necessary?"* cites **page 4** specifically;
- every citation resolves to a chunk that was actually retrieved;
- the quiz response contains **no answer key** (`correct_option`,
  `expected_answer` and `rubric` are absent);
- question provenance is never `past_paper`;
- mastery is recorded but stays labelled `unknown` while evidence is thin;
- reopening resumes at the saved page and topic.

A second test constructs a **new app instance over the same data directory** to
simulate quitting and relaunching.

### Other backend files

| File | Covers |
| --- | --- |
| `test_ingestion.py` | Extraction fidelity, heading detection, bounding-box sanity, preview rendering, equation detection, page limits reported rather than silent, magic-byte validation, PPTX declined honestly, chunk parent/child linkage |
| `test_retrieval.py` | RRF agreement and weighting, query classification, embedder determinism and normalisation, **cross-user isolation**, document and course scoping, insufficiency on off-syllabus questions, corrective reformulation, FTS5 syntax in a student's question, deletion removing material from retrieval |
| `test_learning.py` | That one wrong answer does **not** collapse mastery; that mastery is not claimed without evidence; MCQ being weaker evidence than a written answer; partial credit landing between right and wrong; prerequisite-first recommendations; SM-2 lapse and growth behaviour |
| `test_assessment.py` | Line-wrap reflow, page-furniture stripping, rejection of morphologically confusable distractors, deterministic generation under a seed, exact MCQ and numeric marking, tolerance behaviour near zero, rubric partial credit, unmarkable answers reported as such |
| `test_supervision.py` | The parent-process watchdog, including an end-to-end test that a watched grandchild exits by itself when its parent is killed — the behaviour the packaged app depends on |
| `test_openapi_contract.py` | The committed OpenAPI snapshot still matches the live app; `AnswerResponse` keeps its honesty fields; `QuestionOut` never exposes the answer key |
| `test_security.py` | Auth required on every route, token file permissions, repository-level ownership, 404-not-403 for another user's rows, **path traversal** on previews, disguised uploads, size limits, the two-part consent gate, provider egress declarations, privacy defaults all off, prompt-injection containment, **fabricated citation markers being stripped**, ungrounded LLM questions rejected, export and delete-everything, webcam attention refused, log redaction |

### What the frontend suite covers

| File | Covers |
| --- | --- |
| `api-client.test.ts` | Token attachment and single resolution, no token in the web host, cache reset after 401, query serialisation, consent and validation error translation, network vs abort distinction, 204 handling, multipart upload shape |
| `grounded-answer.test.tsx` | A resolved `[n]` renders as a clickable citation; an **unresolved one renders as plain text, not a dead link**; multiple markers; markdown links keep `noopener`; KaTeX renders; a marker inside a code block is left alone |
| `formatting.test.ts` | Byte, duration and relative-time formatting including unparseable input; provenance, mastery and action labels |
| `api-contract.test.ts` | Drives the real client against a stub transport and asserts **every URL it produces exists in the backend's committed OpenAPI document** — so a renamed route fails here rather than at runtime |

---

## Tests that encode a product rule

Several tests exist to pin behaviour that would be easy to regress into
dishonesty:

- `test_abstains_rather_than_guessing` — an off-syllabus question must be
  refused, not answered from world knowledge.
- `test_injected_document_text_cannot_fabricate_a_citation` — markers with no
  matching passage are stripped and a warning recorded.
- `test_answer_is_not_grounded_without_a_valid_citation` — `grounded` is false
  when nothing was cited inline.
- `test_extractive_provider_never_invents_text` — every quoted sentence must
  appear verbatim in the supplied evidence.
- `test_llm_questions_must_be_grounded_in_supplied_evidence` — a question
  naming a chunk that was not supplied is dropped.
- `test_one_wrong_answer_does_not_collapse_mastery`.
- `test_mastery_is_not_claimed_without_evidence`.
- `test_webcam_attention_is_refused_until_implemented`.
- `test_privacy_defaults_are_the_private_option`.

---

## Bugs this suite caught

Recorded because they justify the tests existing.

1. **SQLite write deadlock.** `SparseIndex` opened its own connection and
   deadlocked against the request's transaction. Because index failures are
   non-fatal, documents were silently indexed for dense search only; the suite
   took 52 s instead of 4 s waiting on busy timeouts.
2. **A meaningless sufficiency gate.** Stop words in the BM25 query plus
   scale-free RRF scores meant *"What is the capital of Portugal?"* was judged
   well-supported by a deck on convolutional networks.
3. **Inconsistent chunk counts.** The dashboard counted parent and child
   chunks; upload reported children only.
4. **Equation detection missing ASCII notation**, so no page in the fixture was
   marked as containing mathematics.
5. **The token file created lazily**, after the dev proxy had already tried to
   read it — every request 401'd on a fresh install.
6. **An orphaned backend after quitting the packaged app.** Found by launching
   the built `.app` and checking the process table, not by a test. Tauri kills
   its direct child, but a one-file PyInstaller build forks a worker that
   survived — still serving on its port with the database open. Fixed with a
   parent-process watchdog in the backend, now covered by
   `test_supervision.py`.

---

## Fixtures

`fixtures/sample-lecture-cnn.pdf` is a real nine-page PDF about convolutional
neural networks, generated by `scripts/make_fixtures.py` with reportlab. It has
genuine heading hierarchy, prose, three equations and page footers, so
extraction, topic detection and question generation are exercised against
something shaped like a real lecture deck rather than a contrived string.

It is committed, so the suite runs without reportlab installed. Regenerate with:

```bash
npm run fixtures
```

---

## Gaps — what is **not** tested

Stated plainly rather than implied by omission.

- **No Playwright end-to-end tests.** `@playwright/test` is installed and
  `npm run test:e2e` is wired, but no specs are written. The browser paths —
  upload through the real file picker, pdf.js canvas rendering, drag-to-resize,
  keyboard navigation — were verified by hand, not automatically.
- **No Rust tests.** The Tauri shell's port reservation, token generation,
  readiness polling and shutdown were verified by building and running the app,
  not by `cargo test`.
- **No automated test launches the packaged `.app`.** `scripts/build-sidecar.sh`
  smoke-tests the bundled backend (liveness plus an authenticated
  `/api/health`), but the assembled application is verified by hand.
- **Cloud providers are not integration-tested.** `anthropic` and `openai` have
  no recorded-cassette tests; only the consent gate and failure handling around
  them are covered.
- **No performance or memory benchmarks** against the 8 GB baseline.
- **No accessibility audit.** Semantics, focus-visible styling, `aria` labels
  and reduced-motion support are implemented throughout, but not verified by
  axe or a screen reader.
- **Component-level UI tests are thin.** The citation renderer and the API
  client are covered; the screens are not.

The highest-value next addition is a Playwright run of the full workflow
against a real dev server, which would close most of the first and last gaps at
once.
