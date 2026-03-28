#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
gns3_push.py — Envoyer des fichiers .cfg vers les consoles telnet des nœuds GNS3
================================================================================

Contexte :
  GNS3 expose chaque routeur sur un port TCP localhost (champ ``console`` dans le .gns3).
  Ce module ouvre une session telnet, passe en ``enable`` puis ``configure terminal``,
  envoie ligne par ligne le contenu du ``<nom>.cfg``, et optionnellement ``write memory``.

Composants principaux :
  - ``load_node_consoles`` : lit le JSON du projet → dict nom → (port, type).
  - ``iter_cfg_lines`` : filtre commentaires et ``end`` final (on sort du config mode nous-mêmes).
  - ``TelnetIOSSession`` : boucle asyncio ``lire le tampon → attendre un prompt → envoyer``.
  - ``push_one`` : une routeur, un fichier.
  - ``run_push`` : planifie tous les nœuds (séquentiel ou threads + ``asyncio.run`` par tâche).

Regex sur flux binaire :
  IOS renvoie du texte bruité (syslog, retours chariot). Les motifs cherchent la *fin*
  du tampon (``\\Z``) pour éviter de croire qu'on a un prompt au milieu d'un gros bloc.

Réutilisation :
  ``add_push_cli_arguments`` + ``run_push`` sont appelés depuis ``cli.generate`` et
  ``config_diff`` quand l'utilisateur ajoute ``--push``.

Usage CLI direct :
  ``python -m cisco_intent push <dossier_projet_gns3> <dossier_cfg> [options]``

Exemple :
  ``python -m cisco_intent push gns3/projet_gns3_1 Configs/Configs-20260327-120000 --only PE1,P1 --write-memory``
================================================================================
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


try:
    import telnetlib3  # type: ignore
except Exception as e:  # pragma: no cover
    telnetlib3 = None
    _TELNETLIB3_IMPORT_ERROR = e


# --- Détection de prompts et erreurs IOS (octets, pas str : encodage console variable) ---
# Les prompts doivent matcher en *fin de tampon* : sinon une ligne contenant « # » au milieu
# d'un syslog pourrait être prise pour un prompt d'exécution.
# Using \Z avoids multiline '$' matching mid-buffer.
IOS_ANY_PROMPT_RE = re.compile(rb"[^\r\n]*(?:\([^\)]*\))?[>#]\s*\Z")
IOS_ENABLE_PROMPT_RE = re.compile(rb"[^\r\n]*#\s*\Z")
IOS_CONFIG_PROMPT_RE = re.compile(rb"[^\r\n]*\([^\)]*config[^\)]*\)#\s*\Z")
IOS_CONFIRM_RE = re.compile(rb"(?i)\[confirm\]\s*\Z")
IOS_WRITE_OK_RE = re.compile(rb"(?i)\[ok\]")
# Prefer "Building configuration" then any text/newlines then [OK] (IOS may interleave syslog; [OK] can be glued to a %LINK- line).
WRITE_MEMORY_OK_RE = re.compile(rb"(?is)Building\s+configuration.*?\[ok\]")
# IOS often reports CLI/runtime failures as lines starting with '%'.
# Keep this broad so push never silently continues after an IOS error.
# Exclude Cisco syslog lines (%SYS-5-..., %LINK-3-...) from CLI error detection.
IOS_ERROR_LINE_RE = re.compile(rb"(?im)^\s*%(?![A-Z][A-Z0-9]*-\d+-)[^\r\n]*$")

IOS_INIT_DIALOG_RE = re.compile(
    rb"(?is)Would you like to enter the initial configuration dialog\?\s*\[yes/no\]:\s*$"
)
IOS_PRESS_RETURN_RE = re.compile(rb"(?is)Press\s+RETURN\s+to\s+get\s+started!?")
# Exec prompt line after "write memory" [OK]: syslog may follow the prompt, so we must not require '#' at buffer end.
IOS_ENABLE_LINE_RE = re.compile(rb"(?m)^(?!\*)\s*[^\r\n]+#\s*$")

def _write_memory_ok_match(buf: bytes) -> Optional[re.Match[bytes]]:
    """Repère ``[OK]`` après ``write memory`` (forme longue ou courte selon la sortie IOS)."""
    m = WRITE_MEMORY_OK_RE.search(buf)
    if m:
        return m
    return IOS_WRITE_OK_RE.search(buf)


def _has_enable_prompt_after_write_ok(buf: bytes) -> bool:
    """Vrai si, après le ``[OK]`` du save, une ligne de prompt enable est présente dans le tampon."""
    m = _write_memory_ok_match(buf)
    if not m:
        return False
    tail = buf[m.end() :]
    return bool(IOS_ENABLE_LINE_RE.search(tail))


def _tail_bytes(b: bytes, n: int = 220) -> str:
    """Fin du tampon RX décodée en texte lisible (échappement ``\\r``/``\\n``) pour le debug."""
    try:
        s = b.decode("utf-8", errors="replace")
    except Exception:
        s = repr(b)
    s2 = s.replace("\r", "\\r").replace("\n", "\\n")
    return s2[-n:]


def _decode_bytes(b: bytes) -> str:
    """Décode des octets en UTF-8 tolérant ; retourne ``repr`` en secours."""
    try:
        return b.decode("utf-8", errors="replace")
    except Exception:
        return repr(b)


def _assert_no_ios_cli_error(buf: bytes, *, context: str) -> None:
    """
    Lève ``RuntimeError`` si IOS a émis une ligne d'erreur CLI (``%...`` hors syslog connu).
    Évite de poursuivre le push alors que des commandes ont échoué.
    """
    m = IOS_ERROR_LINE_RE.search(buf)
    if not m:
        return
    msg = _decode_bytes(m.group(0)).strip()
    raise RuntimeError(f"{context}: {msg}")


@dataclass(frozen=True)
class NodeConsole:
    """Console d'un nœud GNS3 : nom affiché, port TCP localhost, type (ex. telnet)."""

    name: str
    port: int
    console_type: str


def _eprint(msg: str) -> None:
    """Écrit sur stderr (messages d'erreur et mode verbose)."""
    print(msg, file=sys.stderr)


def find_gns3_file(project_dir: Path, explicit: Optional[Path]) -> Path:
    """
    Résout le chemin du fichier ``.gns3`` : explicite, unique dans le dossier projet,
    ou erreur si aucun / plusieurs sans ``--gns3-file``.
    """
    if explicit is not None:
        p = explicit
        if not p.is_absolute():
            p = project_dir / p
        if not p.exists():
            raise FileNotFoundError(f".gns3 file not found: {p}")
        return p

    candidates = sorted(project_dir.glob("*.gns3"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"No .gns3 file found in: {project_dir}")
    raise RuntimeError(
        "Multiple .gns3 files found; use --gns3-file to choose one:\n"
        + "\n".join(f"- {c.name}" for c in candidates)
    )


def load_node_consoles(gns3_path: Path) -> Dict[str, NodeConsole]:
    """Lit le projet GNS3 et retourne ``nom_routeur -> NodeConsole`` (ports console valides)."""
    data = json.loads(gns3_path.read_text(encoding="utf-8"))
    topo = data.get("topology", {})
    nodes = topo.get("nodes", [])

    consoles: Dict[str, NodeConsole] = {}
    for n in nodes:
        name = n.get("name")
        port = n.get("console")
        ctype = n.get("console_type", "telnet")
        if not name or port is None:
            continue
        try:
            port_i = int(port)
        except (TypeError, ValueError):
            continue
        consoles[name] = NodeConsole(name=name, port=port_i, console_type=str(ctype))
    return consoles


def parse_only_list(value: Optional[str]) -> Optional[set[str]]:
    """Découpe une liste ``a,b,c`` en ensemble de noms ; ``None``/vide → pas de filtre."""
    if not value:
        return None
    items = [x.strip() for x in value.split(",") if x.strip()]
    return set(items) if items else None


def iter_cfg_lines(cfg_text: str) -> Iterable[str]:
    """
    Itère les lignes à envoyer au routeur : ignore vides, commentaires ``!``, ``end`` final
    (la session quitte le mode configuration séparément).
    """
    raw_lines = [ln.rstrip("\r\n") for ln in cfg_text.splitlines()]

    # Drop trailing empty lines/comments
    i = len(raw_lines) - 1
    while i >= 0 and (not raw_lines[i].strip() or raw_lines[i].lstrip().startswith("!")):
        i -= 1
    raw_lines = raw_lines[: i + 1]

    # Drop trailing 'end'
    if raw_lines and raw_lines[-1].strip().lower() == "end":
        raw_lines = raw_lines[:-1]

    for ln in raw_lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("!"):
            continue
        yield ln


class TelnetIOSSession:
    """
    Session telnet asyncio vers une console IOS : tampon RX interne, attentes par regex,
    envoi CRLF (attendu par la plupart des consoles Cisco).
    """

    def __init__(self, host: str, port: int, timeout: float, *, verbose: bool = False) -> None:
        """Ouvre plus tard (async) une connexion vers ``host:port`` ; ``verbose`` trace RX/TX."""
        self.host = host
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self.reader = None
        self.writer = None
        self._buf = b""

    async def __aenter__(self) -> "TelnetIOSSession":
        """Établit la connexion telnet binaire via telnetlib3."""
        if telnetlib3 is None:  # pragma: no cover
            raise RuntimeError(
                "telnetlib3 is required but could not be imported. "
                "Install it with: pip install telnetlib3. "
                f"Import error: {_TELNETLIB3_IMPORT_ERROR}"
            )
        self.reader, self.writer = await telnetlib3.open_connection(
            host=self.host,
            port=self.port,
            encoding=False,
            force_binary=True,
            connect_minwait=0.05,
            connect_maxwait=float(self.timeout),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Ferme proprement l'écrivain telnet."""
        if self.writer is not None:
            try:
                self.writer.close()
            except Exception:
                pass

    async def _read_some(self, deadline: float) -> bytes:
        """Lit jusqu'à un petit quota d'octets avant ``deadline`` (non bloquant au-delà)."""
        if self.reader is None:
            return b""
        remaining = max(0.0, deadline - time.time())
        if remaining <= 0:
            return b""
        try:
            # reader.read returns '' on EOF
            chunk = await asyncio.wait_for(self.reader.read(65535), timeout=min(0.5, remaining))
        except asyncio.TimeoutError:
            return b""
        except Exception:
            return b""
        chunk = chunk or b""
        if chunk and self.verbose:
            _eprint(f"[VERBOSE][RX {self.host}:{self.port}] {_tail_bytes(chunk, 200)}")
        return chunk

    async def _expect_regex(self, pattern: re.Pattern[bytes], timeout: float) -> bytes:
        """Attend que ``pattern`` matche dans ``_buf`` ; retourne le tampon ou lève ``TimeoutError``."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pattern.search(self._buf):
                if self.verbose:
                    _eprint(f"[VERBOSE][MATCH] {pattern.pattern!r} | buf_tail={_tail_bytes(self._buf)}")
                return self._buf
            chunk = await self._read_some(deadline)
            if chunk:
                self._buf += chunk
                continue
            await asyncio.sleep(0.05)
        raise TimeoutError(f"Timeout waiting for pattern: {pattern.pattern!r}")

    async def _expect_any_regex(
        self, patterns: Sequence[re.Pattern[bytes]], timeout: float
    ) -> re.Pattern[bytes]:
        """Comme ``_expect_regex`` mais retourne le motif qui a matché en premier."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for pattern in patterns:
                if pattern.search(self._buf):
                    if self.verbose:
                        _eprint(f"[VERBOSE][MATCH] {pattern.pattern!r} | buf_tail={_tail_bytes(self._buf)}")
                    return pattern
            chunk = await self._read_some(deadline)
            if chunk:
                self._buf += chunk
                continue
            await asyncio.sleep(0.05)
        joined = b" | ".join(p.pattern for p in patterns)
        raise TimeoutError(f"Timeout waiting for one of patterns: {joined!r}")

    def sendline(self, line: str) -> None:
        """Envoie une ligne terminée par CRLF (attendu par les consoles IOS)."""
        if self.writer is None:
            raise RuntimeError("telnet session not connected")
        if self.verbose:
            _eprint(f"[VERBOSE][TX {self.host}:{self.port}] {line}")
        # IOS consoles typically expect CRLF.
        self.writer.write(line.encode("utf-8", errors="ignore") + b"\r\n")

    def clear_buffer(self) -> None:
        """Réinitialise le tampon RX (après une étape consommée ou pour éviter faux positifs)."""
        self._buf = b""

    async def wake(self) -> None:
        """
        Atteint un prompt IOS : envoie des lignes vides, répond ``no`` au wizard initial si besoin.
        """
        for _ in range(6):
            self.sendline("")  # wake up console / advance boot prompts
            try:
                await self._expect_regex(IOS_ANY_PROMPT_RE, timeout=min(2.0, self.timeout))
                return
            except TimeoutError:
                # fallthrough to boot prompt handling
                pass

            # Handle first-boot prompts
            if IOS_INIT_DIALOG_RE.search(self._buf):
                if self.verbose:
                    _eprint("[VERBOSE][BOOT] initial configuration dialog detected -> answering 'no'")
                self.sendline("no")
                continue

            if IOS_PRESS_RETURN_RE.search(self._buf):
                if self.verbose:
                    _eprint("[VERBOSE][BOOT] press RETURN detected -> sending empty line")
                self.sendline("")
                continue

        # If still no prompt, continue anyway; later steps will timeout with context.

    async def ensure_enable(self) -> None:
        """Envoie ``enable`` et attend le prompt privilégié ``#``."""
        # Toujours envoyer enable (sans effet si déjà en mode enable).
        self.sendline("enable")
        await self._expect_regex(IOS_ENABLE_PROMPT_RE, timeout=self.timeout)

    async def enter_config(self) -> None:
        """``terminal length 0`` puis ``configure terminal`` jusqu'au prompt de config."""
        self.clear_buffer()
        self.sendline("terminal length 0")
        await self._expect_regex(IOS_ENABLE_PROMPT_RE, timeout=self.timeout)
        self.clear_buffer()
        self.sendline("configure terminal")
        try:
            await self._expect_regex(IOS_CONFIG_PROMPT_RE, timeout=self.timeout)
        except TimeoutError:
            raise

    async def exit_config(self) -> None:
        """Quitte le mode configuration (``end``) et revient au prompt enable."""
        self.sendline("end")
        await self._expect_regex(IOS_ENABLE_PROMPT_RE, timeout=self.timeout)


@dataclass
class PushResult:
    """Résultat d'un push pour un nœud : statut libre (OK, SKIP, FAIL…) et détail optionnel."""

    node: str
    port: int
    status: str
    detail: str = ""


async def complete_write_memory(sess: TelnetIOSSession, timeout: float) -> None:
    """
    Exécute ``write memory`` : répond aux ``[confirm]``, attend ``[OK]`` et un prompt enable fiable.
    """
    sess.clear_buffer()
    sess.sendline("write memory")
    # NVRAM warnings + multiple confirms + Building configuration can exceed default --timeout.
    deadline = time.time() + max(45.0, timeout * 4)
    while time.time() < deadline:
        rem = deadline - time.time()
        if rem <= 0:
            break
        try:
            matched = await sess._expect_any_regex(
                (IOS_CONFIRM_RE, WRITE_MEMORY_OK_RE, IOS_WRITE_OK_RE),
                timeout=min(15.0, rem),
            )
        except TimeoutError:
            continue

        if matched is IOS_CONFIRM_RE:
            sess.sendline("")
            # Buffer still ends with "[confirm]" until new data arrives; without clearing, the next
            # _expect_any_regex would match IOS_CONFIRM_RE again immediately (busy-loop on Enter).
            sess.clear_buffer()
            continue

        # [OK] may be glued to syslog text on the same line; syslog may continue after the exec prompt.
        await _expect_enable_prompt_after_write_ok(sess, timeout=max(timeout, 15.0))
        _assert_no_ios_cli_error(sess._buf, context="write memory")
        return

    raise TimeoutError("write memory: timeout waiting for [OK] after confirmations")


async def _expect_enable_prompt_after_write_ok(sess: TelnetIOSSession, timeout: float) -> None:
    """Attend la présence d'un prompt enable après le ``[OK]`` du save (lit le flux au besoin)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _has_enable_prompt_after_write_ok(sess._buf):
            return
        rem = deadline - time.time()
        if rem <= 0:
            break
        chunk = await sess._read_some(time.time() + min(0.35, rem))
        if chunk:
            sess._buf += chunk
        else:
            await asyncio.sleep(0.05)
    if not _has_enable_prompt_after_write_ok(sess._buf):
        raise TimeoutError("write memory: timeout waiting for exec prompt after [OK]")


async def _push_config_lines(
    sess: TelnetIOSSession,
    lines: List[str],
    *,
    timeout: float,
    delay_line: float,
    write_memory: bool,
) -> None:
    """Entre en config, envoie chaque ligne avec délai optionnel, sort, ``write memory`` si demandé."""
    await sess.enter_config()
    for ln in lines:
        sess.sendline(ln)
        if delay_line > 0:
            await asyncio.sleep(delay_line)
    sess.clear_buffer()
    await sess.exit_config()
    if write_memory:
        await complete_write_memory(sess, timeout)


async def push_one(
    node: NodeConsole,
    cfg_path: Path,
    *,
    timeout: float,
    delay_line: float,
    write_memory: bool,
    verbose: bool,
) -> PushResult:
    """Applique un seul ``<nom>.cfg`` : connexion → enable → config → lignes → end → wr mem."""
    try:
        cfg_text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except Exception as es:
        return PushResult(node=node.name, port=node.port, status="FAIL(read_cfg)", detail=str(es))

    lines = list(iter_cfg_lines(cfg_text))
    if not lines:
        return PushResult(node=node.name, port=node.port, status="SKIP(empty_cfg)")

    try:
        host = "127.0.0.1"
        port = node.port

        async with TelnetIOSSession(host, port, timeout=timeout, verbose=verbose) as sess:
            await sess.wake()
            await sess.ensure_enable()

            await _push_config_lines(
                sess,
                lines,
                timeout=timeout,
                delay_line=delay_line,
                write_memory=write_memory,
            )

        return PushResult(node=node.name, port=node.port, status="OK", detail=str(cfg_path.name))
    except TimeoutError as e:
        return PushResult(node=node.name, port=node.port, status="FAIL(prompt)", detail=str(e))
    except (ConnectionRefusedError, OSError) as e:
        return PushResult(node=node.name, port=node.port, status="FAIL(connect)", detail=str(e))
    except Exception as e:
        return PushResult(node=node.name, port=node.port, status="FAIL", detail=str(e))


def run_push(
    gns3_project_dir: Path,
    cfg_dir: Path,
    *,
    gns3_file: Optional[Path] = None,
    only: Optional[str] = None,
    strict: bool = False,
    dry_run: bool = False,
    timeout: float = 6.0,
    delay_line: float = 0.02,
    write_memory: bool = False,
    no_write: bool = False,
    verbose: bool = False,
    workers: int = 1,
) -> int:
    """
    Pousse les <name>.cfg de cfg_dir vers les consoles telnet du projet GNS3.
    Même logique que la sous-commande ``push`` (réutilisable depuis generate/diff).

    Parallélisme : chaque worker lance ``asyncio.run(push_one(...))`` dans un thread
    (telnetlib3 est asyncio ; plusieurs boucles event en parallèle = un thread par push).
    """
    if workers < 1:
        _eprint("[ERR] --workers must be >= 1")
        return 2

    project_dir = gns3_project_dir
    if not project_dir.exists():
        _eprint(f"[ERR] project dir not found: {project_dir}")
        return 2
    if not cfg_dir.exists():
        _eprint(f"[ERR] cfg dir not found: {cfg_dir}")
        return 2

    gns3_path = find_gns3_file(project_dir, gns3_file)
    consoles = load_node_consoles(gns3_path)

    only_nodes = parse_only_list(only)
    nodes = [c for c in consoles.values() if c.console_type == "telnet"]
    nodes.sort(key=lambda n: n.name.lower())
    if only_nodes is not None:
        nodes = [n for n in nodes if n.name in only_nodes]

    if not nodes:
        _eprint("[ERR] No telnet console nodes found (after filters).")
        return 2

    missing: List[str] = []
    planned: List[Tuple[NodeConsole, Path]] = []
    for n in nodes:
        p = cfg_dir / f"{n.name}.cfg"
        if not p.exists():
            missing.append(n.name)
            continue
        planned.append((n, p))

    print(f"[INFO] GNS3 file: {gns3_path}")
    print(f"[INFO] Nodes(telnet): {len(nodes)} | With cfg: {len(planned)} | Missing cfg: {len(missing)}")

    if missing:
        print("[WARN] Missing configs for:", ", ".join(missing))
        if strict:
            return 3

    if dry_run:
        for n in nodes:
            cfg = cfg_dir / f"{n.name}.cfg"
            status = "FOUND" if cfg.exists() else "MISSING"
            print(f"[DRY] {n.name:<12} localhost:{n.port} cfg={status} ({cfg.name})")
        return 0

    results: List[PushResult] = []
    do_write_memory = bool(write_memory) and not bool(no_write)

    push_kwargs = {
        "timeout": float(timeout),
        "delay_line": float(delay_line),
        "write_memory": do_write_memory,
        "verbose": bool(verbose),
    }

    if workers == 1:
        # Un seul event loop : simple et prévisible pour le lab
        for n, cfg in planned:
            print(f"[PUSH] {n.name} -> localhost:{n.port} ({cfg.name})")
            res = asyncio.run(push_one(n, cfg, **push_kwargs))
            results.append(res)
            print(f"[{res.status}] {n.name} {res.detail}".rstrip())
    else:
        print(f"[INFO] Parallel mode enabled: workers={workers}")
        for n, cfg in planned:
            print(f"[QUEUE] {n.name} -> localhost:{n.port} ({cfg.name})")

        # Chaque future exécute sa propre boucle asyncio (isolation par thread)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(asyncio.run, push_one(n, cfg, **push_kwargs)): (n, cfg)
                for n, cfg in planned
            }
            for fut in as_completed(future_map):
                n, _cfg = future_map[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = PushResult(node=n.name, port=n.port, status="FAIL(thread)", detail=str(e))
                results.append(res)
                print(f"[{res.status}] {res.node} {res.detail}".rstrip())

    ok = sum(1 for r in results if r.status == "OK")
    fail = sum(1 for r in results if r.status.startswith("FAIL"))
    skip = sum(1 for r in results if r.status.startswith("SKIP"))
    print(f"\n[SUMMARY] OK={ok} FAIL={fail} SKIP={skip}")
    return 0 if fail == 0 else 4


def add_push_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Enregistre sur ``parser`` les flags ``--push``, ``--gns3-project``, timeouts, workers, etc."""
    parser.add_argument(
        "--push",
        action="store_true",
        help="Après succès, pousser les .cfg vers les consoles telnet GNS3",
    )
    parser.add_argument(
        "--gns3-project",
        type=Path,
        default=None,
        metavar="DIR",
        help="Répertoire du projet GNS3 (requis avec --push)",
    )
    parser.add_argument(
        "--gns3-file",
        type=Path,
        default=None,
        help="Fichier .gns3 explicite (relatif au projet ou absolu)",
    )
    parser.add_argument(
        "--push-only",
        type=str,
        default=None,
        metavar="NODES",
        help="Avec --push : nœuds à pousser (liste séparée par virgules). Pour ``diff``, utiliser plutôt --only.",
    )
    parser.add_argument(
        "--push-strict",
        action="store_true",
        help="Avec --push : échouer si un .cfg attendu manque",
    )
    parser.add_argument(
        "--push-dry-run",
        action="store_true",
        help="Avec --push : afficher le plan sans connexion telnet",
    )
    parser.add_argument("--push-timeout", type=float, default=6.0, help="Timeout telnet (s), défaut 6")
    parser.add_argument(
        "--push-delay-line",
        type=float,
        default=0.02,
        help="Délai entre lignes de config (s)",
    )
    parser.add_argument(
        "--push-write-memory",
        action="store_true",
        help="Avec --push : exécuter write memory à la fin",
    )
    parser.add_argument("--push-verbose", action="store_true", help="Avec --push : traces telnet sur stderr")
    parser.add_argument(
        "--push-workers",
        type=int,
        default=1,
        help="Avec --push : pushes parallèles (défaut 1)",
    )


def main(argv: Sequence[str]) -> int:
    """Sous-commande ``push`` : parse les arguments puis appelle ``run_push``."""
    ap = argparse.ArgumentParser(description="Push <name>.cfg configs into GNS3 nodes over telnet.")
    ap.add_argument("gns3_project_dir", type=Path, help="Folder containing the GNS3 project (.gns3 file)")
    ap.add_argument("cfg_dir", type=Path, help="Folder containing <name>.cfg files to push (source)")
    ap.add_argument("--gns3-file", type=Path, default=None, help="Explicit .gns3 file (relative to project dir or absolute)")
    ap.add_argument("--only", type=str, default=None, help="Comma-separated node names to push (ex: PE1,P1)")
    ap.add_argument("--strict", action="store_true", help="Fail if any node config is missing")
    ap.add_argument("--dry-run", action="store_true", help="Print mapping and planned actions without connecting")
    ap.add_argument("--timeout", type=float, default=6.0, help="Telnet/prompt timeout (seconds)")
    ap.add_argument("--delay-line", type=float, default=0.02, help="Delay between config lines (seconds)")
    ap.add_argument("--write-memory", action="store_true", help="Run 'write memory' at the end (default: off)")
    ap.add_argument("--no-write", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--verbose", action="store_true", help="Verbose telnet I/O to stderr (debug)")
    ap.add_argument("--workers", type=int, default=1, help="Number of parallel pushes (default: 1)")

    args = ap.parse_args(list(argv))
    return run_push(
        args.gns3_project_dir,
        args.cfg_dir,
        gns3_file=args.gns3_file,
        only=args.only,
        strict=args.strict,
        dry_run=args.dry_run,
        timeout=args.timeout,
        delay_line=args.delay_line,
        write_memory=args.write_memory,
        no_write=args.no_write,
        verbose=args.verbose,
        workers=args.workers,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

