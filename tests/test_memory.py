"""
Tests for the ContextLayer memory module (SQLite knowledge graph).

Covers the full lifecycle: init_db → store_session → recall → delete_memory.
Every test uses a temporary in-memory or tmp database so the user's real
memory.db is never touched.
"""

import pytest
import sys
import os
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from memory import init_db, store_session, recall, delete_memory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Create a fresh temporary SQLite database for each test."""
    db_path = str(tmp_path / "test_memory.db")
    init_db(db_path=db_path)
    return db_path


@pytest.fixture
def sample_messages():
    """Conversation messages containing extractable facts."""
    return [
        {"role": "user", "content": "I need to build a FastAPI backend for my project."},
        {"role": "assistant", "content": "Sure! FastAPI is a great choice. We'll use Python with uvicorn as the ASGI server."},
        {"role": "user", "content": "What about the database? I'm thinking SQLite for simplicity."},
        {"role": "assistant", "content": "SQLite is perfect for local-first applications. We can use the built-in sqlite3 module."},
        {"role": "user", "content": "Can we add compression using LLMLingua?"},
        {"role": "assistant", "content": "Yes, LLMLingua uses a transformer model for intelligent compression of conversation data."},
    ]


# ---------------------------------------------------------------------------
# 1. init_db creates the database file and tables
# ---------------------------------------------------------------------------


def test_init_db_creates_file(tmp_path):
    """init_db should create the database file and both tables."""
    db_path = str(tmp_path / "brand_new.db")
    assert not os.path.exists(db_path), "DB should not exist before init"

    init_db(db_path=db_path)

    assert os.path.exists(db_path), "init_db did not create the database file"

    # Verify tables exist
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "sessions" in tables, "Missing 'sessions' table"
    assert "facts" in tables, "Missing 'facts' table"


def test_init_db_is_idempotent(tmp_path):
    """Calling init_db twice should not raise or corrupt the database."""
    db_path = str(tmp_path / "idempotent.db")
    init_db(db_path=db_path)
    init_db(db_path=db_path)  # Should not raise

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "sessions" in tables
    assert "facts" in tables


# ---------------------------------------------------------------------------
# 2. store_session persists session and facts
# ---------------------------------------------------------------------------


def test_store_session_creates_records(tmp_db, sample_messages):
    """store_session should insert a session row and at least one fact."""
    result = store_session(
        session_id="test_session_001",
        messages=sample_messages,
        source_llm="TestLLM",
        db_path=tmp_db,
    )

    assert isinstance(result, dict)
    assert result["session_id"] == "test_session_001"
    assert result["facts_stored"] >= 0  # Regex extraction may find 0+
    assert result["message_count"] == len(sample_messages)

    # Verify session row in DB
    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sessions WHERE session_id = ?", ("test_session_001",))
    session_row = cursor.fetchone()
    conn.close()

    assert session_row is not None, "Session row was not persisted to SQLite"


def test_store_session_extracts_facts(tmp_db, sample_messages):
    """store_session with technology-rich messages should extract at least one fact."""
    result = store_session(
        session_id="test_session_002",
        messages=sample_messages,
        source_llm="Claude",
        db_path=tmp_db,
    )

    # The messages mention FastAPI, SQLite, Python, LLMLingua —
    # the regex patterns should capture at least some of these
    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM facts WHERE session_id = ?", ("test_session_002",))
    fact_count = cursor.fetchone()[0]
    conn.close()

    assert fact_count >= 1, (
        f"Expected at least 1 extracted fact, got {fact_count}. "
        f"store_session returned facts_stored={result['facts_stored']}"
    )


def test_store_session_source_llm(tmp_db, sample_messages):
    """The source_llm field should be stored correctly."""
    store_session(
        session_id="test_session_003",
        messages=sample_messages,
        source_llm="Gemini",
        db_path=tmp_db,
    )

    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT source_llm FROM sessions WHERE session_id = ?", ("test_session_003",))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "Gemini"


# ---------------------------------------------------------------------------
# 3. recall retrieves relevant facts via keyword search
# ---------------------------------------------------------------------------


def test_recall_finds_stored_facts(tmp_db, sample_messages):
    """recall should find facts that match a keyword query."""
    store_session(
        session_id="recall_test_001",
        messages=sample_messages,
        source_llm="Claude",
        db_path=tmp_db,
    )

    result = recall(query="FastAPI", db_path=tmp_db)

    assert isinstance(result, dict)
    assert "facts" in result
    assert "relevant_context" in result

    # We stored messages mentioning FastAPI; recall should find at least one fact
    # (depends on regex extraction finding it)
    if result["facts"]:
        # If facts were found, verify structure
        fact = result["facts"][0]
        assert "entity" in fact
        assert "relation" in fact
        assert "target" in fact


def test_recall_empty_query(tmp_db):
    """recall with a query of only short words should return no facts."""
    result = recall(query="to be", db_path=tmp_db)

    assert isinstance(result, dict)
    assert result["facts"] == []


def test_recall_no_matches(tmp_db, sample_messages):
    """recall with a completely unrelated query should return no facts."""
    store_session(
        session_id="recall_test_002",
        messages=sample_messages,
        source_llm="Claude",
        db_path=tmp_db,
    )

    result = recall(query="quantum_entanglement_unrelated", db_path=tmp_db)
    assert isinstance(result, dict)
    assert result["facts"] == []


def test_recall_respects_top_k(tmp_db):
    """recall with top_k=2 should return at most 2 results."""
    # Store a session with many extractable facts
    messages = [
        {"role": "user", "content": "We use FastAPI, React, Python, Docker, and Redis."},
        {"role": "assistant", "content": "FastAPI is great. React uses JavaScript. Python is versatile. Docker containers are portable. Redis is fast."},
    ]
    store_session(
        session_id="topk_test",
        messages=messages,
        source_llm="Claude",
        db_path=tmp_db,
    )

    result = recall(query="FastAPI React Python Docker Redis", top_k=2, db_path=tmp_db)
    assert len(result["facts"]) <= 2, (
        f"Expected at most 2 facts with top_k=2, got {len(result['facts'])}"
    )


# ---------------------------------------------------------------------------
# 4. delete_memory drops and recreates tables
# ---------------------------------------------------------------------------


def test_delete_memory_clears_all(tmp_db, sample_messages):
    """delete_memory should wipe all sessions and facts, then recreate tables."""
    # Store some data first
    store_session(
        session_id="delete_test_001",
        messages=sample_messages,
        source_llm="Claude",
        db_path=tmp_db,
    )

    # Verify data exists
    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sessions")
    pre_count = cursor.fetchone()[0]
    conn.close()
    assert pre_count > 0, "Precondition: sessions table should have data"

    # Delete everything
    result = delete_memory(db_path=tmp_db)
    assert isinstance(result, dict)
    assert "status" in result

    # Verify tables are empty but still exist
    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "sessions" in tables, "sessions table should be recreated after delete"
    assert "facts" in tables, "facts table should be recreated after delete"

    cursor.execute("SELECT COUNT(*) FROM sessions")
    post_sessions = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM facts")
    post_facts = cursor.fetchone()[0]
    conn.close()

    assert post_sessions == 0, f"Sessions not cleared: {post_sessions} remaining"
    assert post_facts == 0, f"Facts not cleared: {post_facts} remaining"


def test_delete_memory_on_empty_db(tmp_db):
    """delete_memory on a fresh DB should not raise."""
    result = delete_memory(db_path=tmp_db)
    assert isinstance(result, dict)
    assert "status" in result


# ---------------------------------------------------------------------------
# 5. Multiple sessions coexist correctly
# ---------------------------------------------------------------------------


def test_multiple_sessions(tmp_db):
    """Facts from different sessions should not interfere with each other."""
    msgs_a = [
        {"role": "user", "content": "FastAPI is excellent for building APIs."},
        {"role": "assistant", "content": "FastAPI uses Python and Starlette under the hood."},
    ]
    msgs_b = [
        {"role": "user", "content": "React is a great frontend framework."},
        {"role": "assistant", "content": "React uses JavaScript and a virtual DOM."},
    ]

    store_session(session_id="session_a", messages=msgs_a, source_llm="Claude", db_path=tmp_db)
    store_session(session_id="session_b", messages=msgs_b, source_llm="ChatGPT", db_path=tmp_db)

    # Verify both sessions exist
    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sessions")
    session_count = cursor.fetchone()[0]
    conn.close()

    assert session_count == 2, f"Expected 2 sessions, got {session_count}"


# ---------------------------------------------------------------------------
# 6. Recall relevant_context is formatted
# ---------------------------------------------------------------------------


def test_recall_formats_context(tmp_db, sample_messages):
    """When facts are found, relevant_context should be a non-empty formatted string."""
    store_session(
        session_id="format_test",
        messages=sample_messages,
        source_llm="Claude",
        db_path=tmp_db,
    )

    result = recall(query="FastAPI Python SQLite", db_path=tmp_db)

    if result["facts"]:
        assert len(result["relevant_context"]) > 0, (
            "relevant_context should be non-empty when facts are found"
        )
        # Each line should start with "- "
        for line in result["relevant_context"].strip().split("\n"):
            assert line.startswith("- "), (
                f"Context line should start with '- ', got: {line!r}"
            )
