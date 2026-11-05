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
| Backend (`services/api/tests`) | 350 | ✅ all passing (337 + 13 PostgreSQL integration) |
| Frontend (`tests/frontend`) | 104 | ✅ all passing |
| ESLint | — | ✅ clean |
| TypeScript | — | ✅ clean |
| Ruff lint + format | — | ✅ clean |
| Playwright end-to-end | 3 | ✅ the MVP loop, the PPTX → map → coach loop, and folder upload + Drive setup, in a real browser |

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
| `test_auth.py` | Hosted accounts: registration, sign-in that reveals nothing about which addresses exist, Argon2id hashing and salting, token forgery and `alg:none` rejection, session revocation on password change and sign-out-everywhere, every data route requiring a session, and **isolation between accounts** across listings, documents, previews, retrieval and deletion |
| `test_cookie_security.py` | The session cookie is HttpOnly and `SameSite=Lax` always, and `Secure` exactly when the request arrived over TLS — including behind a proxy, where only `x-forwarded-proto` records the real scheme |
| `test_database_selection.py` | That `STUDYPILOT_DATABASE_URL` is actually honoured, end to end into the runtime context — the regression test for the silent SQLite fallback described below |
| `test_object_storage.py` | `S3BlobStore` against a simulated S3 (`moto`): content addressing, asset prefixes, multi-batch deletion past the 1000-key API cap, prefix isolation between deployments, and that both store implementations are genuinely substitutable |
| `test_storage_reachability.py` | That the object store is verified at boot — a missing bucket names the settings to fix, a read-only token is distinguished from an unreachable one, the probe leaves nothing behind, and the boto3 checksum behaviour that Cloudflare R2 requires is actually set |
| `test_pptx_import.py` | A deck is recognised by its contents, not its name; a zip that is not a deck is refused; old `.ppt` gets its own message; every slide becomes a page of the slide's size with its words in place, pictures as images, tables cell by cell, bullets and dashes as real glyphs; speaker notes render below the slide, not on it; the slide limit is a warning; through the API the deck is indexed and `/file` serves a PDF |
| `test_concept_map.py` | Document order and stored prerequisites both become edges (prerequisite wins for the same pair); mastery colours nodes and "start here" is a weak topic with no weak predecessor; topic names cannot inject Mermaid syntax; an empty course is an empty map; the route serves the indexed course |
| `test_assignment_attempts.py` | The hint level climbs one rung per review and stops at five; a review names the error, cites real passages and drops fake markers; earlier reviews raise the level and reach the prompt; prose from the model is kept as the summary; unknown verdicts normalise; no model → refused with advice; through the API reviews persist per step, oldest first, and a missing model is a 409 |
| `test_provider_keys.py` | Desktop keys go to the secret store (an in-memory one in tests), never a file; load into the environment at startup; the environment wins and is reported as not removable; delete removes everywhere but never a key the environment set; a legacy `provider_keys.json` is migrated and deleted; the file fallback is `0600`; hosted mode never reads the store; the API reports status and storage but never a value; an unknown provider is 404; the user can choose the active provider |
| `test_assignment_graph.py` | The planner's state machine: two coverage-only `beyond` steps trigger exactly one corrective pass (three model calls, never four); one such step does not; a declined step stays declined and an approved one approved on the next draft, by title match that tolerates small rewording; memory never touches a grounded step or invents a decision; earlier decisions and course-level accepted gaps reach the plan prompt |
| `test_assignment_planner.py` | The grounding rule: a cited, covered step is grounded; a step the model flags as beyond is never grounded even with citations; an uncited step and a step whose citations do not cover its terms are both `beyond` with a disclosure naming what is missing; fabricated markers are stripped; retrieval is scoped to lecture material; without a model the plan is honest about being a requirements list; the disclosure is written by code |
| `test_media_briefs.py` | Media type from bytes not names; images and audio accepted by validation; an image becomes one page with the transcript, the image handed to the model as bytes; a text-only provider refuses an image; audio falls back from local to a consented cloud provider, stays local when pinned, is refused when off or oversized; Ollama refuses attachments; Anthropic sends an image as a base64 block; Gemini's request shape and audio routing to the transcription model; no candidates is an error |
| `test_neon_auth.py` | Neon Auth JWT verification: a genuine EdDSA token is accepted; one signed by another key, from another issuer or audience, expired, unsigned (`alg: none`), or without a subject is rejected. Uses a local keypair, so no network |
| `test_postgres_integration.py` | The hosted data path against **real PostgreSQL**: `tsvector` ranking and scoping, tsquery syntax in a student's question, pgvector similarity and allow-list filtering, dimension-change rebuilds, the full upload-to-quiz workflow, and account isolation. Skipped unless `STUDYPILOT_TEST_DATABASE_URL` is set |
| `test_supervision.py` | The parent-process watchdog, including an end-to-end test that a watched grandchild exits by itself when its parent is killed — the behaviour the packaged app depends on |
| `test_openapi_contract.py` | The committed OpenAPI snapshot still matches the live app; `AnswerResponse` keeps its honesty fields; `QuestionOut` never exposes the answer key |
| `test_google_drive.py` | Against a fake Google: the consent request is PKCE-bound, read-only and on the loopback; no client → a message saying what to enter; a callback for a sign-in this process did not start is refused and the code never exchanged; a state is single-use; tokens are stored encrypted, the key in the secret store, and never reported; a listing marks what can be imported and why not; **importing a folder leaves no bytes on disk** (no blob, no previews) while pages, search, citations and the reader all work; sync re-reads changed files, adds new ones and removes gone ones; an expired access token is refreshed without a second refresh; disconnect revokes, forgets links, keeps documents and makes the reader say the connection is gone; folder links and ids are both accepted; the PKCE challenge matches RFC 7636; hosted mode derives the token key from the operator secret and uses the public URLs |
| `test_model_choice.py` | Anthropic defaults to Opus 5.5 and offers the Claude 5 family; the user's model per provider reaches the provider that answers and never leaks to another; a row from an older database reads as no choice; the API stores the choice and reports the active model; an empty id clears it; a model id has to look like one |
| `test_ppt_import.py` | Decks built record by record and wrapped in a real compound file (`cfb_writer.py`): slide order and placeholder text, anchors in master units, inline boxes, pictures, speaker notes, field markers dropped, a group's own coordinate space, the newest incremental save winning, a password-protected deck refused with a message, rendering to pages of the slide's size, metafiles reported, the slide limit a warning, a damaged container refused, and the API indexing and serving an old deck as a PDF. A lecturer's own deck runs with `STUDYPILOT_TEST_PPT` |
| `test_archives.py` | A zip with the fixtures plus Finder junk, a text file, an image, a nested archive and a `../` path: readable members out, the rest named with a reason, nothing escapes; limits skip rather than read; the kind comes from the bytes; a broken zip is an error; through the API each member is its own document; a GIF becomes a PNG on the way in; without a model, images and recordings are skipped with one reason and no failed rows; with a consented model every one is indexed. A real `.rar` runs with `STUDYPILOT_TEST_RAR` |
| `test_security.py` | Auth required on every route, token file permissions, repository-level ownership, 404-not-403 for another user's rows, **path traversal** on previews, disguised uploads, size limits, the two-part consent gate, provider egress declarations, privacy defaults all off, prompt-injection containment, **fabricated citation markers being stripped**, ungrounded LLM questions rejected, export and delete-everything, webcam attention refused, log redaction |

### What the frontend suite covers

| File | Covers |
| --- | --- |
| `api-client.test.ts` | Token attachment and single resolution, no token in the web host, cache reset after 401, query serialisation, consent and validation error translation, network vs abort distinction, 204 handling, multipart upload shape. Also the sign-in gate's inputs: a missing auth route is "no accounts here" on the desktop and an error on the web, and a Neon bearer is re-fetched for every request rather than cached |
| `grounded-answer.test.tsx` | A resolved `[n]` renders as a clickable citation; an **unresolved one renders as plain text, not a dead link**; multiple markers; markdown links keep `noopener`; KaTeX renders; a marker inside a code block is left alone |
| `formatting.test.ts` | Byte, duration and relative-time formatting including unparseable input; provenance, mastery and action labels |
| `uploads.test.ts` | A folder selection: supported files taken, hidden files dropped, `.docx`/`.ppt`/empty files listed with a reason, duplicates removed, same name in two folders kept, a file with no declared type judged by name |
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
6. **`STUDYPILOT_DATABASE_URL` silently ignored.** A patch to
   `Database.from_settings` never applied — the formatter had already removed
   the quotes from the type annotation the replacement was matching — so every
   hosted deployment would have used ephemeral SQLite while *looking*
   correctly configured. The durability warning designed to catch this stayed
   silent, because the setting was present and only the code reading it was
   missing. Found by running the real Docker image against PostgreSQL, not by
   any unit test. Now pinned by `test_database_selection.py`.
7. **`sslmode=require` forced onto every DSN**, including loopback, so a local
   PostgreSQL refused every connection. Found only by running the integration
   tests against a real database; TLS is now required for remote hosts only.
8. **An orphaned backend after quitting the packaged app.** Found by launching
   the built `.app` and checking the process table, not by a test. Tauri kills
   its direct child, but a one-file PyInstaller build forks a worker that
   survived — still serving on its port with the database open. Fixed with a
   parent-process watchdog in the backend, now covered by
   `test_supervision.py`.
9. **The Neon Auth issuer compared against the wrong value.** `iss` on a Neon
   token is the *origin*, while `NEON_AUTH_BASE_URL` carries a path as well
   (`.../neondb/auth`), so every genuine token was rejected with
   `Invalid issuer`. Found against a live Neon branch; pinned by
   `test_neon_auth.py::test_issuer_is_the_origin_not_the_base_url`.
10. **`queryClient.clear()` froze the sign-in gate.** Clearing the cache after
   a sign-in or sign-out removes the auth-status query itself, which leaves
   the gate's observer holding the removed query's last value with nothing to
   re-subscribe to. Nothing re-rendered and the follow-up `refetchQueries`
   matched no query, so the cache emptied while the screen stayed exactly as
   it was: a sign-out that looked like a failure, and a sign-in that never
   arrived. `resetSessionCache()` now *resets* the auth query and removes the
   rest. Found by driving the browser, not by a test — see the gap below.
11. **A missing auth route failed open on the web.** The API client answered a
   404 on `/api/auth/status` with "this backend has no accounts", which is
   true of the desktop sidecar — it does not mount those routes — and exactly
   backwards for a website, where a 404 means the API is missing or
   misrouted. The first Vercel deployment, with no API behind it at all,
   therefore served the entire interface as an authenticated session. The
   shortcut is now restricted to the desktop host and hosted deployments fail
   closed. Found by opening the deployed site, not by a test; now pinned by
   two cases in `tests/frontend/api-client.test.ts`.
12. **The hosted image could not verify a single Neon token.** The `hosted`
   extra declared plain `pyjwt`, and PyJWT registers EdDSA only when
   `cryptography` is installed. The development environment had it anyway,
   pulled in by another dependency, so all sixteen Neon Auth tests passed.
   The image built, booted, reported healthy, connected to Neon, and then
   answered every sign-in with `neon_token_rejected: EdDSA requires
   'cryptography' to be installed` — in the container log, which on a free
   hosting plan nobody reads. Found by running the real image against the
   real branch with a real browser. Now `pyjwt[crypto]`, pinned by a test
   that reads the manifest, because no test of the code could have seen it.
13. **Binary fetches sent the wrong credential.** `fetchBlobUrl` attached only
   the desktop token, so on a hosted deployment the reader's PDF and page
   previews were the only requests in the application still 401ing while
   every other screen worked. Both paths now build their headers in one
   place.
14. **Two first requests raced to create the local profile.** On a fresh
    install the app shell asks for the dashboard and the course list at once;
    each found no profile and inserted, and the second hit the unique handle
    with a 500. Found by the first end-to-end run, which is the only place the
    race occurs naturally. `ensure_local_profile` now inserts in a savepoint
    and, on `IntegrityError`, reads the row the other request made.
15. **A new conversation's first answer never appeared on the Tutor screen.**
    The answer created a session server-side, but the screen kept
    `sessionId` undefined, so the message query stayed disabled and the panel
    showed its empty state over a saved answer. `TutorPanel` now reports the
    created session; the reader never hit this because it resumes a session
    per document before the first question.
16. **The Tutor panel remounted when the course list loaded.** Its React key
    included the course id, which is empty until courses arrive; a question
    typed in that window was wiped. The key is now the session alone.
17. **An uploaded brief did not appear until a reload.** The upload hook
    invalidated the document queries but not the assignments list (or the
    concept map, which the new lecture changes), so the Assignments screen
    kept showing "No assignment briefs yet" over a successful upload.

---

## Fixtures

`fixtures/sample-lecture-cnn.pptx` is the same lecture as a PowerPoint deck —
title slide, bullets, a table, a picture, speaker notes — for the import
tests. `fixtures/sample-lecture-cnn.pdf` is a real nine-page PDF about convolutional
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

- **End-to-end coverage is two specs.** `tests/e2e/mvp-loop.spec.ts` drives
  the real website against the real API in Chromium (both started by
  `playwright.config.ts` with a throw-away data directory and the offline
  providers): course → PDF upload → reader → cited answer → citation opens the
  page → quiz → marked → reload; and PPTX upload → reader → concept map →
  assignment guide → "Check my attempt" refused honestly without a model.
  Drag-to-resize, keyboard navigation and the desktop shell are still
  hand-verified. Run with `npm run test:e2e` (needs `npx playwright install
  chromium` once).
- **No automated test calls a real model.** The planner and the readers are
  tested with scripted providers, and the Anthropic and Gemini request
  bodies are pinned by intercepting the HTTP call. The real models were run
  by hand on 2026-11-05 against `fixtures/sample-assignment-brief.pdf` with
  the CNN lecture indexed:
  - **Claude (`claude-sonnet-5-5`)**: 14 typed requirements, 11 steps, 59 s,
    4,053 input / 4,807 output tokens. Grounded: architecture design, same
    padding and its justification. Held for approval with precise reasons:
    tooling, pooling-size arithmetic ("the output-size expression is given
    for convolution only"), training, batch normalisation ("does not appear
    anywhere in the supplied passages"), plots, report structure, submission.
  - **Gemini (`gemini-3.6-flash`)**: 7 requirements, 8 steps, 59 s,
    2,571 / 1,812 tokens, same shape of result. Returned 503 "high demand"
    for a stretch; the provider now falls back once to `gemini-3.5-flash-lite`.
  - **Image brief via Claude vision**: an exact transcript of a rendered
    page in 3.6 s, preview stored, `extractor: vision:anthropic`.
  - **Audio via Gemini** (direct call): a correct timestamped transcript of a
    15-second clip; Google's dedicated transcribe model rejected the request
    shape and is not used.
  - **Ollama (`llama3.1:8b`)** on this 8 GB Mac: the *requirements* call
    alone took 13 minutes, so a plan would take half an hour. It is left
    available but is not a practical planner model here; the tutor's short
    turns are a different matter. Choose a smaller model with
    `STUDYPILOT_OLLAMA_MODEL` if on-device planning matters.
  Three bugs those runs found, none visible to the scripted tests: Claude
  5.x rejects `temperature` (every request 400'd until the body was read);
  the plan's 3,500-token budget cut Claude's JSON off mid-array (the budget
  is 8,000 now and complete steps are salvaged from a cut-off response);
  and a long model call held the request's database transaction open,
  which on SQLite blocked every other writer for the duration.
- **No test runs the hosted container.** The two worst bugs in the list above
  — EdDSA missing from the image, and the API client failing open on a 404 —
  existed only in the deployed artefact and were found by hand: building the
  image, running it against a real Neon branch with the real environment, and
  driving it from a browser on a second origin. That procedure is written
  down in `docs/DEPLOYMENT.md` under "Running the hosted stack locally", but
  nothing runs it automatically.
- **The Neon Auth browser flows have no automated test.** Sign-up, sign-in,
  sign-out, session restoration after reload, per-account isolation and the
  wrong-password error were each exercised by hand against a live Neon branch,
  and the cache bug above was found that way. `test_neon_auth.py` covers token
  verification only; nothing covers the React side.
- **Object storage has never been exercised against a real provider.** The
  S3 path is covered by `moto` in the unit tests, and the whole round trip —
  upload through the API, a content-addressed PDF and nine page previews
  landing in a bucket, the file read back byte-identical — was verified
  against a standalone S3 server using the real container image. Neither is
  Cloudflare R2. Endpoint and credential handling against an actual provider
  is still unverified; the startup probe is what catches it, in the deploy
  log.

The highest-value next addition is a Playwright run of the full workflow
against a real dev server, which would close most of the first and last gaps at
once.
