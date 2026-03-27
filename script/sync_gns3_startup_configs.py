#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class DynamipsNode:
    name: str
    node_id: str
    dynamips_id: int

    @property
    def startup_filename(self) -> str:
        return f"i{self.dynamips_id}_startup-config.cfg"


CONFIGS_DIR_RE = re.compile(r"^Configs-(\d{8}-\d{6})$")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_dynamips_nodes(gns3_json: dict[str, Any]) -> Iterable[DynamipsNode]:
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


def find_latest_configs_dir(configs_base: Path) -> Path:
    if not configs_base.exists():
        raise FileNotFoundError(f"Dossier configs_base introuvable: {configs_base}")
    if not configs_base.is_dir():
        raise NotADirectoryError(f"configs_base n'est pas un dossier: {configs_base}")

    candidates: list[tuple[str, Path]] = []
    for p in configs_base.iterdir():
        if not p.is_dir():
            continue
        m = CONFIGS_DIR_RE.match(p.name)
        if not m:
            continue
        candidates.append((m.group(1), p))

    if not candidates:
        raise FileNotFoundError(f"Aucun dossier Configs-YYYYMMDD-HHMMSS trouvé dans {configs_base}")

    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def format_row(cols: list[str], widths: list[int]) -> str:
    padded = [(c[: w - 1] + "…") if len(c) > w else c for c, w in zip(cols, widths)]
    return " | ".join(c.ljust(w) for c, w in zip(padded, widths))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyse un projet GNS3 (.gns3) pour lier node_id <-> name, puis copie la dernière config "
            "générée (script/Configs/Configs-*/<name>.cfg) vers le startup-config Dynamips attendu par GNS3."
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
        "--configs-base",
        default=str(Path("script") / "Configs"),
        help="Dossier contenant les runs Configs-YYYYMMDD-HHMMSS (défaut: script/Configs)",
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

    # Détermination de la racine du projet GNS3
    positional_root = Path(args.project_root)
    project_root = Path(args.project_root) if args.project_root else positional_root

    # Si aucun --gns3-file n'est fourni, on déduit le nom du fichier à partir du dossier
    if args.gns3_file is not None:
        gns3_file = Path(args.gns3_file)
    else:
        project_name = project_root.name
        gns3_file = project_root / f"{project_name}.gns3"
    configs_base = Path(args.configs_base)
    strict = not args.no_strict

    gns3_json = _load_json(gns3_file)
    nodes = sorted(iter_dynamips_nodes(gns3_json), key=lambda n: n.name)
    if not nodes:
        raise RuntimeError("Aucun node dynamips trouvé dans le fichier .gns3")

    latest_dir = find_latest_configs_dir(configs_base)

    dynamips_root = project_root / "project-files" / "dynamips"
    if not dynamips_root.is_dir():
        raise FileNotFoundError(f"Dossier dynamips introuvable: {dynamips_root}")

    widths = [18, 36, 10, 10]
    print(f"Fichier .gns3: {gns3_file}")
    print(f"Dossier configs source (dernier): {latest_dir}")
    print(f"Dossier dynamips: {dynamips_root}")
    print("")
    print(format_row(["name", "node_id", "dyn_id", "status"], widths))
    print(format_row(["-" * 4, "-" * 7, "-" * 6, "-" * 6], widths))

    errors: list[str] = []
    copied = 0
    skipped = 0

    for n in nodes:
        src_cfg = latest_dir / f"{n.name}.cfg"
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


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
