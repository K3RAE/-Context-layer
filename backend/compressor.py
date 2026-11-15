"""
ContextLayer — Conversation Compressor

Provides LLMLingua-2 compression with a rule-based fallback.
Token counting via tiktoken (cl100k_base).
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("contextlayer.compressor")

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENCODER = None

# Probe whether llmlingua is installed (used by /health).  find_spec avoids
# importing torch at startup; the real import happens lazily on first use.
def _llmlingua_enabled() -> bool:
    return os.getenv("LLMLINGUA_ENABLED", "true").strip().lower() not in ("0", "false", "no")


try:
    import importlib.util

    LLMLINGUA_AVAILABLE = _llmlingua_enabled() and importlib.util.find_spec("llmlingua") is not None
except Exception:
    LLMLINGUA_AVAILABLE = False


def count_tokens(text: str) -> int:
    """Return the number of tokens in *text* using cl100k_base.

    Falls back to a rough word-based heuristic if tiktoken is unavailable.
    """
    if _ENCODER is not None:
        return len(_ENCODER.encode(text))
    # rough fallback: ~4 chars per token
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# LLMLingua-2 compression (optional heavy dependency)
# ---------------------------------------------------------------------------

_llmlingua_compressor = None
_llmlingua_failed = False


def _get_llmlingua():
    global _llmlingua_compressor, _llmlingua_failed
    if _llmlingua_compressor is not None:
        return _llmlingua_compressor
    if _llmlingua_failed:
        return None
    try:
        from llmlingua import PromptCompressor

        model_name = os.getenv(
            "LLMLINGUA_MODEL",
            "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
        )
        logger.warning(
            "LLMLingua-2: Downloading model (~700MB) on first run… this may take a few minutes."
        )
        _llmlingua_compressor = PromptCompressor(
            model_name=model_name,
            use_llmlingua2=True,
            device_map="cpu",
        )
        logger.info("LLMLingua-2 model loaded successfully.")
        return _llmlingua_compressor
    except Exception as exc:
        logger.warning("LLMLingua-2 init failed, using rule-based compression: %s", exc)
        _llmlingua_failed = True
        return None


def _compress_with_llmlingua(text: str, rate: float = 0.3) -> str:
    """Compress *text* with LLMLingua-2. Returns the compressed string."""
    compressor = _get_llmlingua()
    if compressor is None:
        raise RuntimeError("LLMLingua-2 is not available")
    result = compressor.compress_prompt(
        [text],
        rate=rate,
        force_tokens=["\n", ".", "!", "?", ","],
    )
    return result["compressed_prompt"]


# ---------------------------------------------------------------------------
# Rule-based fallback compression
# ---------------------------------------------------------------------------


def rule_based_compress(
    messages: List[Dict[str, str]],
    preserve_last_n: int = 4,
) -> List[Dict[str, str]]:
    """Apply deterministic heuristics to shrink a conversation.

    Rules applied to messages *outside* the last ``preserve_last_n``:
    1. Remove **assistant** messages with content shorter than 20 characters.
       User messages are NEVER removed regardless of length.
    2. Truncate any message whose content exceeds 500 tokens to
       ``first_200_tokens + ' [...truncated...] ' + last_100_tokens``.
    3. Remove consecutive duplicate assistant messages (keep the first).
    """
    if not messages:
        return []

    # Split into compressible head and preserved tail
    if preserve_last_n >= len(messages):
        return list(messages)

    head = messages[:-preserve_last_n] if preserve_last_n > 0 else list(messages)
    tail = messages[-preserve_last_n:] if preserve_last_n > 0 else []

    compressed: List[Dict[str, str]] = []
    prev_assistant_content: Optional[str] = None

    for msg in head:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Rule 1: remove short assistant messages (NEVER remove user messages)
        if role == "assistant" and len(content) < 20:
            prev_assistant_content = None
            continue

        # Rule 3: deduplicate consecutive assistant messages
        if role == "assistant":
            if content == prev_assistant_content:
                continue
            prev_assistant_content = content
        else:
            prev_assistant_content = None

        # Rule 2: truncate long messages
        token_count = count_tokens(content)
        if token_count > 500:
            if _ENCODER is not None:
                tokens = _ENCODER.encode(content)
                first_part = _ENCODER.decode(tokens[:200])
                last_part = _ENCODER.decode(tokens[-100:])
            else:
                words = content.split()
                first_part = " ".join(words[:200])
                last_part = " ".join(words[-100:])
            content = first_part + " [...truncated...] " + last_part

        compressed.append({"role": role, "content": content})

    return compressed + list(tail)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compress_conversation(
    messages: List[Dict[str, str]],
    preserve_last_n: int = 4,
    use_llmlingua: bool = True,
) -> Dict[str, Any]:
    """Compress a conversation and return stats.

    Parameters
    ----------
    messages : list of dicts
        Each dict has ``role`` and ``content`` keys.
    preserve_last_n : int
        Number of tail messages to keep verbatim.
    use_llmlingua : bool
        Try LLMLingua-2 first; fall back to rule-based if unavailable.

    Returns
    -------
    dict with keys:
        ``compressed_messages``, ``original_tokens``, ``compressed_tokens``,
        ``saved_percent``, ``method``, ``preserve_last_n``.
    """
    if not messages:
        return {
            "compressed_messages": [],
            "original_tokens": 0,
            "compressed_tokens": 0,
            "saved_percent": 0.0,
            "method": "none",
            "preserve_last_n": preserve_last_n,
        }

    original_text = "\n".join(m.get("content", "") for m in messages)
    original_tokens = count_tokens(original_text)

    # Try LLMLingua-2 first
    llmlingua_available = LLMLINGUA_AVAILABLE
    if use_llmlingua and llmlingua_available:
        try:
            compressor = _get_llmlingua()
            if compressor is not None:
                # Compress the non-preserved portion
                if preserve_last_n > 0 and preserve_last_n < len(messages):
                    head = messages[:-preserve_last_n]
                    tail = messages[-preserve_last_n:]
                else:
                    head = messages
                    tail = []

                head_text = "\n".join(m.get("content", "") for m in head)
                compressed_text = _compress_with_llmlingua(head_text)
                compressed_msgs = [{"role": "system", "content": compressed_text}] + list(tail)
                compressed_tokens = count_tokens(
                    "\n".join(m.get("content", "") for m in compressed_msgs)
                )
                saved_pct = round(
                    (1 - compressed_tokens / max(original_tokens, 1)) * 100, 1
                )
                return {
                    "compressed_messages": compressed_msgs,
                    "original_tokens": original_tokens,
                    "compressed_tokens": compressed_tokens,
                    "saved_percent": saved_pct,
                    "method": "llmlingua2",
                    "preserve_last_n": preserve_last_n,
                }
        except Exception as exc:
            logger.warning("LLMLingua-2 compression failed, falling back: %s", exc)

    # Rule-based fallback
    compressed_msgs = rule_based_compress(messages, preserve_last_n=preserve_last_n)
    compressed_text = "\n".join(m.get("content", "") for m in compressed_msgs)
    compressed_tokens = count_tokens(compressed_text)

    saved_pct = round(
        (1 - compressed_tokens / max(original_tokens, 1)) * 100, 1
    )
    return {
        "compressed_messages": compressed_msgs,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "saved_percent": saved_pct,
        "method": "rule_based",
        "preserve_last_n": preserve_last_n,
    }
