#!/usr/bin/env python3
"""
push_gns3_configs.py

Push Cisco IOS-like configs (<name>.cfg) into GNS3 nodes via telnet consoles.

Usage:
  python script/push_gns3_configs.py <gns3_project_dir> <cfg_dir> [options]

Example:
  python script/push_gns3_configs.py gns3/projet_gns3_1 Configs/Configs-20260327-120000 --only PE1,P1
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


# NOTE: prompts must be matched at *end of buffer*, not just any line.
# Using \Z avoids multiline '$' matching mid-buffer.
IOS_ANY_PROMPT_RE = re.compile(rb"[^\r\n]*(?:\([^\)]*\))?[>#]\s*\Z")
IOS_ENABLE_PROMPT_RE = re.compile(rb"[^\r\n]*#\s*\Z")
IOS_CONFIG_PROMPT_RE = re.compile(rb"[^\r\n]*\([^\)]*config[^\)]*\)#\s*\Z")
IOS_CONFIRM_RE = re.compile(rb"(?i)\[confirm\]\s*\Z")
IOS_WRITE_OK_RE = re.compile(rb"(?i)\[ok\]")

IOS_INIT_DIALOG_RE = re.compile(
    rb"(?is)Would you like to enter the initial configuration dialog\?\s*\[yes/no\]:\s*$"
)
IOS_PRESS_RETURN_RE = re.compile(rb"(?is)Press\s+RETURN\s+to\s+get\s+started!?")

def _tail_bytes(b: bytes, n: int = 220) -> str:
    try:
        s = b.decode("utf-8", errors="replace")
    except Exception:
        s = repr(b)
    s2 = s.replace("\r", "\\r").replace("\n", "\\n")
    return s2[-n:]


@dataclass(frozen=True)
class NodeConsole:
    name: str
    port: int
    console_type: str


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def find_gns3_file(project_dir: Path, explicit: Optional[Path]) -> Path:
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
    if not value:
        return None
    items = [x.strip() for x in value.split(",") if x.strip()]
    return set(items) if items else None


def iter_cfg_lines(cfg_text: str) -> Iterable[str]:
    """
    Return lines to push, excluding:
    - empty lines
    - pure comments starting with '!'
    - trailing 'end' (we control session exit)
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
    def __init__(self, host: str, port: int, timeout: float, *, verbose: bool = False) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self.reader = None
        self.writer = None
        self._buf = b""

    async def __aenter__(self) -> "TelnetIOSSession":
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
        if self.writer is not None:
            try:
                self.writer.close()
            except Exception:
                pass

    async def _read_some(self, deadline: float) -> bytes:
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
        if self.writer is None:
            raise RuntimeError("telnet session not connected")
        if self.verbose:
            _eprint(f"[VERBOSE][TX {self.host}:{self.port}] {line}")
        # IOS consoles typically expect CRLF.
        self.writer.write(line.encode("utf-8", errors="ignore") + b"\r\n")

    def clear_buffer(self) -> None:
        self._buf = b""

    async def wake(self) -> None:
        """
        Ensure we reach an IOS prompt, handling common first-boot questions.
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
        # Always send 'enable' (safe if already enabled).
        self.sendline("enable")
        await self._expect_regex(IOS_ENABLE_PROMPT_RE, timeout=self.timeout)

    async def enter_config(self) -> None:
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
        self.sendline("end")
        await self._expect_regex(IOS_ENABLE_PROMPT_RE, timeout=self.timeout)


@dataclass
class PushResult:
    node: str
    port: int
    status: str
    detail: str = ""


async def push_one(
    node: NodeConsole,
    cfg_path: Path,
    *,
    timeout: float,
    delay_line: float,
    write_memory: bool,
    verbose: bool,
) -> PushResult:
    try:
        cfg_text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return PushResult(node=node.name, port=node.port, status="FAIL(read_cfg)", detail=str(e))

    lines = list(iter_cfg_lines(cfg_text))
    if not lines:
        return PushResult(node=node.name, port=node.port, status="SKIP(empty_cfg)")

    try:
        async with TelnetIOSSession("127.0.0.1", node.port, timeout=timeout, verbose=verbose) as sess:
            await sess.wake()
            await sess.ensure_enable()
            await sess.enter_config()

            for ln in lines:
                sess.sendline(ln)
                if delay_line > 0:
                    await asyncio.sleep(delay_line)

            sess.clear_buffer()
            await sess.exit_config()
            if write_memory:
                sess.clear_buffer()
                sess.sendline("write memory")
                matched = await sess._expect_any_regex(
                    (IOS_CONFIRM_RE, IOS_WRITE_OK_RE, IOS_ENABLE_PROMPT_RE),
                    timeout=timeout,
                )
                if matched is IOS_CONFIRM_RE:
                    # IOS asks confirmation, pressing Enter accepts default.
                    sess.clear_buffer()
                    sess.sendline("")
                    await sess._expect_regex(IOS_WRITE_OK_RE, timeout=timeout)
                    await sess._expect_regex(IOS_ENABLE_PROMPT_RE, timeout=timeout)
                elif matched is IOS_WRITE_OK_RE:
                    await sess._expect_regex(IOS_ENABLE_PROMPT_RE, timeout=timeout)
                else:
                    # We got '#' quickly; still wait a bit for explicit [OK] if emitted.
                    # Some images print [OK] before returning prompt, others may skip it.
                    try:
                        await sess._expect_regex(IOS_WRITE_OK_RE, timeout=min(2.0, timeout))
                    except TimeoutError:
                        pass

        return PushResult(node=node.name, port=node.port, status="OK", detail=str(cfg_path.name))
    except TimeoutError as e:
        return PushResult(node=node.name, port=node.port, status="FAIL(prompt)", detail=str(e))
    except (ConnectionRefusedError, OSError) as e:
        return PushResult(node=node.name, port=node.port, status="FAIL(connect)", detail=str(e))
    except Exception as e:
        return PushResult(node=node.name, port=node.port, status="FAIL", detail=str(e))


def main(argv: Sequence[str]) -> int:
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
    if args.workers < 1:
        _eprint("[ERR] --workers must be >= 1")
        return 2

    project_dir: Path = args.gns3_project_dir
    cfg_dir: Path = args.cfg_dir
    if not project_dir.exists():
        _eprint(f"[ERR] project dir not found: {project_dir}")
        return 2
    if not cfg_dir.exists():
        _eprint(f"[ERR] cfg dir not found: {cfg_dir}")
        return 2

    gns3_path = find_gns3_file(project_dir, args.gns3_file)
    consoles = load_node_consoles(gns3_path)

    only = parse_only_list(args.only)
    nodes = [c for c in consoles.values() if c.console_type == "telnet"]
    nodes.sort(key=lambda n: n.name.lower())
    if only is not None:
        nodes = [n for n in nodes if n.name in only]

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
        if args.strict:
            return 3

    if args.dry_run:
        for n in nodes:
            cfg = cfg_dir / f"{n.name}.cfg"
            status = "FOUND" if cfg.exists() else "MISSING"
            print(f"[DRY] {n.name:<12} localhost:{n.port} cfg={status} ({cfg.name})")
        return 0

    results: List[PushResult] = []
    push_kwargs = {
        "timeout": float(args.timeout),
        "delay_line": float(args.delay_line),
        "write_memory": bool(args.write_memory) and not bool(args.no_write),
        "verbose": bool(args.verbose),
    }

    if args.workers == 1:
        for n, cfg in planned:
            print(f"[PUSH] {n.name} -> localhost:{n.port} ({cfg.name})")
            res = asyncio.run(push_one(n, cfg, **push_kwargs))
            results.append(res)
            print(f"[{res.status}] {n.name} {res.detail}".rstrip())
    else:
        print(f"[INFO] Parallel mode enabled: workers={args.workers}")
        for n, cfg in planned:
            print(f"[QUEUE] {n.name} -> localhost:{n.port} ({cfg.name})")

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

