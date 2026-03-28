# -*- coding: utf-8 -*-
"""
================================================================================
paths.py — Ancrage de tous les chemins sur la racine du dépôt Git
================================================================================

Pourquoi ce module ?
  Les sorties vivent sous ``configs/`` (``live/``, ``staging/``, ``backup/``, …). Si on se basait sur le
  répertoire courant (cwd), lancer une commande depuis un autre dossier casserait
  les chemins. Ici, ``PROJECT_ROOT`` est dérivé de l'emplacement de *ce fichier* :
  le parent du package ``cisco_intent/`` est toujours la racine du projet.

Données / effets :
  - Retourne des ``pathlib.Path`` ; ``prepare_dir_for_generation`` prépare un dossier cible.

Liens : ``generator.generate_configs`` écrit où indique ``output_dir`` (``live/`` ou ``staging/`` selon la CLI) ;
         ``config_update`` utilise ``staging_dir``, ``live_dir`` ; les modifs vont en zip sous ``backup/modifs/`` ;
         ``sync_live_from_run`` met à jour ``configs/live/``.
================================================================================
"""

import shutil
from datetime import datetime
from pathlib import Path

# ``Path(__file__)`` = ce fichier ; .parent = cisco_intent/ ; .parent.parent = racine du repo
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def configs_data_root() -> Path:
    """Racine ``configs/`` : live, staging, backup, scratch_old."""
    return PROJECT_ROOT / "configs"


def live_dir() -> Path:
    """Configs « appliquées » / référence : ``*.cfg`` + copie d'intent (voir CLI ``generate`` / ``push``)."""
    return configs_data_root() / "live"


def live_dir_has_cfg_files() -> bool:
    """True si ``live/`` existe et contient au moins un ``*.cfg``."""
    ld = live_dir()
    return ld.is_dir() and any(ld.glob("*.cfg"))


def staging_dir() -> Path:
    """
    Brouillon : jeu complet produit par ``update``, ou par ``generate`` sans ``--push``
    lorsque ``live/`` contient déjà des ``*.cfg``. Vidé après copie vers ``live/``
    (``update --push`` réussi ou ``push`` manuel depuis un dossier autre que ``live/``).
    """
    return configs_data_root() / "staging"


def staging_dir_has_cfg_files() -> bool:
    """True si ``staging/`` existe et contient au moins un ``*.cfg``."""
    sd = staging_dir()
    return sd.is_dir() and any(sd.glob("*.cfg"))


def scratch_old_intent_dir() -> Path:
    """Baseline OLD lorsque ``update`` est lancé avec ``--old-intent`` (régénération depuis un JSON)."""
    return configs_data_root() / "scratch_old"


def backup_full_configs_dir() -> Path:
    """Archives zip des snapshots de configs complètes (nom ``Configs-YYYYMMDD-HHMMSS.zip``)."""
    return configs_data_root() / "backup" / "full_configs"


def backup_modifs_dir() -> Path:
    """Archives zip des runs ``Modifs-*``."""
    return configs_data_root() / "backup" / "modifs"


def prepare_dir_for_generation(path: Path) -> Path:
    """
    Crée ``path`` s'il manque, supprime tous les fichiers directs (pas les sous-dossiers)
    pour une écriture de génération propre.
    """
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    for p in list(path.iterdir()):
        if p.is_file():
            p.unlink()
    return path


def sync_live_from_run(source_run: Path) -> None:
    """
    Remplace le contenu fichier de ``configs/live/`` par les fichiers réguliers de ``source_run``.
    Sans effet si ``source_run`` est déjà ``live/`` (évite d'effacer puis recopier depuis soi-même).
    """
    source_run = source_run.resolve()
    if not source_run.is_dir():
        raise NotADirectoryError(f"sync_live_from_run: pas un dossier: {source_run}")
    dest = live_dir().resolve()
    if source_run == dest:
        return
    live_dir().mkdir(parents=True, exist_ok=True)
    for p in list(dest.iterdir()):
        if p.is_file():
            p.unlink()
    for p in source_run.iterdir():
        if p.is_file():
            shutil.copy2(p, dest / p.name)


def configs_backup_stamp() -> str:
    """Horodatage pour nommer un zip ``Configs-*.zip`` dans ``backup/full_configs``."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")
