#!/usr/bin/env python3
"""
ContextLayer project manager — works the same on Windows, macOS and Linux.

Uses only the Python standard library, so it runs before anything is
installed.  The setup.* / start.* wrappers in the repo root call this file.

    python scripts/manage.py setup [--ml] [--dev] [--extension]
    python scripts/manage.py start
    python scripts/manage.py test
    python scripts/manage.py doctor
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
EXTENSION = ROOT / "extension"
VENV = ROOT / ".venv"
ENV_FILE = BACKEND / ".env"
ENV_EXAMPLE = BACKEND / ".env.example"
MIN_PY = (3, 10)
IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"[contextlayer] {msg}", flush=True)


def die(msg: str, code: int = 1) -> "None":
    print(f"[contextlayer] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def run(cmd: list, **kwargs) -> None:
    info("$ " + " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], **kwargs)
    if result.returncode != 0:
        die(f"command failed with exit code {result.returncode}", result.returncode)


def read_env() -> dict:
    """Minimal .env reader (python-dotenv may not be installed yet)."""
    values: dict = {}
    for path in (ENV_EXAMPLE, ENV_FILE):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update({k: v for k, v in os.environ.items() if k in values})
    return values


def check_python() -> None:
    if sys.version_info < MIN_PY:
        die(
            f"Python {MIN_PY[0]}.{MIN_PY[1]}+ is required, but this is "
            f"{sys.version.split()[0]}. Install a newer Python from https://www.python.org/downloads/"
        )


def venv_is_healthy() -> bool:
    py = venv_python()
    if not py.exists():
        return False
    try:
        subprocess.run(
            [str(py), "-c", "import fastapi, uvicorn, dotenv, httpx, pydantic"],
            check=True, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> None:
    check_python()
    info(f"Using Python {sys.version.split()[0]} at {sys.executable}")

    if venv_python().exists() and not venv_is_healthy() and args.recreate:
        info("Removing broken virtual environment ...")
        shutil.rmtree(VENV)

    if not venv_python().exists():
        info(f"Creating virtual environment in {VENV} ...")
        try:
            import venv  # noqa: F401
        except ImportError:
            die("The 'venv' module is missing. On Debian/Ubuntu run: sudo apt install python3-venv")
        result = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
        if result.returncode != 0 or not venv_python().exists():
            if VENV.exists():
                shutil.rmtree(VENV, ignore_errors=True)
            die(
                "Could not create the virtual environment. On Debian/Ubuntu install it with: "
                "sudo apt install python3-venv"
            )

    py = venv_python()
    run([py, "-m", "pip", "install", "--upgrade", "pip"])

    req = "requirements.txt"
    if args.ml:
        req = "requirements-ml.txt"
    run([py, "-m", "pip", "install", "-r", BACKEND / req])
    if args.dev:
        run([py, "-m", "pip", "install", "-r", BACKEND / "requirements-dev.txt"])

    if not ENV_FILE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        info(f"Created {ENV_FILE.relative_to(ROOT)} from .env.example")
    else:
        info(f"Keeping existing {ENV_FILE.relative_to(ROOT)}")

    if args.extension:
        npm = shutil.which("npm")
        if not npm:
            info("npm not found - skipping extension build (it is optional; load extension/ directly).")
        else:
            run([npm, "install", "--no-audit", "--no-fund"], cwd=EXTENSION)
            run([npm, "run", "build"], cwd=EXTENSION)

    start_cmd = r".\start.bat" if IS_WINDOWS else "./start.sh"
    print()
    info("Setup complete.")
    info(f"  1. Start the backend:     {start_cmd}")
    info("  2. Load the extension:    chrome://extensions -> Developer mode -> Load unpacked -> "
         f"{EXTENSION}")
    info("  3. (Optional) Ollama:     install from https://ollama.com and run: ollama pull llama3.2:3b")


def cmd_start(args: argparse.Namespace) -> None:
    if not venv_is_healthy():
        info("Dependencies not installed yet - running setup first ...")
        cmd_setup(argparse.Namespace(ml=False, dev=False, extension=False, recreate=True))

    env = read_env()
    host = env.get("BACKEND_HOST", "127.0.0.1")
    port = int(env.get("BACKEND_PORT", "8000") or 8000)
    if _port_in_use(host, port):
        die(
            f"Port {port} is already in use. Is ContextLayer already running? "
            f"Otherwise change BACKEND_PORT in backend/.env"
        )

    info(f"Starting ContextLayer backend on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        result = subprocess.run([str(venv_python()), str(BACKEND / "main.py")], cwd=str(BACKEND))
    except KeyboardInterrupt:
        return
    sys.exit(result.returncode)


def cmd_test(_args: argparse.Namespace) -> None:
    if not venv_python().exists():
        cmd_setup(argparse.Namespace(ml=False, dev=True, extension=False, recreate=True))
    else:
        try:
            subprocess.run([str(venv_python()), "-c", "import pytest"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            run([venv_python(), "-m", "pip", "install", "-r", BACKEND / "requirements-dev.txt"])
    run([venv_python(), "-m", "pytest"], cwd=str(ROOT))


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1" if host in ("0.0.0.0", "") else host, port)) == 0


def cmd_doctor(_args: argparse.Namespace) -> None:
    ok = True

    def report(name: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'OK' if good else '!!'}] {name}{(' - ' + detail) if detail else ''}")

    env = read_env()
    print("ContextLayer doctor")
    report("Python version", sys.version_info >= MIN_PY, sys.version.split()[0])
    report("Virtual environment", venv_python().exists(), str(VENV))
    report("Backend dependencies", venv_is_healthy(), "run setup if missing")
    report("backend/.env", ENV_FILE.exists(), "optional; defaults are used if missing")

    port = int(env.get("BACKEND_PORT", "8000") or 8000)
    host = env.get("BACKEND_HOST", "127.0.0.1")
    running = _port_in_use(host, port)
    print(f"  [--] Backend on port {port}: {'running' if running else 'not running'}")

    ollama = env.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{ollama}/api/tags", timeout=2) as resp:
            print(f"  [--] Ollama at {ollama}: reachable (HTTP {resp.status})")
    except Exception:
        print(f"  [--] Ollama at {ollama}: not reachable (optional - rule-based fallback is used)")

    print(f"  [--] npm: {'found' if shutil.which('npm') else 'not found (only needed to build the extension)'}")
    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="ContextLayer project manager")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="create .venv, install dependencies, create backend/.env")
    p.add_argument("--ml", action="store_true", help="also install LLMLingua-2 (large: PyTorch)")
    p.add_argument("--dev", action="store_true", help="also install test/lint tools")
    p.add_argument("--extension", action="store_true", help="also npm-build the Chrome extension")
    p.add_argument("--recreate", action="store_true", help="recreate .venv if it is broken")
    p.set_defaults(func=cmd_setup)

    sub.add_parser("start", help="start the backend (runs setup first if needed)").set_defaults(func=cmd_start)
    sub.add_parser("test", help="run the test-suite").set_defaults(func=cmd_test)
    sub.add_parser("doctor", help="diagnose installation problems").set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
