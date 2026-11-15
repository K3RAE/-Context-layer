"""
End-to-end tests for the FastAPI app (every route), using a temporary memory
DB and a mocked Ollama so no network, model download or user data is touched.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import handoff  # noqa: E402
import main  # noqa: E402
import ollama_client  # noqa: E402

CONVO = [
    {"role": "user", "content": "I am building a Chrome extension called ContextLayer."},
    {"role": "assistant", "content": "Great! We will use FastAPI for the backend in `main.py`."},
    {"role": "user", "content": "FastAPI uses Python. Store memory in SQLite."},
    {"role": "assistant", "content": "SQLite is a good local-first choice for memory storage."},
    {"role": "user", "content": "What next?"},
    {"role": "assistant", "content": "Next we wire up the popup to call the backend endpoints."},
]


async def _offline() -> bool:
    return False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("LLMLINGUA_ENABLED", "false")
    monkeypatch.setattr(ollama_client, "is_available", _offline)
    with TestClient(main.app) as c:
        yield c


def test_health(client, tmp_path):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ollama"] is False
    assert body["memory_db"] == str(tmp_path / "api.db")


def test_compress(client):
    r = client.post("/compress", json={"messages": CONVO, "preserve_last_n": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] in ("rule_based", "llmlingua2")
    assert body["compressed_messages"][-2:] == CONVO[-2:]
    assert body["original_tokens"] > 0


def test_compress_validation_error(client):
    r = client.post("/compress", json={"messages": CONVO, "preserve_last_n": -1})
    assert r.status_code == 422


def test_handoff_rule_based(client):
    r = client.post("/handoff", json={"messages": CONVO, "session_id": "s1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "rule_based"
    assert "[CONTEXT HANDOFF]" in body["handoff_note"]
    assert body["structured"]["next_step"] == "What next?"
    assert "main.py" in body["structured"]["key_files_or_code"]


def test_handoff_ollama_string_fields_are_normalized(client, monkeypatch):
    """Ollama often returns strings where the schema wants lists — must not 500."""

    async def online() -> bool:
        return True

    async def models() -> list:
        return ["llama3.2:3b"]

    async def fake_generate(**_kwargs) -> str:
        return (
            'Sure! {"task": "Build ContextLayer", "decisions": "Use FastAPI\\nUse SQLite", '
            '"current_state": "Backend done", "constraints": "Local only", '
            '"rejected_approaches": "None", "next_step": "Wire popup", '
            '"key_files_or_code": ["main.py"]}'
        )

    monkeypatch.setattr(ollama_client, "is_available", online)
    monkeypatch.setattr(ollama_client, "get_available_models", models)
    monkeypatch.setattr(ollama_client, "generate", fake_generate)

    r = client.post("/handoff", json={"messages": CONVO, "session_id": "s2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "ollama"
    s = body["structured"]
    assert s["decisions"] == ["Use FastAPI", "Use SQLite"]
    assert s["constraints"] == ["Local only"]
    assert s["rejected_approaches"] == []
    assert s["tone"] == "neutral"
    assert "- Use FastAPI" in body["handoff_note"]


def test_remember_recall_delete_roundtrip(client):
    r = client.post(
        "/remember",
        json={"session_id": "sess-a", "messages": CONVO, "source_llm": "Claude"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts_stored"] >= 1
    assert body["session_id"] == "sess-a"
    assert "FastAPI" in body["entities"]

    r = client.post("/recall", json={"query": "FastAPI", "top_k": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts"], body
    assert body["source_sessions"] == ["sess-a"]
    assert "FastAPI" in body["relevant_context"]

    # Session filter
    r = client.post("/recall", json={"query": "FastAPI", "session_id": "other"})
    assert r.json()["facts"] == []

    r = client.delete("/memory")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True

    r = client.post("/recall", json={"query": "FastAPI"})
    assert r.json()["facts"] == []


def test_cors_allows_extension_origin(client):
    origin = "chrome-extension://" + "a" * 32
    r = client.options(
        "/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == origin


def test_cors_rejects_random_site(client):
    r = client.options(
        "/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers


def test_normalize_structured_handles_garbage():
    out = handoff.normalize_structured(None)
    assert set(out) == {
        "task", "decisions", "current_state", "constraints",
        "rejected_approaches", "next_step", "key_files_or_code", "tone",
    }
    assert out["decisions"] == []
