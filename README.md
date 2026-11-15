# ContextLayer

[![CI](https://github.com/K3RAE/-Context-layer/actions/workflows/ci.yml/badge.svg)](https://github.com/K3RAE/-Context-layer/actions/workflows/ci.yml)

Local-first **context compression, conversation handoff and persistent memory**
for LLM chat sites (Claude, ChatGPT, Gemini, Perplexity, Grok).

It has two parts:

| Part | What it does |
| --- | --- |
| `extension/` | Chrome extension (Manifest V3). Reads the conversation on the page, shows token usage, and calls the backend. |
| `backend/` | FastAPI server on `localhost`. Compresses, builds handoff notes and stores "memory" facts in a local SQLite DB. |

Nothing leaves your machine. [Ollama](https://ollama.com) and
[LLMLingua-2](https://github.com/microsoft/LLMLingua) are **optional**: without them
ContextLayer uses rule-based fallbacks.

---

## Quick start

**Requirements:** Python 3.10+ and Google Chrome (or another Chromium browser).
Node.js is *not* required unless you want to build a minified extension bundle.

### 1. Get the code

```bash
git clone https://github.com/K3RAE/-Context-layer.git ContextLayer
cd ContextLayer
```

### 2. Install & start the backend

| OS | Command |
| --- | --- |
| **Windows** | Double-click `start.bat` (or run `start.bat` in a terminal) |
| **macOS / Linux** | `./start.sh` |

The first run creates a virtual environment in `.venv/`, installs the
dependencies and creates `backend/.env`. Later runs start in a couple of seconds.
The API listens on <http://127.0.0.1:8000>. Check it with <http://127.0.0.1:8000/health>.

To install without starting, run `setup.bat` / `./setup.sh`. Options:

| Flag | Effect |
| --- | --- |
| `--ml` | Also install LLMLingua-2 neural compression (large: pulls in PyTorch, ~700 MB model download on first use) |
| `--dev` | Also install test and lint tools |
| `--extension` | Also build the extension with npm (optional) |
| `--recreate` | Rebuild a broken `.venv` |

### 3. Load the Chrome extension

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose the **`extension`** folder of this repo.
4. Pin the ContextLayer icon. Open a chat on a supported site and click it.

### 4. (Optional) A local LLM for smarter handoffs

Without an LLM, every feature still works using rule-based logic. With one,
handoff notes become real summaries. Use **either**:

- **Ollama.** Install it from <https://ollama.com> and run `ollama pull llama3.2:3b`. It is detected automatically.
- **Any OpenAI-compatible server**, such as [LM Studio](https://lmstudio.ai), the llama.cpp server, [Jan](https://jan.ai), LocalAI or vLLM. Start its local server, then set the URL in `backend/.env`:

  ```ini
  OPENAI_BASE_URL=http://localhost:1234/v1   # LM Studio's default
  OPENAI_MODEL=                               # optional; blank = first listed model
  ```

`LLM_PROVIDER=auto` uses the OpenAI-compatible server if it is set and running,
then Ollama, then rule-based. You can force one with `ollama`, `openai` or `none`.
`/health` shows which provider is active.

---

## Troubleshooting

Run the doctor:

```bash
python scripts/manage.py doctor
```

| Problem | Fix |
| --- | --- |
| `Python 3.10+ was not found` | Windows: `winget install Python.Python.3.12`, or install from python.org and tick *Add python.exe to PATH*. macOS: `brew install python`. Ubuntu/Debian: `sudo apt install python3 python3-venv` |
| `Could not create the virtual environment` (Linux) | `sudo apt install python3-venv`, then `./setup.sh --recreate` |
| `./start.sh: Permission denied` | `chmod +x setup.sh start.sh` |
| `Port 8000 is already in use` | ContextLayer is already running, or change `BACKEND_PORT` in `backend/.env` *and* the Backend URL in the extension's settings |
| Popup says "Backend offline" | Start the backend; check <http://127.0.0.1:8000/health> |
| Popup shows 0 tokens | Reload the chat tab after installing or updating the extension |

## Configuration

All settings live in `backend/.env` (copied from [`backend/.env.example`](backend/.env.example)):

| Variable | Default | Description |
| --- | --- | --- |
| `BACKEND_HOST` | `127.0.0.1` | Interface to bind. The API has no auth, so keep it local |
| `BACKEND_PORT` | `8000` | Port |
| `BACKEND_RELOAD` | `false` | Auto-reload on code changes (development) |
| `LLM_PROVIDER` | `auto` | `auto`, `ollama`, `openai` or `none` |
| `OPENAI_BASE_URL` | *(blank)* | OpenAI-compatible server, e.g. `http://localhost:1234/v1` |
| `OPENAI_API_KEY` | *(blank)* | Only if your server needs one |
| `OPENAI_MODEL` | *(auto)* | Model id to use |
| `LLM_MAX_PROMPT_TOKENS` | `6000` | Long chats are trimmed to this size before being sent to the model |
| `LLM_TIMEOUT` | `120` | Seconds to wait for the model |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | *(auto)* | Force a specific Ollama model |
| `MEMORY_DB_PATH` | `~/.contextlayer/memory.db` | SQLite memory database |
| `LLMLINGUA_ENABLED` | `true` | Use LLMLingua-2 if it is installed |
| `LOG_LEVEL` | `INFO` | Logging level |

## Development

```bash
python scripts/manage.py setup --dev   # or: setup.bat --dev / ./setup.sh --dev
python scripts/manage.py test          # run the test-suite
```

Build a minified extension bundle into `extension/dist/` (optional):

```bash
cd extension
npm ci
npm run check
npm run build:prod
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) does three things:

- It runs lint, tests and a live server smoke test on Windows, macOS and Linux with Python 3.10, 3.12 and 3.13.
- It runs the `setup.bat` / `setup.sh` installers on every OS.
- It validates and builds the extension.

See [`backend/README.md`](backend/README.md) for the API reference.

## Known limitations

The chat-page DOM selectors in `extension/content.js` (`SITE_CONFIG`) are
written for each site's current markup. LLM sites change their HTML often. If
a site shows 0 messages, update its `userSelector` / `assistantSelector` there.

## License

MIT
