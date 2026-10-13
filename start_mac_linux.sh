#!/usr/bin/env bash
# Install (first time) and start the Forum Post Extractor.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
source .venv/bin/activate
if [ ! -f .venv/installed.flag ]; then
  pip install -r requirements.txt
  python -m playwright install chromium
  touch .venv/installed.flag
fi
python -m forum_extractor serve
