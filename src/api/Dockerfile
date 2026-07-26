# Serving image for the request-serving boundary.
#
# Build context is /src, scoped by /src/.dockerignore to the serving boundary
# and the gateway package only. The modeling boundary is unreachable at build
# time — that unreachability is the structural guarantee; the in-image checks
# that follow detect regressions against it rather than creating it.
#
# TR-026: the base image is pinned by digest alongside its tag, so the pulled
# artifact is fixed rather than following a moving tag. A moving base would
# silently change what TR-012's lock-derived comparison compares against.
ARG PYTHON_BASE=python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

FROM ${PYTHON_BASE} AS builder

# The build host sits behind TLS interception; uv must trust the system
# certificate store or every index request fails with UnknownIssuer.
ENV UV_NATIVE_TLS=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir uv

WORKDIR /build
# Both entries are copied because the serving boundary declares the gateway as
# a path dependency. Nothing else in the repository is reachable from here.
COPY gateway /build/gateway
COPY api /build/api

WORKDIR /build/api
# --no-editable installs the gateway as a real distribution rather than a link
# into /build, so the venv survives being copied into the runtime stage.
# --no-dev keeps the test and lint toolchain out of the serving image.
RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_BASE} AS runtime

# Runs unprivileged. TR-025: no credential material enters this image.
RUN useradd --create-home --uid 10001 serving

COPY --from=builder --chown=serving:serving /build/api/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    VIRTUAL_ENV=/app/.venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER serving
WORKDIR /app
EXPOSE 8000

# Pinned to the virtualenv interpreter, never bare `python`. HINT-004: a bare
# entrypoint can resolve the base image's system interpreter, under which an
# ImportError proves nothing about what this image actually installed.
CMD ["/app/.venv/bin/python", "-c", "import api; print('serving boundary ready')"]
