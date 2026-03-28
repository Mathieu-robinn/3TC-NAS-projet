#!/usr/bin/env bash
# Installe les dépendances Python du projet (Linux / macOS / Git Bash).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[ERR] python3 ou python introuvable." >&2
  exit 1
fi

if [[ "${1:-}" == "--venv" ]]; then
  echo "[INFO] Création du venv .venv ..."
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PY=python
fi

echo "[INFO] Mise à jour pip ..."
"$PY" -m pip install --upgrade pip

echo "[INFO] Installation depuis requirements.txt ..."
"$PY" -m pip install -r requirements.txt

echo "[OK] Terminé. Active le venv avec: source .venv/bin/activate (si --venv utilisé)."
