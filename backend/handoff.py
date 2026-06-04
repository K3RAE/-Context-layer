"""
ContextLayer — Handoff Generator

Generates structured handoff notes from conversations.
Uses Ollama for intelligent extraction with a rule-based fallback.
"""

import json
import re
from typing import List, Dict, Any, Optional

# ---------------------------------------------------------------------------
# Ollama client (optional)
# ---------------------------------------------------------------------------

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

OLLAMA_URL = "http://localhost:11434"

HANDOFF_SYSTEM_PROMPT = """You are a context handoff assistant. Analyze the conversation and extract a structured summary.

Return ONLY valid JSON with these exact keys:
{
  "task": "What the user is trying to accomplish",
  "decisions": "Key decisions made during the conversation",
  "current_state": "Where things stand right now",
  "constraints": "Any constraints or requirements mentioned",
  "rejected_approaches": "Approaches that were tried and didn't work",
  "next_step": "What should be done next",
  "key_files_or_code": "Important files, code snippets, or references",
  "tone": "The conversational tone (e.g., technical, casual, frustrated)"
}"""


# ---------------------------------------------------------------------------
# Ollama-based handoff
# ---------------------------------------------------------------------------


async def ollama_handoff(messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Try to generate a handoff via Ollama. Returns None on failure."""
    if not _HTTPX_AVAILABLE:
        return None

    conversation_text = "\n".join(
        f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in messages
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Check availability
            health = await client.get(f"{OLLAMA_URL}/api/tags")
            if health.status_code != 200:
                return None

            tags = health.json()
            models = [m["name"] for m in tags.get("models", [])]
            if not models:
                return None

            # Pick the best available model
            preferred = ["llama3.1:8b", "llama3:8b", "mistral:7b", "gemma2:9b"]
            model = next((m for m in preferred if m in models), models[0])

            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": f"Analyze this conversation:\n\n{conversation_text}",
                    "system": HANDOFF_SYSTEM_PROMPT,
                    "stream": False,
                },
            )
            if response.status_code != 200:
                return None

            raw = response.json().get("response", "")
            # Try to parse JSON from the response
            json_match = re.search(r"\{[\s\S]*\}", raw)
            if json_match:
                return json.loads(json_match.group())
            return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------


def rule_based_handoff(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Generate a handoff using deterministic rules (no LLM required).

    Returns a dict with keys: ``structured``, ``handoff_note``,
    ``token_count``, ``method``.
    """
    if not messages:
        messages = []

    user_messages = [m for m in messages if m.get("role") == "user"]
    assistant_messages = [m for m in messages if m.get("role") == "assistant"]

    # Extract last user message as next_step
    last_user = user_messages[-1]["content"] if user_messages else ""

    # Build current_state from the last 3 exchanges
    last_exchanges = messages[-6:] if len(messages) >= 6 else messages
    current_state = " | ".join(
        f"{m.get('role', '?')}: {m.get('content', '')[:100]}" for m in last_exchanges
    )

    # Gather decisions (look for keywords in assistant messages)
    decision_keywords = ["decided", "chose", "selected", "going with", "let's use", "will use"]
    decisions = []
    for msg in assistant_messages:
        content = msg.get("content", "")
        for kw in decision_keywords:
            if kw.lower() in content.lower():
                # Grab the sentence containing the keyword
                sentences = re.split(r"[.!?]", content)
                for s in sentences:
                    if kw.lower() in s.lower() and s.strip():
                        decisions.append(s.strip())
                        break
                break
    structured = {
        "task": user_messages[0]["content"][:200] if user_messages else "",
        "decisions": decisions,
        "current_state": current_state[:500] if current_state else "",
        "constraints": [],
        "rejected_approaches": [],
        "next_step": last_user,
        "key_files_or_code": [],
        "tone": "",
    }

    # Format the handoff note
    handoff_note = _format_handoff_note(structured)

    # Approximate token count
    try:
        from compressor import count_tokens
        token_count = count_tokens(handoff_note)
    except Exception:
        token_count = len(handoff_note) // 4

    return {
        "structured": structured,
        "handoff_note": handoff_note,
        "token_count": token_count,
        "method": "rule_based",
    }


def _format_handoff_note(structured: Dict[str, str]) -> str:
    """Format a structured dict into the standard handoff note string."""
    return f"""[CONTEXT HANDOFF]

TASK: {structured.get('task', 'Unknown')}

DECISIONS MADE:
{structured.get('decisions', 'None')}

CURRENT STATE:
{structured.get('current_state', 'Unknown')}

CONSTRAINTS:
{structured.get('constraints', 'None')}

REJECTED APPROACHES:
{structured.get('rejected_approaches', 'None')}

NEXT STEP:
{structured.get('next_step', 'Unknown')}

KEY FILES / CODE:
{structured.get('key_files_or_code', 'None')}

TONE: {structured.get('tone', 'neutral')}

[END HANDOFF]"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def generate_handoff(
    messages: List[Dict[str, str]],
    session_id: str = "",
) -> Dict[str, Any]:
    """Generate a handoff note, trying Ollama first then falling back to rules.

    Returns a dict with keys: ``structured``, ``handoff_note``,
    ``token_count``, ``method``, ``session_id``.
    """
    # Try Ollama first
    try:
        ollama_result = await ollama_handoff(messages)
        if ollama_result is not None:
            handoff_note = _format_handoff_note(ollama_result)
            try:
                from compressor import count_tokens
                token_count = count_tokens(handoff_note)
            except Exception:
                token_count = len(handoff_note) // 4

            return {
                "structured": ollama_result,
                "handoff_note": handoff_note,
                "token_count": token_count,
                "method": "ollama",
                "session_id": session_id,
            }
    except Exception:
        pass

    # Fall back to rule-based
    result = rule_based_handoff(messages)
    result["session_id"] = session_id
    return result
