"""
ContextLayer — Handoff Generator

Generates structured handoff notes from conversations.
Uses a local LLM (Ollama or any OpenAI-compatible server, see llm_client)
for intelligent extraction, with a rule-based fallback.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Field schema
# ---------------------------------------------------------------------------

_LIST_FIELDS = ("decisions", "constraints", "rejected_approaches", "key_files_or_code")
_STR_FIELDS = ("task", "current_state", "next_step", "tone")

HANDOFF_SYSTEM_PROMPT = """You are a context handoff assistant. Analyze the conversation and extract a structured summary.

Return ONLY valid JSON (no prose, no markdown fences) with these exact keys:
{
  "task": "What the user is trying to accomplish (string)",
  "decisions": ["Key decisions made during the conversation"],
  "current_state": "Where things stand right now (string)",
  "constraints": ["Constraints or requirements mentioned"],
  "rejected_approaches": ["Approaches that were tried and did not work"],
  "next_step": "What should be done next (string)",
  "key_files_or_code": ["Important files, code snippets, or references"],
  "tone": "The conversational tone, e.g. technical, casual, frustrated (string)"
}"""


def _count_tokens(text: str) -> int:
    try:
        from compressor import count_tokens

        return count_tokens(text)
    except Exception:
        return max(1, len(text) // 4) if text else 0


def _as_list(value: Any) -> List[str]:
    """Coerce an LLM-produced value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    text = str(value).strip()
    if not text or text.lower() in ("none", "n/a", "null", "[]"):
        return []
    parts = [p.strip(" -*\t") for p in re.split(r"\n+|;\s+", text)]
    return [p for p in parts if p]


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value if v is not None)
    return str(value).strip()


def normalize_structured(data: Any) -> Dict[str, Any]:
    """Return a dict with exactly the 8 handoff fields, in the expected types."""
    data = data if isinstance(data, dict) else {}
    out: Dict[str, Any] = {}
    for key in _STR_FIELDS:
        out[key] = _as_str(data.get(key))
    for key in _LIST_FIELDS:
        out[key] = _as_list(data.get(key))
    if not out["tone"]:
        out["tone"] = "neutral"
    return out


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from an LLM response."""
    if not raw:
        return None
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# LLM-based handoff (Ollama or any OpenAI-compatible server)
# ---------------------------------------------------------------------------


def conversation_for_prompt(messages: List[Dict[str, str]], max_tokens: int) -> str:
    """Render the conversation, keeping it within ``max_tokens``.

    Small local models have small context windows, so long chats are cut
    down to the first user message (the task) plus as many of the most
    recent messages as fit, with a marker where messages were left out.
    """
    lines = [f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in messages]
    full = "\n".join(lines)
    if _count_tokens(full) <= max_tokens:
        return full

    first_user = next((i for i, m in enumerate(messages) if m.get("role") == "user"), 0)
    head = lines[first_user]
    if _count_tokens(head) > max_tokens // 4:
        head = head[: max_tokens]  # ~4 chars/token, so this is at most ~1/4 budget

    budget = max_tokens - _count_tokens(head) - 20
    tail: List[str] = []
    for line in reversed(lines[first_user + 1:]):
        cost = _count_tokens(line)
        if cost > budget:
            if not tail:  # always keep part of the latest message
                tail.append(line[: max(0, budget) * 4])
            break
        tail.append(line)
        budget -= cost
    tail.reverse()
    omitted = len(lines) - first_user - 1 - len(tail)
    marker = [f"[... {omitted} earlier messages omitted ...]"] if omitted > 0 else []
    return "\n".join([head] + marker + tail)


async def llm_handoff(messages: List[Dict[str, str]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Try to generate a structured handoff with the configured LLM provider.

    Returns ``(structured_or_None, provider_name)``.
    """
    if not messages:
        return None, "none"
    try:
        import llm_client
    except Exception:
        return None, "none"

    try:
        conversation_text = conversation_for_prompt(messages, llm_client.max_prompt_tokens())
        raw, provider = await llm_client.generate_json(
            prompt=f"Analyze this conversation:\n\n{conversation_text}",
            system_prompt=HANDOFF_SYSTEM_PROMPT,
        )
        parsed = _parse_json_object(raw)
        if parsed is None:
            return None, provider
        return normalize_structured(parsed), provider
    except Exception:
        return None, "none"


async def ollama_handoff(messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Backwards-compatible alias for :func:`llm_handoff`."""
    structured, _provider = await llm_handoff(messages)
    return structured


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------

_CODE_REF_RE = re.compile(
    r"`([^`\n]{2,80})`"
    r"|\b([\w./\\-]+\.(?:py|js|ts|tsx|jsx|json|md|html|css|yml|yaml|toml|sh|bat|ps1|sql))\b"
)


def _extract_code_refs(messages: List[Dict[str, str]], limit: int = 10) -> List[str]:
    """Pull file names and inline-code snippets out of the conversation."""
    refs: List[str] = []
    for msg in messages:
        for match in _CODE_REF_RE.finditer(msg.get("content", "")):
            ref = (match.group(1) or match.group(2) or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


def rule_based_handoff(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Generate a handoff using deterministic rules (no LLM required).

    Returns a dict with keys: ``structured``, ``handoff_note``,
    ``token_count``, ``method``.
    """
    messages = messages or []

    user_messages = [m for m in messages if m.get("role") == "user"]
    assistant_messages = [m for m in messages if m.get("role") == "assistant"]

    # Last user message is the best guess at the next step
    last_user = user_messages[-1].get("content", "") if user_messages else ""

    # Build current_state from the last 3 exchanges
    last_exchanges = messages[-6:]
    current_state = " | ".join(
        f"{m.get('role', '?')}: {m.get('content', '')[:100]}" for m in last_exchanges
    )

    # Gather decisions (look for keywords in assistant messages)
    decision_keywords = ["decided", "chose", "selected", "going with", "let's use", "will use"]
    decisions: List[str] = []
    for msg in assistant_messages:
        content = msg.get("content", "")
        lowered = content.lower()
        for kw in decision_keywords:
            if kw in lowered:
                for sentence in re.split(r"[.!?]", content):
                    if kw in sentence.lower() and sentence.strip():
                        decisions.append(sentence.strip())
                        break
                break

    structured = {
        "task": user_messages[0].get("content", "")[:200] if user_messages else "",
        "decisions": decisions,
        "current_state": current_state[:500],
        "constraints": [],
        "rejected_approaches": [],
        "next_step": last_user,
        "key_files_or_code": _extract_code_refs(messages),
        "tone": "neutral",
    }

    handoff_note = _format_handoff_note(structured)

    return {
        "structured": structured,
        "handoff_note": handoff_note,
        "token_count": _count_tokens(handoff_note),
        "method": "rule_based",
    }


def _fmt_list(items: Any) -> str:
    items = _as_list(items)
    if not items:
        return "None"
    return "\n".join(f"- {item}" for item in items)


def _format_handoff_note(structured: Dict[str, Any]) -> str:
    """Format a structured dict into the standard handoff note string."""
    return f"""[CONTEXT HANDOFF]

TASK: {_as_str(structured.get('task')) or 'Unknown'}

DECISIONS MADE:
{_fmt_list(structured.get('decisions'))}

CURRENT STATE:
{_as_str(structured.get('current_state')) or 'Unknown'}

CONSTRAINTS:
{_fmt_list(structured.get('constraints'))}

REJECTED APPROACHES:
{_fmt_list(structured.get('rejected_approaches'))}

NEXT STEP:
{_as_str(structured.get('next_step')) or 'Unknown'}

KEY FILES / CODE:
{_fmt_list(structured.get('key_files_or_code'))}

TONE: {_as_str(structured.get('tone')) or 'neutral'}

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
    try:
        llm_result, provider = await llm_handoff(messages)
    except Exception:
        llm_result, provider = None, "none"

    if llm_result is not None:
        handoff_note = _format_handoff_note(llm_result)
        return {
            "structured": llm_result,
            "handoff_note": handoff_note,
            "token_count": _count_tokens(handoff_note),
            "method": provider,
            "session_id": session_id,
        }

    result = rule_based_handoff(messages)
    result["session_id"] = session_id
    return result
