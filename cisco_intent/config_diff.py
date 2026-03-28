#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
config_diff.py — Comparer deux jeux de .cfg → fichiers de « modifs » (hot-push)
================================================================================

Idée :
  Tu compares deux runs de configs (OLD vs NEW). Au lieu de remplacer toute la config sur
  les routeurs, ce module calcule des *lignes de commandes* qui passent de l'état OLD à
  l'état NEW (``no ...``, nouvelles interfaces, etc.).

Flux typique :
  1. Déterminer le dossier ``Configs-*`` pour OLD (dernier run, ou chemin explicite).
  2. Régénérer NEW à partir d'un nouvel intent (``run_generator``). Avec ``--only``,
     les autres ``*.cfg`` sont copiés depuis le run OLD pour garder un dossier NEW cohérent.
  3. Parser chaque paire de ``<node>.cfg`` en blocs (global vs ``interface`` / ``router``…).
  4. ``diff_cfg`` produit la liste de lignes ; écriture dans ``modifs/Modifs-*``.

Sécurité :
  ``BANNED_CMD_RE`` et ``iter_effective_cfg_lines`` évitent de traiter des commandes
  destructrices (reload, erase, etc.).

Option ``--push`` :
  Après écriture des modifs, délègue à ``gns3_push.run_push`` comme ``generate --push``.

Pour étendre :
  - Nouveau type de bloc IOS : ajoute un préfixe dans ``is_mode_header`` et une règle
    dans ``removal_for_block_header`` si la suppression doit être spéciale.
================================================================================
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from cisco_intent.gns3_push import add_push_cli_arguments, run_push
from cisco_intent.paths import configs_base_dir, modifs_base_dir

# Dossiers produits par ``make_configs_run_dir`` : horodatage YYYYMMDD-HHMMSS
CONFIGS_DIR_RE = re.compile(r"^Configs-(\d{8}-\d{6})$")
INTENT_FILE_RE = re.compile(r"^Intent.*\.json$", re.IGNORECASE)

BANNED_CMD_RE = re.compile(
    r"(?i)^\s*(reload|write\s+erase|erase\s+(startup-config|nvram:)|format\s+|copy\s+.*startup-config|configure\s+replace)\b"
)


def _now_stamp() -> str:
    """Horodatage compact pour nommer un run ``Modifs-YYYYMMDD-HHMMSS``."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _eprint(msg: str) -> None:
    """Écrit un message sur stderr (logs d'information sans polluuer stdout)."""
    print(msg, file=sys.stderr)


def parse_only_list(value: Optional[str]) -> Optional[Set[str]]:
    """Parse une liste de noms séparés par des virgules ; ``None`` ou vide → pas de filtre."""
    if not value:
        return None
    items = [x.strip() for x in value.split(",") if x.strip()]
    return set(items) if items else None


def find_latest_run_dir(configs_base: Path) -> Path:
    """Retourne le dossier ``Configs-YYYYMMDD-HHMMSS`` le plus récent sous ``configs_base``."""
    if not configs_base.exists():
        raise FileNotFoundError(f"configs_base introuvable: {configs_base}")
    if not configs_base.is_dir():
        raise NotADirectoryError(f"configs_base n'est pas un dossier: {configs_base}")

    candidates: List[Tuple[str, Path]] = []
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


def find_intent_in_run_dir(run_dir: Path) -> Path:
    """Choisit un fichier ``Intent*.json`` dans un run de configs (préférence ``intent.v4.json``)."""
    intents = [p for p in run_dir.iterdir() if p.is_file() and INTENT_FILE_RE.match(p.name)]
    if not intents:
        raise FileNotFoundError(f"Aucun intent (Intent*.json) trouvé dans {run_dir}")
    intents.sort(key=lambda p: p.name.lower())
    for p in intents:
        if p.name.lower() == "intent.v4.json":
            return p
    return intents[0]


def run_generator(
    new_intent: Path,
    *,
    only_nodes: Optional[Set[str]] = None,
    fill_from_run_dir: Optional[Path] = None,
) -> None:
    """
    Lance ``generate_configs`` sur l'intent donné ; lève si le code de retour est non nul.

    Avec ``only_nodes`` + ``fill_from_run_dir`` (ex. ``diff --only``), les .cfg hors liste
    sont copiés depuis le run OLD pour que le dossier Configs-* NEW reflète seulement les
    nœuds réellement régénérés.
    """
    if not new_intent.exists():
        raise FileNotFoundError(f"Intent NEW introuvable: {new_intent}")
    from cisco_intent.generator import generate_configs

    _eprint(f"[INFO] Génération configs pour: {new_intent}")
    if only_nodes:
        _eprint(f"[INFO] Copie des .cfg non listés depuis: {fill_from_run_dir}")
    rc, _ = generate_configs(new_intent, only_nodes=only_nodes, fill_from_run_dir=fill_from_run_dir)
    if rc != 0:
        raise RuntimeError("Génération configs échouée")


def iter_effective_cfg_lines(cfg_text: str) -> Iterable[str]:
    """Ignore commentaires ``!``, lignes vides, ``end`` final ; refuse les commandes dangereuses."""
    raw_lines = [ln.rstrip("\r\n") for ln in cfg_text.splitlines()]

    i = len(raw_lines) - 1
    while i >= 0 and (not raw_lines[i].strip() or raw_lines[i].lstrip().startswith("!")):
        i -= 1
    raw_lines = raw_lines[: i + 1]

    if raw_lines and raw_lines[-1].strip().lower() == "end":
        raw_lines = raw_lines[:-1]

    for ln in raw_lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("!"):
            continue
        if BANNED_CMD_RE.search(s):
            raise ValueError(f"Commande interdite détectée dans une config: {s!r}")
        yield ln.rstrip()


def is_mode_header(line: str) -> bool:
    """Indique si la ligne démarre un sous-mode IOS (interface, router, vrf, etc.)."""
    s = line.strip()
    starters = (
        "interface ",
        "router ",
        "ip vrf ",
        "vrf definition ",
        "route-map ",
        "ip access-list ",
        "class-map ",
        "policy-map ",
        "line ",
    )
    return any(s.lower().startswith(p) for p in starters)


@dataclass
class ParsedCfg:
    """Résultat du parseur : commandes globales + blocs indexés par leur ligne d'en-tête."""

    global_lines: List[str]
    blocks: Dict[str, List[str]]


def parse_cfg(cfg_text: str) -> ParsedCfg:
    """
    Découpe une config en lignes « globales » et blocs nommés (clé = première ligne du bloc,
    ex. ``interface Gi0/0``). Les sous-lignes indentées appartiennent au bloc courant.
    """
    global_lines: List[str] = []
    blocks_acc: DefaultDict[str, List[str]] = defaultdict(list)

    current_header: Optional[str] = None
    current_sublines: List[str] = []

    def flush_block() -> None:
        """Enregistre le bloc courant dans ``blocks_acc`` puis réinitialise l'état."""
        nonlocal current_header, current_sublines
        if current_header is None:
            return
        if current_sublines:
            blocks_acc[current_header].extend(current_sublines)
        current_header = None
        current_sublines = []

    for ln in iter_effective_cfg_lines(cfg_text):
        if ln[:1].isspace():
            if current_header is None:
                global_lines.append(ln.strip())
            else:
                current_sublines.append(ln.rstrip())
            continue

        if is_mode_header(ln):
            flush_block()
            current_header = ln.strip()
            current_sublines = []
        else:
            flush_block()
            global_lines.append(ln.strip())

    flush_block()
    return ParsedCfg(global_lines=global_lines, blocks=dict(blocks_acc))


def negate_cmd(cmd: str) -> str:
    """Ajoute ou retire le préfixe ``no `` pour inverser une ligne de configuration."""
    s = cmd.strip()
    if s.lower().startswith("no "):
        return s[3:].lstrip()
    return "no " + s


def _sort_interface_patch_additions(add_lines: List[str]) -> List[str]:
    """Ordonne les sous-commandes ``interface`` pour un ordre IOS sûr (descr/VRF avant IP)."""

    def rank(ln: str) -> Tuple[int, str]:
        """Clé de tri : priorité numérique puis texte pour stabilité."""
        s = ln.strip().lower()
        if s.startswith("description ") or s.startswith("encapsulation "):
            return (0, ln)
        if s.startswith("vrf forwarding ") or s.startswith("ip vrf forwarding "):
            return (1, ln)
        if (
            s.startswith("ip address ")
            or s.startswith("ipv6 address ")
            or s.startswith("ip unnumbered ")
        ):
            return (2, ln)
        return (3, ln)

    return sorted(add_lines, key=rank)


def _removed_vrf_names(old_headers: Set[str], new_headers: Set[str]) -> Set[str]:
    """Noms de VRF dont le bloc disparaît du NEW (``vrf definition`` / ``ip vrf``)."""
    names: Set[str] = set()
    for h in old_headers - new_headers:
        hs = h.strip()
        hll = hs.lower()
        if hll.startswith("vrf definition "):
            names.add(hs.split(None, 2)[2])
        elif hll.startswith("ip vrf "):
            names.add(hs.split(None, 2)[2])
    return names


def _should_skip_negate_ip_after_vrf_deleted(
    sub: str,
    old_sub: List[str],
    removed_vrf_names: Set[str],
) -> bool:
    """
    Après ``no vrf definition X``, IOS enlève déjà les IP des interfaces dans X.
    Évite d'émettre ``no ip address …`` / ``no ipv6 address …`` qui provoquent « Invalid address ».
    """
    if not removed_vrf_names:
        return False
    sl = sub.strip().lower()
    if not (sl.startswith("ip address ") or sl.startswith("ipv6 address ")):
        return False
    for ln in old_sub:
        parts = ln.strip().split()
        if len(parts) < 3:
            continue
        p0 = parts[0].lower()
        if p0 == "vrf" and parts[1].lower() == "forwarding" and parts[2] in removed_vrf_names:
            return True
        if (
            p0 == "ip"
            and len(parts) >= 4
            and parts[1].lower() == "vrf"
            and parts[2].lower() == "forwarding"
            and parts[3] in removed_vrf_names
        ):
            return True
    return False


def removal_for_block_header(header: str) -> List[str]:
    """Lignes IOS pour supprimer ou réinitialiser un bloc identifié par ``header``."""
    h = header.strip()
    hl = h.lower()
    if hl.startswith("interface "):
        iface = h.split(None, 1)[1]
        if "." in iface:
            return [f"no interface {iface}"]
        return [f"default interface {iface}"]

    if hl.startswith("router "):
        return [f"no {h}"]
    if hl.startswith("route-map "):
        return [f"no {h}"]
    if hl.startswith("vrf definition "):
        return [f"no {h}"]
    if hl.startswith("ip vrf "):
        return [f"no {h}"]
    if hl.startswith("ip access-list "):
        return [f"no {h}"]
    if hl.startswith("class-map "):
        return [f"no {h}"]
    if hl.startswith("policy-map "):
        return [f"no {h}"]
    if hl.startswith("line "):
        return [f"default {h}"]

    return [f"no {h}"]


def diff_cfg(old_cfg: ParsedCfg, new_cfg: ParsedCfg) -> List[str]:
    """
    Diff globals ensembliste puis blocs ; BGP est remplacé en bloc entier si changement.

    Sur IOS, les processus BGP peuvent retenir une VRF (RD) tant que ``router bgp`` existe.
    Si l'on ajoute une nouvelle ``vrf definition`` avant ``no router bgp``, le même RD peut
    rester attribué à l'ancienne VRF : on supprime donc **d'abord** tout ``router bgp`` concerné,
    puis on retire/ajoute VRF et autres blocs, puis on réinjecte le bloc BGP **à la fin**.

    Après ``no vrf definition X``, IOS enlève déjà les adresses IP des interfaces dans X : on
    n'émet pas les ``no ip address`` / ``no ipv6 address`` correspondants sur ces interfaces
    (sinon « Invalid address »).
    """
    out: List[str] = []

    old_headers = set(old_cfg.blocks.keys())
    new_headers = set(new_cfg.blocks.keys())
    intersect = old_headers & new_headers

    bgp_headers_full_replace: List[str] = []
    for header in intersect:
        if not header.lower().startswith("router bgp "):
            continue
        old_sub = old_cfg.blocks.get(header, [])
        new_sub = new_cfg.blocks.get(header, [])
        if set(old_sub) != set(new_sub):
            bgp_headers_full_replace.append(header)
    bgp_headers_full_replace.sort()

    removed_vrf_names = _removed_vrf_names(old_headers, new_headers)

    old_globals = set(old_cfg.global_lines)
    new_globals = set(new_cfg.global_lines)
    globals_to_remove = sorted(old_globals - new_globals)
    globals_to_add = sorted(new_globals - old_globals)

    for ln in globals_to_remove:
        out.append(negate_cmd(ln))
    for ln in globals_to_add:
        out.append(ln)

    for header in bgp_headers_full_replace:
        out.extend(removal_for_block_header(header))

    for header in sorted(old_headers - new_headers):
        out.extend(removal_for_block_header(header))

    for header in sorted(new_headers - old_headers):
        out.append(header)
        for sub in new_cfg.blocks[header]:
            out.append(sub)

    for header in sorted(intersect):
        if header in bgp_headers_full_replace:
            continue

        old_sub = old_cfg.blocks.get(header, [])
        new_sub = new_cfg.blocks.get(header, [])
        old_set = set(old_sub)
        new_set = set(new_sub)

        to_remove = sorted(old_set - new_set)
        to_add = sorted(new_set - old_set)
        if not to_remove and not to_add:
            continue

        if header.lower().startswith("router bgp "):
            continue

        out.append(header)
        if header.lower().startswith("interface "):
            for sub in to_remove:
                if _should_skip_negate_ip_after_vrf_deleted(sub, old_sub, removed_vrf_names):
                    continue
                out.append(negate_cmd(sub))
            to_add = _sort_interface_patch_additions(to_add)
        else:
            for sub in to_remove:
                out.append(negate_cmd(sub))
        for sub in to_add:
            out.append(sub)

    for header in bgp_headers_full_replace:
        out.append(header)
        for sub in new_cfg.blocks[header]:
            out.append(sub)

    for ln in out:
        if BANNED_CMD_RE.search(ln.strip()):
            raise ValueError(f"Commande interdite générée par le diff: {ln!r}")

    return out


def load_text(path: Path) -> str:
    """Lit un fichier texte en UTF-8 avec remplacement des octets invalides."""
    return path.read_text(encoding="utf-8", errors="replace")


def list_nodes_in_run_dir(run_dir: Path) -> List[str]:
    """Noms de routeurs présents dans ``run_dir`` (fichiers ``*.cfg``, sans extension)."""
    names = []
    for p in run_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".cfg":
            names.append(p.stem)
    names.sort(key=str.lower)
    return names


def write_modifs_run(
    *,
    modifs_base: Path,
    old_intent_path: Path,
    new_intent_path: Path,
    old_run_dir: Path,
    new_run_dir: Path,
    only: Optional[Set[str]],
    dry_run: bool,
) -> Path:
    """
    Crée un run ``Modifs-*`` avec les diffs par nœud, métadonnées et copies d'intent.
    En ``dry_run``, affiche le résumé et les volumes de diff sans créer de fichiers.
    Retourne le chemin du dossier de sortie prévu (même en dry-run, pour uniformiser l'API).
    """
    stamp = _now_stamp()
    out_dir = modifs_base / f"Modifs-{stamp}"

    nodes = sorted(set(list_nodes_in_run_dir(old_run_dir)) | set(list_nodes_in_run_dir(new_run_dir)), key=str.lower)
    if only is not None:
        nodes = [n for n in nodes if n in only]

    summary = {
        "timestamp": stamp,
        "old_intent": str(old_intent_path),
        "new_intent": str(new_intent_path),
        "old_configs_dir": str(old_run_dir),
        "new_configs_dir": str(new_run_dir),
        "nodes": nodes,
    }

    if dry_run:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        for n in nodes:
            old_cfg_p = old_run_dir / f"{n}.cfg"
            new_cfg_p = new_run_dir / f"{n}.cfg"
            old_text = load_text(old_cfg_p) if old_cfg_p.exists() else ""
            new_text = load_text(new_cfg_p) if new_cfg_p.exists() else ""
            diff_lines = diff_cfg(parse_cfg(old_text), parse_cfg(new_text))
            print(f"[DRY] {n}: {len(diff_lines)} lignes")
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "OLD.intent.json").write_text(old_intent_path.read_text(encoding="utf-8"), encoding="utf-8")
    (out_dir / "NEW.intent.json").write_text(new_intent_path.read_text(encoding="utf-8"), encoding="utf-8")
    (out_dir / "metadata.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    written = 0
    for n in nodes:
        old_cfg_p = old_run_dir / f"{n}.cfg"
        new_cfg_p = new_run_dir / f"{n}.cfg"
        old_text = load_text(old_cfg_p) if old_cfg_p.exists() else ""
        new_text = load_text(new_cfg_p) if new_cfg_p.exists() else ""
        diff_lines = diff_cfg(parse_cfg(old_text), parse_cfg(new_text))

        dst = out_dir / f"{n}.cfg"
        dst.write_text("\n".join(diff_lines) + ("\n" if diff_lines else ""), encoding="utf-8")
        written += 1

    print(f"[OK] Modifs générées: {out_dir} ({written} fichiers)")
    return out_dir


def _resolve_cli_path(p: Path) -> Path:
    """Absolutise un chemin CLI : relatif au répertoire courant, pas à la racine du dépôt."""
    return p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()


def main(argv: Sequence[str]) -> int:
    """Sous-commande ``diff`` : argparse, génération NEW, diff, option ``--push``."""
    ap = argparse.ArgumentParser(
        description=(
            "Génère des configs de modifications (hot-push) en comparant les configs issues de 2 intents "
            "(OLD vs NEW). Exécute le générateur pour produire les nouvelles configs complètes."
        )
    )
    ap.add_argument("--old-intent", type=Path, default=None, help="Chemin vers l'intent OLD (optionnel si auto-détection)")
    ap.add_argument("--new-intent", type=Path, required=True, help="Chemin vers l'intent NEW")
    ap.add_argument(
        "--configs-base",
        type=Path,
        default=None,
        help="Base des runs Configs-* (défaut: Configs/ à la racine du dépôt)",
    )
    ap.add_argument("--old-configs-dir", type=Path, default=None, help="Override: dossier Configs-* pour OLD")
    ap.add_argument("--new-configs-dir", type=Path, default=None, help="Override: dossier Configs-* pour NEW")
    ap.add_argument(
        "--modifs-base",
        type=Path,
        default=None,
        help="Base de sortie des runs Modifs-* (défaut: modifs/ à la racine du dépôt)",
    )
    ap.add_argument(
        "--only",
        type=str,
        default=None,
        help="Nœuds dont on régénère le .cfg ; les autres sont copiés depuis OLD (même dossier Configs-* cohérent pour un prochain diff)",
    )
    ap.add_argument("--dry-run", action="store_true", help="N'écrit rien, affiche un résumé et le volume de diffs")
    add_push_cli_arguments(ap)
    args = ap.parse_args(list(argv))

    if args.gns3_project is not None and not args.push:
        ap.error("--gns3-project sans --push (ajoutez --push ou retirez --gns3-project)")
    if args.push_only is not None and not args.push:
        ap.error("--push-only sans --push")
    if args.push and args.gns3_project is None:
        ap.error("--gns3-project requis avec --push")

    configs_base = args.configs_base if args.configs_base is not None else configs_base_dir()
    configs_base = _resolve_cli_path(configs_base)

    modifs_base = args.modifs_base if args.modifs_base is not None else modifs_base_dir()
    modifs_base = _resolve_cli_path(modifs_base)

    new_intent = _resolve_cli_path(args.new_intent)
    only = parse_only_list(args.only)

    if args.old_configs_dir is not None:
        ocd = args.old_configs_dir
        old_run_dir = ocd.resolve() if ocd.is_absolute() else (configs_base / ocd).resolve()
        if args.old_intent is not None:
            old_intent = _resolve_cli_path(args.old_intent)
        else:
            old_intent = find_intent_in_run_dir(old_run_dir)
    elif args.old_intent is not None:
        old_intent = _resolve_cli_path(args.old_intent)
        run_generator(old_intent)
        old_run_dir = find_latest_run_dir(configs_base)
    else:
        old_run_dir = find_latest_run_dir(configs_base)
        old_intent = find_intent_in_run_dir(old_run_dir)

    # Deux appels à ``generate_configs`` dans la même seconde partageraient le même nom de dossier.
    time.sleep(1.1)
    if only:
        run_generator(new_intent, only_nodes=only, fill_from_run_dir=old_run_dir)
    else:
        run_generator(new_intent)

    if args.new_configs_dir is not None:
        ncd = args.new_configs_dir
        new_run_dir = ncd.resolve() if ncd.is_absolute() else (configs_base / ncd).resolve()
    else:
        new_run_dir = find_latest_run_dir(configs_base)

    if old_run_dir.resolve() == new_run_dir.resolve():
        raise RuntimeError(
            "OLD et NEW pointent vers le même dossier Configs-*. "
            "Assure-toi que le générateur a bien produit un nouveau run, ou utilise --old-configs-dir."
        )

    _eprint(f"[INFO] OLD run: {old_run_dir}")
    _eprint(f"[INFO] NEW run: {new_run_dir}")

    out_dir = write_modifs_run(
        modifs_base=modifs_base,
        old_intent_path=old_intent,
        new_intent_path=new_intent,
        old_run_dir=old_run_dir,
        new_run_dir=new_run_dir,
        only=only,
        dry_run=bool(args.dry_run),
    )

    if args.push:
        if args.dry_run:
            _eprint("[INFO] --push ignoré (--dry-run actif pour le diff)")
            return 0
        gns3_proj = _resolve_cli_path(args.gns3_project)
        only_push = args.only or args.push_only
        gns3_file = _resolve_cli_path(args.gns3_file) if args.gns3_file else None
        return run_push(
            gns3_proj,
            out_dir,
            gns3_file=gns3_file,
            only=only_push,
            strict=args.push_strict,
            dry_run=args.push_dry_run,
            timeout=args.push_timeout,
            delay_line=args.push_delay_line,
            write_memory=args.push_write_memory,
            verbose=args.push_verbose,
            workers=args.push_workers,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
