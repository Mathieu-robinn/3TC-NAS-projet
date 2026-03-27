from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import TYPE_CHECKING, Dict, Any

import ttkbootstrap as ttk
if TYPE_CHECKING:  # pragma: no cover
    from ..app import IntentApp


class LANFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "IntentApp") -> None:
        super().__init__(master, padding=16)
        self.app = app

        self.enabled_var = tk.BooleanVar()
        self.type_var = tk.StringVar()
        self.base_pool_var = tk.StringVar()
        self.prefix_var = tk.StringVar()
        self.strategy_var = tk.StringVar()
        self.loopback_name_var = tk.StringVar()
        self.interface_name_var = tk.StringVar()
        self.sub_parent_var = tk.StringVar()
        self.sub_vlan_base_var = tk.StringVar()
        self.bgp_advertise_var = tk.BooleanVar()
        self.bgp_method_var = tk.StringVar()

        self._build()

    def _build(self) -> None:
        top = ttk.Labelframe(self, text="LAN de test sur CE", bootstyle="secondary")
        top.pack(fill="both", expand=True)

        row = 0
        ttk.Checkbutton(top, text="Activer LAN", variable=self.enabled_var).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=5, pady=4
        )
        row += 1

        ttk.Label(top, text="Type:").grid(row=row, column=0, sticky="w", padx=5, pady=4)
        type_combo = ttk.Combobox(
            top,
            textvariable=self.type_var,
            values=["loopback", "interface", "subinterface_vlan"],
            state="readonly",
            width=18,
        )
        type_combo.grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        ttk.Label(top, text="Base pool:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(top, textvariable=self.base_pool_var).grid(
            row=row, column=1, sticky="we", pady=4
        )
        row += 1

        ttk.Label(top, text="Prefix:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(top, textvariable=self.prefix_var, width=6).grid(
            row=row, column=1, sticky="w", pady=4
        )
        row += 1

        ttk.Label(top, text="Strategy:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(top, textvariable=self.strategy_var).grid(
            row=row, column=1, sticky="we", pady=4
        )
        row += 1

        # Type-specific options
        spec = ttk.Labelframe(top, text="Options du type", bootstyle="secondary")
        spec.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(4, 4))

        ttk.Label(spec, text="Loopback name:").grid(
            row=0, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(spec, textvariable=self.loopback_name_var).grid(
            row=0, column=1, sticky="we", pady=4
        )

        ttk.Label(spec, text="Interface name:").grid(
            row=1, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(spec, textvariable=self.interface_name_var).grid(
            row=1, column=1, sticky="we", pady=4
        )

        ttk.Label(spec, text="Subif parent:").grid(
            row=2, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(spec, textvariable=self.sub_parent_var).grid(
            row=2, column=1, sticky="we", pady=4
        )

        ttk.Label(spec, text="VLAN base:").grid(
            row=3, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(spec, textvariable=self.sub_vlan_base_var, width=8).grid(
            row=3, column=1, sticky="w", pady=4
        )

        # BGP
        bgp_frame = ttk.Labelframe(top, text="Annonce BGP", bootstyle="secondary")
        bgp_frame.grid(row=row + 1, column=0, columnspan=2, sticky="nsew", pady=(4, 4))

        ttk.Checkbutton(
            bgp_frame, text="Annoncer le LAN", variable=self.bgp_advertise_var
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=4)

        ttk.Label(bgp_frame, text="Méthode:").grid(
            row=1, column=0, sticky="w", padx=5, pady=4
        )
        method_combo = ttk.Combobox(
            bgp_frame,
            textvariable=self.bgp_method_var,
            values=["network_statement", "redistribute_connected"],
            state="readonly",
            width=22,
        )
        method_combo.grid(row=1, column=1, sticky="w", pady=4)

        # Actions
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Appliquer", command=self._apply, bootstyle="primary").pack(
            side="left", padx=4
        )

        top.columnconfigure(1, weight=1)
        spec.columnconfigure(1, weight=1)
        bgp_frame.columnconfigure(1, weight=1)

    def _lan(self) -> Dict[str, Any]:
        return self.app.intent.setdefault("lan", {})

    def _apply(self) -> None:
        lan = self._lan()
        lan["enabled"] = bool(self.enabled_var.get())
        lan["type"] = self.type_var.get() or "loopback"

        addr = lan.setdefault("addressing", {})
        addr["base_pool"] = self.base_pool_var.get().strip()
        try:
            addr["prefix"] = int(self.prefix_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "Prefix doit être un entier.", parent=self)
            return
        addr["strategy"] = self.strategy_var.get().strip() or "per_site"

        lan.setdefault("naming", {})["pattern"] = (
            self.loopback_name_var.get().strip() or "Loopback0"
        )
        lan.setdefault("interface", {})["name"] = (
            self.interface_name_var.get().strip() or "GigabitEthernet0/1"
        )
        sub = lan.setdefault("subinterface", {})
        sub["parent"] = self.sub_parent_var.get().strip() or "GigabitEthernet0/1"
        try:
            sub["vlan_base"] = int(self.sub_vlan_base_var.get() or "200")
        except ValueError:
            messagebox.showerror("Erreur", "VLAN base doit être un entier.", parent=self)
            return

        bgp = lan.setdefault("bgp", {})
        bgp["advertise"] = bool(self.bgp_advertise_var.get())
        bgp["method"] = self.bgp_method_var.get() or "network_statement"

    def refresh(self) -> None:
        lan = self._lan()
        self.enabled_var.set(bool(lan.get("enabled", True)))
        self.type_var.set(lan.get("type", "loopback"))

        addr = lan.get("addressing", {}) or {}
        self.base_pool_var.set(addr.get("base_pool", "10.0.0.0/8"))
        self.prefix_var.set(str(addr.get("prefix", 32)))
        self.strategy_var.set(addr.get("strategy", "per_site"))

        naming = lan.get("naming", {}) or {}
        self.loopback_name_var.set(naming.get("pattern", "Loopback0"))
        iface = lan.get("interface", {}) or {}
        self.interface_name_var.set(iface.get("name", "GigabitEthernet0/1"))
        sub = lan.get("subinterface", {}) or {}
        self.sub_parent_var.set(sub.get("parent", "GigabitEthernet0/1"))
        self.sub_vlan_base_var.set(str(sub.get("vlan_base", 200)))

        bgp = lan.get("bgp", {}) or {}
        self.bgp_advertise_var.set(bool(bgp.get("advertise", True)))
        self.bgp_method_var.set(bgp.get("method", "network_statement"))

