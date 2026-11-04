# The hosted StudyPilot backend.
#
# Deliberately not a serverless function: ingestion renders PDF pages and
# builds an index, which wants a warm process and a few seconds of CPU rather
# than a cold start per request. Any container host (Railway, Render, Fly.io)
# runs this unchanged.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first, so a code change does not re-resolve the whole tree.
COPY services/api/pyproject.toml ./services/api/pyproject.toml
COPY services/api/app/__init__.py ./services/api/app/__init__.py
RUN pip install --no-cache-dir -e "./services/api[hosted]"

COPY services/api/app ./services/api/app

# Run as an unprivileged user; nothing here needs to write outside /tmp.
RUN useradd --create-home --uid 10001 studypilot \
    && chown -R studypilot:studypilot /srv
USER studypilot

ENV STUDYPILOT_DEPLOYMENT_MODE=hosted \
    STUDYPILOT_HOST=0.0.0.0 \
    STUDYPILOT_PORT=8000 \
    PYTHONPATH=/srv/services/api

EXPOSE 8000

# The platform's own health check should hit /healthz, which needs no auth.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT', os.environ.get('STUDYPILOT_PORT','8000'))+'/healthz', timeout=4).status==200 else 1)"

# Most hosts inject $PORT; fall back to the configured one when they do not.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-${STUDYPILOT_PORT:-8000}} --no-access-log"]
