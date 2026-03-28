# -*- coding: utf-8 -*-
"""
================================================================================
paths.py — Ancrage de tous les chemins sur la racine du dépôt Git
================================================================================

Pourquoi ce module ?
  Les outils écrivent dans ``Configs/``, ``modifs/``, etc. Si on se basait sur le
  répertoire courant (cwd), lancer une commande depuis un autre dossier casserait
  les chemins. Ici, ``PROJECT_ROOT`` est dérivé de l'emplacement de *ce fichier* :
  le parent du package ``cisco_intent/`` est toujours la racine du projet.

Données / effets :
  - Retourne des ``pathlib.Path`` ; ``make_configs_run_dir`` crée des dossiers sur disque.

Comment l'étendre ?
  Ajoute une fonction du type ``ma_dir() -> Path`` qui retourne
  ``PROJECT_ROOT / "mon_dossier"``. Garde ce fichier limité aux chemins, sans logique métier.

Liens : ``generator.generate_configs`` appelle ``make_configs_run_dir`` ;
         ``config_diff`` utilise ``configs_base_dir`` et ``modifs_base_dir`` par défaut.
================================================================================
"""

from datetime import datetime
from pathlib import Path

# ``Path(__file__)`` = ce fichier ; .parent = cisco_intent/ ; .parent.parent = racine du repo
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def configs_base_dir() -> Path:
    """Parent des sous-dossiers datés ``Configs-YYYYMMDD-HHMMSS`` (un run par génération)."""
    return PROJECT_ROOT / "Configs"


def modifs_base_dir() -> Path:
    """Parent des dossiers ``Modifs-*`` (sortie du ``diff`` : commandes IOS incrémentales)."""
    return PROJECT_ROOT / "modifs"


def make_configs_run_dir() -> Path:
    """Crée et retourne ``Configs/Configs-YYYYMMDD-HHMMSS/`` (horodatage à la seconde)."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = configs_base_dir()
    base.mkdir(parents=True, exist_ok=True)  # parents=True : crée Configs/ si besoin
    run_dir = base / f"Configs-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
