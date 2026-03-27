from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import Optional

import ttkbootstrap as ttk

from intent_model import (
    IntentDict,
    new_default_intent,
    load_intent,
    save_intent,
    validate_intent,
)
from .frames.general_frame import GeneralFrame
from .frames.addressing_frame import AddressingFrame
from .frames.core_as_frame import CoreASFrame
from .frames.customers_frame import CustomersFrame
from .frames.vpn_services_frame import VPNServicesFrame
from .frames.lan_frame import LANFrame


class IntentApp(ttk.Window):
    def __init__(self) -> None:
        super().__init__(themename="darkly")
        self.title("Intent Generator")
        self.geometry("1150x720")
        self.minsize(950, 620)

        self._current_path: Optional[Path] = None
        self._intent: IntentDict = new_default_intent()

        self._configure_style()
        self._create_menu()
        self._create_widgets()
        self._refresh_all_frames()

        self.bind_all("<Control-n>", lambda e: self.new_intent())
        self.bind_all("<Control-o>", lambda e: self.open_intent())
        self.bind_all("<Control-s>", lambda e: self.save_intent())
        self.bind_all("<Control-q>", lambda e: self.quit())

    # ─── PUBLIC API ──────────────────────────────────────────────────────────

    def run(self) -> None:
        self.mainloop()

    # ─── INTERNAL SETUP ─────────────────────────────────────────────────────

    def _configure_style(self) -> None:
        self.option_add("*Font", ("Segoe UI", 10))
        self.option_add("*TButton.padding", 6)
        self.option_add("*TEntry.padding", 4)

    def _create_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Nouveau Intent", command=self.new_intent, accelerator="Ctrl+N")
        file_menu.add_command(label="Ouvrir…", command=self.open_intent, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Enregistrer", command=self.save_intent, accelerator="Ctrl+S")
        file_menu.add_command(label="Enregistrer sous…", command=self.save_intent_as)
        file_menu.add_separator()
        file_menu.add_command(label="Quitter", command=self.quit, accelerator="Ctrl+Q")
        menubar.add_cascade(label="Fichier", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
        tools_menu.add_command(
            label="Scripts de génération (à venir)…",
            state="disabled",
        )
        menubar.add_cascade(label="Outils", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(
            label="À propos",
            command=lambda: messagebox.showinfo(
                "À propos",
                "Intent Generator\n\nInterface Tkinter pour créer/éditer des intents v4.\n"
                "Consulte aussi la documentation dans docs/intent/.",
                parent=self,
            ),
        )
        menubar.add_cascade(label="Aide", menu=help_menu)

        self.config(menu=menubar)

    def _create_widgets(self) -> None:
        self._container = ttk.Frame(self, padding=12)
        self._container.pack(fill="both", expand=True)

        notebook = ttk.Notebook(self._container, bootstyle="dark")
        notebook.pack(fill="both", expand=True)

        self.general_frame = GeneralFrame(notebook, self)
        self.addressing_frame = AddressingFrame(notebook, self)
        self.core_as_frame = CoreASFrame(notebook, self)
        self.customers_frame = CustomersFrame(notebook, self)
        self.vpn_services_frame = VPNServicesFrame(notebook, self)
        self.lan_frame = LANFrame(notebook, self)

        notebook.add(self.general_frame, text="Général")
        notebook.add(self.addressing_frame, text="Addressing")
        notebook.add(self.core_as_frame, text="Core (AS)")
        notebook.add(self.customers_frame, text="Clients")
        notebook.add(self.vpn_services_frame, text="VPN Services")
        notebook.add(self.lan_frame, text="LAN")

        self.status_var = tk.StringVar(value="Prêt")
        status = ttk.Label(
            self._container,
            textvariable=self.status_var,
            anchor="w",
            bootstyle="secondary",
        )
        status.pack(fill="x", pady=(10, 0))

    # ─── INTENT MANAGEMENT ──────────────────────────────────────────────────

    @property
    def intent(self) -> IntentDict:
        return self._intent

    @property
    def current_path(self) -> Optional[Path]:
        return self._current_path

    def set_intent(self, intent: IntentDict, path: Optional[Path] = None) -> None:
        self._intent = intent
        self._current_path = path
        self._refresh_all_frames()
        self._update_status()

    def new_intent(self) -> None:
        if not self._confirm_discard_changes():
            return
        self.set_intent(new_default_intent(), None)

    def open_intent(self) -> None:
        if not self._confirm_discard_changes():
            return
        filename = filedialog.askopenfilename(
            title="Ouvrir un intent JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            parent=self,
        )
        if not filename:
            return
        try:
            intent = load_intent(filename)
        except Exception as exc:
            messagebox.showerror("Erreur", f"Impossible de charger le fichier:\n{exc}", parent=self)
            return
        self.set_intent(intent, Path(filename))

    def save_intent(self) -> None:
        if not self._current_path:
            self.save_intent_as()
            return
        errors = validate_intent(self._intent)
        if errors:
            if not messagebox.askyesno(
                "Intent invalide",
                "Le fichier présente des problèmes:\n\n- "
                + "\n- ".join(errors)
                + "\n\nVoulez-vous enregistrer quand même ?",
                parent=self,
            ):
                return
        try:
            save_intent(self._intent, self._current_path)
        except Exception as exc:
            messagebox.showerror("Erreur", f"Impossible d'enregistrer le fichier:\n{exc}", parent=self)
            return
        messagebox.showinfo("Enregistré", f"Intent sauvegardé dans:\n{self._current_path}", parent=self)
        self.general_frame.refresh()
        self._update_status()

    def save_intent_as(self) -> None:
        default_dir = Path("script").resolve()
        default_dir.mkdir(parents=True, exist_ok=True)
        filename = filedialog.asksaveasfilename(
            title="Enregistrer l'intent sous…",
            initialdir=str(default_dir),
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            parent=self,
        )
        if not filename:
            return
        self._current_path = Path(filename)
        self.save_intent()

    def _confirm_discard_changes(self) -> bool:
        # Simple confirmation for now; no dirty-tracking yet.
        return messagebox.askyesno(
            "Confirmation",
            "Les changements non enregistrés seront perdus.\nContinuer ?",
            parent=self,
        )

    def _refresh_all_frames(self) -> None:
        self.general_frame.refresh()
        self.addressing_frame.refresh()
        self.core_as_frame.refresh()
        self.customers_frame.refresh()
        self.vpn_services_frame.refresh()
        self.lan_frame.refresh()
        self._update_status()

    def _update_status(self) -> None:
        path = str(self._current_path) if self._current_path else "(non enregistré)"
        errors = validate_intent(self._intent)
        if errors:
            self.status_var.set(f"Fichier: {path}   •   Validation: {len(errors)} problème(s)")
        else:
            self.status_var.set(f"Fichier: {path}   •   Validation: OK")

