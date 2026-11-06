# Privacy

What this document is: a factual description of where data goes, what the
software does, and what it does **not** do. Where a capability is not
implemented, it says so rather than describing an intention as a feature.

---

## Default posture

Out of the box, with no configuration:

- Every uploaded document, every index, every conversation and all progress
  stay on the device.
- **No network request is made to any third party.** The default model provider
  performs no generation; the default embedder requires no download.
- Every consent switch is **off**.

Nothing leaves the device until a cloud provider is both enabled in Settings
*and* opted into on the specific request that would transmit the content.

---

## Where data lives

| Data | Location (desktop) |
| --- | --- |
| Database (courses, pages, chunks, sessions, progress) | `~/Library/Application Support/StudyPilot/studypilot.sqlite3` |
| Uploaded files | `…/StudyPilot/documents/` (content-addressed by SHA-256) |
| Rendered page previews | `…/StudyPilot/previews/` |
| Vector index | `…/StudyPilot/vectors/` |
| API token | `…/StudyPilot/api_token` (mode `0600`) |
| Cloud provider API keys entered in Settings | The **macOS Keychain** (service `StudyPilot`), encrypted and unlocked with your login — never a plain file. Only sent to that provider, with the text you ask about. If no keychain is usable, a `0600` file in the data directory, and Settings says so. |
| Google Drive sign-in | The refresh token is in the database **encrypted**; the key that unlocks it is in the Keychain. Full Drive scope, used to read the folders you link and to write notes into a `StudyPilot Notes` subfolder of one — nothing else. Disconnect revokes it at Google. |
| Documents from a **linked folder on this Mac** | **Not copied.** Only their index is stored; the file is read from its own path when opened, and only while the folder stays linked |
| Documents linked from Google Drive | **Not stored.** Only their text index (pages, topics, chunks, vectors) is here; the file is read from Drive when you open it. |

The data directory is created `rwx------`. On the web, the same data lives
wherever the backend that the site is configured to talk to stores it.

---

## What leaves the device, and when

| Action | Leaves the device? |
| --- | --- |
| Uploading, indexing, searching a document | No |
| Asking a question with the default provider | No |
| Asking a question with Ollama | No |
| Generating quizzes, flashcards or notes without an AI model | No |
| Asking a question with Anthropic or Gemini enabled **and** opted into | **Yes** — the question and the retrieved passages |
| Drafting an assignment guide with a cloud provider | **Yes** — the brief's text and the lecture passages retrieved for it |
| Uploading an **image** brief | **Yes, if** cloud processing is on and you opt in on the upload — the image is sent to the vision model. Otherwise the upload is refused with a message, never read silently |
| Uploading an **audio** brief | **No** when the local Whisper model is installed; otherwise **yes** under the same two-part consent — the recording is sent to Gemini |
| Uploading a **scanned PDF** (no text layer) | **Yes, if** cloud processing is on and you opt in on the upload — each page image is sent to the vision model. Otherwise it stays partly indexed with a note |
| Images or recordings inside a **folder or archive** upload | Same as above, decided once per batch by the switch in the upload dialog (off unless cloud processing is on in Settings). Declined, they are skipped with a note, never read silently |
| Connecting Google Drive | **Yes** — a sign-in with Google, in your browser. StudyPilot receives a read-only token; it sends Google nothing of yours |
| Indexing or opening a file from Drive | Data comes **from** Google to this device (the file's bytes, read and dropped) |
| Saving a note to a Drive folder | **Yes** — that note's Markdown, into `StudyPilot Notes` under the linked folder, only when you press Save |
| Telemetry | Never — none is collected or transmitted |

Each provider declares `sends_data_off_device` in code, and the Settings screen
and the badge under every answer read that flag. There is no provider whose
egress behaviour is hidden.

### The consent gate

Using a cloud provider requires separate affirmations from everyone involved:

1. the user's `allow_cloud_processing` toggle in Settings, and
2. an explicit flag on the individual request — and, on a hosted deployment,
3. the operator's `STUDYPILOT_ALLOW_CLOUD_PROCESSING` switch, off by default.

On the desktop the operator is the user, so an unset operator switch defers to
the first two; otherwise the toggle in the app could never do anything.

A single global toggle, once flipped, silently applies to everything
afterwards. Requiring per-request consent keeps the egress decision attached to
the action that causes it. If either is missing, the request **degrades** to
offline quoting with a visible warning rather than failing or proceeding.

---

## Logging

Logs are single-line JSON on stderr, local only.

Field names associated with private content — `question`, `answer`, `prompt`,
`text`, `content`, `document_text`, `student_answer`, `token`, `api_key`,
`authorization` — are replaced with `<redacted>` unless
`STUDYPILOT_LOG_SENSITIVE_CONTENT` is explicitly enabled for debugging.

Identifiers, counts, durations and status values are logged so problems can be
diagnosed without exposing material.

---

## Your controls

| Control | Where | Effect |
| --- | --- | --- |
| Export everything | Settings → Your data | Full JSON: profile, settings, courses, documents, conversations, flashcards, assessments, notes, mastery |
| Delete everything | Settings → Your data | Hard-deletes the profile, every row, the vector index, the sparse index and all stored files |
| Delete one document | Lecture Library | Removes the file, its previews and every index entry derived from it |
| Delete one course | My Courses | Cascades to its documents, topics and indexes |
| Disconnect Google Drive | Settings → Google Drive | Revokes the token at Google, forgets it and every linked folder. Indexed documents stay until you delete them |
| Unlink a Drive folder | Lecture Library | Stops syncing; documents stay |
| Unlink a folder on this Mac | Lecture Library | Drops the index built from it; the files are untouched |
| Per-feature consent | Settings → Privacy | Each off by default |

Deletion is immediate and not reversible. The export exists so you can take a
copy first; it reports the path of your uploaded files rather than inlining
them, because they are already on your disk.

---

## Attention tracking — **not implemented**

The settings screen has an "Attention estimation (experimental)" switch and the
API accepts attention data. To be unambiguous about what exists:

- **There is no camera code in this release.** No webcam is accessed, no video
  is captured, no gaze is estimated.
- The API accepts only **per-element dwell aggregates** (page number, element
  index, milliseconds). It has no endpoint that accepts gaze coordinates or
  image data, by design.
- Samples marked `source: "webcam"` are **refused**: `403` without consent,
  `501 Not Implemented` with it. The feature cannot be switched on by accident
  because it does not exist yet.
- Only `source: "simulated"` data is accepted, so the interface and the UI can
  be built and tested honestly.

**When it is built**, the design constraints are: explicit camera consent,
client-side processing only, no video recording, no upload of camera frames, no
transmission of raw gaze coordinates, and a visible way to pause, disable and
delete.

**Dwell time is not comprehension.** The report says so on every response. The
most it can support is "you spent comparatively little time on this region, it
may be worth a second look". It cannot diagnose attention, intelligence or
learning ability, and nothing in this product will claim that it can.

---

## Federated learning — **not implemented**

No federated training runs, no model updates are computed, and nothing is
transmitted to any aggregation server. The switch records a preference for a
capability that does not yet exist; `research/federated_learning/` holds the
design notes and threat model.

When it is built: participation will be opt-in, raw documents and learning
histories will never be uploaded, and the privacy properties will be stated
precisely and measured. **Federated learning does not make data anonymous** —
model updates can leak information about the data that produced them, which is
why secure aggregation and differential privacy are prerequisites rather than
nice-to-haves.

---

## Isolation between users

The desktop runs a single local profile, but the schema and repository layer are
multi-user throughout:

- every row that can hold private content carries a `user_id`;
- every repository method takes an explicit `user_id` and filters on it;
- route handlers never build their own queries;
- retrieval resolves the permitted chunk ids **before** searching, and passes
  that allow-list into both retrievers;
- `NotFoundError` is returned identically whether a row is absent or owned by
  someone else, so the API cannot be used to probe for another user's material.

This is covered by tests in `services/api/tests/test_security.py`.

---

## Treating uploaded documents as untrusted

A PDF can contain text crafted to manipulate a language model. Accordingly:

- document text is delimited and labelled as data in every system prompt;
- the model is instructed that instructions inside it must be reported, not
  obeyed;
- the agent graph exposes **no tools**, so injected text has nothing to invoke;
- citation markers are verified against the evidence actually supplied, and
  unmatched ones are stripped before display;
- generated questions are rejected unless they name a passage that was supplied;
- uploads are validated by magic bytes and size before anything is written.

---

## UK GDPR

The technical groundwork is here: data minimisation (only element-level dwell
aggregates, never raw gaze), purpose limitation (consent per feature), the right
to access (export), the right to erasure (delete), storage limitation (a
retention preference), and local-first processing by default.

**Technical controls are not legal compliance.** A deployment handling other
people's data still needs a lawful basis, a privacy notice, a record of
processing, a DPIA for the experimental features, controller/processor
agreements with any model provider used, and a retention policy. Nothing here
constitutes legal advice.

---

## Claims deliberately **not** made

- Not end-to-end encrypted. Data is protected by filesystem permissions and a
  local access token.
- The database is not encrypted at rest beyond macOS FileVault, if enabled.
- No differential privacy is implemented.
- No anonymisation is performed.
- Cloud providers' own retention is governed by their terms, not by this
  application.
