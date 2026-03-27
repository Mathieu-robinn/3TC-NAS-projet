from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import TYPE_CHECKING, Dict, Any, List

import ttkbootstrap as ttk
if TYPE_CHECKING:  # pragma: no cover
    from ..app import IntentApp


class VPNServicesFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "IntentApp") -> None:
        super().__init__(master, padding=16)
        self.app = app

        self.type_var = tk.StringVar()
        self.rd_mode_var = tk.StringVar()
        self.rd_base_var = tk.StringVar()
        self.rt_strategy_var = tk.StringVar()

        self._build()

    def _build(self) -> None:
        top = ttk.Labelframe(self, text="Paramètres VPN", bootstyle="secondary")
        top.pack(fill="x", expand=False)

        help_text = (
            "RD identifie chaque VRF de façon unique.\n"
            "RT contrôle l'import/export des routes VPN entre VRFs."
        )
        ttk.Label(top, text=help_text, justify="left", bootstyle="info").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=5, pady=(4, 8)
        )

        row = 1
        ttk.Label(top, text="Type de service:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        type_combo = ttk.Combobox(
            top, textvariable=self.type_var, values=["l3vpn"], state="readonly", width=10
        )
        type_combo.grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        ttk.Label(top, text="Mode RD:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        rd_combo = ttk.Combobox(
            top,
            textvariable=self.rd_mode_var,
            values=["asn_vrfid", "asn_hash"],
            state="readonly",
            width=12,
        )
        rd_combo.grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        ttk.Label(top, text="Base RD (entier):").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(top, textvariable=self.rd_base_var, width=10).grid(
            row=row, column=1, sticky="w", pady=4
        )
        row += 1

        ttk.Label(top, text="Stratégie RT:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        rt_combo = ttk.Combobox(
            top,
            textvariable=self.rt_strategy_var,
            values=["auto_per_vrf", "auto_per_customer_asn"],
            state="readonly",
            width=20,
        )
        rt_combo.grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        ttk.Button(top, text="Appliquer", command=self._apply, bootstyle="primary").grid(
            row=row, column=0, columnspan=2, pady=(4, 8)
        )
        top.columnconfigure(1, weight=1)

        vrf_frame = ttk.Labelframe(self, text="VRFs", bootstyle="secondary")
        vrf_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.vrf_tree = ttk.Treeview(
            vrf_frame,
            columns=("name", "customer"),
            show="headings",
            height=6,
        )
        self.vrf_tree.heading("name", text="Nom VRF")
        self.vrf_tree.heading("customer", text="Client")
        self.vrf_tree.column("name", width=160, anchor="center")
        self.vrf_tree.column("customer", width=160, anchor="center")
        self.vrf_tree.pack(fill="both", expand=True, side="top")

        btns = ttk.Frame(vrf_frame)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Ajouter VRF", command=self._add_vrf, bootstyle="success").pack(
            side="left", padx=4
        )
        ttk.Button(btns, text="Supprimer VRF", command=self._delete_vrf, bootstyle="danger").pack(
            side="left", padx=4
        )

    def _vpn(self) -> Dict[str, Any]:
        return self.app.intent.setdefault("vpn_services", {})

    def _vrfs(self) -> List[Dict[str, Any]]:
        vpn = self._vpn()
        return vpn.setdefault("vrfs", [])

    def _apply(self) -> None:
        vpn = self._vpn()
        vpn["type"] = self.type_var.get() or "l3vpn"
        rd = vpn.setdefault("rd", {})
        rd["mode"] = self.rd_mode_var.get() or "asn_vrfid"
        try:
            rd["base"] = int(self.rd_base_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "RD base doit être un entier.", parent=self)
            return
        rt = vpn.setdefault("rt", {})
        rt["strategy"] = self.rt_strategy_var.get() or "auto_per_vrf"

    def _add_vrf(self) -> None:
        customers = self.app.intent.get("customers", []) or []
        if not customers:
            messagebox.showinfo(
                "Info",
                "Aucun client défini.\nAjoute d'abord un client dans l'onglet Clients.",
                parent=self,
            )
            return
        cust_name = customers[0].get("name", "CUST1")
        self._vrfs().append({"name": cust_name, "customer": cust_name})
        self.refresh()
        messagebox.showinfo(
            "VRF ajoutée",
            f"VRF '{cust_name}' ajoutée et liée au client '{cust_name}'.",
            parent=self,
        )

    def _delete_vrf(self) -> None:
        selection = self.vrf_tree.selection()
        if not selection:
            messagebox.showinfo("Info", "Sélectionne d'abord une VRF à supprimer.", parent=self)
            return
        vrfs = self._vrfs()
        index = self.vrf_tree.index(selection[0])
        if 0 <= index < len(vrfs):
            vrf_name = vrfs[index].get("name", "VRF")
            if not messagebox.askyesno(
                "Confirmation",
                f"Supprimer la VRF '{vrf_name}' ?",
                parent=self,
            ):
                return
            vrfs.pop(index)
        self.refresh()

    def refresh(self) -> None:
        vpn = self._vpn()
        self.type_var.set(vpn.get("type", "l3vpn"))
        rd = vpn.get("rd", {}) or {}
        self.rd_mode_var.set(rd.get("mode", "asn_vrfid"))
        self.rd_base_var.set(str(rd.get("base", 100)))
        rt = vpn.get("rt", {}) or {}
        self.rt_strategy_var.set(rt.get("strategy", "auto_per_vrf"))

        self.vrf_tree.delete(*self.vrf_tree.get_children())
        for vrf in self._vrfs():
            self.vrf_tree.insert(
                "",
                "end",
                values=(vrf.get("name", ""), vrf.get("customer", "")),
            )

