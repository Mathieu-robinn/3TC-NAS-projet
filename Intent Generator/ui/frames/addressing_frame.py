from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import TYPE_CHECKING

import ttkbootstrap as ttk
from intent_model import validate_intent

if TYPE_CHECKING:  # pragma: no cover
    from ..app import IntentApp


class AddressingFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "IntentApp") -> None:
        super().__init__(master, padding=16)
        self.app = app

        self.loopback_pool_var = tk.StringVar()
        self.p2p_pool_var = tk.StringVar()
        self.customer_pool_var = tk.StringVar()
        self.p2p_prefix_var = tk.StringVar()
        self.ce_pe_prefix_var = tk.StringVar()

        self._build()

    def _build(self) -> None:
        grid = ttk.Labelframe(self, text="Addressing", bootstyle="secondary")
        grid.pack(fill="both", expand=True)

        labels = [
            ("Loopback pool (CIDR)", self.loopback_pool_var, "1.0.0.0/8"),
            ("P2P pool (CIDR)", self.p2p_pool_var, "10.0.0.0/16"),
            ("Customer pool (CIDR)", self.customer_pool_var, "172.16.0.0/16"),
            ("P2P prefix", self.p2p_prefix_var, "30"),
            ("CE-PE prefix", self.ce_pe_prefix_var, "30"),
        ]

        for row, (label, var, placeholder) in enumerate(labels):
            ttk.Label(grid, text=label).grid(
                row=row, column=0, sticky="w", padx=(8, 8), pady=6
            )
            entry = ttk.Entry(grid, textvariable=var)
            entry.grid(row=row, column=1, sticky="we", pady=6)

        grid.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_frame, text="Appliquer", command=self._apply, bootstyle="primary").pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Valider", command=self._validate, bootstyle="info").pack(
            side="left", padx=4
        )

    def _apply(self) -> None:
        intent = self.app.intent
        addr = intent.setdefault("addressing", {})
        addr["loopback_pool"] = self.loopback_pool_var.get().strip()
        addr["p2p_pool"] = self.p2p_pool_var.get().strip()
        addr["customer_pool"] = self.customer_pool_var.get().strip()
        try:
            addr["p2p_prefix"] = int(self.p2p_prefix_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "P2P prefix doit être un entier.", parent=self)
            return
        try:
            addr["ce_pe_prefix"] = int(self.ce_pe_prefix_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "CE-PE prefix doit être un entier.", parent=self)
            return

        if hasattr(self.app, "_update_status"):
            self.app._update_status()

    def _validate(self) -> None:
        self._apply()
        errors = validate_intent(self.app.intent)
        if errors:
            messagebox.showwarning(
                "Problèmes détectés",
                "Validation de l'intent :\n\n- " + "\n- ".join(errors),
                parent=self,
            )
        else:
            messagebox.showinfo(
                "OK",
                "Aucun problème détecté sur les champs requis.",
                parent=self,
            )

    def refresh(self) -> None:
        addr = self.app.intent.get("addressing", {}) or {}
        self.loopback_pool_var.set(addr.get("loopback_pool", "1.0.0.0/8"))
        self.p2p_pool_var.set(addr.get("p2p_pool", "10.0.0.0/16"))
        self.customer_pool_var.set(addr.get("customer_pool", "172.16.0.0/16"))
        self.p2p_prefix_var.set(str(addr.get("p2p_prefix", 30)))
        self.ce_pe_prefix_var.set(str(addr.get("ce_pe_prefix", 30)))

