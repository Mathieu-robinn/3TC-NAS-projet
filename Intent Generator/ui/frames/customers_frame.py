from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import TYPE_CHECKING, Dict, Any, List

import ttkbootstrap as ttk
if TYPE_CHECKING:  # pragma: no cover
    from ..app import IntentApp


class CustomersFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "IntentApp") -> None:
        super().__init__(master, padding=16)
        self.app = app

        self._selected_index: int | None = None

        self._build()

    def _build(self) -> None:
        main_pane = ttk.Panedwindow(self, orient="horizontal")
        main_pane.pack(fill="both", expand=True)

        left = ttk.Frame(main_pane)
        right = ttk.Frame(main_pane)
        main_pane.add(left, weight=1)
        main_pane.add(right, weight=3)

        # Customers list
        cust_frame = ttk.Labelframe(left, text="Clients", bootstyle="secondary")
        cust_frame.pack(fill="both", expand=True)

        self.cust_tree = ttk.Treeview(
            cust_frame, columns=("name", "asn", "sites"), show="headings", height=8
        )
        self.cust_tree.heading("name", text="Nom")
        self.cust_tree.heading("asn", text="ASN")
        self.cust_tree.heading("sites", text="#Sites")
        self.cust_tree.column("name", width=160, anchor="w")
        self.cust_tree.column("asn", width=90, anchor="center")
        self.cust_tree.column("sites", width=80, anchor="center")
        self.cust_tree.pack(fill="both", expand=True, side="top")
        self.cust_tree.bind("<<TreeviewSelect>>", lambda e: self._on_customer_selected())

        btns = ttk.Frame(cust_frame)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Ajouter Client", command=self._add_customer, bootstyle="success").pack(
            side="left", padx=4
        )
        ttk.Button(btns, text="Supprimer Client", command=self._delete_customer, bootstyle="danger").pack(
            side="left", padx=4
        )

        # Right side: selected customer details
        self.detail_frame = ttk.Labelframe(right, text="Détails du client", bootstyle="secondary")
        self.detail_frame.pack(fill="both", expand=True)

        self.name_var = tk.StringVar()
        self.asn_var = tk.StringVar()

        row = 0
        ttk.Label(self.detail_frame, text="Nom:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(self.detail_frame, textvariable=self.name_var).grid(
            row=row, column=1, sticky="we", pady=4
        )
        row += 1
        ttk.Label(self.detail_frame, text="ASN:").grid(
            row=row, column=0, sticky="w", padx=5, pady=4
        )
        ttk.Entry(self.detail_frame, textvariable=self.asn_var).grid(
            row=row, column=1, sticky="we", pady=4
        )
        row += 1

        ttk.Button(self.detail_frame, text="Appliquer", command=self._apply_detail, bootstyle="primary").grid(
            row=row, column=0, columnspan=2, pady=(4, 8)
        )

        row += 1
        sites_frame = ttk.Labelframe(self.detail_frame, text="Sites", bootstyle="secondary")
        sites_frame.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(4, 0))

        self.sites_tree = ttk.Treeview(
            sites_frame,
            columns=("ce", "pe"),
            show="headings",
            height=5,
        )
        self.sites_tree.heading("ce", text="CE")
        self.sites_tree.heading("pe", text="PE")
        self.sites_tree.column("ce", width=120, anchor="center")
        self.sites_tree.column("pe", width=120, anchor="center")
        self.sites_tree.pack(fill="both", expand=True, side="top")

        site_btns = ttk.Frame(sites_frame)
        site_btns.pack(fill="x", pady=4)
        ttk.Button(site_btns, text="Ajouter Site", command=self._add_site, bootstyle="success").pack(
            side="left", padx=4
        )
        ttk.Button(site_btns, text="Supprimer Site", command=self._delete_site, bootstyle="danger").pack(
            side="left", padx=4
        )

        self.detail_frame.columnconfigure(1, weight=1)
        self.detail_frame.rowconfigure(row, weight=1)

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _customers(self) -> List[Dict[str, Any]]:
        return self.app.intent.setdefault("customers", [])

    def _current_customer(self) -> Dict[str, Any] | None:
        if self._selected_index is None:
            return None
        customers = self._customers()
        if 0 <= self._selected_index < len(customers):
            return customers[self._selected_index]
        return None

    # ─── Events & operations ─────────────────────────────────────────────────

    def _on_customer_selected(self) -> None:
        selection = self.cust_tree.selection()
        if not selection:
            self._selected_index = None
            self._refresh_detail()
            return
        iid = selection[0]
        self._selected_index = self.cust_tree.index(iid)
        self._refresh_detail()

    def _add_customer(self) -> None:
        customers = self._customers()
        customers.append({"name": "NEW_CUSTOMER", "asn": 65000, "sites": []})
        self.refresh()

    def _delete_customer(self) -> None:
        if self._selected_index is None:
            return
        customers = self._customers()
        if not (0 <= self._selected_index < len(customers)):
            return
        if not messagebox.askyesno(
            "Confirmation", "Supprimer ce client ?", parent=self
        ):
            return
        customers.pop(self._selected_index)
        self._selected_index = None
        self.refresh()

    def _apply_detail(self) -> None:
        cust = self._current_customer()
        if cust is None:
            return
        cust["name"] = self.name_var.get().strip()
        try:
            cust["asn"] = int(self.asn_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "ASN doit être un entier.", parent=self)
            return
        self.refresh()

    def _add_site(self) -> None:
        cust = self._current_customer()
        if cust is None:
            messagebox.showinfo("Info", "Sélectionne d'abord un client.", parent=self)
            return
        sites = cust.setdefault("sites", [])
        index = len(sites) + 1
        sites.append(
            {
                "ce": f"{cust.get('name', 'CE')}-{index}",
                "pe": "PE1",
                "link": {
                    "endpoints": [
                        {
                            "node": f"{cust.get('name', 'CE')}-{index}",
                            "interface": "GigabitEthernet2/0",
                        },
                        {"node": "PE1", "interface": "GigabitEthernet2/0"},
                    ]
                },
            }
        )
        self._refresh_detail()

    def _delete_site(self) -> None:
        cust = self._current_customer()
        if cust is None:
            return
        selection = self.sites_tree.selection()
        if not selection:
            return
        index = self.sites_tree.index(selection[0])
        sites = cust.get("sites", [])
        if 0 <= index < len(sites):
            sites.pop(index)
        self._refresh_detail()

    # ─── Refresh ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self.cust_tree.delete(*self.cust_tree.get_children())

        customers = self._customers()
        target_index = self._selected_index if self._selected_index is not None else 0
        selected_iid = None
        for i, cust in enumerate(customers):
            iid = self.cust_tree.insert(
                "",
                "end",
                values=(
                    cust.get("name", f"CUST{i+1}"),
                    cust.get("asn", ""),
                    len(cust.get("sites", []) or []),
                ),
            )
            if i == target_index:
                selected_iid = iid

        if selected_iid is not None:
            self.cust_tree.selection_set(selected_iid)
            self._selected_index = target_index
        elif not customers:
            self._selected_index = None
        else:
            self._selected_index = len(customers) - 1
            all_items = self.cust_tree.get_children()
            if all_items:
                self.cust_tree.selection_set(all_items[-1])

        self._refresh_detail()

    def _refresh_detail(self) -> None:
        cust = self._current_customer()
        if cust is None:
            self.name_var.set("")
            self.asn_var.set("")
            self.sites_tree.delete(*self.sites_tree.get_children())
            return

        self.name_var.set(cust.get("name", ""))
        self.asn_var.set(str(cust.get("asn", "")))

        self.sites_tree.delete(*self.sites_tree.get_children())
        for site in cust.get("sites", []) or []:
            self.sites_tree.insert(
                "",
                "end",
                values=(site.get("ce", ""), site.get("pe", "")),
            )

