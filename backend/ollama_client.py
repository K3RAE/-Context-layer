"""
ContextLayer — Ollama Client
=============================

Thin async wrapper around the local Ollama HTTP API.  Every public function
is designed to fail gracefully: if Ollama is unreachable the caller gets a
clear ``False`` / ``None`` / empty-list instead of a crash.

Environment
-----------
OLLAMA_URL      Base URL for the Ollama server (default ``http://localhost:11434``).
OLLAMA_MODEL    Optional: force a specific installed model.
OLLAMA_TIMEOUT  Optional: generation timeout in seconds (default 120).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("contextlayer.ollama")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_URL = "http://localhost:11434"


def base_url() -> str:
    """Ollama base URL, read at call time so ``.env`` values are honoured."""
    return (os.getenv("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")


# Model priority lists keyed by logical task name.  Matching is by prefix, so
# "llama3.2:3b" also matches "llama3.2:3b-instruct-q4_0".
_MODEL_PRIORITY: dict[str, list[str]] = {
    "compression": ["qwen2.5:0.5b", "qwen2:0.5b", "phi3:mini", "llama3.2:3b"],
    "handoff": [
        "llama3.2:3b",
        "llama3.1:8b",
        "qwen2.5:3b",
        "phi3:mini",
        "qwen2.5:0.5b",
        "qwen2:0.5b",
    ],
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


async def is_available() -> bool:
    """Return ``True`` if the Ollama server answers ``/api/tags`` within 3s."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url()}/api/tags")
            return resp.status_code == 200
    except Exception:
        logger.debug("Ollama is not reachable at %s", base_url())
        return False


async def get_available_models() -> list[str]:
    """Return the names of models installed in Ollama (empty list on failure)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url()}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [m["name"] for m in models if isinstance(m, dict) and "name" in m]
    except Exception as exc:
        logger.debug("Failed to fetch Ollama models: %s", exc)
        return []


def get_best_model(task: str, available_models: list[str]) -> Optional[str]:
    """Pick the best locally-installed model for ``task``.

    Returns the ``OLLAMA_MODEL`` override if set and installed, otherwise the
    first match from the priority list, otherwise the first installed model.
    Returns ``None`` only when no models are installed.
    """
    if not available_models:
        return None

    forced = (os.getenv("OLLAMA_MODEL") or "").strip()
    if forced:
        for available in available_models:
            if available == forced or available.startswith(forced):
                return available
        logger.warning("OLLAMA_MODEL=%s is not installed; picking automatically.", forced)

    priority = _MODEL_PRIORITY.get(task, _MODEL_PRIORITY["handoff"])
    for preferred in priority:
        for available in available_models:
            if available == preferred or available.startswith(preferred):
                return available
    return available_models[0]


async def generate(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    json_mode: bool = False,
) -> str:
    """Generate a completion from Ollama.

    Returns the generated text, or an empty string on any failure so the
    caller can fall back to a rule-based approach.
    """
    if model is None:
        model = get_best_model("handoff", await get_available_models())
        if model is None:
            logger.warning("No Ollama models installed; returning empty response.")
            return ""

    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if system_prompt:
        payload["system"] = system_prompt
    if json_mode:
        payload["format"] = "json"

    timeout = float(os.getenv("OLLAMA_TIMEOUT") or 120)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url()}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")
    except httpx.TimeoutException:
        logger.error("Ollama generation timed out after %ss (model=%s).", timeout, model)
        return ""
    except Exception as exc:
        logger.error("Ollama generation failed: %s", exc)
        return ""
