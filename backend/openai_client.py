"""
ContextLayer — OpenAI-compatible client
=======================================

Talks to any server that implements the OpenAI ``/v1/models`` and
``/v1/chat/completions`` endpoints, for example:

* LM Studio          http://localhost:1234/v1
* llama.cpp server   http://localhost:8080/v1
* Jan                http://localhost:1337/v1
* LocalAI / vLLM     http://localhost:8000/v1   (pick a different BACKEND_PORT)
* Ollama's OpenAI layer  http://localhost:11434/v1

It is only used when ``OPENAI_BASE_URL`` is set — nothing is probed
automatically.  Like ``ollama_client`` every function fails soft.

Environment
-----------
OPENAI_BASE_URL   Base URL of the server (``/v1`` is appended if missing).
OPENAI_API_KEY    Optional bearer token (most local servers ignore it).
OPENAI_MODEL      Optional model id; defaults to the first one the server lists.
LLM_TIMEOUT       Generation timeout in seconds (default 120).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("contextlayer.openai")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
_warned_remote = False


def normalize_base_url(url: str) -> str:
    """Turn user input into a ``.../v1``-style base URL.

    ``http://host:1234``                    -> ``http://host:1234/v1``
    ``http://host:1234/``                   -> ``http://host:1234/v1``
    ``http://host:1234/v1/``                -> ``http://host:1234/v1``
    ``http://host/api/v1/chat/completions`` -> ``http://host/api/v1``
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    url = re.sub(r"/(chat/completions|completions|models)$", "", url)
    if not re.search(r"/v\d+(beta)?$", url):
        url += "/v1"
    return url


def base_url() -> str:
    return normalize_base_url(os.getenv("OPENAI_BASE_URL", ""))


def is_configured() -> bool:
    return bool(base_url())


def is_local(url: Optional[str] = None) -> bool:
    host = (urlparse(url or base_url()).hostname or "").lower()
    return host in _LOCAL_HOSTS or host.endswith(".local") or host.endswith(".localhost")


def _headers() -> dict:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _warn_if_remote() -> None:
    global _warned_remote
    if not _warned_remote and is_configured() and not is_local():
        logger.warning(
            "OPENAI_BASE_URL points to a non-local host (%s): conversations will be "
            "sent off this machine.",
            urlparse(base_url()).hostname,
        )
        _warned_remote = True


async def get_available_models() -> list[str]:
    """Model ids from ``GET /models`` (empty list on any failure)."""
    if not is_configured():
        return []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url()}/models", headers=_headers())
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", []) if isinstance(data, dict) else data
            return [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
    except Exception as exc:
        logger.debug("OpenAI-compatible /models failed: %s", exc)
        return []


async def is_available() -> bool:
    if not is_configured():
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url()}/models", headers=_headers())
            return resp.status_code == 200
    except Exception:
        return False


def pick_model(available: list[str]) -> Optional[str]:
    forced = (os.getenv("OPENAI_MODEL") or "").strip()
    if forced:
        return forced  # servers may serve models they do not list
    return available[0] if available else None


async def generate(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    json_mode: bool = False,
) -> str:
    """Chat completion; returns the assistant text or ``""`` on failure."""
    if not is_configured():
        return ""
    _warn_if_remote()

    if model is None:
        model = pick_model(await get_available_models())
        if model is None:
            logger.warning("OpenAI-compatible server lists no models and OPENAI_MODEL is unset.")
            return ""

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}

    timeout = float(os.getenv("LLM_TIMEOUT") or os.getenv("OLLAMA_TIMEOUT") or 120)
    url = f"{base_url()}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = None
            if json_mode:
                # Not every server supports response_format; retry without it on 4xx.
                resp = await client.post(
                    url, headers=_headers(), json={**payload, "response_format": {"type": "json_object"}}
                )
                if 400 <= resp.status_code < 500:
                    resp = None
            if resp is None:
                resp = await client.post(url, headers=_headers(), json=payload)
            resp.raise_for_status()
            choices = resp.json().get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            return message.get("content") or choices[0].get("text") or ""
    except httpx.TimeoutException:
        logger.error("OpenAI-compatible generation timed out after %ss (model=%s).", timeout, model)
        return ""
    except Exception as exc:
        logger.error("OpenAI-compatible generation failed: %s", exc)
        return ""
