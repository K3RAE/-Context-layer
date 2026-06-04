"""
ContextLayer — Ollama Client
=============================

Thin async wrapper around the local Ollama HTTP API.  Every public function
is designed to fail gracefully: if Ollama is unreachable the caller gets a
clear ``False`` / ``None`` / empty-list instead of a crash.

Environment
-----------
OLLAMA_URL  –  Base URL for the Ollama server (default ``http://localhost:11434``).
"""

from __future__ import annotations

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger("contextlayer.ollama")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")

# Model priority lists keyed by logical task name.
_MODEL_PRIORITY: dict[str, list[str]] = {
    "compression": ["phi3:mini", "phi3:latest", "llama3.2:3b"],
    "handoff": ["llama3.2:3b", "phi3:mini", "phi3:latest"],
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


async def is_available() -> bool:
    """Check whether the Ollama server is reachable.

    Sends a lightweight GET to ``/api/tags`` with a 3-second timeout.
    Returns ``True`` if the server responds with HTTP 200, ``False`` otherwise.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        logger.debug("Ollama is not reachable at %s", OLLAMA_URL)
        return False


async def get_available_models() -> list[str]:
    """Return the list of model names currently loaded in Ollama.

    Parses the ``models[].name`` field from the ``/api/tags`` response.
    Returns an empty list if Ollama is unreachable or returns unexpected data.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            return [m["name"] for m in models if "name" in m]
    except Exception as exc:
        logger.warning("Failed to fetch Ollama models: %s", exc)
        return []


def get_best_model(task: str, available_models: list[str]) -> Optional[str]:
    """Pick the best locally-available model for a given task.

    Parameters
    ----------
    task:
        One of ``'compression'``, ``'handoff'``, or ``'memory'``.
    available_models:
        Model name strings reported by Ollama (e.g. ``['llama3.2:3b', …]``).

    Returns
    -------
    str | None
        The first matching model name from the priority list, or ``None`` if
        none of the preferred models are available locally.
    """
    try:
        priority = _MODEL_PRIORITY.get(task, _MODEL_PRIORITY["handoff"])

        # Normalise available model names for substring matching so that
        # a pulled model like "llama3.2:3b-instruct-q4_0" still matches the
        # priority entry "llama3.2:3b".
        for preferred in priority:
            for available in available_models:
                if preferred in available or available in preferred:
                    return available
    except httpx.ConnectError:
        return None
    except httpx.TimeoutException:
        return None
    except Exception:
        return None
    return None


async def generate(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
) -> str:
    """Generate a completion from Ollama.

    Parameters
    ----------
    prompt:
        The user / main prompt text.
    system_prompt:
        Optional system-level instruction prepended to the generation context.
    model:
        Specific model name to use.  If ``None`` the function will attempt to
        pick the best available model for a generic task.

    Returns
    -------
    str
        The generated text.  Returns an empty string on any failure so the
        caller can fall back to a rule-based approach.
    """
    if model is None:
        available = await get_available_models()
        model = get_best_model("handoff", available)
        if model is None:
            logger.warning("No suitable Ollama model found — returning empty response.")
            return ""

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if system_prompt:
        payload["system"] = system_prompt

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")
    except httpx.TimeoutException:
        logger.error("Ollama generation timed out (model=%s).", model)
        return ""
    except Exception as exc:
        logger.error("Ollama generation failed: %s", exc)
        return ""
