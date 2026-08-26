#!/usr/bin/env bash
# Regenerate the technical documentation figures and Word documents.
#   ./scripts/docs/build_docs.sh        (run from the repo root)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
cd "$REPO/scripts/docs"

mkdir -p "$REPO/Docs/technical/fig" fig   # fig/ is the scripts' local output dir
"$PY" fig_brief.py && "$PY" fig_detail.py && "$PY" fig_path.py && "$PY" fig_storage.py
cp fig/*.png "$REPO/Docs/technical/fig/"

"$PY" md2docx.py "$REPO/Docs/technical/01-technical-brief.md" \
  "$REPO/Docs/technical/2PP_Platform_Technical_Brief.docx" \
  "AI-Driven Lab Control — Technical Brief" \
  "How the system works, in plain language" \
  "labgate platform  ·  PhotonicsAI Lab  ·  companion to the detailed technical documentation" fig

"$PY" md2docx.py "$REPO/Docs/technical/02-detailed-technical-documentation.md" \
  "$REPO/Docs/technical/2PP_Platform_Technical_Documentation.docx" \
  "AI-Driven Lab Control — Technical Documentation" \
  "Architecture, code paths, data model and interfaces" \
  "labgate platform  ·  PhotonicsAI Lab  ·  every code reference and record sample taken from a live run" fig

echo "Documents rebuilt in Docs/technical/"
