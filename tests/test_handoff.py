"""
Tests for the ContextLayer handoff module.

Covers: rule_based_handoff (deterministic, no Ollama dependency).
Validates that the structured output has all 8 mandatory fields,
the handoff note contains both opening and closing markers, and
that the next_step is derived from the last user message.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from handoff import rule_based_handoff


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_history():
    """A 10-message sample conversation for handoff tests."""
    return [
        {"role": "user", "content": "I need to build a FastAPI backend for my project."},
        {"role": "assistant", "content": "Sure! Let's start by setting up the project structure. We'll use FastAPI with uvicorn as the ASGI server."},
        {"role": "user", "content": "What about the database? I'm thinking SQLite for simplicity."},
        {"role": "assistant", "content": "SQLite is a great choice for local-first applications. We can use the built-in sqlite3 module. Let me set up the schema with sessions and facts tables."},
        {"role": "user", "content": "Can we add compression for the conversation data?"},
        {"role": "assistant", "content": "Yes, we decided to use LLMLingua-2 for intelligent compression. It uses a transformer model to identify and remove redundant tokens while preserving meaning."},
        {"role": "user", "content": "What if LLMLingua isn't available?"},
        {"role": "assistant", "content": "We'll implement a rule-based fallback that removes short pleasantries, truncates very long messages, and deduplicates consecutive assistant replies."},
        {"role": "user", "content": "Sounds good. Let's also add a handoff feature."},
        {"role": "assistant", "content": "The handoff feature will generate a structured context summary that can be pasted into a new LLM conversation to preserve context."},
    ]


# ---------------------------------------------------------------------------
# 1. Response has the correct top-level structure
# ---------------------------------------------------------------------------


def test_rule_based_handoff_structure(chat_history):
    """rule_based_handoff must return a dict with keys:
    structured, handoff_note, token_count, method.
    """
    result = rule_based_handoff(chat_history)

    assert isinstance(result, dict), "Return value must be a dict"
    for key in ("structured", "handoff_note", "token_count", "method"):
        assert key in result, f"Missing top-level key: '{key}'"

    assert isinstance(result["structured"], dict)
    assert isinstance(result["handoff_note"], str)
    assert isinstance(result["token_count"], int)
    assert isinstance(result["method"], str)


# ---------------------------------------------------------------------------
# 2. Structured output has all 8 mandatory fields
# ---------------------------------------------------------------------------


_REQUIRED_FIELDS = [
    "task",
    "decisions",
    "current_state",
    "constraints",
    "rejected_approaches",
    "next_step",
    "key_files_or_code",
    "tone",
]


def test_structured_has_all_fields(chat_history):
    """The structured output dict must contain all 8 fields from the spec."""
    result = rule_based_handoff(chat_history)
    structured = result["structured"]

    for field in _REQUIRED_FIELDS:
        assert field in structured, (
            f"Structured output missing required field: '{field}'"
        )
    # Every field value should be non-None
    for field in _REQUIRED_FIELDS:
        assert structured[field] is not None, (
            f"Field '{field}' is None — must have a value"
        )


# ---------------------------------------------------------------------------
# 3. Handoff note contains the opening marker
# ---------------------------------------------------------------------------


def test_handoff_note_contains_marker(chat_history):
    """The handoff_note string must contain 'CONTEXT HANDOFF'."""
    result = rule_based_handoff(chat_history)
    assert "CONTEXT HANDOFF" in result["handoff_note"], (
        "Handoff note must contain the 'CONTEXT HANDOFF' marker"
    )


# ---------------------------------------------------------------------------
# 4. Handoff note contains the closing marker
# ---------------------------------------------------------------------------


def test_handoff_note_contains_end_marker(chat_history):
    """The handoff_note string must contain 'END HANDOFF'."""
    result = rule_based_handoff(chat_history)
    assert "END HANDOFF" in result["handoff_note"], (
        "Handoff note must contain the 'END HANDOFF' closing marker"
    )


# ---------------------------------------------------------------------------
# 5. next_step is derived from the last user message
# ---------------------------------------------------------------------------


def test_next_step_from_last_user_message(chat_history):
    """next_step should equal the content of the last user message."""
    result = rule_based_handoff(chat_history)
    structured = result["structured"]

    # Find the last user message in chat_history
    last_user_content = ""
    for msg in reversed(chat_history):
        if msg.get("role") == "user":
            last_user_content = msg["content"]
            break

    assert last_user_content, "Test data must contain at least one user message"
    assert structured["next_step"] == last_user_content, (
        f"next_step should be the last user message.\n"
        f"  Expected: {last_user_content!r}\n"
        f"  Got:      {structured['next_step']!r}"
    )


# ---------------------------------------------------------------------------
# 6. method is 'rule_based'
# ---------------------------------------------------------------------------


def test_method_is_rule_based(chat_history):
    """Without Ollama, the method must always be 'rule_based'."""
    result = rule_based_handoff(chat_history)
    assert result["method"] == "rule_based", (
        f"Expected method='rule_based', got '{result['method']}'"
    )


# ---------------------------------------------------------------------------
# 7. token_count is positive for non-empty conversations
# ---------------------------------------------------------------------------


def test_token_count_positive(chat_history):
    """The handoff note should have a positive token count."""
    result = rule_based_handoff(chat_history)
    assert result["token_count"] > 0, (
        f"token_count should be > 0, got {result['token_count']}"
    )


# ---------------------------------------------------------------------------
# 8. Empty conversation produces valid output
# ---------------------------------------------------------------------------


def test_empty_conversation_handoff():
    """rule_based_handoff with an empty list should still return valid structure."""
    result = rule_based_handoff([])

    assert isinstance(result, dict)
    assert "structured" in result
    assert "handoff_note" in result
    assert "method" in result
    assert result["method"] == "rule_based"

    # Structured should still have all 8 fields
    for field in _REQUIRED_FIELDS:
        assert field in result["structured"], (
            f"Empty conversation: structured missing field '{field}'"
        )


# ---------------------------------------------------------------------------
# 9. Handoff note includes key content from structured fields
# ---------------------------------------------------------------------------


def test_handoff_note_includes_task(chat_history):
    """The handoff note should include the extracted task description."""
    result = rule_based_handoff(chat_history)
    note = result["handoff_note"]
    task = result["structured"]["task"]

    # The task should appear somewhere in the note
    assert task[:50] in note, (
        f"Handoff note should contain the task text.\n"
        f"  Task:  {task[:80]!r}\n"
        f"  Note excerpt: {note[:200]!r}"
    )
