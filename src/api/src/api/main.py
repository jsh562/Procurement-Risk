"""The request-serving application.

Composition only — every behaviour lives in a module the import contracts can
reason about, and nothing here reaches the model-provider gateway. That is
asserted rather than intended: `src/api/pyproject.toml` carries a contract
barring `api.routes` from `gateway`, so a provider dependency added here would
fail the build rather than surface as a slow page.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.retrieval.routes import router as retrieval_router
from api.routes.worklist import router as worklist_router

#: The interface tier's origin, which is a *different* origin from this one:
#: FR-024 puts the two in separate tiers, so every client-side re-query an
#: adjustment triggers (FR-031) is cross-origin and the browser blocks it
#: without these headers. The server-rendered first paint is unaffected, which
#: is why the gap is invisible until someone adjusts a date — an end-to-end run
#: found it, and no unit test could have.
#:
#: An explicit allowlist rather than `*`. There is no authentication and no
#: cookie to protect (FR-056), so a wildcard would leak nothing today — but it
#: would be a standing invitation for the first credential this system gains to
#: be readable by any page on the internet, and the reversal condition FR-056
#: records is exactly that one.
ALLOWED_ORIGINS: Final[list[str]] = [
    origin.strip()
    for origin in os.environ.get(
        "WORKLIST_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    """Warm the inference sessions before the application receives requests.

    FR-017. The lifespan hook runs everything before its yield point before
    traffic arrives, which is a stronger gate than a readiness endpoint: there
    is no window in which a request meets an unwarmed session.

    **A reranker failure is caught and still yields** (FR-021). Raising here
    would report not-ready, and an orchestrator would restart the container in a
    loop over a fault restarting cannot fix, while a working fusion-only service
    sat unused. The encoder is different and is allowed to leave the process
    not-ready: without a query embedding the dense arm cannot run at all.
    """
    from api.config import load_retrieval_config
    from api.retrieval.readiness import (
        encoder_directory,
        readiness,
        reranker_directory,
        warm_rerankers,
    )

    config = load_retrieval_config()
    try:
        from gateway.inference.session import load_encoder

        load_encoder(
            encoder_directory(),
            intra_op_threads=config.intra_op_threads,
            inter_op_threads=config.inter_op_threads,
        )
        readiness.encoder_ready = True
    except Exception:  # noqa: BLE001 - reported as not-ready rather than raised
        readiness.encoder_ready = False
    warm_rerankers(
        reranker_directory(),
        intra_op_threads=config.intra_op_threads,
        inter_op_threads=config.inter_op_threads,
    )
    yield


app = FastAPI(
    title="Procurement Risk Copilot — serving boundary",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # GET only. The worklist has no write path (FR-031), so permitting more
    # would advertise methods the route table does not answer.
    allow_methods=["GET"],
    allow_headers=["If-None-Match"],
    # `ETag` is not a simple response header, so a browser cannot read it unless
    # it is exposed — and without it FR-020a's validator never reaches the
    # client that would send it back.
    expose_headers=["ETag"],
)

app.include_router(worklist_router)
app.include_router(retrieval_router)
