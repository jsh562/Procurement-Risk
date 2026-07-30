"""The request-serving application.

Composition only — every behaviour lives in a module the import contracts can
reason about, and nothing here reaches the model-provider gateway. That is
asserted rather than intended: `src/api/pyproject.toml` carries a contract
barring `api.routes` from `gateway`, so a provider dependency added here would
fail the build rather than surface as a slow page.
"""

from __future__ import annotations

import os
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

app = FastAPI(
    title="Procurement Risk Copilot — serving boundary",
    version="1.0.0",
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
