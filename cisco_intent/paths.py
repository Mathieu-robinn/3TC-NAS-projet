# -*- coding: utf-8 -*-
"""
================================================================================
paths.py — Ancrage de tous les chemins sur la racine du dépôt Git
================================================================================

Pourquoi ce module ?
  Les sorties vivent sous ``configs/<topology>/`` (``live/``, ``staging/``, ``backup/``, …).
  ``topology`` est le champ racine ``name`` de l'intent JSON. Si on se basait sur le cwd,
  lancer une commande depuis un autre dossier casserait les chemins. ``PROJECT_ROOT`` est
  dérivé de l'emplacement de *ce fichier* : le parent du package ``cisco_intent/`` est la
  racine du projet.

Données / effets :
  - Retourne des ``pathlib.Path`` ; ``prepare_dir_for_generation`` prépare un dossier cible.

Liens : ``generator.generate_configs`` écrit dans ``live_dir(topology)`` ou ``output_dir`` ;
         ``config_update`` utilise ``staging_dir(topology)``, ``live_dir(topology)`` ;
         les modifs vont en zip sous ``configs/<topology>/backup/modifs/`` ;
         ``sync_live_from_run`` met à jour ``configs/<topology>/live/``.
================================================================================
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# ``Path(__file__)`` = ce fichier ; .parent = cisco_intent/ ; .parent.parent = racine du repo
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

_INTENT_JSON_SKIP = frozenset({"old.intent.json", "new.intent.json", "metadata.json"})


def configs_data_root() -> Path:
    """Racine ``configs/`` : uniquement des sous-dossiers par topologie (``name`` intent)."""
    return PROJECT_ROOT / "configs"


def intent_json_files_in_dir(run_dir: Path) -> List[Path]:
    """Fichiers intent JSON copiés dans un run (``live/``, ``staging/``, …), hors métadonnées update."""
    run_dir = run_dir.resolve()
    return sorted(
        [
            p
            for p in run_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() == ".json"
            and p.name.lower() not in _INTENT_JSON_SKIP
        ],
        key=lambda p: p.name.lower(),
    )


def find_intent_in_run_dir(run_dir: Path) -> Path:
    """
    Choisit l'intent JSON dans un dossier de configs.

    Préfère un nom commençant par ``Intent`` ; sinon le seul ``*.json`` présent.
    """
    intents = intent_json_files_in_dir(run_dir)
    if not intents:
        raise FileNotFoundError(f"Aucun intent JSON trouvé dans {run_dir}")
    for p in intents:
        if p.name.lower().startswith("intent"):
            return p
    if len(intents) == 1:
        return intents[0]
    names = ", ".join(p.name for p in intents)
    raise ValueError(f"Plusieurs fichiers intent JSON dans {run_dir}: {names}")


def default_c7200_startup_template() -> Path:
    """Config IOS de base C7200 : copiée vers chaque startup Dynamips par ``reset``."""
    return configs_data_root() / "default" / "default-conf-C7200.txt"


def topology_root(topology: str) -> Path:
    """Racine d'une topologie : ``configs/<topology>/``."""
    return configs_data_root() / topology


def live_dir(topology: str) -> Path:
    """Configs « appliquées » / référence : ``*.cfg`` + copie d'intent (voir CLI ``generate`` / ``push``)."""
    return topology_root(topology) / "live"


def live_dir_has_cfg_files(topology: str) -> bool:
    """True si ``live/`` existe et contient au moins un ``*.cfg``."""
    ld = live_dir(topology)
    return ld.is_dir() and any(ld.glob("*.cfg"))


def staging_dir(topology: str) -> Path:
    """
    Brouillon : jeu complet produit par ``update``, ou par ``generate`` sans ``--push``
    lorsque ``live/`` contient déjà des ``*.cfg``. Vidé après copie vers ``live/``
    (``update --push`` réussi ou ``push`` manuel depuis un dossier autre que ``live/``).
    """
    return topology_root(topology) / "staging"


def staging_dir_has_cfg_files(topology: str) -> bool:
    """True si ``staging/`` existe et contient au moins un ``*.cfg``."""
    sd = staging_dir(topology)
    return sd.is_dir() and any(sd.glob("*.cfg"))


def scratch_old_intent_dir(topology: str) -> Path:
    """Baseline OLD lorsque ``update`` est lancé avec ``--old-intent`` (régénération depuis un JSON)."""
    return topology_root(topology) / "scratch_old"


def backup_full_configs_dir(topology: str) -> Path:
    """Archives zip des snapshots de configs complètes (nom ``Configs-YYYYMMDD-HHMMSS.zip``)."""
    return topology_root(topology) / "backup" / "full_configs"


def backup_modifs_dir(topology: str) -> Path:
    """Archives zip des runs ``Modifs-*``."""
    return topology_root(topology) / "backup" / "modifs"


def prepare_dir_for_generation(path: Path) -> Path:
    """
    Crée ``path`` s'il manque, supprime tous les fichiers directs (pas les sous-dossiers)
    pour une écriture de génération propre.

    Les sous-dossiers sont conservés volontairement: les dossiers de topologie
    contiennent aussi ``backup/`` et parfois des espaces de travail. Les outputs de
    génération attendus à ce niveau sont des fichiers directs ``*.cfg`` et ``Intent*.json``.
    """
    # On résout le chemin pour travailler avec un chemin absolu et éviter les
    # surprises si le répertoire courant change pendant l'exécution.
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    for p in list(path.iterdir()):
        if p.is_file():
            # On ne supprime que les fichiers directs générés au run précédent:
            # PE1.cfg, P1.cfg, Intent_*.json, metadata.json, etc.
            # Les sous-dossiers sont laissés en place volontairement.
            p.unlink()
    return path


def sync_live_from_run(source_run: Path, topology: str) -> None:
    """
    Remplace le contenu fichier de ``configs/<topology>/live/`` par les fichiers réguliers de ``source_run``.
    Sans effet si ``source_run`` est déjà ce ``live/`` (évite d'effacer puis recopier depuis soi-même).
    """
    source_run = source_run.resolve()
    if not source_run.is_dir():
        raise NotADirectoryError(f"sync_live_from_run: pas un dossier: {source_run}")
    dest = live_dir(topology).resolve()
    if source_run == dest:
        return
    live_dir(topology).mkdir(parents=True, exist_ok=True)
    for p in list(dest.iterdir()):
        if p.is_file():
            p.unlink()
    for p in source_run.iterdir():
        if p.is_file():
            shutil.copy2(p, dest / p.name)


def infer_topology_from_configs_path(cfg_dir: Path) -> Optional[str]:
    """
    Si ``cfg_dir`` ressemble à ``.../configs/<topology>/live`` ou ``.../staging``,
    retourne ``<topology>`` ; sinon ``None``.
    """
    resolved = cfg_dir.resolve()
    parts = resolved.parts
    for i, part in enumerate(parts):
        if part == "configs" and i + 2 < len(parts) and parts[i + 2] in ("live", "staging"):
            return parts[i + 1]
    return None


def configs_backup_stamp() -> str:
    """Horodatage pour nommer un zip ``Configs-*.zip`` dans ``backup/full_configs``."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")
