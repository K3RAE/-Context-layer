"""
ContextLayer — FastAPI Application
====================================

The main entry-point for the ContextLayer backend.  Exposes five endpoints:

- ``POST /compress``  — compress a conversation history
- ``POST /handoff``   — generate a structured context handoff
- ``POST /remember``  — extract and store knowledge from a conversation
- ``POST /recall``    — search stored knowledge
- ``DELETE /memory``  — wipe all stored knowledge
- ``GET  /health``    — subsystem health check

Run with::

    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
import os
import time
import traceback

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Environment & logging
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("contextlayer")

# ---------------------------------------------------------------------------
# Lazy imports — each subsystem might be unavailable and that's fine.
# ---------------------------------------------------------------------------

_compressor_available = False
_handoff_available = False
_memory_available = False
_ollama_module_available = False

try:
    import compressor  # noqa: F401

    _compressor_available = True
except Exception as exc:
    logger.warning("Compressor module unavailable: %s", exc)

try:
    import handoff  # noqa: F401

    _handoff_available = True
except Exception as exc:
    logger.warning("Handoff module unavailable: %s", exc)

try:
    import memory  # noqa: F401

    _memory_available = True
except Exception as exc:
    logger.warning("Memory module unavailable: %s", exc)

try:
    import ollama_client  # noqa: F401

    _ollama_module_available = True
except Exception as exc:
    logger.warning("Ollama client module unavailable: %s", exc)

# Import Pydantic models (always expected to be present).
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

app = FastAPI(
    title="ContextLayer API",
    description="Local-first context compression, handoff, and memory for LLM conversations.",
    version="1.0.0",
)

# CORS — allow Chrome extensions and localhost dev servers.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"(chrome-extension://.*|http://localhost:.*)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch any unhandled exception and return a structured JSON error.

    This guarantees the API never crashes with an opaque 500 HTML page.
    """
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "detail": traceback.format_exc(),
        },
    )


# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _startup() -> None:
    """Run once when the server starts: initialise subsystems and log status."""
    if _memory_available:
        try:
            memory.init_db()
            logger.info("✅  Memory DB initialised.")
        except Exception as exc:
            logger.error("❌  Memory DB init failed: %s", exc)

    logger.info("─" * 50)
    logger.info("ContextLayer backend starting up")
    logger.info("  Compressor : %s", "✅" if _compressor_available else "❌")
    logger.info("  Handoff    : %s", "✅" if _handoff_available else "❌")
    logger.info("  Memory     : %s", "✅" if _memory_available else "❌")
    logger.info("  Ollama mod : %s", "✅" if _ollama_module_available else "❌")
    logger.info("─" * 50)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/compress", response_model=CompressResponse)
async def compress_endpoint(req: CompressRequest) -> CompressResponse:
    """Compress a conversation's older messages to reduce token count.

    Preserves the most recent ``preserve_last_n`` messages verbatim and
    compresses the rest using LLMLingua-2 (preferred) or rule-based
    heuristics (fallback).
    """
    if not _compressor_available:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"error": "Compressor subsystem is not available."},
        )

    result = compressor.compress_conversation(
        messages=req.messages,
        preserve_last_n=req.preserve_last_n,
    )
    return CompressResponse(**result)


@app.post("/handoff", response_model=HandoffResponse)
async def handoff_endpoint(req: HandoffRequest) -> HandoffResponse:
    """Generate a structured context handoff from a conversation.

    Uses Ollama for intelligent extraction when available, otherwise falls
    back to deterministic heuristics.
    """
    if not _handoff_available:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"error": "Handoff subsystem is not available."},
        )

    result = await handoff.generate_handoff(
        messages=req.messages,
        session_id=req.session_id,
    )
    return HandoffResponse(**result)


@app.post("/remember", response_model=RememberResponse)
async def remember_endpoint(req: RememberRequest) -> RememberResponse:
    """Extract knowledge from a conversation and store it in the memory DB.

    Entities, relations, and targets are persisted as fact triples so they
    can be recalled later via ``/recall``.
    """
    if not _memory_available:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"error": "Memory subsystem is not available."},
        )

    result = await memory.store_session(
        session_id=req.session_id,
        messages=req.messages,
        source_llm=req.source_llm,
    )
    return RememberResponse(**result)


@app.post("/recall", response_model=RecallResponse)
async def recall_endpoint(req: RecallRequest) -> RecallResponse:
    """Search stored knowledge for facts relevant to a query.

    Returns both a human-readable summary and the raw fact triples.
    """
    if not _memory_available:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"error": "Memory subsystem is not available."},
        )

    result = await memory.recall(
        query=req.query,
        session_id=req.session_id,
        top_k=req.top_k,
    )
    return RecallResponse(**result)


@app.delete("/memory", response_model=DeleteMemoryResponse)
async def delete_memory_endpoint() -> DeleteMemoryResponse:
    """Wipe all stored knowledge — drops and recreates the fact tables."""
    if not _memory_available:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"error": "Memory subsystem is not available."},
        )

    result = memory.clear_memory()
    return DeleteMemoryResponse(**result)


@app.get("/health", response_model=HealthResponse)
async def health_endpoint() -> HealthResponse:
    """Return the health status of every subsystem.

    Checks Ollama reachability, LLMLingua availability, memory DB status,
    and reports server uptime.
    """
    ollama_ok = False
    ollama_models: list[str] = []

    if _ollama_module_available:
        try:
            ollama_ok = await ollama_client.is_available()
            if ollama_ok:
                ollama_models = await ollama_client.get_available_models()
        except Exception:
            pass

    llmlingua_ok = False
    if _compressor_available:
        try:
            llmlingua_ok = getattr(compressor, "LLMLINGUA_AVAILABLE", False)
        except Exception:
            pass

    memory_db_path = ""
    if _memory_available:
        memory_db_path = str(memory._resolve_db_path())

    all_ok = ollama_ok or _compressor_available or _memory_available
    status = "ok" if all_ok else "degraded"

    return HealthResponse(
        status=status,
        ollama=ollama_ok,
        ollama_models=ollama_models,
        llmlingua=llmlingua_ok,
        graphiti=False,
        memory_db=memory_db_path,
        uptime_seconds=round(time.time() - _STARTUP_TIME, 2),
    )
