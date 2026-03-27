from __future__ import annotations

import tkinter as tk
import ttkbootstrap as ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..app import IntentApp


class GeneralFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "IntentApp") -> None:
        super().__init__(master, padding=16)
        self.app = app

        self.intent_version_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.summary_var = tk.StringVar()

        self._build()

    def _build(self) -> None:
        top = ttk.Labelframe(self, text="Intent", bootstyle="secondary")
        top.pack(fill="x", expand=False, pady=(0, 10))

        ttk.Label(top, text="Version:").grid(
            row=0, column=0, sticky="w", padx=(8, 8), pady=6
        )
        entry_version = ttk.Entry(top, textvariable=self.intent_version_var, width=10)
        entry_version.grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(top, text="Fichier:").grid(
            row=1, column=0, sticky="w", padx=(8, 8), pady=6
        )
        entry_path = ttk.Entry(top, textvariable=self.path_var, state="readonly")
        entry_path.grid(row=1, column=1, sticky="we", pady=6)

        btns = ttk.Frame(top)
        btns.grid(row=0, column=2, rowspan=2, sticky="e", padx=(10, 5))
        ttk.Button(btns, text="Nouveau", command=self.app.new_intent, bootstyle="secondary").grid(
            row=0, column=0, padx=4, pady=4
        )
        ttk.Button(btns, text="Ouvrir…", command=self.app.open_intent, bootstyle="secondary").grid(
            row=0, column=1, padx=4, pady=4
        )
        ttk.Button(btns, text="Enregistrer", command=self.app.save_intent, bootstyle="success").grid(
            row=1, column=0, padx=4, pady=4
        )
        ttk.Button(btns, text="Enregistrer sous…", command=self.app.save_intent_as, bootstyle="primary").grid(
            row=1, column=1, padx=4, pady=4
        )

        top.columnconfigure(1, weight=1)

        summary_box = ttk.Labelframe(self, text="Résumé", bootstyle="secondary")
        summary_box.pack(fill="both", expand=True)
        label_summary = ttk.Label(
            summary_box, textvariable=self.summary_var, justify="left", anchor="nw"
        )
        label_summary.pack(fill="both", expand=True, padx=5, pady=5)

        entry_version.bind("<FocusOut>", lambda e: self._on_version_changed())

    def _on_version_changed(self) -> None:
        self.app.intent["intent_version"] = self.intent_version_var.get().strip()

    def refresh(self) -> None:
        intent = self.app.intent
        self.intent_version_var.set(str(intent.get("intent_version", "4.0")))

        if self.app.current_path:
            self.path_var.set(str(self.app.current_path))
        else:
            self.path_var.set("(non enregistré)")

        autos = intent.get("autonomous_systems", {}) or {}
        customers = intent.get("customers", []) or []
        vpn = intent.get("vpn_services", {}) or {}
        vrfs = vpn.get("vrfs", []) if isinstance(vpn, dict) else []

        summary_lines = [
            f"Autonomous systems : {len(autos)}",
            f"Clients           : {len(customers)}",
            f"VRFs              : {len(vrfs)}",
        ]
        self.summary_var.set("\n".join(summary_lines))

