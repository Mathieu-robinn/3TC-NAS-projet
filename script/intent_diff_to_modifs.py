#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple


CONFIGS_DIR_RE = re.compile(r"^Configs-(\d{8}-\d{6})$")
INTENT_FILE_RE = re.compile(r"^Intent.*\.json$", re.IGNORECASE)


BANNED_CMD_RE = re.compile(
    r"(?i)^\s*(reload|write\s+erase|erase\s+(startup-config|nvram:)|format\s+|copy\s+.*startup-config|configure\s+replace)\b"
)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_only_list(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    items = [x.strip() for x in value.split(",") if x.strip()]
    return set(items) if items else None


def find_latest_run_dir(configs_base: Path) -> Path:
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
    intents = [p for p in run_dir.iterdir() if p.is_file() and INTENT_FILE_RE.match(p.name)]
    if not intents:
        raise FileNotFoundError(f"Aucun intent (Intent*.json) trouvé dans {run_dir}")
    intents.sort(key=lambda p: p.name.lower())
    # Prefer Intent.v4.json when present
    for p in intents:
        if p.name.lower() == "intent.v4.json":
            return p
    return intents[0]


def run_generator(generator_py: Path, new_intent: Path, *, cwd: Path) -> None:
    if not generator_py.exists():
        raise FileNotFoundError(f"Générateur introuvable: {generator_py}")
    if not new_intent.exists():
        raise FileNotFoundError(f"Intent NEW introuvable: {new_intent}")

    cmd = [sys.executable, str(generator_py), str(new_intent)]
    _eprint("[INFO] Exécution du générateur: " + " ".join(cmd))
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if res.returncode != 0:
        _eprint(res.stdout)
        _eprint(res.stderr)
        raise RuntimeError(f"Génération configs échouée (code {res.returncode})")


def iter_effective_cfg_lines(cfg_text: str) -> Iterable[str]:
    """
    Similar to push_gns3_configs.iter_cfg_lines, but keep indentation (modes).
    Exclude empty lines, comments '!', and trailing 'end'.
    """
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
        # forbid dangerous operational commands
        if BANNED_CMD_RE.search(s):
            raise ValueError(f"Commande interdite détectée dans une config: {s!r}")
        yield ln.rstrip()


def is_mode_header(line: str) -> bool:
    """
    Decide if a top-level line opens a configuration mode (block).
    This is intentionally conservative and tailored for generated configs in this repo.
    """
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
    global_lines: List[str]
    blocks: Dict[str, List[str]]  # header -> sublines (indented or not, as-is)


def parse_cfg(cfg_text: str) -> ParsedCfg:
    global_lines: List[str] = []
    # Some generators may emit the same mode header multiple times (ex: 'router bgp 1').
    # We merge occurrences by concatenating their sublines in order.
    blocks_acc: DefaultDict[str, List[str]] = DefaultDict(list)

    current_header: Optional[str] = None
    current_sublines: List[str] = []

    def flush_block() -> None:
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
                # Should not happen with generated configs; treat as global.
                global_lines.append(ln.strip())
            else:
                current_sublines.append(ln.strip())
            continue

        # top-level line
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
    s = cmd.strip()
    if s.lower().startswith("no "):
        return s[3:].lstrip()
    return "no " + s


def removal_for_block_header(header: str) -> List[str]:
    h = header.strip()
    hl = h.lower()
    if hl.startswith("interface "):
        iface = h.split(None, 1)[1]
        if "." in iface:
            return [f"no interface {iface}"]
        return [f"default interface {iface}"]

    # Prefer explicit removals for known block types
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
        # IOS often uses 'default line ...' but 'no line' isn't correct.
        # For safety, default the line.
        return [f"default {h}"]

    return [f"no {h}"]


def diff_cfg(old_cfg: ParsedCfg, new_cfg: ParsedCfg) -> List[str]:
    """
    Return IOS config lines suitable for 'configure terminal' pushing.
    Order: removals first, then additions.
    """
    out: List[str] = []

    # Global lines: treat as standalone commands
    old_globals = set(old_cfg.global_lines)
    new_globals = set(new_cfg.global_lines)
    globals_to_remove = sorted(old_globals - new_globals)
    globals_to_add = sorted(new_globals - old_globals)

    for ln in globals_to_remove:
        out.append(negate_cmd(ln))
    for ln in globals_to_add:
        out.append(ln)

    # Blocks: remove missing blocks
    old_headers = set(old_cfg.blocks.keys())
    new_headers = set(new_cfg.blocks.keys())

    for header in sorted(old_headers - new_headers):
        out.extend(removal_for_block_header(header))

    # Blocks: add new blocks (full content)
    for header in sorted(new_headers - old_headers):
        out.append(header)
        for sub in new_cfg.blocks[header]:
            out.append(sub)

    # Blocks: in both, diff sublines inside mode
    for header in sorted(old_headers & new_headers):
        old_sub = old_cfg.blocks.get(header, [])
        new_sub = new_cfg.blocks.get(header, [])
        old_set = set(old_sub)
        new_set = set(new_sub)

        to_remove = sorted(old_set - new_set)
        to_add = sorted(new_set - old_set)
        if not to_remove and not to_add:
            continue

        # BGP blocks include nested address-family context. Line-level negation can
        # produce invalid commands ("no neighbor ... activate" at wrong level).
        # For safety, replace the whole BGP block when it changes.
        if header.lower().startswith("router bgp "):
            out.extend(removal_for_block_header(header))
            out.append(header)
            for sub in new_sub:
                out.append(sub)
            continue

        out.append(header)
        for sub in to_remove:
            out.append(negate_cmd(sub))
        for sub in to_add:
            out.append(sub)

    # Final safety check
    for ln in out:
        if BANNED_CMD_RE.search(ln.strip()):
            raise ValueError(f"Commande interdite générée par le diff: {ln!r}")

    return out


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def list_nodes_in_run_dir(run_dir: Path) -> List[str]:
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


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Génère des configs de modifications (hot-push) en comparant les configs issues de 2 intents "
            "(OLD vs NEW). Le script exécute aussi le générateur pour produire les nouvelles configs complètes."
        )
    )
    ap.add_argument("--old-intent", type=Path, default=None, help="Chemin vers l'intent OLD (optionnel si auto-détection)")
    ap.add_argument("--new-intent", type=Path, required=True, help="Chemin vers l'intent NEW")
    ap.add_argument("--generator", type=Path, default=Path("script_intent_to_configs.py"), help="Script générateur (défaut: script_intent_to_configs.py)")
    ap.add_argument("--configs-base", type=Path, default=Path("Configs"), help="Base des runs Configs-* (défaut: Configs, relatif au cwd)")
    ap.add_argument("--old-configs-dir", type=Path, default=None, help="Override: dossier Configs-* pour OLD (sinon dernier run)")
    ap.add_argument("--new-configs-dir", type=Path, default=None, help="Override: dossier Configs-* pour NEW (sinon dernier run après génération)")
    ap.add_argument("--modifs-base", type=Path, default=Path("modifs"), help="Base de sortie des runs Modifs-* (défaut: modifs, relatif au cwd)")
    ap.add_argument("--only", type=str, default=None, help="Liste de nodes (noms) séparés par virgule")
    ap.add_argument("--dry-run", action="store_true", help="N'écrit rien, affiche un résumé et le volume de diffs")
    args = ap.parse_args(list(argv))

    script_dir = Path(__file__).resolve().parent
    cwd = script_dir  # keep same behavior as current workflows (script/Configs)

    generator = args.generator
    if not generator.is_absolute():
        generator = (script_dir / generator).resolve()

    configs_base = args.configs_base
    if not configs_base.is_absolute():
        configs_base = (cwd / configs_base).resolve()

    modifs_base = args.modifs_base
    if not modifs_base.is_absolute():
        modifs_base = (cwd / modifs_base).resolve()

    new_intent = args.new_intent
    if not new_intent.is_absolute():
        new_intent = (Path.cwd() / new_intent).resolve()

    only = parse_only_list(args.only)

    # OLD run dir + intent
    # Priority:
    # 1) --old-configs-dir: explicit OLD configs source
    # 2) --old-intent: generate an OLD run from this intent (so option actually affects diff)
    # 3) fallback: latest existing Configs-* + auto-detected intent from that run
    if args.old_configs_dir is not None:
        old_run_dir = args.old_configs_dir
        if not old_run_dir.is_absolute():
            old_run_dir = (configs_base / old_run_dir).resolve() if not str(old_run_dir).startswith(str(configs_base)) else old_run_dir.resolve()
        if args.old_intent is not None:
            old_intent = args.old_intent
            if not old_intent.is_absolute():
                old_intent = (Path.cwd() / old_intent).resolve()
        else:
            old_intent = find_intent_in_run_dir(old_run_dir)
    elif args.old_intent is not None:
        old_intent = args.old_intent
        if not old_intent.is_absolute():
            old_intent = (Path.cwd() / old_intent).resolve()
        # Build OLD configs from provided old intent.
        run_generator(generator, old_intent, cwd=cwd)
        old_run_dir = find_latest_run_dir(configs_base)
        # make sure NEW generation gets a distinct Configs-<timestamp> directory
        time.sleep(1.1)
    else:
        old_run_dir = find_latest_run_dir(configs_base)
        old_intent = find_intent_in_run_dir(old_run_dir)

    # Generate NEW configs
    run_generator(generator, new_intent, cwd=cwd)

    # NEW run dir
    if args.new_configs_dir is not None:
        new_run_dir = args.new_configs_dir
        if not new_run_dir.is_absolute():
            new_run_dir = (configs_base / new_run_dir).resolve() if not str(new_run_dir).startswith(str(configs_base)) else new_run_dir.resolve()
    else:
        new_run_dir = find_latest_run_dir(configs_base)

    if old_run_dir.resolve() == new_run_dir.resolve():
        raise RuntimeError(
            "OLD et NEW pointent vers le même dossier Configs-*. "
            "Assure-toi que le générateur a bien produit un nouveau run, ou utilise --old-configs-dir."
        )

    _eprint(f"[INFO] OLD run: {old_run_dir}")
    _eprint(f"[INFO] NEW run: {new_run_dir}")

    write_modifs_run(
        modifs_base=modifs_base,
        old_intent_path=old_intent,
        new_intent_path=new_intent,
        old_run_dir=old_run_dir,
        new_run_dir=new_run_dir,
        only=only,
        dry_run=bool(args.dry_run),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

