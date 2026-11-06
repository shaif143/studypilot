# Deploying StudyPilot

The website and the API deploy separately, because they want different things:
the frontend is static files on a CDN, while the API renders PDFs and builds a
search index and wants a warm process with a disk.

```text
                         browser
                        │        │
         Authorization: │        │  the page itself
         Bearer <jwt>   │        │
                        ▼        ▼
   ┌───────────────────────┐  ┌──────────────────────────┐
   │  Container host (API) │  │  Vercel (static site)    │
   │  Render / Railway /   │  │  apps/web, built to       │
   │  Fly.io               │  │  static assets            │
   └──────┬─────────┬──────┘  └──────────────────────────┘
          │         │
   PostgreSQL     S3-compatible        Neon Auth issues the token
   + pgvector     object storage       the browser presents above
```

**Two origins, joined by an allowlist.** The site and the API are served from
different hosts, so the API names the origins allowed to call it and the
browser enforces the rest. No cookie is ever sent cross-origin — the API
refuses credentials on every origin — so identity travels in an
`Authorization` header and there is no CSRF surface to defend.

The alternative is one origin, with Vercel rewriting `/api/*` to the API. That
is what the built-in cookie sessions need, and `scripts/set-api-host.sh` sets
it up. Section 3 compares the two.

## Where this deployment lives

| Piece | Where |
| --- | --- |
| Website | <https://studypilot-swart-theta.vercel.app> (Vercel) |
| API | <https://studypilot-api-rvmt.onrender.com> (Render, free plan) |
| Database, vector and full-text index | Neon, project `tiny-art-03416162` (`aws-eu-central-1`), branch `main` |
| Identity | Neon Auth on the same branch |
| Object storage | Neon Object Storage, bucket `studypilot-files`, on the same branch |

The Neon project is in Frankfurt rather than London because object storage
does not exist in `aws-eu-west-2`; it also puts the database in the same
region as the Render service. The original project, `restless-rain-56302666`,
is superseded.

Render appends a suffix when the service name is taken, which is where
`-rvmt` comes from; yours will differ.

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
project, so this can be two sign-ups rather than three — but only in the
regions where its storage exists: `aws-us-east-1`, `aws-us-east-2`,
`aws-eu-central-1` and `aws-ap-southeast-1`. Create the project in one of
those if you want both from Neon. A project in, say, `aws-eu-west-2` answers
`platform branchable-storage is not available in this region`, and then
object storage has to come from somewhere else.

Object storage is the one piece you can defer. With
`STUDYPILOT_STORAGE_BACKEND=local` the service runs and says so at boot, but
uploaded PDFs live on the container's own disk: the rows survive in
PostgreSQL and the files do not, so after a redeploy — or, on a free
instance, after it sleeps — the library still lists documents the reader can
no longer open. It is a reasonable way to try the thing out and not a way to
keep anything.

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

The API is a container. `render.yaml` is a Render Blueprint that provisions it
with the right build, health check and environment, and it is the path this
section walks through; any container host runs the same Dockerfile.

### On Render, from the blueprint

1. Push this repository to GitHub, if you have not already.
2. In Render, choose **New → Blueprint** and pick the repository. Render reads
   `render.yaml` and shows one service, `studypilot-api`.
3. Render then prompts for the six values the blueprint deliberately does not
   hard-code. Five of them come straight out of `neon env pull`:

   | Prompt | Where it comes from |
   | --- | --- |
   | `STUDYPILOT_DATABASE_URL` | `DATABASE_URL` — the pooled connection |
   | `STUDYPILOT_DATABASE_URL_DIRECT` | `DATABASE_URL_UNPOOLED` — **not optional**, see below |
   | `NEON_AUTH_BASE_URL` | the same name in the pulled env |
   | `NEON_AUTH_JWKS_URL` | the same name in the pulled env |
   | `STUDYPILOT_CORS_ORIGINS` | your website's origin(s), comma-separated |

   The direct URL is required because Neon's pooled connection is PgBouncer in
   transaction mode: `CREATE EXTENSION` and index builds hold session state a
   transaction pooler does not carry, so schema work runs on the direct
   connection instead. Leave it blank and the first boot fails while creating
   the vector index.

4. Deploy. The first build takes a few minutes; afterwards Render redeploys on
   every push to the default branch.

Then check the log, which is where a misconfiguration shows up:

```bash
curl https://studypilot-api-rvmt.onrender.com/healthz
```

**On the free plan the instance sleeps after 15 minutes of inactivity**, and
the next request waits roughly 30–60 seconds for it to wake. That is the
trade-off you chose; nothing is lost, but the first page load after a quiet
spell is slow.

### Does it fit in 512 MB?

Yes, measured rather than assumed — the free instance has 512 MB and
ingestion is the memory-hungry part, so this was checked by running the image
under `--memory=512m --cpus=0.5`:

| | Peak memory | Time |
| --- | --- | --- |
| Idle, after startup | 269 MB (53%) | — |
| 21.5 MB upload, 36 pages, 48 chunks | 352 MB (69%) | 6.6 s |
| 0.1 MB upload, 162 pages, 216 chunks | 349 MB (68%) | 9.3 s |

Nothing was killed and the container stayed healthy, so the 25 MB upload cap
in `render.yaml` is one the free instance can actually honour — with about
160 MB to spare. Raise `STUDYPILOT_MAX_UPLOAD_BYTES` only alongside the
instance size; the headroom above is what pays for a larger file.

### Any other container host

Point it at `services/api/Dockerfile` with the repository root as the build
context, and set the same variables. `.env.example` is the annotated
reference. On Railway:

```bash
railway up --dockerfile services/api/Dockerfile
```

## 3. Connect the website to the API

The website is on Vercel and the API is on Render, which are different
origins. There are two ways to join them, and they are not equivalent.

### Cross-origin, with an explicit allowlist (what this deployment uses)

The browser calls the API directly. `STUDYPILOT_CORS_ORIGINS` names the
origins that are allowed to do so — comma-separated, scheme included, no
trailing slash, and a wildcard is refused:

```bash
STUDYPILOT_CORS_ORIGINS=https://studypilot-swart-theta.vercel.app,https://studypilot-shaifs-projects.vercel.app
```

Then tell the website where the API is, as a Vercel environment variable for
the production environment, and redeploy:

```bash
VITE_API_BASE_URL=https://studypilot-api-rvmt.onrender.com
```

This works because identity travels in an `Authorization` header rather than
a cookie. The API never allows credentials on any origin, so no cookie is
ever sent cross-origin and there is no CSRF surface to defend — which is also
why **this arrangement requires Neon Auth**; the built-in cookie sessions
cannot work across origins. The service says so at boot if you configure both.

### Same-origin, through a Vercel rewrite

The alternative keeps one origin by having Vercel proxy `/api/*` to the API,
which is what the built-in cookie sessions need:

```bash
./scripts/set-api-host.sh studypilot-api-rvmt.onrender.com
```

Commit the change — Vercel reads `vercel.json` from the commit it builds —
and leave `VITE_API_BASE_URL` and `STUDYPILOT_CORS_ORIGINS` unset.

The catch is uploads. Every byte of a PDF then travels through Vercel's proxy,
and Vercel documents a 4.5 MB request body limit for its Functions; whether an
external rewrite shares that limit is not documented either way. This
application accepts uploads up to 25 MB, so the cross-origin arrangement above
was chosen instead, where the file goes straight to the API and the question
does not arise.

### Deploying the site

Import the GitHub repository at [vercel.com/new](https://vercel.com/new). The
settings in `vercel.json` are picked up automatically; no framework preset is
needed. Or from the command line:

```bash
npx vercel --prod
```

The browser build needs two variables, set in the Vercel project (Settings →
Environment Variables, Production) **before** the build that should pick them
up — Vite inlines them at build time, so changing one requires a redeploy:

| Variable | Value |
| --- | --- |
| `VITE_NEON_AUTH_URL` | the Auth URL from `neon env pull` |
| `VITE_API_BASE_URL` | your Render URL, for the cross-origin arrangement |

Finally, register the site's origins with Neon Auth, or sign-in fails with
`invalid domain`:

```bash
neon neon-auth domain add https://studypilot-swart-theta.vercel.app
```

Localhost is pre-approved for development — but by hostname. `http://localhost:5174`
is accepted and `http://127.0.0.1:5174` is not, which is worth knowing before
you spend an afternoon on it.

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

## Google Drive on a hosted site

The desktop build ships the app's own Google OAuth client when
`GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are in `.env.local`
at build time (a *Desktop app* client; create it once in Google Cloud
Console with the Drive API enabled), so users only press Connect; without
them, Settings asks for the client. A hosted site's client belongs to
whoever runs the site, so the operator provides it:

1. In Google Cloud Console, enable the **Google Drive API** and create an
   OAuth client of type **Web application**.
2. Add the authorised redirect URI
   `https://<your-api-host>/api/integrations/google/callback`, exactly.
3. On the API service set:

```bash
GOOGLE_OAUTH_CLIENT_ID=…
GOOGLE_OAUTH_CLIENT_SECRET=…
STUDYPILOT_PUBLIC_API_URL=https://<your-api-host>     # builds the redirect URI
STUDYPILOT_WEB_ORIGIN=https://<your-site>             # where the browser returns
STUDYPILOT_TOKEN_ENCRYPTION_KEY=<random, 32+ chars>   # encrypts stored tokens
```

`STUDYPILOT_WEB_ORIGIN` defaults to the first CORS origin, and the encryption
key to the JWT secret, so only the first two are strictly new. The scope is
`drive` (read, plus writing notes into a `StudyPilot Notes` subfolder); while the consent screen is in "Testing" each user must be
listed as a test user, and Google shows an "unverified app" notice until the
app is verified. Files are never copied to the server: only their search
index is stored, and the reader streams each file from Drive.

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
- [x] A hosted deployment grants no cross-origin access unless its environment
      names the origins; the development defaults never apply
- [x] `*` is refused as an allowed origin rather than quietly ignored
- [x] Credentials are never allowed on a cross-origin response, so no cookie
      is ever sent to another origin
- [ ] **Close registration** once your account exists — your step
- [ ] **Set a bucket lifecycle/backup policy** — your step
- [ ] **Turn on database backups** — your step, and your provider's feature

## Verifying it worked

Four checks against the API, in order of what they rule out:

```bash
curl https://studypilot-api-rvmt.onrender.com/healthz
```

```bash
curl https://studypilot-api-rvmt.onrender.com/api/auth/status
```

That second one must report `"provider":"neon"`. If it says `builtin`, the
service is running without `STUDYPILOT_AUTH_PROVIDER` and nobody holding a
Neon token will be able to sign in.

A protected route must refuse an anonymous caller:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://studypilot-api-rvmt.onrender.com/api/courses
```

And the allowlist must admit your site and nothing else. The first of these
returns `access-control-allow-origin`; the second must not:

```bash
curl -si -X OPTIONS https://studypilot-api-rvmt.onrender.com/api/courses -H 'Origin: https://studypilot-swart-theta.vercel.app' -H 'Access-Control-Request-Method: GET' | grep -i access-control-allow-origin
```

```bash
curl -si -X OPTIONS https://studypilot-api-rvmt.onrender.com/api/courses -H 'Origin: https://not-your-site.example' -H 'Access-Control-Request-Method: GET' | grep -i access-control-allow-origin
```

Then in the browser: sign in → create a course → upload
`fixtures/sample-lecture-cnn.pdf` → ask *"Why is padding necessary?"* → the
answer should cite page 4, and clicking the citation should jump there and
highlight the passage. The first request after a quiet spell waits for the
free instance to wake.

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
| `configuration_risk` | Something is still on the container's disk, or the identity provider cannot work with the origins configured. Named in the message. |

## Adding object storage

Until this is done, uploaded PDFs live on the container's own disk and a free
instance wipes it whenever it sleeps. This is the step that makes uploads
durable.

### Neon Object Storage (what this deployment uses)

It comes from the same project as the database, branches with it, and the
free plan includes 5 GB — so there is no second provider to sign up for and
no payment method to add. The catch is regional: it exists only in
`aws-eu-central-1`, `aws-us-east-1`, `aws-us-east-2` and
`aws-ap-southeast-1`. A project elsewhere answers

```text
ERROR: platform branchable-storage is not available in this region
```

and a bucket cannot be placed in a different region from its branch — it
belongs to the branch. If your project is in the wrong region the choice is
to recreate it in a supported one, or use an external provider.

Declare the bucket in `neon.ts` and deploy:

```bash
neon config add bucket studypilot-files
```

```bash
neon deploy
```

`neon deploy` writes `AWS_ENDPOINT_URL_S3`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`
and `AWS_SECRET_ACCESS_KEY` into `.env.local` — the access key is a
storage-scoped Neon credential, not an account-wide one. Set those four on
the API along with:

```bash
STUDYPILOT_STORAGE_BACKEND=s3
STUDYPILOT_S3_BUCKET=studypilot-files
```

### Cloudflare R2, or any other S3 provider

Create the bucket, then an API token scoped to **Object Read & Write on that
bucket alone**. The same six variables apply; the endpoint is
`https://<account-id>.r2.cloudflarestorage.com` and `AWS_REGION=auto`,
because R2 ignores the region but boto3 insists on having one.

Worth knowing before you choose: R2's free tier is larger (10 GB) but
Cloudflare requires a payment method on the account to enable R2 at all, and
usage is not capped, so going over the allowance is billable.

### Checking it worked

The service writes and deletes a probe object at startup and **refuses to
start if that fails**, so a wrong bucket, a mistyped endpoint or a read-only
token shows up in the deploy log rather than on your first upload. A correct
start says `"storage_backend": "s3"` on the `runtime_ready` line and emits no
`configuration_risk` warnings at all.

After one upload the bucket holds a content-addressed PDF under `documents/`
and one PNG per page under `previews/<document-id>/`. Deleting the document
through the application removes both.

### What happens to documents uploaded before the switch

Their rows stay in the database and their text stays in the index, so **they
remain searchable and the tutor still answers from them with citations** —
the chunks live in PostgreSQL, not in the blob store. What is gone is the
file itself: opening such a document in the reader returns

```json
{"detail": "The stored file is missing. Upload it again."}
```

with a 404, and the page previews are missing too. Upload the document again
and everything is whole; the old row can be deleted from the library.

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

### Running the real image before you deploy

The integration tests above run the code. They do not run the *artefact*, and
the two worst deployment bugs this project has had lived only in the artefact:
a dependency that was present in the development environment but never
declared for the image, and a client that failed open when the API was
missing. Both passed every test and both were obvious within a minute of
doing this:

```bash
docker build -f services/api/Dockerfile -t studypilot-api:check .
```

Write the environment Render will have into a file — the same variables the
blueprint prompts for — and run it on a spare port:

```bash
docker run --rm --env-file ./render.env -p 10000:10000 studypilot-api:check
```

Then read the startup log, and check the four things that cannot be tested
any other way:

```bash
curl -s http://127.0.0.1:10000/api/auth/status
```

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:10000/api/courses
```

A preflight from an origin on the allowlist must come back with a matching
`access-control-allow-origin`, and one from any other origin must not:

```bash
curl -s -D- -o /dev/null -X OPTIONS http://127.0.0.1:10000/api/courses -H 'Origin: https://not-listed.example' -H 'Access-Control-Request-Method: GET'
```

Finally drive it from a browser, which is the only thing that exercises the
real token: run the website with `VITE_API_BASE_URL=http://127.0.0.1:10000`
on a port listed in `STUDYPILOT_CORS_ORIGINS`, open it on `localhost` (not
`127.0.0.1`, which Neon Auth does not treat as localhost) and sign in. Delete
the environment file afterwards; it holds your database password.
