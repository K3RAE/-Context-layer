"""
ContextLayer — FastAPI Application
====================================

The main entry-point for the ContextLayer backend.  Exposes:

- ``POST   /compress``  — compress a conversation history
- ``POST   /handoff``   — generate a structured context handoff
- ``POST   /remember``  — extract and store knowledge from a conversation
- ``POST   /recall``    — search stored knowledge
- ``DELETE /memory``    — wipe all stored knowledge
- ``GET    /health``    — subsystem health check

Run with (from the ``backend`` directory)::

    python main.py
    # or
    uvicorn main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Make sibling modules importable no matter where the app is launched from
# (``uvicorn main:app`` inside backend/, ``uvicorn backend.main:app`` from the
# repo root, or the test-suite).
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

# ---------------------------------------------------------------------------
# Environment & logging
# ---------------------------------------------------------------------------

# Load backend/.env explicitly so the working directory does not matter.
# Real environment variables take precedence over the file.
load_dotenv(_BACKEND_DIR / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("contextlayer")

# ---------------------------------------------------------------------------
# Optional subsystems — each might be unavailable and that's fine.
# ---------------------------------------------------------------------------

_compressor_available = False
_handoff_available = False
_memory_available = False
_ollama_module_available = False

try:
    import compressor

    _compressor_available = True
except Exception as exc:  # pragma: no cover - depends on environment
    logger.warning("Compressor module unavailable: %s", exc)

try:
    import handoff

    _handoff_available = True
except Exception as exc:  # pragma: no cover
    logger.warning("Handoff module unavailable: %s", exc)

try:
    import memory

    _memory_available = True
except Exception as exc:  # pragma: no cover
    logger.warning("Memory module unavailable: %s", exc)

try:
    import ollama_client

    _ollama_module_available = True
except Exception as exc:  # pragma: no cover
    logger.warning("Ollama client module unavailable: %s", exc)

from models import (  # noqa: E402
    CompressRequest,
    CompressResponse,
    DeleteMemoryResponse,
    HandoffRequest,
    HandoffResponse,
    HealthResponse,
    RecallRequest,
    RecallResponse,
    RememberRequest,
    RememberResponse,
)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

_STARTUP_TIME = time.time()
__version__ = "1.1.0"


def _mark(ok: bool) -> str:
    return "OK" if ok else "unavailable"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialise subsystems once at startup and log their status."""
    global _memory_available
    if _memory_available:
        try:
            memory.init_db()
            logger.info("Memory DB ready at %s", memory._resolve_db_path())
        except Exception as exc:
            logger.error("Memory DB init failed: %s", exc)
            _memory_available = False

    logger.info("-" * 50)
    logger.info("ContextLayer backend v%s starting up", __version__)
    logger.info("  Compressor : %s", _mark(_compressor_available))
    logger.info(
        "  LLMLingua  : %s",
        _mark(_compressor_available and getattr(compressor, "LLMLINGUA_AVAILABLE", False)),
    )
    logger.info("  Handoff    : %s", _mark(_handoff_available))
    logger.info("  Memory     : %s", _mark(_memory_available))
    logger.info("  Ollama     : %s", ollama_client.base_url() if _ollama_module_available else "n/a")
    logger.info("-" * 50)
    yield


app = FastAPI(
    title="ContextLayer API",
    description="Local-first context compression, handoff, and memory for LLM conversations.",
    version=__version__,
    lifespan=lifespan,
)

# CORS — allow the Chrome extension and localhost dev pages only.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://[a-p]{32}|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return structured JSON instead of an opaque 500 page (no stack traces)."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


def _unavailable(name: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": f"{name} subsystem is not available."})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/compress", response_model=CompressResponse)
async def compress_endpoint(req: CompressRequest):
    """Compress a conversation's older messages to reduce token count.

    Preserves the most recent ``preserve_last_n`` messages verbatim and
    compresses the rest using LLMLingua-2 (if installed) or rule-based
    heuristics.  Runs in a worker thread because LLMLingua is CPU-bound.
    """
    if not _compressor_available:
        return _unavailable("Compressor")

    result = await run_in_threadpool(
        compressor.compress_conversation,
        messages=req.messages,
        preserve_last_n=req.preserve_last_n,
    )
    return CompressResponse(**result)


@app.post("/handoff", response_model=HandoffResponse)
async def handoff_endpoint(req: HandoffRequest):
    """Generate a structured context handoff from a conversation."""
    if not _handoff_available:
        return _unavailable("Handoff")

    result = await handoff.generate_handoff(messages=req.messages, session_id=req.session_id)
    return HandoffResponse(**result)


@app.post("/remember", response_model=RememberResponse)
async def remember_endpoint(req: RememberRequest):
    """Extract knowledge from a conversation and store it in the memory DB."""
    if not _memory_available:
        return _unavailable("Memory")

    result = await run_in_threadpool(
        memory.store_session,
        session_id=req.session_id,
        messages=req.messages,
        source_llm=req.source_llm,
    )
    return RememberResponse(**result)


@app.post("/recall", response_model=RecallResponse)
async def recall_endpoint(req: RecallRequest):
    """Search stored knowledge for facts relevant to a query."""
    if not _memory_available:
        return _unavailable("Memory")

    result = await run_in_threadpool(
        memory.recall,
        query=req.query,
        top_k=req.top_k,
        session_id=req.session_id,
    )
    return RecallResponse(**result)


@app.delete("/memory", response_model=DeleteMemoryResponse)
async def delete_memory_endpoint():
    """Wipe all stored knowledge — drops and recreates the fact tables."""
    if not _memory_available:
        return _unavailable("Memory")

    result = await run_in_threadpool(memory.delete_memory)
    return DeleteMemoryResponse(deleted=True, message=result.get("status", "memory cleared"))


@app.get("/health", response_model=HealthResponse)
async def health_endpoint():
    """Return the health status of every subsystem."""
    ollama_ok = False
    ollama_models: list[str] = []

    if _ollama_module_available:
        try:
            ollama_ok = await ollama_client.is_available()
            if ollama_ok:
                ollama_models = await ollama_client.get_available_models()
        except Exception:
            pass

    llmlingua_ok = bool(_compressor_available and getattr(compressor, "LLMLINGUA_AVAILABLE", False))
    memory_db_path = str(memory._resolve_db_path()) if _memory_available else ""

    core_ok = _compressor_available and _handoff_available and _memory_available
    return HealthResponse(
        status="ok" if core_ok else "degraded",
        ollama=ollama_ok,
        ollama_models=ollama_models,
        llmlingua=llmlingua_ok,
        graphiti=False,
        memory_db=memory_db_path,
        uptime_seconds=round(time.time() - _STARTUP_TIME, 2),
    )


# ---------------------------------------------------------------------------
# ``python main.py`` entry point
# ---------------------------------------------------------------------------


def run() -> None:
    import uvicorn

    host = os.getenv("BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    reload = os.getenv("BACKEND_RELOAD", "").lower() in ("1", "true", "yes")
    logger.info("Serving on http://%s:%s", host, port)
    if reload:
        uvicorn.run("main:app", host=host, port=port, reload=True, app_dir=str(_BACKEND_DIR))
    else:
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
