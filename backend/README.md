# ContextLayer — FastAPI Backend

Local-first context compression, conversation handoff, and persistent memory for LLM chat sessions.

## Architecture

```
Chrome Extension  ──►  FastAPI backend (this)  ──►  Ollama (local LLM)
                              │
                              ├─ /compress   →  LLMLingua-2 / rule-based
                              ├─ /handoff    →  Ollama / rule-based
                              ├─ /remember   →  SQLite knowledge graph
                              ├─ /recall     →  SQLite search
                              ├─ /memory     →  clear knowledge graph
                              └─ /health     →  subsystem status
```

## Quick Start

The easiest way is the one-command scripts in the repo root. They work on
Windows, macOS and Linux and handle the virtual environment, dependencies and
`.env` for you. See the [main README](../README.md).

```bash
# Windows
.\start.bat
# macOS / Linux
./start.sh
```

### Manual setup

```bash
cd backend
python -m venv .venv
# Windows:      .venv\Scriptsctivate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt          # core (small)
# pip install -r requirements-ml.txt     # optional: LLMLingua-2 (PyTorch, large)
cp .env.example .env                     # Windows: copy .env.example .env
python main.py                           # serves on http://127.0.0.1:8000
```

### Ollama (optional)

```bash
ollama pull llama3.2:3b     # best handoff quality
ollama pull qwen2.5:0.5b    # smallest / fastest
```

### Verify with curl

```bash
# Health check
curl http://localhost:8000/health | python -m json.tool

# Test compression
curl -X POST http://localhost:8000/compress \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Hello, how are you?"},
      {"role": "assistant", "content": "I am doing well, thank you for asking! How can I help you today?"},
      {"role": "user", "content": "Tell me about Python."},
      {"role": "assistant", "content": "Python is a high-level programming language known for its simplicity."},
      {"role": "user", "content": "What about FastAPI?"},
      {"role": "assistant", "content": "FastAPI is a modern web framework for building APIs with Python."}
    ],
    "preserve_last_n": 2
  }' | python -m json.tool

# Test handoff
curl -X POST http://localhost:8000/handoff \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "I need help building a REST API."},
      {"role": "assistant", "content": "Sure! Let us use FastAPI."}
    ],
    "session_id": "test-session-001"
  }' | python -m json.tool

# Test remember
curl -X POST http://localhost:8000/remember \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-001",
    "messages": [
      {"role": "user", "content": "I am building a Chrome extension called ContextLayer."},
      {"role": "assistant", "content": "Great! We will use FastAPI for the backend."}
    ],
    "source_llm": "gpt-4o"
  }' | python -m json.tool

# Test recall
curl -X POST http://localhost:8000/recall \
  -H "Content-Type: application/json" \
  -d '{"query": "Chrome extension", "top_k": 5}' | python -m json.tool

# Clear memory
curl -X DELETE http://localhost:8000/memory | python -m json.tool
```

## API Reference

| Method   | Path        | Description                              |
| -------- | ----------- | ---------------------------------------- |
| `POST`   | `/compress` | Compress conversation history            |
| `POST`   | `/handoff`  | Generate structured context handoff      |
| `POST`   | `/remember` | Extract & store knowledge from messages  |
| `POST`   | `/recall`   | Search stored knowledge                  |
| `DELETE` | `/memory`   | Wipe all stored facts                    |
| `GET`    | `/health`   | Subsystem health & uptime                |

## Graceful Degradation

ContextLayer is designed to work even when optional subsystems are unavailable:

| Subsystem   | When unavailable                                      |
| ----------- | ----------------------------------------------------- |
| Ollama      | Handoff & memory extraction fall back to rule-based   |
| LLMLingua-2 | Compression falls back to rule-based heuristics       |
| SQLite DB   | Memory endpoints return 503                           |

`/handoff` returns `"method": "ollama"` or `"rule_based"` so you can see which path ran.

## Project Structure

```
backend/
├── main.py             # FastAPI app, routes, middleware (python main.py to run)
├── models.py           # Pydantic request/response schemas
├── ollama_client.py    # Async Ollama HTTP wrapper
├── compressor.py       # LLMLingua-2 + rule-based compression
├── handoff.py          # Structured handoff extraction
├── memory.py           # SQLite knowledge graph
├── requirements.txt    # Core Python dependencies
├── requirements-ml.txt # Optional LLMLingua-2 (PyTorch)
├── requirements-dev.txt# Test / lint tools
├── .env.example        # Environment variable template
└── README.md           # This file
```

## License

MIT
