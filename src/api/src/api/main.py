"""The request-serving application.

Composition only — every behaviour lives in a module the import contracts can
reason about, and nothing here reaches the model-provider gateway. That is
asserted rather than intended: `src/api/pyproject.toml` carries a contract
barring `api.routes` from `gateway`, so a provider dependency added here would
fail the build rather than surface as a slow page.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.routes.worklist import router as worklist_router

app = FastAPI(
    title="Procurement Risk Copilot — serving boundary",
    version="1.0.0",
)
app.include_router(worklist_router)
