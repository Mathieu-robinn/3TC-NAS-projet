from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import TYPE_CHECKING, Dict, Any

import ttkbootstrap as ttk
if TYPE_CHECKING:  # pragma: no cover
    from ..app import IntentApp


class CoreASFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, app: "IntentApp") -> None:
        super().__init__(master, padding=16)
        self.app = app

        self._selected_as_name: str | None = None

        self._build()

    def _build(self) -> None:
        main_pane = ttk.Panedwindow(self, orient="horizontal")
        main_pane.pack(fill="both", expand=True)

        left = ttk.Frame(main_pane)
        right = ttk.Frame(main_pane)
        main_pane.add(left, weight=1)
        main_pane.add(right, weight=3)

        # AS list
        as_frame = ttk.Labelframe(left, text="Autonomous Systems", bootstyle="secondary")
        as_frame.pack(fill="both", expand=True)

        self.as_tree = ttk.Treeview(
            as_frame,
            columns=("asn", "nodes", "links"),
            show="headings",
            height=6,
        )
        self.as_tree.heading("asn", text="ASN")
        self.as_tree.heading("nodes", text="#Nodes")
        self.as_tree.heading("links", text="#Links")
        self.as_tree.column("asn", width=80, anchor="center")
        self.as_tree.column("nodes", width=70, anchor="center")
        self.as_tree.column("links", width=70, anchor="center")
        self.as_tree.pack(fill="both", expand=True, side="top")
        self.as_tree.bind("<<TreeviewSelect>>", lambda e: self._on_as_selected())

        btns = ttk.Frame(as_frame)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Ajouter AS", command=self._add_as, bootstyle="success").pack(
            side="left", padx=4
        )
        ttk.Button(btns, text="Supprimer AS", command=self._delete_as, bootstyle="danger").pack(
            side="left", padx=4
        )

        # Right side: nodes and links
        nodes_frame = ttk.Labelframe(right, text="Nodes", bootstyle="secondary")
        nodes_frame.pack(fill="both", expand=True, pady=(0, 6))

        self.nodes_tree = ttk.Treeview(
            nodes_frame, columns=("role",), show="headings", height=5
        )
        self.nodes_tree.heading("role", text="Role (PE/P)")
        self.nodes_tree.column("role", width=80, anchor="center")
        self.nodes_tree.pack(fill="both", expand=True, side="top")

        node_btns = ttk.Frame(nodes_frame)
        node_btns.pack(fill="x", pady=4)
        ttk.Button(node_btns, text="Ajouter Node", command=self._add_node, bootstyle="success").pack(
            side="left", padx=4
        )
        ttk.Button(node_btns, text="Supprimer Node", command=self._delete_node, bootstyle="danger").pack(
            side="left", padx=4
        )

        links_frame = ttk.Labelframe(right, text="Links", bootstyle="secondary")
        links_frame.pack(fill="both", expand=True)

        self.links_tree = ttk.Treeview(
            links_frame,
            columns=("a_node", "a_if", "b_node", "b_if"),
            show="headings",
            height=6,
        )
        for col, text in [
            ("a_node", "A node"),
            ("a_if", "A if"),
            ("b_node", "B node"),
            ("b_if", "B if"),
        ]:
            self.links_tree.heading(col, text=text)
            self.links_tree.column(col, width=120, anchor="center")
        self.links_tree.pack(fill="both", expand=True, side="top")

        link_btns = ttk.Frame(links_frame)
        link_btns.pack(fill="x", pady=4)
        ttk.Button(link_btns, text="Ajouter Lien", command=self._add_link, bootstyle="success").pack(
            side="left", padx=4
        )
        ttk.Button(link_btns, text="Supprimer Lien", command=self._delete_link, bootstyle="danger").pack(
            side="left", padx=4
        )

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _current_as(self) -> Dict[str, Any] | None:
        if self._selected_as_name is None:
            return None
        return self.app.intent.get("autonomous_systems", {}).get(self._selected_as_name)

    # ─── AS operations ───────────────────────────────────────────────────────

    def _on_as_selected(self) -> None:
        selection = self.as_tree.selection()
        if not selection:
            self._selected_as_name = None
            self._refresh_nodes_links()
            return
        item_id = selection[0]
        self._selected_as_name = self.as_tree.item(item_id, "text")
        self._refresh_nodes_links()

    def _add_as(self) -> None:
        name = self._simple_prompt("Nom de l'AS (ex: AS1) :")
        if not name:
            return
        autos = self.app.intent.setdefault("autonomous_systems", {})
        if name in autos:
            messagebox.showerror("Erreur", "Un AS avec ce nom existe déjà.", parent=self)
            return
        autos[name] = {
            "asn": 64512,
            "nodes": {},
            "links": [],
            "underlay": {"igp": {"protocol": "ospf", "area": {"mode": "single_area"}}},
            "bgp": {
                "type": "ibgp",
                "vpnv4": True,
                "peering": {"strategy": "full_mesh", "transport": "loopback"},
            },
        }
        self.refresh()

    def _delete_as(self) -> None:
        if not self._selected_as_name:
            return
        if not messagebox.askyesno(
            "Confirmation",
            f"Supprimer l'AS {self._selected_as_name} ?",
            parent=self,
        ):
            return
        autos = self.app.intent.get("autonomous_systems", {})
        autos.pop(self._selected_as_name, None)
        self._selected_as_name = None
        self.refresh()

    # ─── Nodes operations ────────────────────────────────────────────────────

    def _add_node(self) -> None:
        as_data = self._current_as()
        if as_data is None:
            messagebox.showinfo("Info", "Sélectionne d'abord un AS.", parent=self)
            return
        name = self._simple_prompt("Nom du node (ex: PE1) :")
        if not name:
            return
        nodes = as_data.setdefault("nodes", {})
        if name in nodes:
            messagebox.showerror("Erreur", "Ce node existe déjà.", parent=self)
            return
        role = self._simple_prompt("Role (PE ou P) :", default_value="PE")
        if not role:
            role = "PE"
        nodes[name] = {"role": role.upper()}
        self._refresh_nodes_links()

    def _delete_node(self) -> None:
        as_data = self._current_as()
        if as_data is None:
            return
        selection = self.nodes_tree.selection()
        if not selection:
            return
        node_name = self.nodes_tree.item(selection[0], "text")
        if not messagebox.askyesno(
            "Confirmation",
            f"Supprimer le node {node_name} et ses liens ?",
            parent=self,
        ):
            return
        nodes = as_data.get("nodes", {})
        nodes.pop(node_name, None)
        links = as_data.get("links", [])
        as_data["links"] = [
            l
            for l in links
            if not any(ep.get("node") == node_name for ep in l.get("endpoints", []))
        ]
        self._refresh_nodes_links()

    # ─── Links operations ────────────────────────────────────────────────────

    def _add_link(self) -> None:
        as_data = self._current_as()
        if as_data is None:
            messagebox.showinfo("Info", "Sélectionne d'abord un AS.", parent=self)
            return
        a_node = self._simple_prompt("Node A :", default_value="PE1")
        b_node = self._simple_prompt("Node B :", default_value="P1")
        a_if = self._simple_prompt("Interface A :", default_value="GigabitEthernet1/0")
        b_if = self._simple_prompt("Interface B :", default_value="GigabitEthernet1/0")
        if not (a_node and b_node):
            return
        link = {
            "endpoints": [
                {"node": a_node, "interface": a_if},
                {"node": b_node, "interface": b_if},
            ]
        }
        as_data.setdefault("links", []).append(link)
        self._refresh_nodes_links()

    def _delete_link(self) -> None:
        as_data = self._current_as()
        if as_data is None:
            return
        selection = self.links_tree.selection()
        if not selection:
            return
        index = self.links_tree.index(selection[0])
        links = as_data.get("links", [])
        if 0 <= index < len(links):
            del links[index]
        self._refresh_nodes_links()

    # ─── Refresh ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self.as_tree.delete(*self.as_tree.get_children())

        autos = self.app.intent.get("autonomous_systems", {}) or {}
        for name, as_data in autos.items():
            asn = as_data.get("asn", "")
            nodes = as_data.get("nodes", {}) or {}
            links = as_data.get("links", []) or []
            iid = self.as_tree.insert(
                "",
                "end",
                text=name,
                values=(asn, len(nodes), len(links)),
            )
            if name == self._selected_as_name:
                self.as_tree.selection_set(iid)

        if not self._selected_as_name and autos:
            first_name = next(iter(autos.keys()))
            for iid in self.as_tree.get_children():
                if self.as_tree.item(iid, "text") == first_name:
                    self.as_tree.selection_set(iid)
                    self._selected_as_name = first_name
                    break

        self._refresh_nodes_links()

    def _refresh_nodes_links(self) -> None:
        self.nodes_tree.delete(*self.nodes_tree.get_children())
        self.links_tree.delete(*self.links_tree.get_children())

        as_data = self._current_as()
        if as_data is None:
            return

        for name, meta in (as_data.get("nodes", {}) or {}).items():
            self.nodes_tree.insert("", "end", text=name, values=(meta.get("role", ""),))

        for link in as_data.get("links", []) or []:
            eps = link.get("endpoints", [])
            if len(eps) != 2:
                continue
            a, b = eps
            self.links_tree.insert(
                "",
                "end",
                values=(
                    a.get("node", ""),
                    a.get("interface", ""),
                    b.get("node", ""),
                    b.get("interface", ""),
                ),
            )

    # ─── Small prompt helper ─────────────────────────────────────────────────

    def _simple_prompt(self, message: str, default_value: str | None = None) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title("Saisie")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text=message).pack(padx=10, pady=(10, 4))
        var = tk.StringVar(value=default_value or "")
        entry = ttk.Entry(dialog, textvariable=var)
        entry.pack(padx=10, pady=4, fill="x")
        entry.focus_set()

        value: dict[str, str | None] = {"result": None}

        def on_ok() -> None:
            value["result"] = var.get().strip()
            dialog.destroy()

        def on_cancel() -> None:
            dialog.destroy()

        btns = ttk.Frame(dialog)
        btns.pack(padx=10, pady=(4, 10))
        ttk.Button(btns, text="OK", command=on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Annuler", command=on_cancel).pack(side="left", padx=4)

        dialog.bind("<Return>", lambda e: on_ok())
        dialog.bind("<Escape>", lambda e: on_cancel())

        self.wait_window(dialog)
        return value["result"]

