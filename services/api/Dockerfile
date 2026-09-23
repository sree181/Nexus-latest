# syntax=docker/dockerfile:1.7
# Build context is the repository root. Every external base is pinned by digest;
# refreshes are an intentional supply-chain change, not an implicit rebuild.
FROM gcc:13.3.0-bookworm@sha256:1d71f0f3450214bef38fe09e6f610fb6cca90cf97b43f4ce845bfc32a4168818 AS native-build
WORKDIR /src/hypermesh_core
COPY services/engine/hypermesh_core/ ./
RUN make libhypermesh.so

FROM python:3.12.3-slim-bookworm@sha256:afc139a0a640942491ec481ad8dda10f2c5b753f5c969393b12480155fe15a63 AS wheels
WORKDIR /wheels
COPY services/api/requirements.lock.txt /requirements.lock.txt
RUN python -m pip wheel --disable-pip-version-check --no-cache-dir \
    --require-hashes --wheel-dir /wheels -r /requirements.lock.txt

FROM python:3.12.3-slim-bookworm@sha256:afc139a0a640942491ec481ad8dda10f2c5b753f5c969393b12480155fe15a63 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/engine \
    HOME=/tmp \
    LD_LIBRARY_PATH=/usr/local/lib
WORKDIR /app

RUN groupadd --system --gid 10001 meshagent \
    && useradd --system --uid 10001 --gid meshagent --home-dir /nonexistent --shell /usr/sbin/nologin meshagent \
    && mkdir -p /var/lib/meshagent \
    && chown meshagent:meshagent /var/lib/meshagent

COPY --from=wheels /wheels /wheels
COPY services/api/requirements.lock.txt /requirements.lock.txt
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index \
    --find-links=/wheels --require-hashes -r /requirements.lock.txt \
    && rm -rf /wheels /requirements.lock.txt

COPY --chown=meshagent:meshagent services/api/app ./app
COPY --chown=meshagent:meshagent services/engine ./engine
COPY --from=native-build --chown=meshagent:meshagent /src/hypermesh_core/libhypermesh.so /app/engine/hypermesh_core/libhypermesh.so

USER meshagent:meshagent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).close()"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
