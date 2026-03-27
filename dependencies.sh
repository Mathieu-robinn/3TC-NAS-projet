#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== 3TC NAS Project – installation des dépendances Python ==="

if ! command -v python >/dev/null 2>&1 && ! command_v python3 >/dev/null 2>&1; then
  echo "Erreur : Python n'est pas installé ou introuvable dans le PATH."
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! "$PYTHON_BIN" -V >/dev/null 2>&1; then
  PYTHON_BIN=python3
fi

echo "Utilisation de l'interpréteur : $PYTHON_BIN"

VENV_DIR="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "Création de l'environnement virtuel dans .venv ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  echo "Environnement virtuel déjà présent dans .venv"
fi

if [ -f "$VENV_DIR/bin/activate" ]; then
  # Linux / macOS / Git Bash
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
  # Windows (Git Bash / WSL)
  # shellcheck disable=SC1091
  source "$VENV_DIR/Scripts/activate"
fi

REQ_FILE="$PROJECT_ROOT/requirements.txt"
if [ -f "$REQ_FILE" ]; then
  echo "Installation des dépendances depuis requirements.txt ..."
  pip install --upgrade pip
  pip install -r "$REQ_FILE"
else
  echo "Aucun requirements.txt trouvé."
  echo "Les scripts existants et l'interface Tkinter utilisent uniquement la bibliothèque standard Python."
fi

echo
echo "Dépendances installées."
echo "Pour utiliser l'environnement :"
echo "  source .venv/bin/activate    # Linux/macOS/Git Bash"
echo "  source .venv/Scripts/activate  # Windows (Git Bash)"

