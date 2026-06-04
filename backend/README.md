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

### 1. Install Python dependencies

```bash
# Create and activate a virtual environment (recommended)
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Install & start Ollama

Download Ollama from [ollama.com](https://ollama.com/) and install it for your platform.

```bash
# Start Ollama (if not running as a service)
ollama serve

# Pull recommended models (smallest → largest)
ollama pull qwen2:0.5b       # Fast, good for compression
ollama pull phi3-mini         # Balanced
ollama pull llama3.2:3b       # Best accuracy for handoff & memory
```

### 3. Configure environment

```bash
# Copy the example env file
cp .env.example .env

# Edit .env if you need to change defaults (port, Ollama URL, DB path)
```

### 4. Start the backend

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API is now available at `http://localhost:8000`.

### 5. Load the Chrome extension

1. Open `chrome://extensions/` in Chrome.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select the `context-layer/extension/` directory.
4. The ContextLayer icon should appear in your toolbar.

### 6. Verify with curl

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

## Project Structure

```
backend/
├── main.py             # FastAPI app, routes, middleware
├── models.py           # Pydantic request/response schemas
├── ollama_client.py    # Async Ollama HTTP wrapper
├── compressor.py       # LLMLingua-2 + rule-based compression
├── handoff.py          # Structured handoff extraction
├── memory.py           # SQLite knowledge graph
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
└── README.md           # This file
```

## License

MIT
