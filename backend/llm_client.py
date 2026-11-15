"""
ContextLayer — LLM provider layer
=================================

Single entry point the rest of the backend uses for LLM calls.  Picks a
provider based on ``LLM_PROVIDER``:

``auto`` (default)  OpenAI-compatible server if ``OPENAI_BASE_URL`` is set and
                    reachable, otherwise Ollama if reachable, otherwise none.
``ollama``          Only Ollama.
``openai``          Only the OpenAI-compatible server.
``none``            Never call an LLM (rule-based only).

When no provider is reachable callers get ``None`` / ``""`` and fall back to
rule-based logic.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import ollama_client
import openai_client

logger = logging.getLogger("contextlayer.llm")

PROVIDERS = ("auto", "ollama", "openai", "none")


@dataclass
class ProviderStatus:
    provider: str = "none"          # provider that will be used: ollama | openai | none
    available: bool = False
    models: list[str] = field(default_factory=list)
    base_url: str = ""


def configured_provider() -> str:
    value = (os.getenv("LLM_PROVIDER") or "auto").strip().lower()
    if value not in PROVIDERS:
        logger.warning("Unknown LLM_PROVIDER=%r, using 'auto'.", value)
        return "auto"
    return value


async def _openai_status() -> ProviderStatus:
    if not openai_client.is_configured():
        return ProviderStatus("openai", False, [], "")
    models = await openai_client.get_available_models()
    available = bool(models) or await openai_client.is_available()
    return ProviderStatus("openai", available, models, openai_client.base_url())


async def _ollama_status() -> ProviderStatus:
    available = await ollama_client.is_available()
    models = await ollama_client.get_available_models() if available else []
    return ProviderStatus("ollama", available, models, ollama_client.base_url())


async def status_all() -> tuple[ProviderStatus, ProviderStatus]:
    """Probe the relevant providers once each, concurrently.

    Returns ``(chosen, ollama_status)``. Probing in parallel matters: on
    Windows a refused connection takes ~2 s, so sequential probes made
    /health slower than the popup's timeout.
    """
    choice = configured_provider()
    if choice == "none":
        return ProviderStatus(), ProviderStatus("ollama")

    async def ollama() -> ProviderStatus:
        if choice in ("auto", "ollama"):
            return await _ollama_status()
        return ProviderStatus("ollama")

    async def openai() -> ProviderStatus:
        if choice in ("auto", "openai"):
            return await _openai_status()
        return ProviderStatus("openai")

    oll, oai = await asyncio.gather(ollama(), openai())

    if choice == "openai":
        return oai, oll
    if choice == "ollama":
        return oll, oll
    # auto: an explicitly configured OpenAI-compatible server wins
    if oai.available:
        return oai, oll
    if oll.available:
        return oll, oll
    return ProviderStatus(), oll


async def resolve() -> ProviderStatus:
    """Return the provider that would be used right now."""
    chosen, _ollama = await status_all()
    return chosen


def max_prompt_tokens() -> int:
    try:
        return max(500, int(os.getenv("LLM_MAX_PROMPT_TOKENS", "6000")))
    except ValueError:
        return 6000


async def generate_json(prompt: str, system_prompt: str) -> tuple[str, str]:
    """Ask the active provider for a JSON answer.

    Returns ``(raw_text, provider_name)``; ``raw_text`` is ``""`` when no
    provider is available or the call failed.
    """
    status = await resolve()
    if not status.available:
        return "", "none"

    if status.provider == "openai":
        model = openai_client.pick_model(status.models)
        text = await openai_client.generate(
            prompt=prompt, system_prompt=system_prompt, model=model, json_mode=True
        )
        return text, "openai"

    model: Optional[str] = ollama_client.get_best_model("handoff", status.models)
    if model is None:
        return "", "none"
    text = await ollama_client.generate(
        prompt=prompt, system_prompt=system_prompt, model=model, json_mode=True
    )
    return text, "ollama"
