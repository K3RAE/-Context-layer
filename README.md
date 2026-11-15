# ContextLayer

[![CI](https://github.com/K3RAE/-Context-layer/actions/workflows/ci.yml/badge.svg)](https://github.com/K3RAE/-Context-layer/actions/workflows/ci.yml)

Local-first **context compression, conversation handoff and persistent memory**
for AI chat sites. It has built-in rules for Claude, ChatGPT, Gemini, Perplexity
and Grok, and automatic detection for other chat pages (DeepSeek, Mistral,
Copilot, Poe, and more). See [Site support](#site-support).

It has two parts:

| Part | What it does |
| --- | --- |
| `extension/` | Chrome extension (Manifest V3). Reads the conversation on the page, shows token usage, and calls the backend. |
| `backend/` | FastAPI server on `localhost`. Compresses, builds handoff notes and stores "memory" facts in a local SQLite DB. |

**Privacy:** your conversations are processed on your own machine. There are two exceptions:

- If you point `OPENAI_BASE_URL` at a non-local server, conversations are sent there.
- The extension downloads a small list of site rules (`selectors.json`, data only)
  from this repository once a day. You can turn this off in the popup's settings.

[Ollama](https://ollama.com), OpenAI-compatible servers and
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
npm test          # scraper tests
npm run check
npm run build:prod
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) does three things:

- It runs lint, tests and a live server smoke test on Windows, macOS and Linux with Python 3.10, 3.12 and 3.13.
- It runs the `setup.bat` / `setup.sh` installers on every OS.
- It validates and builds the extension.

See [`backend/README.md`](backend/README.md) for the API reference.

## Site support

AI chat sites change their HTML often, which breaks extensions that depend on
fixed CSS selectors. ContextLayer reads the conversation with three layers
([`extension/scraper.js`](extension/scraper.js)). It tries each in order, and a
layer only counts if its result looks like a real conversation:

1. **Site rules.** Per-site CSS selectors from
   [`extension/selectors.json`](extension/selectors.json). Each site has several
   candidates, newest markup first.
2. **Attribute detection.** Works on any site. It finds messages by role hints
   in the markup, such as `user-message`, `data-role="assistant"`,
   `model-response` or `aria-label="Your message"`, and ignores navigation,
   avatars and menus.
3. **Layout detection.** Finds the list of repeated message blocks and assigns
   user/assistant roles from hints or alternation.

The popup shows which layer read the chat ("site rules", "auto-detected" or
"no conversation found").

**Self-updating rules.** Once a day the extension fetches `selectors.json` from
this repo's `main` branch. It uses that copy if its `version` is higher than the
bundled one. The file is data only: it is validated, and it can change
selectors but not code or permissions. To fix a broken site for every user:

1. Open the site, press F12, and find an element that wraps each user message
   and each AI message.
2. Add the selectors to that site's lists in `extension/selectors.json`, newest
   first, and **bump `version`**.
3. Run `cd extension && npm test && npm run check`, then merge to `main`.
   Installed extensions pick up the change within a day, or straight away via
   Settings → **Check now**.

**Any site.** Click the ContextLayer icon on any chat page. If the site isn't in
the built-in list, the extension reads that tab on demand. This works only on
the tab you clicked, using Chrome's `activeTab` permission; ContextLayer never
gets blanket access to every site.

**Reporting problems.** Use Settings → **Copy diagnostics**. It copies which
layers ran and how many messages each one found, with no message text, which
you can paste into an issue.

**Limits.** Detection is best effort; no algorithm can guarantee it on a site
whose markup it has never seen.

- Rules for sites other than Claude, ChatGPT, Gemini, Perplexity and Grok start
  empty, so those sites rely on automatic detection.
- The site rules have not been verified against the live sites.
- The scraper tests (`npm test`) use hand-written pages that imitate common
  chat layouts.
- ContextLayer only reads the page you have open in your own browser. It does
  not crawl sites or get around their protections.

## License

MIT
