#!/usr/bin/env sh
# ContextLayer - one-time setup (macOS / Linux).
# Usage: ./setup.sh [--ml] [--dev] [--extension]
set -e
cd "$(dirname "$0")"
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    exec "$candidate" scripts/manage.py setup "$@"
  fi
done
echo "Python 3.10+ was not found. Install it from https://www.python.org/downloads/" >&2
echo "  macOS:  brew install python" >&2
echo "  Ubuntu: sudo apt install python3 python3-venv" >&2
exit 1
