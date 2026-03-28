# -*- coding: utf-8 -*-
"""
================================================================================
cli.py — Interface en ligne de commande (router des sous-commandes)
================================================================================

Rôle :
  C'est le « tableau de bord » : selon le premier argument (generate, diff, push,
  sync-startup), on importe le module concerné et on lui passe le reste des arguments.

Pourquoi des imports à l'intérieur des ``if cmd == ...`` ?
  Accélère le démarrage : par exemple ``python -m cisco_intent push`` n'importe pas
  le générateur tant que tu ne lances pas ``generate``.

Flux ``generate`` :
  - Sans ``--push`` : écrit dans ``configs/live/`` si celui-ci est vide (aucun ``*.cfg``),
    sinon dans ``configs/staging/``.
  - Avec ``--push`` : écrit toujours dans ``live/``, puis ``run_push`` ; après succès
    (sans ``--push-dry-run``), ``sync`` depuis le dossier poussé (souvent ``live`` déjà à jour).

Pour étendre :
  Ajoute un ``if cmd == "ma_commande":`` qui parse ``rest`` avec ``argparse`` ou
  délègue à une fonction ``main`` d'un nouveau module.

Liens : ``generator.generate_configs``, ``config_diff.main``, ``gns3_push.run_push``,
         ``gns3_sync.main``.
================================================================================
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence


def _print_global_help() -> None:
    """Affiche l'aide globale quand aucune sous-commande n'est fournie ou pour ``--help``."""
    print(
        """\
Usage: python -m cisco_intent <command> ...

Commands:
  generate <intent.json>   Génère les .cfg (live si vide, sinon staging ; toujours live avec --push)
  diff ...                 Diff intents -> modifs ; voir: python -m cisco_intent diff -h
  push ...                 Push telnet GNS3 ; voir: python -m cisco_intent push -h
  sync-startup ...         Copie configs -> startup Dynamips ; voir: python -m cisco_intent sync-startup -h
"""
    )


def _resolve_cli_path(p: Path) -> Path:
    """Chemin absolu : si relatif, interprété depuis le répertoire courant (pas PROJECT_ROOT)."""
    return p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Point d'entrée CLI : route vers ``generate``, ``diff``, ``push`` ou ``sync-startup``.

    Retourne un code de sortie entier (0 succès, 2 aide/erreur d'usage, autres codes selon sous-commande).
    """
    argv_list: List[str] = list(sys.argv[1:] if argv is None else argv)

    if not argv_list or argv_list[0] in ("-h", "--help"):
        _print_global_help()
        return 0 if argv_list else 2

    cmd, *rest = argv_list

    if cmd == "generate":
        from cisco_intent.generator import generate_configs
        from cisco_intent.gns3_push import add_push_cli_arguments, run_push
        from cisco_intent.paths import live_dir_has_cfg_files, staging_dir

        p = argparse.ArgumentParser(prog="python -m cisco_intent generate")
        p.add_argument("intent", type=Path, help="Fichier intent JSON")
        add_push_cli_arguments(p)
        args = p.parse_args(rest)

        # Cohérence : --push impose le projet GNS3 ; évite les combinaisons ambiguës
        if args.gns3_project is not None and not args.push:
            p.error("--gns3-project sans --push (ajoutez --push ou retirez --gns3-project)")
        if args.push_only is not None and not args.push:
            p.error("--push-only sans --push")
        if args.push and args.gns3_project is None:
            p.error("--gns3-project requis avec --push")

        if args.push:
            gen_output_dir = None
        elif live_dir_has_cfg_files():
            gen_output_dir = staging_dir()
        else:
            gen_output_dir = None

        rc, run_dir = generate_configs(args.intent, output_dir=gen_output_dir)
        if rc != 0:
            return rc
        if not args.push:
            if (
                gen_output_dir is not None
                and run_dir is not None
                and run_dir.resolve() == staging_dir().resolve()
            ):
                print(
                    "[INFO] configs/live/ contient déjà des .cfg : "
                    "génération écrite dans configs/staging/ (push manuel ou generate --push pour mettre live à jour).",
                    file=sys.stderr,
                )
            return 0
        assert run_dir is not None
        from cisco_intent.paths import sync_live_from_run

        gns3_proj = _resolve_cli_path(args.gns3_project)
        gns3_file = _resolve_cli_path(args.gns3_file) if args.gns3_file else None
        prc = run_push(
            gns3_proj,
            run_dir,
            gns3_file=gns3_file,
            only=args.push_only,
            strict=args.push_strict,
            dry_run=args.push_dry_run,
            timeout=args.push_timeout,
            delay_line=args.push_delay_line,
            write_memory=args.push_write_memory,
            verbose=args.push_verbose,
            workers=args.push_workers,
        )
        if prc == 0 and not args.push_dry_run:
            try:
                sync_live_from_run(run_dir)
                print(f"[INFO] configs/live/ mis à jour depuis {run_dir}")
            except OSError as e:
                print(f"[WARN] sync configs/live/: {e}", file=sys.stderr)
        return prc

    if cmd == "diff":
        from cisco_intent.config_diff import main as diff_main

        return diff_main(rest)

    if cmd == "push":
        from cisco_intent.gns3_push import main as push_main

        return push_main(rest)

    if cmd == "sync-startup":
        from cisco_intent.gns3_sync import main as sync_main

        return sync_main(rest)

    print(f"Commande inconnue: {cmd!r}", file=sys.stderr)
    _print_global_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
