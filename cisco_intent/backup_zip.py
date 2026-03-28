# -*- coding: utf-8 -*-
"""Archive un run (Configs-* ou Modifs-*) en .zip (contenu à la racine de l'archive)."""

from __future__ import annotations

import zipfile
from pathlib import Path


def zip_run_dir(src_dir: Path, dest_zip: Path) -> None:
    """
    Compresse tous les fichiers directs de ``src_dir`` dans ``dest_zip``
    (noms d'entrée = noms de fichiers seuls, sans préfixe de dossier).
    """
    src_dir = src_dir.resolve()
    if not src_dir.is_dir():
        raise NotADirectoryError(f"zip_run_dir: pas un dossier: {src_dir}")
    dest_zip = dest_zip.resolve()
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.exists():
        dest_zip.unlink()
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src_dir.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file():
                zf.write(p, arcname=p.name)
