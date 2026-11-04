# Deploying StudyPilot

The website and the API deploy separately, because they want different things:
the frontend is static files on a CDN, while the API renders PDFs and builds a
search index and wants a warm process with a disk.

```text
            browser
               │
               ▼
   ┌──────────────────────────┐
   │  Vercel (static site)    │   apps/web built to static assets
   │                          │
   │  /api/*  ──rewrite──────────────┐   same-origin, so the session
   └──────────────────────────┘      │   cookie is SameSite=Lax and
                                     ▼   no CORS or CSRF token is needed
                      ┌──────────────────────────────┐
                      │  Container host (API)        │
                      │  Railway / Render / Fly.io   │
                      └───────┬──────────────┬───────┘
                              │              │
                      PostgreSQL          S3-compatible
                      + pgvector          object storage
```

**The rewrite is the important part.** Vercel proxies `/api/*` to the backend,
so the browser only ever talks to one origin. That is what lets the session
cookie be `SameSite=Lax` — it is withheld from genuine cross-site requests, so
no separate CSRF token is needed — and it means no CORS configuration at all.

## Why not run the API on Vercel too?

You can host Python functions on Vercel, and the bundle would fit (the limit is
500 MB; this is about 350 MB). The blocker is state, not size: **Vercel's
filesystem is read-only except `/tmp`, which is ephemeral and not shared
between instances.** This API writes a database, uploaded PDFs, page previews
and a vector index. Point all four at managed services, as below, and the
remaining problems are cold starts with PyMuPDF and long ingestion requests —
which is why a container is the better fit.

---

## Using Neon's CLI (fastest path)

Neon's CLI provisions the database and auth, and writes their credentials into
`.env.local`:

```bash
npm i -g neon@latest
```

```bash
neon init -y --org-id <your-org> --project-id <your-project> --branch production --services postgres,auth
```

```bash
neon deploy
```

That writes `neon.ts` (the services this project declares) and pulls
`DATABASE_URL`, `DATABASE_URL_UNPOOLED`, `NEON_AUTH_BASE_URL` and
`NEON_AUTH_JWKS_URL`. The backend reads all four under those exact names, so
nothing needs renaming.

**Object storage is still separate.** It is not among the services above, and
Neon Object Storage is unavailable in some regions (including `aws-eu-west-2`).
Use Cloudflare R2 or another S3-compatible bucket — `S3BlobStore` works with
any of them.

### Pooled and direct connections

Neon gives two connection strings and the backend uses both:

| Variable | Used for |
| --- | --- |
| `DATABASE_URL` (pooled, `-pooler` host) | Normal query traffic |
| `DATABASE_URL_UNPOOLED` (direct) | `create_all`, `CREATE EXTENSION`, index builds |

The pooled connection goes through PgBouncer in transaction mode, which cannot
hold session state, so schema work must not use it. Prepared statements are
also disabled on the pooled engine: psycopg3 begins using them after a few
executions, and behind PgBouncer a later connection inherits the name but not
the statement — producing `prepared statement "_pg3_0" already exists`
intermittently, under load, long after deployment.

### Identity: built-in or Neon Auth

`STUDYPILOT_AUTH_PROVIDER` selects between them.

| Value | Behaviour |
| --- | --- |
| `builtin` *(default)* | This application's own accounts: Argon2id passwords, session cookies. Needs `STUDYPILOT_JWT_SECRET`. |
| `neon` | Identity is delegated to Neon Auth. The browser signs in with `@neon/auth` and sends `Authorization: Bearer <jwt>`; this backend verifies the EdDSA signature against `NEON_AUTH_JWKS_URL`. No local signing secret is needed — Neon signs. |

Under `neon`, a Neon subject is mapped to a local `users` row keyed
`neon:<sub>` and created on first sight, so no separate provisioning step is
required. The desktop application is unaffected: it has no browser to run a
JavaScript auth client in and keeps its local profile.

Neon Auth tokens last 15 minutes, so a browser client must refresh them —
`@neon/auth` does this itself.

### The browser side of Neon Auth

Neon Auth is a TypeScript service, so the sign-in itself happens in the
browser; the Python backend only verifies the token that comes back. The
wiring is deliberately confined to the web host:

| File | Role |
| --- | --- |
| `apps/web/src/neon-auth.ts` | The only module that imports `@neon/auth`. It is loaded by dynamic `import()` so the client is a separate chunk and the desktop bundle never pulls it in. |
| `packages/shared/src/platform.ts` | Declares `neonAuth` and `getBearerToken()` structurally, so `packages/app` depends on the *shape* of the client and not on the package. |
| `packages/api-client/src/client.ts` | Calls `getBearerToken()` on **every** request. Tokens expire after 15 minutes, so caching one would start failing silently a quarter of an hour in. |
| `packages/app/src/hooks/useNeonAuth.ts` | Sign-in, sign-up and sign-out mutations. |

`SignInScreen` renders the same form for both providers and switches only the
mutation behind it, driven by `provider` in `GET /api/auth/status`.

The browser needs `VITE_NEON_AUTH_URL` at build time. `neon env pull` writes
it to the repository root, which is why `vite.base.ts` sets `envDir` to the
root — Vite otherwise looks only inside `apps/web/` and finds nothing, with no
warning.

#### Trusted domains

Neon Auth will only redirect to origins on its allowlist. Localhost ports are
pre-approved, so development works untouched, but **every deployed origin must
be registered before anyone signs in there** — otherwise sign-in fails with
`invalid domain`. Register the production alias and any preview origin you
hand to real users:

```bash
neon neon-auth domain add https://studypilot-swart-theta.vercel.app
```

```bash
neon neon-auth domain list
```

Note that Vercel also serves each deployment on a unique immutable URL; those
are not aliases and are not covered by registering the alias.

### Testing against Neon

Never point the integration tests at a branch holding real data: they tear
down with `DROP SCHEMA public CASCADE`. Use a throwaway branch, which is what
Neon branching is for:

```bash
neon branch create --name studypilot-ci --parent production
```

```bash
STUDYPILOT_TEST_DATABASE_URL="$(neon connection-string studypilot-ci)" npm run api:test
```

```bash
neon branch delete studypilot-ci
```

## 1. Provision the three services

You need accounts on these; each has a free tier that comfortably fits one
person's course material.

| Need | Suggested | Why |
| --- | --- | --- |
| PostgreSQL **with a vector extension** | [Neon](https://neon.tech) | Holds everything: rows, full-text index, *and* the vector index |
| Object storage | **Neon Object Storage** (same project), or [Cloudflare R2](https://developers.cloudflare.com/r2/) / Backblaze B2 / AWS S3 | Uploaded PDFs and rendered page previews |
| Container host | [Render](https://render.com), [Railway](https://railway.app), or [Fly.io](https://fly.io) | Runs `services/api/Dockerfile` |

Neon provides both the database and S3-compatible object storage from one
project, so this is two sign-ups rather than three. Its storage is available
in `aws-us-east-1`, `aws-us-east-2`, `aws-eu-central-1` and
`aws-ap-southeast-1` — create the project in one of those.

pgvector is why there is no separate search service: the vector index lives
inside the same database as everything else.

Providers differ in how they spell it, so the application probes rather than
assumes — Neon ships `lakebase_vector` (which installs pgvector) and indexes
with `lakebase_ann`; everywhere else ships `vector` and indexes with `hnsw`.
The query syntax is identical either way, and a provider with neither index
still works correctly, just by sequential scan.

It enables the extension itself at startup if the role is permitted. If the
log says it could not, run whichever your provider supports:

```sql
CREATE EXTENSION IF NOT EXISTS lakebase_vector CASCADE;  -- Neon
CREATE EXTENSION IF NOT EXISTS vector;                   -- elsewhere
```

## 2. Deploy the API

Point your host at this repository with `services/api/Dockerfile`. On Railway:

```bash
railway up --dockerfile services/api/Dockerfile
```

Set these environment variables — `.env.example` is the annotated reference:

```bash
STUDYPILOT_DEPLOYMENT_MODE=hosted
STUDYPILOT_JWT_SECRET=<openssl rand -base64 48>
STUDYPILOT_DATABASE_URL=<your postgres DSN>
STUDYPILOT_VECTOR_STORE=pgvector
STUDYPILOT_STORAGE_BACKEND=s3
STUDYPILOT_S3_BUCKET=<bucket>

# Neon prints these four with `neon env pull`; paste them as-is. The code
# reads the AWS-standard names, so nothing needs renaming.
AWS_ENDPOINT_URL_S3=<bucket endpoint>
AWS_REGION=<e.g. us-east-2, or "auto" for R2>
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
```

**The signing secret must be identical across every instance and must not
change** — rotating it signs everyone out.

The app refuses to start in hosted mode without a secret, and logs a specific
warning for each piece of state still on local disk. Check the startup logs: if
you see `durability_risk`, something will be lost on redeploy.

Verify:

```bash
curl https://your-api-host/healthz
```

## 3. Deploy the website

Point the website at your API. The committed `vercel.json` has no API rewrite,
so the site deploys cleanly before a backend exists; this adds it:

```bash
./scripts/set-api-host.sh your-api-host.up.railway.app
```

Commit the change — Vercel reads `vercel.json` from the repository at deploy
time, so it must be in the commit it builds.

Then import the GitHub repository at
[vercel.com/new](https://vercel.com/new). The settings in `vercel.json` are
picked up automatically; no framework preset is needed.

Or from the command line:

```bash
npx vercel --prod
```

## 4. Create your account and close registration

Open the site, create your account, then set:

```bash
STUDYPILOT_ALLOW_REGISTRATION=false
```

and redeploy the API. Otherwise anyone who finds the URL can sign up.

---

## Configuring a model

The default provider does **no generation**: it quotes passages from your own
material with citations and says so. Everything else — search, citations,
quizzes, flashcards, progress — works fully.

For real explanations, set one of these on the API:

```bash
STUDYPILOT_LLM_PROVIDER=anthropic   # plus ANTHROPIC_API_KEY
STUDYPILOT_LLM_PROVIDER=openai      # plus OPENAI_API_KEY
```

A cloud provider is still refused per request unless the user has turned on
cloud processing in Settings — the key alone does not start sending anything.

Retrieval quality is worth one more change. The default `hash` embedder needs
no download but matches wording rather than meaning:

```bash
STUDYPILOT_EMBEDDING_PROVIDER=model2vec
```

That needs the `local-models` extra in the image (~30 MB). **Changing the
embedding model invalidates the index** — the stored vectors have a different
width and meaning — so re-upload your documents afterwards.

---

## Security checklist

Verified by the test suite unless noted:

- [x] Hosted mode refuses to start without a ≥32-character signing secret
- [x] Hosted mode refuses to start with authentication disabled
- [x] Every data route requires a session; only `/healthz` is open
- [x] Passwords hashed with Argon2id, never logged
- [x] Sign-in reveals nothing about whether an address exists
- [x] Session cookie is HttpOnly, `SameSite=Lax`, and `Secure` over TLS
- [x] A password change or "sign out everywhere" revokes existing sessions
- [x] Accounts cannot see each other's courses, documents, pages or retrieval
- [x] Uploads validated by magic bytes and size before anything is stored
- [x] Document text is treated as untrusted; the agent graph exposes no tools
- [ ] **Close registration** once your account exists — your step
- [ ] **Set a bucket lifecycle/backup policy** — your step
- [ ] **Turn on database backups** — your step, and your provider's feature

## Verifying it worked

```bash
curl https://your-site/healthz
```

Then in the browser: create an account → create a course → upload
`fixtures/sample-lecture-cnn.pdf` → ask *"Why is padding necessary?"* → the
answer should cite page 4, and clicking the citation should jump there and
highlight the passage.

### Reading the startup log

The service checks its own configuration at boot and refuses to start in
hosted mode if the object store is unreachable, so a wrong bucket or a
read-only token shows up in the deploy log rather than on a student's first
upload. A healthy start looks like this:

```json
{"message": "runtime_ready", "vector_backend": "pgvector",
 "sparse_backend": "postgres-tsvector", "storage_backend": "s3",
 "database": "postgresql", "mode": "hosted"}
```

If any value is wrong, that is the problem:

| You see | What it means |
| --- | --- |
| `"database": "sqlite"` | `STUDYPILOT_DATABASE_URL` is not reaching the service. Everything will work until the next redeploy wipes it. |
| `"storage_backend": "local"` | `STUDYPILOT_STORAGE_BACKEND=s3` is not set. Uploads vanish on restart. |
| `"vector_backend": "numpy"` | The vector index is in memory only; search degrades after a restart. |
| `Cannot reach bucket '…'` | Wrong bucket name or endpoint, or the credentials cannot see it. |
| `reachable but not writable` | The R2 token is read-only. It needs Object Read **and** Write. |
| `durability_risk` | Something is still on the container's disk. Named in the message. |

### A note on Cloudflare R2 and boto3

boto3 1.36 began sending an `x-amz-checksum-crc32` header on every upload,
which R2 answers with `501 NotImplemented` — and the resulting error says
nothing about checksums. The client is configured with
`request_checksum_calculation="when_required"`, which restores the behaviour
AWS S3 itself had before 1.36, so uploads work against R2, B2 and MinIO at no
cost against real S3. Nothing to configure; it is noted here because the
symptom is otherwise baffling.

If the answer says it cannot find anything, retrieval is not reaching the
index — check the startup log for the vector and sparse backends:

## Running the hosted stack locally

Worth doing before deploying, and how the Postgres paths are tested:

```bash
docker run -d --name studypilot-pg -e POSTGRES_PASSWORD=studypilot -e POSTGRES_DB=studypilot -p 55432:5432 pgvector/pgvector:pg17
```

```bash
STUDYPILOT_TEST_DATABASE_URL=postgresql://postgres:studypilot@127.0.0.1:55432/studypilot npm run api:test
```

That runs 11 integration tests against real PostgreSQL covering `tsvector`
search, pgvector similarity, the full upload-to-quiz workflow, and isolation
between accounts. They skip cleanly when the variable is unset.
