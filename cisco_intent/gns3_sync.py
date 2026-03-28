#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
gns3_sync.py — Copier les .cfg générés vers les startup-config Dynamips (disque)
================================================================================

Différence avec ``gns3_push`` :
  - ``push`` envoie les commandes *en live* via telnet (console GNS3).
  - ``sync-startup`` écrit les fichiers sur disque dans l'arborescence du projet GNS3
    (``project-files/dynamips/<node_id>/configs/i<dynamips_id>_startup-config.cfg``),
    ce qui alimente le routeur au prochain démarrage.

Correspondance nœud ↔ fichier :
  Le fichier ``.gns3`` liste les nœuds Dynamips avec ``name``, ``node_id`` et
  ``properties.dynamips_id``.   Le nom du routeur (ex. PE1) sert à trouver ``PE1.cfg`` dans ``configs/<topology>/live/`` (via
  ``--topology``) ou dans un dossier explicite via ``--configs-dir``.

Pour étendre :
  - Autre hyperviseur : il faudrait un autre mapping que ``DynamipsNode.startup_filename``.
  - ``reset`` : copie un fichier template unique (défaut ``configs/default/default-conf-C7200.txt``)
    vers chaque startup-config Dynamips ; la séquence ``%h`` dans le template est remplacée par le
    **nom du nœud** GNS3 (ex. ``PE1``), typiquement pour ``hostname …``.
================================================================================
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from cisco_intent.paths import default_c7200_startup_template, live_dir


@dataclass(frozen=True)
class DynamipsNode:
    """Métadonnées issues du JSON GNS3 pour localiser le fichier startup sur disque."""

    name: str
    node_id: str
    dynamips_id: int

    @property
    def startup_filename(self) -> str:
        """Convention Dynamips dans le dossier ``configs`` du nœud."""
        return f"i{self.dynamips_id}_startup-config.cfg"


def _load_json(path: Path) -> Any:
    """Charge un fichier JSON UTF-8 (typiquement le ``.gns3`` du projet)."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_dynamips_nodes(gns3_json: dict[str, Any]) -> Iterable[DynamipsNode]:
    """Itère sur les nœuds ``node_type == dynamips`` du projet, avec champs validés."""
    topo = gns3_json.get("topology", {})
    for node in topo.get("nodes", []):
        if node.get("node_type") != "dynamips":
            continue
        name = node.get("name")
        node_id = node.get("node_id")
        props = node.get("properties", {}) or {}
        dynamips_id = props.get("dynamips_id")

        if not isinstance(name, str) or not name:
            raise ValueError(f"Node dynamips sans name valide: {node}")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError(f"Node dynamips sans node_id valide: {node}")
        if not isinstance(dynamips_id, int):
            raise ValueError(f"Node dynamips {name} sans properties.dynamips_id entier: {dynamips_id!r}")

        yield DynamipsNode(name=name, node_id=node_id, dynamips_id=dynamips_id)


def format_row(cols: list[str], widths: list[int]) -> str:
    """Formate une ligne de tableau alignée (troncature avec « … » si trop long)."""
    padded = [(c[: w - 1] + "…") if len(c) > w else c for c, w in zip(cols, widths)]
    return " | ".join(c.ljust(w) for c, w in zip(padded, widths))


def main(argv: list[str]) -> int:
    """Sous-commande ``sync-startup`` : parse les args, copie les .cfg vers les startup Dynamips."""
    parser = argparse.ArgumentParser(
        description=(
            "Analyse un projet GNS3 (.gns3) pour lier node_id <-> name, puis copie les <name>.cfg "
            "depuis configs/<topology>/live/ (--topology) ou --configs-dir vers le startup-config Dynamips."
        )
    )
    parser.add_argument(
        "project_root",
        help=(
            "Racine du projet GNS3, par ex. gns3/projet_gns3_1. "
            "Le fichier .gns3 est déduit comme <project_root>/<nom_du_dossier>.gns3 sauf si --gns3-file est fourni."
        ),
    )
    parser.add_argument(
        "--gns3-file",
        default=None,
        help=(
            "Chemin vers le fichier .gns3. "
            "Par défaut: <project_root>/<nom_du_dossier>.gns3"
        ),
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help=(
            "Racine du projet GNS3. Par défaut: valeur de l'argument positionnel project_root. "
            "Utile si l'argument positionnel est un alias/symlink."
        ),
    )
    parser.add_argument(
        "--topology",
        default=None,
        metavar="NAME",
        help=(
            "Identifiant de topologie (champ « name » de l'intent) : source = configs/<NAME>/live/. "
            "Requis si --configs-dir est omis."
        ),
    )
    parser.add_argument(
        "--configs-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Dossier contenant directement les <hostname>.cfg. "
            "Ex.: dossier extrait d'une archive backup/full_configs ou configs/<NAME>/staging."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien, affiche seulement les copies qui seraient effectuées.",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Continue même si certaines configs sources manquent (skip). Par défaut, stop à la première erreur.",
    )
    args = parser.parse_args(argv)

    if args.configs_dir is None and args.topology is None:
        parser.error("Fournis --topology <name> (configs/<name>/live/) ou --configs-dir <chemin>.")

    # Détermination de la racine du projet GNS3
    positional_root = Path(args.project_root)
    project_root = Path(args.project_root) if args.project_root else positional_root

    # Si aucun --gns3-file n'est fourni, on déduit le nom du fichier à partir du dossier
    if args.gns3_file is not None:
        gns3_file = Path(args.gns3_file)
    else:
        project_name = project_root.name
        gns3_file = project_root / f"{project_name}.gns3"
    if args.configs_dir is not None:
        cd = args.configs_dir
        configs_source = cd.resolve() if cd.is_absolute() else (Path.cwd() / cd).resolve()
    else:
        assert args.topology is not None
        configs_source = live_dir(args.topology)
    strict = not args.no_strict

    gns3_json = _load_json(gns3_file)
    nodes = sorted(iter_dynamips_nodes(gns3_json), key=lambda n: n.name)
    if not nodes:
        raise RuntimeError("Aucun node dynamips trouvé dans le fichier .gns3")

    if not configs_source.is_dir():
        raise FileNotFoundError(f"Dossier configs source introuvable: {configs_source}")

    dynamips_root = project_root / "project-files" / "dynamips"
    if not dynamips_root.is_dir():
        raise FileNotFoundError(f"Dossier dynamips introuvable: {dynamips_root}")

    widths = [18, 36, 10, 10]
    print(f"Fichier .gns3: {gns3_file}")
    print(f"Dossier configs source: {configs_source}")
    print(f"Dossier dynamips: {dynamips_root}")
    print("")
    print(format_row(["name", "node_id", "dyn_id", "status"], widths))
    print(format_row(["-" * 4, "-" * 7, "-" * 6, "-" * 6], widths))

    errors: list[str] = []
    copied = 0
    skipped = 0

    for n in nodes:
        # Même convention de nommage que le générateur : ``<hostname>.cfg``
        src_cfg = configs_source / f"{n.name}.cfg"
        dst_dir = dynamips_root / n.node_id / "configs"
        dst_cfg = dst_dir / n.startup_filename

        if not src_cfg.is_file():
            msg = f"Config source manquante pour {n.name}: {src_cfg}"
            if strict:
                raise FileNotFoundError(msg)
            errors.append(msg)
            skipped += 1
            print(format_row([n.name, n.node_id, str(n.dynamips_id), "MISSING_SRC"], widths))
            continue

        if args.dry_run:
            print(format_row([n.name, n.node_id, str(n.dynamips_id), "DRY_RUN"], widths))
            continue

        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_cfg, dst_cfg)
        copied += 1
        print(format_row([n.name, n.node_id, str(n.dynamips_id), "COPIED"], widths))

    print("")
    if args.dry_run:
        print(f"Dry-run terminé. {len(nodes)} nodes analysés.")
    else:
        print(f"Copie terminée. {copied} copiés, {skipped} ignorés.")

    if errors:
        print("\nErreurs (mode non-strict):")
        for e in errors:
            print(f"- {e}")
        return 2

    return 0


def main_reset(argv: list[str]) -> int:
    """
    Sous-commande ``reset`` : copie le template IOS par défaut vers chaque startup Dynamips
    du projet (même arborescence ``project-files/dynamips/...`` que ``sync-startup``).
    """
    parser = argparse.ArgumentParser(
        prog="python -m cisco_intent reset",
        description=(
            "Pour chaque nœud Dynamips du projet GNS3, copie la config par défaut C7200 vers le "
            "fichier startup sur disque (même logique de chemins que sync-startup). "
            "Dans le template, %h est remplacé par le nom du routeur (champ name du nœud dans le .gns3)."
        ),
    )
    parser.add_argument(
        "project_root",
        help=(
            "Racine du projet GNS3, par ex. gns3/projet_gns3_1. "
            "Le fichier .gns3 est <project_root>/<nom_du_dossier>.gns3 sauf si --gns3-file."
        ),
    )
    parser.add_argument(
        "--gns3-file",
        default=None,
        help="Chemin vers le fichier .gns3 (relatif au répertoire courant ou absolu).",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        metavar="FILE",
        help="Fichier source (défaut : configs/default/default-conf-C7200.txt à la racine du dépôt).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien, affiche seulement les copies qui seraient effectuées.",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root)
    if args.gns3_file is not None:
        gns3_file = Path(args.gns3_file)
    else:
        gns3_file = project_root / f"{project_root.name}.gns3"

    if args.template is not None:
        tpl = args.template.resolve() if args.template.is_absolute() else (Path.cwd() / args.template).resolve()
    else:
        tpl = default_c7200_startup_template().resolve()

    gns3_json = _load_json(gns3_file)
    nodes = sorted(iter_dynamips_nodes(gns3_json), key=lambda n: n.name)
    if not nodes:
        raise RuntimeError("Aucun node dynamips trouvé dans le fichier .gns3")

    if not tpl.is_file():
        raise FileNotFoundError(f"Fichier template introuvable: {tpl}")

    template_text = tpl.read_text(encoding="utf-8")

    dynamips_root = project_root / "project-files" / "dynamips"
    if not dynamips_root.is_dir():
        raise FileNotFoundError(f"Dossier dynamips introuvable: {dynamips_root}")

    widths = [18, 36, 10, 10]
    print(f"Fichier .gns3: {gns3_file}")
    print(f"Template source: {tpl}")
    print(f"Dossier dynamips: {dynamips_root}")
    print("")
    print(format_row(["name", "node_id", "dyn_id", "status"], widths))
    print(format_row(["-" * 4, "-" * 7, "-" * 6, "-" * 6], widths))

    copied = 0
    for n in nodes:
        dst_dir = dynamips_root / n.node_id / "configs"
        dst_cfg = dst_dir / n.startup_filename
        if args.dry_run:
            print(format_row([n.name, n.node_id, str(n.dynamips_id), "DRY_RUN"], widths))
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        personalized = template_text.replace("%h", n.name)
        dst_cfg.write_text(personalized, encoding="utf-8", newline="\n")
        copied += 1
        print(format_row([n.name, n.node_id, str(n.dynamips_id), "COPIED"], widths))

    print("")
    if args.dry_run:
        print(f"Dry-run terminé. {len(nodes)} nodes analysés.")
    else:
        print(f"Reset terminé. {copied} startup-config écrits.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
