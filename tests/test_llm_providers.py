"""
Tests for the LLM provider layer: OpenAI-compatible client against a stub
HTTP server, provider selection, and prompt trimming.
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import handoff  # noqa: E402
import llm_client  # noqa: E402
import main  # noqa: E402
import ollama_client  # noqa: E402
import openai_client  # noqa: E402

GOOD_JSON = {
    "task": "Build ContextLayer",
    "decisions": ["Use FastAPI"],
    "current_state": "Backend done",
    "constraints": "Local only",
    "rejected_approaches": [],
    "next_step": "Wire popup",
    "key_files_or_code": ["main.py"],
    "tone": "technical",
}


class _StubOpenAI(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible server. Behaviour is set on the class."""

    reject_response_format = False
    models = ["stub-model-1", "stub-model-2"]
    requests: list = []

    def log_message(self, *args):  # silence test output
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": m} for m in self.models]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(("POST", self.path, dict(self.headers), body))
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        if self.reject_response_format and "response_format" in body:
            self._send(400, {"error": "response_format not supported"})
            return
        content = "Here you go: " + json.dumps(GOOD_JSON)
        self._send(200, {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]})


@pytest.fixture
def stub_server(monkeypatch):
    _StubOpenAI.requests = []
    _StubOpenAI.reject_response_format = False
    server = HTTPServer(("127.0.0.1", 0), _StubOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("OPENAI_BASE_URL", base)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    yield base
    server.shutdown()


async def _offline() -> bool:
    return False


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:1234", "http://localhost:1234/v1"),
        ("http://localhost:1234/", "http://localhost:1234/v1"),
        ("http://localhost:1234/v1", "http://localhost:1234/v1"),
        ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
        ("http://localhost:8080/v1/chat/completions", "http://localhost:8080/v1"),
        ("https://example.com/api/v1", "https://example.com/api/v1"),
        ("", ""),
    ],
)
def test_normalize_base_url(raw, expected):
    assert openai_client.normalize_base_url(raw) == expected


def test_is_local():
    assert openai_client.is_local("http://localhost:1234/v1")
    assert openai_client.is_local("http://127.0.0.1:8080/v1")
    assert not openai_client.is_local("https://api.example.com/v1")


# ---------------------------------------------------------------------------
# OpenAI-compatible client against the stub server
# ---------------------------------------------------------------------------


def test_models_and_generate(stub_server):
    assert run(openai_client.get_available_models()) == ["stub-model-1", "stub-model-2"]
    text = run(openai_client.generate("hi", "sys", json_mode=True))
    assert '"task": "Build ContextLayer"' in text

    post = [r for r in _StubOpenAI.requests if r[0] == "POST"][-1]
    body = post[3]
    assert body["model"] == "stub-model-1"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert body["response_format"] == {"type": "json_object"}
    assert "Authorization" not in post[2]


def test_retries_without_response_format(stub_server):
    _StubOpenAI.reject_response_format = True
    text = run(openai_client.generate("hi", json_mode=True))
    assert "Build ContextLayer" in text
    posts = [r[3] for r in _StubOpenAI.requests if r[0] == "POST"]
    assert "response_format" in posts[0]
    assert "response_format" not in posts[1]


def test_api_key_and_forced_model(stub_server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "my-model")
    run(openai_client.generate("hi"))
    post = [r for r in _StubOpenAI.requests if r[0] == "POST"][-1]
    assert post[2].get("Authorization") == "Bearer test-key"
    assert post[3]["model"] == "my-model"


def test_unreachable_server_fails_soft(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9")  # discard port
    assert run(openai_client.get_available_models()) == []
    assert run(openai_client.is_available()) is False
    assert run(openai_client.generate("hi")) == ""


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_auto_prefers_openai_when_configured(stub_server, monkeypatch):
    monkeypatch.setattr(ollama_client, "is_available", _offline)
    status = run(llm_client.resolve())
    assert status.provider == "openai" and status.available


def test_auto_falls_back_to_ollama(monkeypatch):
    async def online():
        return True

    async def models():
        return ["llama3.2:3b"]

    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setattr(ollama_client, "is_available", online)
    monkeypatch.setattr(ollama_client, "get_available_models", models)
    status = run(llm_client.resolve())
    assert status.provider == "ollama" and status.models == ["llama3.2:3b"]


def test_provider_none_disables_llm(stub_server, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    status = run(llm_client.resolve())
    assert status.provider == "none" and not status.available
    assert _StubOpenAI.requests == []


def test_nothing_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setattr(ollama_client, "is_available", _offline)
    assert run(llm_client.resolve()).available is False


# ---------------------------------------------------------------------------
# End to end: /handoff and /health through the OpenAI-compatible provider
# ---------------------------------------------------------------------------


def test_handoff_endpoint_uses_openai(stub_server, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setattr(ollama_client, "is_available", _offline)
    with TestClient(main.app) as client:
        health = client.get("/health").json()
        assert health["llm_provider"] == "openai"
        assert health["llm_models"] == ["stub-model-1", "stub-model-2"]

        r = client.post(
            "/handoff",
            json={"session_id": "s", "messages": [{"role": "user", "content": "Build it"}]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["method"] == "openai"
        assert body["structured"]["constraints"] == ["Local only"]
        assert body["structured"]["tone"] == "technical"


# ---------------------------------------------------------------------------
# Prompt trimming for small context windows
# ---------------------------------------------------------------------------


def test_conversation_for_prompt_keeps_task_and_recent():
    msgs = [{"role": "user", "content": "TASK: build the thing"}]
    for i in range(200):
        msgs.append({"role": "assistant" if i % 2 else "user", "content": f"message number {i} " + "x " * 50})
    text = handoff.conversation_for_prompt(msgs, max_tokens=800)
    assert text.startswith("user: TASK: build the thing")
    assert "earlier messages omitted" in text
    assert "message number 199" in text
    assert handoff._count_tokens(text) <= 800


def test_conversation_for_prompt_short_is_unchanged():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert handoff.conversation_for_prompt(msgs, 6000) == "user: hi\nassistant: hello"
