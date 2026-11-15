"""
Tests for the ContextLayer compression module.

Covers: compress_conversation, rule_based_compress, count_tokens.
All tests work without LLMLingua-2 installed (they exercise the rule-based path).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from compressor import compress_conversation, rule_based_compress, count_tokens


# ---------------------------------------------------------------------------
# 1. compress_conversation reduces token count
# ---------------------------------------------------------------------------


def test_compress_reduces_tokens(large_conversation):
    """A conversation with compressible patterns should produce fewer tokens.

    The large_conversation fixture from conftest.py may be too 'clean' for the
    rule-based compressor (no short assistant messages, no >500-token messages,
    no consecutive duplicates). So we inject compressible patterns here.
    """
    # Inject patterns that trigger all 3 compression rules
    compressible = list(large_conversation) + [
        {"role": "assistant", "content": "Sure!"},             # < 20 chars → removed
        {"role": "assistant", "content": "OK"},                # < 20 chars → removed
        {"role": "user", "content": "Please explain more."},
        {"role": "assistant", "content": "Detailed explanation. " * 200},  # >500 tokens → truncated
        {"role": "user", "content": "Can you repeat that?"},
        {"role": "assistant", "content": "Repeated answer here with sufficient content."},
        {"role": "assistant", "content": "Repeated answer here with sufficient content."},  # duplicate → removed
    ]

    result = compress_conversation(compressible, preserve_last_n=4)

    assert result["compressed_tokens"] < result["original_tokens"], (
        f"Compression did not reduce tokens: "
        f"{result['compressed_tokens']} >= {result['original_tokens']}"
    )
    assert result["method"] in ("llmlingua2", "rule_based")
    assert isinstance(result["compressed_messages"], list)
    assert len(result["compressed_messages"]) > 0


# ---------------------------------------------------------------------------
# 2. rule_based_compress applies all three rules correctly
# ---------------------------------------------------------------------------


def test_rule_based_fallback():
    """Rule-based compression should:
    - Remove short assistant messages (<20 chars)
    - Truncate long messages (>500 tokens) with '[...truncated...]'
    - Deduplicate consecutive assistant messages
    - Preserve ALL user messages
    """
    # Build a conversation with edge cases
    long_content = "This is a detailed explanation. " * 200  # ~800 tokens

    messages = [
        {"role": "user", "content": "Hello, what is Python?"},
        {"role": "assistant", "content": "Hi!"},  # <20 chars → should be REMOVED
        {"role": "user", "content": "Tell me more about it."},
        {"role": "assistant", "content": long_content},  # >500 tokens → truncated
        {"role": "user", "content": "Can you repeat that?"},
        {"role": "assistant", "content": "Sure, Python is a versatile programming language."},
        {"role": "assistant", "content": "Sure, Python is a versatile programming language."},  # duplicate → REMOVED
        {"role": "user", "content": "Thanks!"},
        {"role": "assistant", "content": "You're welcome! Happy coding."},
        {"role": "user", "content": "Bye now."},
    ]

    # Use preserve_last_n=0 so ALL messages are subject to compression
    compressed = rule_based_compress(messages, preserve_last_n=0)

    # Check short assistant messages removed
    short_msgs = [m for m in compressed if m["role"] == "assistant" and m["content"] == "Hi!"]
    assert len(short_msgs) == 0, "Short assistant message 'Hi!' should have been removed"

    # Check long messages are truncated
    truncated = [m for m in compressed if "[...truncated...]" in m["content"]]
    assert len(truncated) >= 1, "Long message should contain '[...truncated...]'"

    # Check consecutive duplicates removed
    assistant_contents = [m["content"] for m in compressed if m["role"] == "assistant"]
    for i in range(len(assistant_contents) - 1):
        assert assistant_contents[i] != assistant_contents[i + 1], (
            f"Consecutive duplicate assistant messages at index {i}"
        )

    # Check ALL user messages preserved
    original_user = [m for m in messages if m["role"] == "user"]
    compressed_user = [m for m in compressed if m["role"] == "user"]
    assert len(compressed_user) == len(original_user), (
        f"User messages lost: {len(compressed_user)} != {len(original_user)}"
    )
    for orig, comp in zip(original_user, compressed_user):
        assert orig["content"] == comp["content"]


# ---------------------------------------------------------------------------
# 3. preserve_last_n keeps tail messages verbatim
# ---------------------------------------------------------------------------


def test_preserve_last_n(sample_conversation):
    """The last N messages must appear exactly in the output."""
    preserve_n = 4
    result = compress_conversation(sample_conversation, preserve_last_n=preserve_n)

    compressed = result["compressed_messages"]
    original_tail = sample_conversation[-preserve_n:]
    compressed_tail = compressed[-preserve_n:]

    assert len(compressed_tail) == preserve_n
    for orig, comp in zip(original_tail, compressed_tail):
        assert orig["role"] == comp["role"], f"Role mismatch: {orig['role']} != {comp['role']}"
        assert orig["content"] == comp["content"], (
            f"Content mismatch in preserved tail:\n"
            f"  Original: {orig['content'][:80]}...\n"
            f"  Got:      {comp['content'][:80]}..."
        )


# ---------------------------------------------------------------------------
# 4. count_tokens returns a positive integer
# ---------------------------------------------------------------------------


def test_count_tokens():
    """count_tokens on a known string should return > 0."""
    text = "The quick brown fox jumps over the lazy dog"
    tokens = count_tokens(text)
    assert isinstance(tokens, int)
    assert tokens > 0, f"Expected positive token count, got {tokens}"


# ---------------------------------------------------------------------------
# 5. Short user messages are NEVER removed
# ---------------------------------------------------------------------------


def test_short_user_messages_preserved():
    """Rule-based compress must NEVER remove user messages, even if < 20 chars."""
    messages = [
        {"role": "user", "content": "Hi"},           # 2 chars
        {"role": "assistant", "content": "Hello! How can I help you today with your project?"},
        {"role": "user", "content": "ok"},            # 2 chars
        {"role": "assistant", "content": "Great, let me know if you have any questions!"},
        {"role": "user", "content": "yes"},           # 3 chars
        {"role": "assistant", "content": "I'm here to help with anything you need."},
        {"role": "user", "content": "thx"},           # 3 chars
        {"role": "assistant", "content": "You're welcome! Feel free to reach out anytime."},
    ]

    compressed = rule_based_compress(messages, preserve_last_n=0)
    compressed_user = [m for m in compressed if m["role"] == "user"]
    original_user = [m for m in messages if m["role"] == "user"]

    assert len(compressed_user) == len(original_user), (
        f"User messages were removed! Expected {len(original_user)}, got {len(compressed_user)}"
    )
    for orig, comp in zip(original_user, compressed_user):
        assert orig["content"] == comp["content"], (
            f"User content changed: '{orig['content']}' != '{comp['content']}'"
        )


# ---------------------------------------------------------------------------
# 6. Empty conversation returns 0 tokens
# ---------------------------------------------------------------------------


def test_empty_conversation():
    """compress_conversation with an empty list should return 0 tokens."""
    result = compress_conversation([])
    assert result["original_tokens"] == 0
    assert result["compressed_tokens"] == 0
    assert result["compressed_messages"] == []
    assert result["saved_percent"] == 0.0
    assert result["method"] == "none"
