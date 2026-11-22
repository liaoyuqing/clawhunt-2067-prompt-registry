"""FastAPI application assembly.

```
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The process starts even without DATABASE_URL: importing the module must never
need configuration, so the deployment can report its own misconfiguration
through /healthz and a 503 on the data endpoints instead of crashing on import.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config
from app.api_eval import router as eval_router
from app.db import reset_engine


@asynccontextmanager
async def lifespan(application: FastAPI):
    yield
    reset_engine()


def create_app() -> FastAPI:
    """Build the application; no database connection is made here."""
    application = FastAPI(
        title="AI evaluation registry",
        description=(
            "Prompt version registry and model profile registry with evaluation "
            "traceability. All endpoints are org scoped via the X-Org-Id header."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(eval_router)

    @application.get("/healthz", tags=["ops"], summary="Liveness and configuration")
    def healthz() -> dict[str, object]:
        """Report whether a database is configured; never connects to it."""
        configured = config.database_url(required=False) is not None
        return {
            "status": "ok",
            "database_configured": configured,
            "default_org_id": str(config.org_id()),
        }

    return application


app = create_app()
