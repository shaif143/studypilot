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

## 1. Provision the three services

You need accounts on these; each has a free tier that comfortably fits one
person's course material.

| Need | Suggested | Why |
| --- | --- | --- |
| PostgreSQL **with pgvector** | [Neon](https://neon.tech), Supabase, or Railway Postgres | Holds everything: rows, full-text index, *and* the vector index |
| Object storage | [Cloudflare R2](https://developers.cloudflare.com/r2/), Backblaze B2, or AWS S3 | Uploaded PDFs and rendered page previews |
| Container host | [Railway](https://railway.app), [Render](https://render.com), or [Fly.io](https://fly.io) | Runs `services/api/Dockerfile` |

pgvector is why there is no fourth service. The vector index lives inside the
same Postgres database as everything else, so there is no separate search
cluster to provision, pay for or keep in sync. Enable it once:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

(The application does this itself at startup if the role is permitted to.)

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
STUDYPILOT_S3_ENDPOINT_URL=<for R2/B2; omit for AWS>
STUDYPILOT_S3_REGION=auto
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

If the answer says it cannot find anything, retrieval is not reaching the
index — check the startup logs for the vector and sparse backends:

```json
{"message": "runtime_ready", "vector_backend": "pgvector",
 "sparse_backend": "postgres-tsvector", "storage_backend": "s3",
 "database": "postgresql", "mode": "hosted"}
```

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
