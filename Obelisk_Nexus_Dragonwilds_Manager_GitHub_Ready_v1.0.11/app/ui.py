from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
import webbrowser
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from .logging_setup import configure_logging
from .models import ModRecord, ServerProfile, WORLD_TYPES
from .paths import ensure_dirs
from .secrets import load_secret, save_secret
from .storage import ProfileStore
from .task_runner import TaskRunner
from .services.backup_service import create_backup, restore_backup, verify_backup
from .services.config_service import IniDocument, discover_configs, save_text_atomic
from .services.discord_service import send_webhook
from .services.image_service import ImageService
from .services.mod_service import install_mod, load_manifests, uninstall_mod
from .services.nexus_service import (
    GAME_DOMAIN, NEXUS_GAME_URL, NexusClient, NexusError, NexusRegistrationRequired, choose_install_file,
)
from .services.profile_service import create_profile_transaction, update_profile_transaction, world_type_requires_save
from .services.public_service import (
    format_readiness_report, public_search_name, repair_public_config,
    request_windows_firewall_rules, validate_world_name,
)
from .services.server_service import ServerService
from .services.world_service import clone_world, delete_world, import_world, list_worlds, rename_world


BG = "#0d1210"
PANEL = "#151c18"
PANEL_2 = "#1b2520"
ACCENT = "#caa75a"
ACCENT_HOVER = "#e0c176"
TEXT = "#eef3ef"
MUTED = "#9cac9f"
GREEN = "#4fa36b"
RED = "#d45f5f"
BLUE = "#5f8fd4"
BORDER = "#2a382f"


class DragonwildsManagerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Dragonwilds Server Manager — Nexus Submission Candidate 1.0.11")
        self.root.geometry("1320x820")
        self.root.minsize(1050, 680)
        self.root.configure(bg=BG)
        self._app_icon_ref = None
        try:
            assets = Path(__file__).resolve().parent.parent / "assets"
            ico = assets / "DragonwildsServerManager.ico"
            png = assets / "Dragonwilds_Server_Manager_Logo_1024.png"
            if os.name == "nt" and ico.exists():
                self.root.iconbitmap(default=str(ico))
            elif png.exists():
                self._app_icon_ref = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._app_icon_ref)
        except (tk.TclError, OSError):
            self._app_icon_ref = None
        self.logger = configure_logging()
        self.root.report_callback_exception = self._report_ui_exception
        self.paths = ensure_dirs()
        self.store = ProfileStore()
        self.runner = TaskRunner(root)
        self.server_service = ServerService()
        self.image_service = ImageService(self.paths["cache"] / "mod-icons")
        self.profiles: list[ServerProfile] = []
        self.selected_id: str | None = None
        self.current_page = "Dashboard"
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.mod_records: list[ModRecord] = []
        self.mod_offset = 0
        self.mod_total = 0
        self.mod_page_size = 20
        self.mod_photo_refs: dict[int, tk.PhotoImage] = {}
        self._mod_render_generation = 0
        self.installed_manifests: dict[str, dict] = {}
        self.config_paths: list[Path] = []
        self.selected_config: Path | None = None
        self._closing = False
        self._heartbeat = 0
        self._profiles_loaded_once = False
        self._build_style()
        self._build_shell()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(75, self._load_profiles_async)
        self.root.after(100, self._heartbeat_tick)

    def _report_ui_exception(self, exc_type, exc_value, exc_traceback) -> None:
        self.logger.error("Unhandled UI callback error", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            self.set_status(f"A UI action failed: {exc_value}", False)
        except Exception:
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=PANEL, foreground=TEXT, fieldbackground=PANEL, rowheight=28, borderwidth=0)
        style.configure("Treeview.Heading", background=PANEL_2, foreground=ACCENT, relief="flat", padding=(8, 6))
        style.map("Treeview", background=[("selected", "#314738")], foreground=[("selected", TEXT)])
        style.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT, arrowcolor=ACCENT)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL_2, bordercolor=PANEL_2, lightcolor=ACCENT, darkcolor=ACCENT)

    def _build_shell(self) -> None:
        top = tk.Frame(self.root, bg=PANEL_2, height=64, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="DRAGONWILDS", font=("Segoe UI Semibold", 18), fg=ACCENT, bg=PANEL_2).pack(side="left", padx=(22, 8), pady=14)
        tk.Label(top, text="SERVER MANAGER", font=("Segoe UI", 13), fg=TEXT, bg=PANEL_2).pack(side="left", pady=17)
        self.profile_selector = ttk.Combobox(top, state="readonly", width=34)
        self.profile_selector.pack(side="right", padx=22, pady=16)
        self.profile_selector.bind("<<ComboboxSelected>>", self._profile_selected)
        tk.Label(top, text="Active server", font=("Segoe UI", 9), fg=MUTED, bg=PANEL_2).pack(side="right", pady=20)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)
        sidebar = tk.Frame(body, bg="#101713", width=190, highlightbackground=BORDER, highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.content = tk.Frame(body, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

        for name in ("Dashboard", "Servers", "Configuration", "Worlds", "Backups", "Mods", "Discord", "Logs", "Settings"):
            button = tk.Button(
                sidebar, text=name, anchor="w", padx=22, pady=11, bd=0,
                font=("Segoe UI", 10), fg=TEXT, bg="#101713", activebackground=PANEL_2,
                activeforeground=ACCENT, cursor="hand2", command=lambda n=name: self.show_page(n),
            )
            button.pack(fill="x", padx=8, pady=2)
            self.nav_buttons[name] = button
        tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", padx=14, pady=10)
        tk.Label(sidebar, text="Unofficial community tool\nNot affiliated with Jagex", justify="left", fg=MUTED, bg="#101713", font=("Segoe UI", 8)).pack(side="bottom", anchor="w", padx=18, pady=18)

        self.status_frame = tk.Frame(self.root, bg=PANEL_2, height=34, highlightbackground=BORDER, highlightthickness=1)
        self.status_frame.pack(fill="x", side="bottom")
        self.status_label = tk.Label(self.status_frame, text="Starting clean rebuild…", fg=MUTED, bg=PANEL_2, font=("Segoe UI", 9), anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True, padx=12)
        self.progress = ttk.Progressbar(self.status_frame, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=12, pady=7)
        self.show_page("Dashboard")

    def _page(self, name: str, builder: Callable[[tk.Frame], None]) -> tk.Frame:
        if name not in self.pages:
            frame = tk.Frame(self.content, bg=BG)
            builder(frame)
            self.pages[name] = frame
        return self.pages[name]

    def show_page(self, name: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        for nav_name, button in self.nav_buttons.items():
            button.configure(bg=PANEL_2 if nav_name == name else "#101713", fg=ACCENT if nav_name == name else TEXT)
        builders = {
            "Dashboard": self._build_dashboard,
            "Servers": self._build_servers,
            "Configuration": self._build_configuration,
            "Worlds": self._build_worlds,
            "Backups": self._build_backups,
            "Mods": self._build_mods,
            "Discord": self._build_discord,
            "Logs": self._build_logs,
            "Settings": self._build_settings,
        }
        page = self._page(name, builders[name])
        page.pack(fill="both", expand=True)
        self.current_page = name
        self._refresh_page(name)

    def _header(self, parent, title: str, subtitle: str) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x", padx=24, pady=(22, 12))
        tk.Label(frame, text=title, fg=TEXT, bg=BG, font=("Segoe UI Semibold", 20)).pack(anchor="w")
        tk.Label(frame, text=subtitle, fg=MUTED, bg=BG, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))
        return frame

    def _card(self, parent, **pack) -> tk.Frame:
        frame = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(**pack)
        return frame

    def _button(self, parent, text: str, command, *, accent=False, danger=False, width=None) -> tk.Button:
        bg = ACCENT if accent else RED if danger else PANEL_2
        fg = "#17160f" if accent else TEXT
        active = ACCENT_HOVER if accent else "#bd5151" if danger else "#27342c"
        return tk.Button(parent, text=text, command=command, width=width, bg=bg, fg=fg, activebackground=active, activeforeground=fg, bd=0, padx=12, pady=8, font=("Segoe UI Semibold", 9), cursor="hand2")

    def _selected(self) -> ServerProfile | None:
        return next((p for p in self.profiles if p.id == self.selected_id), None)

    def set_status(self, text: str, busy: bool = False) -> None:
        self.status_label.configure(text=text)
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()

    def run_task(self, label: str, fn, *args, on_success=None, on_error=None, refresh: str | None = None, **kwargs) -> None:
        self.set_status(label, True)

        def ok(value):
            self.set_status(f"{label} — completed", False)
            if on_success:
                on_success(value)
            if refresh:
                self._refresh_page(refresh)

        def fail(exc):
            self.logger.exception("Task failed: %s", label, exc_info=(type(exc), exc, exc.__traceback__))
            self.set_status(f"{label} — failed: {exc}", False)
            if on_error:
                on_error(exc)
            else:
                messagebox.showerror("Dragonwilds Server Manager", str(exc), parent=self.root)

        self.runner.submit(fn, *args, on_success=ok, on_error=fail, **kwargs)

    def run_stopped_task(self, label: str, profile: ServerProfile, fn, *args, on_success=None, refresh: str | None = None, **kwargs) -> None:
        def guarded():
            if self.server_service.is_running(profile):
                raise RuntimeError("Stop this server before performing that operation.")
            return fn(*args, **kwargs)
        self.run_task(label, guarded, on_success=on_success, refresh=refresh)

    def _load_profiles_async(self) -> None:
        self.run_task("Loading server profiles", self.store.load, on_success=self._profiles_loaded)

    def _profiles_loaded(self, profiles: list[ServerProfile]) -> None:
        self._profiles_loaded_once = True
        self.profiles = profiles
        if profiles and not self.selected_id:
            self.selected_id = profiles[0].id
        self._refresh_profile_selector()
        self._refresh_page(self.current_page)
        self.set_status("Ready", False)

    def _refresh_profile_selector(self) -> None:
        values = [p.name for p in self.profiles]
        self.profile_selector["values"] = values
        selected = self._selected()
        if selected and selected.name in values:
            self.profile_selector.current(values.index(selected.name))
        elif values:
            self.profile_selector.current(0)

    def _profile_selected(self, _event=None) -> None:
        index = self.profile_selector.current()
        if 0 <= index < len(self.profiles):
            self.selected_id = self.profiles[index].id
            self._refresh_page(self.current_page)

    def _heartbeat_tick(self) -> None:
        self._heartbeat += 1
        if not self._closing:
            self.root.after(100, self._heartbeat_tick)

    # Dashboard -----------------------------------------------------------------
    def _build_dashboard(self, page: tk.Frame) -> None:
        self._header(page, "Dashboard", "Control the selected server without blocking the desktop interface.")
        top = tk.Frame(page, bg=BG); top.pack(fill="x", padx=24)
        self.dashboard_status_card = self._card(top, side="left", fill="both", expand=True, padx=(0, 8))
        self.dashboard_actions_card = self._card(top, side="left", fill="both", expand=True, padx=(8, 0))
        self.dashboard_title = tk.Label(self.dashboard_status_card, text="No server selected", fg=TEXT, bg=PANEL, font=("Segoe UI Semibold", 16))
        self.dashboard_title.pack(anchor="w", padx=18, pady=(18, 8))
        self.dashboard_details = tk.Label(self.dashboard_status_card, text="Create a server profile to begin.", justify="left", fg=MUTED, bg=PANEL, font=("Consolas", 10))
        self.dashboard_details.pack(anchor="w", padx=18, pady=(0, 18))
        tk.Label(self.dashboard_actions_card, text="Server controls", fg=ACCENT, bg=PANEL, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=18, pady=(18, 10))
        row = tk.Frame(self.dashboard_actions_card, bg=PANEL); row.pack(fill="x", padx=18, pady=(0, 18))
        self._button(row, "Start", self.start_selected, accent=True).pack(side="left", padx=(0, 8))
        self._button(row, "Stop", self.stop_selected).pack(side="left", padx=8)
        self._button(row, "Restart", self.restart_selected).pack(side="left", padx=8)
        self._button(row, "Backup", self.create_selected_backup).pack(side="left", padx=8)
        self._button(row, "Public listing check", self.public_listing_check).pack(side="left", padx=8)
        lower = tk.Frame(page, bg=BG); lower.pack(fill="both", expand=True, padx=24, pady=16)
        self.dashboard_events = tk.Text(lower, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 9), wrap="word")
        self.dashboard_events.pack(fill="both", expand=True)
        self.dashboard_events.insert("end", "The manager is ready. All blocking work runs in background workers.\n")
        self.dashboard_events.configure(state="disabled")

    def _refresh_dashboard(self) -> None:
        if not hasattr(self, "dashboard_title"):
            return
        profile = self._selected()
        if not profile:
            self.dashboard_title.configure(text="No server selected")
            self.dashboard_details.configure(text="Create a server profile to begin.")
            return
        self.dashboard_title.configure(text=profile.name)
        self.dashboard_details.configure(text=(
            f"Type:         {profile.server_type}\n"
            f"Default world: {profile.world_name}\n"
            f"Public search: Checking newest .sav…\n"
            f"Game port:    UDP {profile.port}\n"
            f"Query port:   UDP {profile.query_port}\n"
            f"Players:      {profile.max_players}\n"
            f"Install:      {profile.install_dir}\n"
            f"Status:       Checking in background…"
        ))
        self.runner.submit(self.server_service.is_running, profile, on_success=lambda running: self._dashboard_running(profile.id, running))
        self.runner.submit(public_search_name, profile, on_success=lambda name: self._dashboard_public_name(profile.id, name))

    def _dashboard_running(self, profile_id: str, running: bool) -> None:
        profile = self._selected()
        if not profile or profile.id != profile_id:
            return
        text = self.dashboard_details.cget("text").replace("Checking in background…", "RUNNING" if running else "Stopped")
        self.dashboard_details.configure(text=text)

    def _dashboard_public_name(self, profile_id: str, name: str) -> None:
        profile = self._selected()
        if not profile or profile.id != profile_id:
            return
        text = self.dashboard_details.cget("text").replace("Checking newest .sav…", name or "Not configured")
        self.dashboard_details.configure(text=text)

    def public_listing_check(self) -> None:
        profile = self._selected()
        if not profile:
            messagebox.showinfo("Public listing", "Create or select a server first.", parent=self.root)
            return
        self.run_task(
            "Checking public server readiness", format_readiness_report, profile,
            on_success=lambda report: self._show_public_listing_report(profile, report),
        )

    def _show_public_listing_report(self, profile: ServerProfile, report: str) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Public server readiness")
        dialog.geometry("840x620")
        dialog.minsize(680, 480)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        header = tk.Frame(dialog, bg=PANEL_2)
        header.pack(fill="x")
        tk.Label(header, text="Public Server Readiness", fg=ACCENT, bg=PANEL_2, font=("Segoe UI Semibold", 16)).pack(side="left", padx=16, pady=14)
        controls = tk.Frame(header, bg=PANEL_2)
        controls.pack(side="right", padx=12, pady=8)
        self._button(controls, "Repair config", lambda: self._repair_public_listing(profile, dialog), accent=True).pack(side="left", padx=4)
        self._button(controls, "Open firewall ports", lambda: self._open_public_ports(profile)).pack(side="left", padx=4)
        self._button(controls, "Copy search name", lambda: self._copy_public_name(profile)).pack(side="left", padx=4)
        text = tk.Text(dialog, bg="#0f1512", fg=TEXT, insertbackground=TEXT, wrap="word", relief="flat", font=("Consolas", 10))
        text.pack(fill="both", expand=True, padx=16, pady=16)
        text.insert("1.0", report)
        text.configure(state="disabled")

    def _repair_public_listing(self, profile: ServerProfile, dialog=None) -> None:
        def repair():
            if self.server_service.is_running(profile):
                raise RuntimeError("Stop the server before repairing DedicatedServer.ini.")
            name = repair_public_config(profile)
            self.store.save(self.profiles)
            return name
        def done(name):
            if dialog and dialog.winfo_exists():
                dialog.destroy()
            messagebox.showinfo(
                "Public listing repaired",
                f"The dedicated config was rewritten in the exact Dragonwilds format.\n\nSearch the Public tab for this exact case-sensitive world name:\n{name}",
                parent=self.root,
            )
            self._refresh_page("Dashboard")
        self.run_task("Repairing public server configuration", repair, on_success=done)

    def _open_public_ports(self, profile: ServerProfile) -> None:
        self.run_task(
            "Opening Windows Firewall setup", request_windows_firewall_rules, profile, self.paths["tools"],
            on_success=lambda _path: messagebox.showinfo(
                "Windows Firewall",
                f"Approve the Windows administrator prompt to allow UDP {profile.port} and UDP {profile.query_port}.\n\nYour router must also forward the game port to this computer for outside players.",
                parent=self.root,
            ),
        )

    def _copy_public_name(self, profile: ServerProfile) -> None:
        def copied(name):
            self.root.clipboard_clear()
            self.root.clipboard_append(name)
            self.set_status(f"Copied public search name: {name}", False)
        self.run_task("Reading public search name", public_search_name, profile, on_success=copied)

    def start_selected(self) -> None:
        profile = self._selected()
        if not profile:
            messagebox.showinfo("Server", "Create or select a server first.", parent=self.root); return
        self.run_task("Starting server", self.server_service.start, profile, on_success=lambda pid: self._server_started(profile, pid), refresh="Dashboard")

    def _server_started(self, profile: ServerProfile, pid: int) -> None:
        self.runner.submit(self.store.save, self.profiles)
        if profile.discord_webhook:
            self.runner.submit(send_webhook, profile.discord_webhook, "Server online", f"**{profile.name}** started on UDP port {profile.port}.")

    def stop_selected(self) -> None:
        profile = self._selected()
        if not profile: return
        self.run_task("Stopping server", self.server_service.stop, profile, on_success=lambda _: self._server_stopped(profile), refresh="Dashboard")

    def _server_stopped(self, profile: ServerProfile) -> None:
        self.runner.submit(self.store.save, self.profiles)
        if profile.discord_webhook:
            self.runner.submit(send_webhook, profile.discord_webhook, "Server offline", f"**{profile.name}** was stopped.")

    def restart_selected(self) -> None:
        profile = self._selected()
        if not profile: return
        self.run_task("Restarting server", self.server_service.restart, profile, on_success=lambda pid: self.runner.submit(self.store.save, self.profiles), refresh="Dashboard")

    # Servers -------------------------------------------------------------------
    def _build_servers(self, page: tk.Frame) -> None:
        header = self._header(page, "Servers", "Create and manage multiple isolated Campaign, Creative, Custom, or imported server instances.")
        self._button(header, "Create server", self.open_server_editor, accent=True).pack(side="right", anchor="n")
        controls = tk.Frame(page, bg=BG); controls.pack(fill="x", padx=24, pady=(0, 10))
        self._button(controls, "Edit selected", lambda: self.open_server_editor(self._selected())).pack(side="left", padx=(0, 8))
        self._button(controls, "Duplicate", self.duplicate_server).pack(side="left", padx=8)
        self._button(controls, "Remove profile", self.remove_server, danger=True).pack(side="left", padx=8)
        self._button(controls, "Install / Update via SteamCMD", self.install_update_server).pack(side="right")
        self.server_tree = ttk.Treeview(page, columns=("type", "world", "port", "query", "status", "path"), show="headings")
        for col, title, width in (("type", "Server type", 145), ("world", "Public search name", 160), ("port", "Game UDP", 75), ("query", "Query UDP", 75), ("status", "Status", 90), ("path", "Installation", 470)):
            self.server_tree.heading(col, text=title); self.server_tree.column(col, width=width, anchor="w")
        self.server_tree.pack(fill="both", expand=True, padx=24, pady=(0, 24))
        self.server_tree.bind("<<TreeviewSelect>>", self._server_tree_selected)

    def _refresh_servers(self) -> None:
        if not hasattr(self, "server_tree"): return
        self.server_tree.delete(*self.server_tree.get_children())
        for profile in self.profiles:
            self.server_tree.insert("", "end", iid=profile.id, values=(profile.server_type, "Checking .sav…", profile.port, profile.query_port, "Checking…", profile.install_dir))
            self.runner.submit(self.server_service.is_running, profile, on_success=lambda running, pid=profile.id: self._server_status_row(pid, running))
            self.runner.submit(public_search_name, profile, on_success=lambda name, pid=profile.id: self._server_public_name_row(pid, name))

    def _server_status_row(self, profile_id: str, running: bool) -> None:
        if hasattr(self, "server_tree") and self.server_tree.exists(profile_id):
            values = list(self.server_tree.item(profile_id, "values")); values[4] = "Running" if running else "Stopped"
            self.server_tree.item(profile_id, values=values)

    def _server_public_name_row(self, profile_id: str, name: str) -> None:
        if hasattr(self, "server_tree") and self.server_tree.exists(profile_id):
            values = list(self.server_tree.item(profile_id, "values")); values[1] = name or "Not configured"
            self.server_tree.item(profile_id, values=values)

    def _server_tree_selected(self, _event=None) -> None:
        selection = self.server_tree.selection()
        if selection:
            self.selected_id = selection[0]
            self._refresh_profile_selector()

    def open_server_editor(self, profile: ServerProfile | None = None) -> None:
        """Create or edit a real type-specific Dragonwilds server profile."""
        editing = profile is not None
        source = copy.deepcopy(profile) if profile else ServerProfile(
            port=self._next_port(), query_port=self._next_query_port()
        )

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit server" if editing else "Create server")
        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        width = min(820, max(660, screen_w - 80))
        height = min(760, max(520, screen_h - 80))
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(min(680, width), min(520, height))
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.grab_set()

        footer = tk.Frame(dialog, bg=PANEL_2, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(side="bottom", fill="x")
        status_var = tk.StringVar(value="Choose a server type. The form will change to match that world type.")
        status_label = tk.Label(
            footer, textvariable=status_var, anchor="w", justify="left",
            fg=MUTED, bg=PANEL_2, font=("Segoe UI", 9), wraplength=480,
        )
        status_label.pack(side="left", fill="x", expand=True, padx=14, pady=12)
        footer_buttons = tk.Frame(footer, bg=PANEL_2)
        footer_buttons.pack(side="right", padx=14, pady=9)

        body = tk.Frame(dialog, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        form = tk.Frame(canvas, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        form_window = canvas.create_window((20, 20), window=form, anchor="nw")
        form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(form_window, width=max(540, e.width - 42)))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll((-1 if e.delta > 0 else 1) * 3, "units"))
        dialog.bind("<Destroy>", lambda _e: canvas.unbind_all("<MouseWheel>"), add="+")

        tk.Label(
            form, text="Server profile", fg=ACCENT, bg=PANEL,
            font=("Segoe UI Semibold", 16),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(16, 12))

        fields: dict[str, tk.Variable] = {
            "name": tk.StringVar(value=source.name),
            "server_type": tk.StringVar(value=source.server_type),
            "install_dir": tk.StringVar(value=source.install_dir),
            "world_name": tk.StringVar(value=source.world_name),
            "owner_id": tk.StringVar(value=source.owner_id),
            "admin_password": tk.StringVar(value=source.admin_password),
            "world_password": tk.StringVar(value=source.world_password),
            "max_players": tk.StringVar(value=str(source.max_players)),
            "port": tk.StringVar(value=str(source.port)),
            "query_port": tk.StringVar(value=str(source.query_port)),
            "launch_args": tk.StringVar(value=source.launch_args),
            "backup_dir": tk.StringVar(value=source.backup_dir),
            "world_source": tk.StringVar(value=""),
        }

        def row(label, key, r, secret=False, combo=None, browse_dir=False, browse_file=False):
            label_widget = tk.Label(form, text=label, fg=MUTED, bg=PANEL, font=("Segoe UI", 9))
            label_widget.grid(row=r, column=0, sticky="w", padx=18, pady=7)
            if combo:
                widget = ttk.Combobox(form, textvariable=fields[key], values=combo, state="readonly")
            else:
                widget = tk.Entry(
                    form, textvariable=fields[key], show="•" if secret else "",
                    bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat",
                    font=("Segoe UI", 10),
                )
            widget.grid(row=r, column=1, sticky="ew", padx=8, pady=7, ipady=5)
            browse_button = None
            if browse_dir:
                browse_button = self._button(
                    form, "Browse",
                    lambda: fields[key].set(filedialog.askdirectory(parent=dialog) or fields[key].get()),
                )
                browse_button.grid(row=r, column=2, padx=(4, 18))
            elif browse_file:
                browse_button = self._button(
                    form, "Choose .sav",
                    lambda: fields[key].set(
                        filedialog.askopenfilename(
                            parent=dialog,
                            title="Choose Dragonwilds world save",
                            filetypes=[("Dragonwilds world", "*.sav"), ("All files", "*.*")],
                        ) or fields[key].get()
                    ),
                )
                browse_button.grid(row=r, column=2, padx=(4, 18))
            return label_widget, widget, browse_button

        first_entry = row("Profile / server name", "name", 1)[1]
        _, type_combo, _ = row("Server / world type", "server_type", 2, combo=WORLD_TYPES)
        row("Installation folder", "install_dir", 3, browse_dir=True)
        world_label, world_entry, _ = row("New Campaign world name", "world_name", 4)
        row("Owner player ID", "owner_id", 5)
        row("Admin password", "admin_password", 6, secret=True)
        row("World password", "world_password", 7, secret=True)
        row("Maximum players (1–6)", "max_players", 8)
        row("Game UDP port", "port", 9)
        row("Query UDP port", "query_port", 10)
        row("Launch arguments", "launch_args", 11)
        row("Backup folder (optional)", "backup_dir", 12, browse_dir=True)
        source_label, source_entry, source_button = row("Source world save", "world_source", 13, browse_file=True)
        form.columnconfigure(1, weight=1)

        type_note_var = tk.StringVar()
        type_note = tk.Label(
            form, textvariable=type_note_var, wraplength=700, justify="left",
            fg=TEXT, bg=PANEL_2, font=("Segoe UI", 9), padx=14, pady=12,
        )
        type_note.grid(row=14, column=0, columnspan=3, sticky="ew", padx=18, pady=(10, 18))

        type_defaults = {
            "Campaign / Standard": "World",
            "Creative": "CreativeWorld",
            "Custom": "CustomWorld",
            "Imported / Other": "ImportedWorld",
        }
        previous_type = {"value": source.server_type}

        def apply_type_mode(_event=None):
            selected = fields["server_type"].get() or WORLD_TYPES[0]
            old_type = previous_type["value"]
            current_world = fields["world_name"].get().strip()
            if not editing and (not current_world or current_world == type_defaults.get(old_type, "World")):
                fields["world_name"].set(type_defaults[selected])
            previous_type["value"] = selected

            requires_save = world_type_requires_save(selected)
            if requires_save:
                source_label.grid()
                source_entry.grid()
                if source_button:
                    source_button.grid()
                world_label.configure(text="Public world name (renames imported .sav)")
            else:
                source_label.grid_remove()
                source_entry.grid_remove()
                if source_button:
                    source_button.grid_remove()
                world_label.configure(text="New Campaign / Standard world name")

            if selected == "Campaign / Standard":
                explanation = (
                    "CAMPAIGN / STANDARD: The manager creates an empty save folder. Dragonwilds creates a new "
                    "standard world on first launch. No imported .sav file is used."
                )
            elif selected == "Creative":
                explanation = (
                    "CREATIVE: Creative rules are stored inside the save. Create the Creative world in the game, "
                    "then choose that .sav file here. The manager copies it and gives it the public name above."
                )
            elif selected == "Custom":
                explanation = (
                    "CUSTOM: Custom difficulty/world rules are stored inside the save. Create the Custom world in "
                    "the game, then choose its .sav file. The manager does not pretend those binary settings are INI fields."
                )
            else:
                explanation = (
                    "IMPORTED / OTHER: Choose an existing Dragonwilds .sav file. The manager copies it into this "
                    "server and uses the public world name above."
                )
            type_note_var.set(explanation)
            status_var.set(explanation)
            status_label.configure(fg=MUTED)

        type_combo.bind("<<ComboboxSelected>>", apply_type_mode)
        apply_type_mode()

        saving = {"active": False}

        def close_dialog():
            if not saving["active"]:
                dialog.destroy()

        cancel_button = self._button(footer_buttons, "Cancel", close_dialog)
        cancel_button.pack(side="right", padx=(8, 0))

        def collect_and_validate() -> str:
            source.name = fields["name"].get().strip() or "Dragonwilds Server"
            source.server_type = fields["server_type"].get()
            source.install_dir = fields["install_dir"].get().strip()
            source.world_name = validate_world_name(fields["world_name"].get())
            source.owner_id = fields["owner_id"].get().strip()
            source.admin_password = fields["admin_password"].get()
            source.world_password = fields["world_password"].get()
            source.max_players = int(fields["max_players"].get())
            source.port = int(fields["port"].get())
            source.query_port = int(fields["query_port"].get())
            source.launch_args = fields["launch_args"].get().strip()
            source.backup_dir = fields["backup_dir"].get().strip()
            initial_world_source = fields["world_source"].get().strip()
            if not (1 <= source.max_players <= 6):
                raise ValueError("Maximum players must be between 1 and 6.")
            if not (1 <= source.port <= 65535):
                raise ValueError("Game port must be between 1 and 65535.")
            if not (1 <= source.query_port <= 65535):
                raise ValueError("Query port must be between 1 and 65535.")
            if source.port == source.query_port:
                raise ValueError("Game and query ports must be different.")
            if not source.install_dir:
                raise ValueError("Choose an installation folder.")
            if world_type_requires_save(source.server_type) and initial_world_source:
                selected_save = Path(initial_world_source)
                if selected_save.suffix.casefold() != ".sav" or not selected_save.is_file():
                    raise ValueError(f"Choose a valid .sav file for {source.server_type}.")
            return initial_world_source

        def save_success(saved_profile):
            saving["active"] = False
            if editing:
                self._after_profile_saved(dialog, saved_profile)
            else:
                self._after_profile_created(dialog, saved_profile)
            self.set_status("Server profile saved", False)

        def save_failed(exc):
            saving["active"] = False
            save_button.configure(state="normal")
            cancel_button.configure(state="normal")
            status_var.set(f"Could not save: {exc}")
            status_label.configure(fg=RED)
            self.set_status(f"Server profile save failed: {exc}", False)
            self.logger.exception("Server profile save failed", exc_info=(type(exc), exc, exc.__traceback__))
            messagebox.showerror("Server profile", str(exc), parent=dialog)
            dialog.deiconify()
            dialog.lift()

        def save():
            if saving["active"]:
                return
            try:
                initial_world_source = collect_and_validate()
            except Exception as exc:
                status_var.set(str(exc))
                status_label.configure(fg=RED)
                messagebox.showerror("Server profile", str(exc), parent=dialog)
                return

            saving["active"] = True
            save_button.configure(state="disabled")
            cancel_button.configure(state="disabled")
            status_label.configure(fg=ACCENT)
            status_var.set("Creating the type-specific server profile…")
            self.set_status("Saving server profile", True)

            transaction = update_profile_transaction if editing else create_profile_transaction
            self.runner.submit(
                transaction, self.store, self.profiles, source, initial_world_source or None,
                on_success=save_success, on_error=save_failed,
            )

        save_button = self._button(
            footer_buttons, "Save Changes" if editing else "Create & Save Server", save, accent=True
        )
        save_button.pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", lambda _e: close_dialog())
        dialog.bind("<Control-s>", lambda _e: save())
        dialog.bind("<Control-S>", lambda _e: save())
        dialog.after(50, lambda: first_entry.focus_set())

    def _after_profile_created(self, dialog, profile):
        self.profiles.append(profile); self.selected_id = profile.id; dialog.destroy(); self._refresh_profile_selector(); self._refresh_page("Servers")
    def _after_profile_saved(self, dialog, profile):
        self.profiles = [profile if p.id == profile.id else p for p in self.profiles]
        self.selected_id = profile.id; dialog.destroy(); self._refresh_profile_selector(); self._refresh_page("Servers")
    def _next_port(self) -> int:
        used = {p.port for p in self.profiles}; port = 7777
        while port in used: port += 1
        return port
    def _next_query_port(self) -> int:
        used = {p.query_port for p in self.profiles}; port = 27015
        while port in used: port += 1
        return port

    def duplicate_server(self) -> None:
        profile = self._selected()
        if not profile: return
        clone = copy.deepcopy(profile); clone.id = ServerProfile().id; clone.name += " Copy"; clone.port = self._next_port(); clone.query_port = self._next_query_port(); clone.process_id = None
        base = Path(profile.install_dir); clone.install_dir = str(base.with_name(base.name + f"-{clone.port}"))
        initial_world = None
        if world_type_requires_save(clone.server_type):
            worlds = list_worlds(profile.save_dir)
            if not worlds:
                messagebox.showerror("Duplicate server", f"{clone.server_type} requires an existing .sav file, but this server has no world to copy.", parent=self.root)
                return
            initial_world = str(worlds[0])
        self.run_task("Duplicating server profile", create_profile_transaction, self.store, self.profiles, clone, initial_world, on_success=lambda p: self._after_duplicate(p), refresh="Servers")
    def _after_duplicate(self, profile): self.profiles.append(profile); self.selected_id = profile.id; self._refresh_profile_selector()

    def remove_server(self) -> None:
        profile = self._selected()
        if not profile: return
        if not messagebox.askyesno("Remove profile", "Remove this profile from the manager? Server files will not be deleted.", parent=self.root): return
        self.profiles = [p for p in self.profiles if p.id != profile.id]; self.selected_id = self.profiles[0].id if self.profiles else None
        self.run_task("Removing profile", self.store.save, self.profiles, on_success=lambda _: self._refresh_profile_selector(), refresh="Servers")

    def install_update_server(self) -> None:
        profile = self._selected()
        if not profile: return
        def install_or_update():
            if self.server_service.is_running(profile):
                raise RuntimeError("Stop this server before installing, updating, or validating it.")
            steamcmd = self.server_service.ensure_steamcmd(self.paths["tools"])
            return self.server_service.run_steamcmd(steamcmd, profile.root, True)
        self.run_task("Installing/updating Dragonwilds server", install_or_update, on_success=lambda result: self._steamcmd_done(result), refresh="Servers")
    def _steamcmd_done(self, result):
        if result.returncode != 0:
            messagebox.showerror("SteamCMD", result.stderr[-1000:] or result.stdout[-1000:] or "SteamCMD failed.", parent=self.root)
            return
        messagebox.showinfo("SteamCMD", "Server installation/update completed.", parent=self.root)

    # Configuration -------------------------------------------------------------
    def _build_configuration(self, page: tk.Frame) -> None:
        header = self._header(page, "Configuration", "Every supported text config is discoverable; INI files preserve comments, unknown keys, and future settings.")
        self._button(header, "Refresh files", self.refresh_configs, accent=True).pack(side="right")
        self._button(header, "Open config file", self.open_config_file).pack(side="right", padx=8)
        split = tk.PanedWindow(page, orient="horizontal", bg=BG, sashwidth=6, sashrelief="flat")
        split.pack(fill="both", expand=True, padx=24, pady=(0, 24))
        left = tk.Frame(split, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); right = tk.Frame(split, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        split.add(left, minsize=280); split.add(right, minsize=600)
        self.config_list = tk.Listbox(left, bg=PANEL, fg=TEXT, selectbackground="#314738", selectforeground=TEXT, bd=0, font=("Consolas", 9))
        self.config_list.pack(fill="both", expand=True, padx=10, pady=10); self.config_list.bind("<<ListboxSelect>>", self._config_selected)
        toolbar = tk.Frame(right, bg=PANEL); toolbar.pack(fill="x", padx=10, pady=10)
        self.config_title = tk.Label(toolbar, text="Select a configuration file", fg=ACCENT, bg=PANEL, font=("Segoe UI Semibold", 11)); self.config_title.pack(side="left")
        self._button(toolbar, "Save exact text", self.save_current_config, accent=True).pack(side="right")
        self.config_text = tk.Text(right, bg="#0f1512", fg=TEXT, insertbackground=TEXT, undo=True, wrap="none", font=("Consolas", 10), relief="flat")
        self.config_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        bottom = tk.Frame(right, bg=PANEL); bottom.pack(fill="x", padx=10, pady=(0, 10))
        self._button(bottom, "Edit selected INI value", self.edit_selected_ini).pack(side="right")
        self.config_tree = ttk.Treeview(bottom, columns=("section", "key", "value"), show="headings", height=7)
        for col, title, width in (("section","Section",140),("key","Key",190),("value","Value",340)):
            self.config_tree.heading(col,text=title); self.config_tree.column(col,width=width,anchor="w")
        self.config_tree.pack(fill="x", expand=True)

    def open_config_file(self) -> None:
        profile = self._selected()
        if not profile: return
        selected = filedialog.askopenfilename(
            title="Open a server or mod configuration file",
            initialdir=str(profile.root),
            filetypes=[("Configuration files", "*.ini *.cfg *.conf *.json *.yaml *.yml *.toml *.txt *.xml *.properties *.env *.lua"), ("All files", "*.*")],
            parent=self.root,
        )
        if not selected: return
        path = Path(selected).resolve()
        try:
            path.relative_to(profile.root.resolve())
        except ValueError:
            messagebox.showerror("Configuration", "Choose a file inside the selected server folder.", parent=self.root); return
        if path.suffix.lower() not in {".ini", ".cfg", ".conf", ".json", ".yaml", ".yml", ".toml", ".txt", ".xml", ".properties", ".env", ".lua"}:
            messagebox.showerror("Configuration", "That file type is not supported by the text editor.", parent=self.root); return
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Configuration files larger than 4 MB are blocked to keep the UI responsive.")
        except OSError as exc:
            messagebox.showerror("Configuration", str(exc), parent=self.root); return
        if path not in self.config_paths:
            self.config_paths.append(path); self.config_list.insert("end", str(path.relative_to(profile.root)))
        index = self.config_paths.index(path); self.config_list.selection_clear(0, "end"); self.config_list.selection_set(index); self.config_list.see(index); self._config_selected()

    def refresh_configs(self) -> None:
        profile = self._selected()
        if not profile: return
        self.run_task("Scanning configuration files", discover_configs, profile.root, on_success=self._configs_loaded)
    def _configs_loaded(self, paths):
        self.config_paths = paths; self.config_list.delete(0,"end")
        profile = self._selected(); root = profile.root if profile else Path(".")
        for path in paths:
            try: label = path.relative_to(root).as_posix()
            except ValueError: label = str(path)
            self.config_list.insert("end", label)
    def _config_selected(self, _event=None):
        sel = self.config_list.curselection()
        if not sel: return
        path = self.config_paths[sel[0]]; self.selected_config = path
        def load_config():
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            entries = IniDocument(text).entries() if path.suffix.lower() == ".ini" else []
            return text, entries
        self.run_task("Loading configuration", load_config, on_success=lambda result: self._config_text_loaded(path, result[0], result[1]))
    def _config_text_loaded(self,path,text,entries=None):
        self.config_title.configure(text=str(path.name)); self.config_text.delete("1.0","end"); self.config_text.insert("1.0",text)
        self.config_tree.delete(*self.config_tree.get_children())
        if entries is None and path.suffix.lower()==".ini":
            entries = IniDocument(text).entries()
        for entry in entries or []:
            self.config_tree.insert("","end",values=(entry.section,entry.key,entry.value))
    def save_current_config(self):
        if not self.selected_config: return
        profile = self._selected(); path = self.selected_config
        text = self.config_text.get("1.0","end-1c")
        def save_checked():
            if profile and self.server_service.is_running(profile):
                raise RuntimeError("Stop the server before saving configuration changes.")
            backup = save_text_atomic(path, text)
            entries = IniDocument(text).entries() if path.suffix.lower() == ".ini" else []
            return backup, entries
        self.run_task("Saving configuration", save_checked, on_success=lambda result: self._config_text_loaded(path, text, result[1]))
    def edit_selected_ini(self):
        selection=self.config_tree.selection()
        if not selection or not self.selected_config or self.selected_config.suffix.lower()!=".ini": return
        section,key,value=self.config_tree.item(selection[0],"values")
        new=simpledialog.askstring("Edit INI value",f"[{section}] {key}",initialvalue=value,parent=self.root)
        if new is None:return
        doc=IniDocument(self.config_text.get("1.0","end-1c")); doc.set(section,key,new); self._config_text_loaded(self.selected_config,doc.render())

    # Worlds --------------------------------------------------------------------
    def _build_worlds(self,page):
        header=self._header(page,"Worlds","Import, clone, export, and delete .sav worlds with mandatory safety backups.")
        self._button(header,"Import world",self.import_world_ui,accent=True).pack(side="right")
        row=tk.Frame(page,bg=BG);row.pack(fill="x",padx=24,pady=(0,10))
        self._button(row,"Refresh",self.refresh_worlds).pack(side="left"); self._button(row,"Rename for public list",self.rename_world_ui).pack(side="left",padx=8); self._button(row,"Clone",self.clone_world_ui).pack(side="left",padx=8); self._button(row,"Export",self.export_world_ui).pack(side="left",padx=8); self._button(row,"Delete",self.delete_world_ui,danger=True).pack(side="left",padx=8)
        self.world_tree=ttk.Treeview(page,columns=("name","size","modified","path"),show="headings")
        for col,title,width in (("name","World",200),("size","Size",90),("modified","Modified",180),("path","Path",620)):
            self.world_tree.heading(col,text=title);self.world_tree.column(col,width=width,anchor="w")
        self.world_tree.pack(fill="both",expand=True,padx=24,pady=(0,24))
    def refresh_worlds(self):
        profile=self._selected()
        if not profile:return
        def scan():
            rows=[]
            for path in list_worlds(profile.save_dir):
                stat=path.stat(); rows.append((path,path.name,stat.st_size,stat.st_mtime))
            return rows
        self.run_task("Scanning worlds",scan,on_success=self._worlds_loaded)
    def _worlds_loaded(self,worlds):
        self.world_tree.delete(*self.world_tree.get_children())
        for p,name,size,modified in worlds:
            self.world_tree.insert("","end",iid=str(p),values=(name,f"{size/1024:.1f} KB",time.strftime("%Y-%m-%d %H:%M",time.localtime(modified)),str(p)))
    def _selected_world(self):
        sel=self.world_tree.selection();return Path(sel[0]) if sel else None
    def _backup_dir(self,profile):return Path(profile.backup_dir) if profile.backup_dir else profile.root/"ManagerBackups"
    def import_world_ui(self):
        profile=self._selected()
        if not profile:return
        source=filedialog.askopenfilename(filetypes=[("Dragonwilds worlds","*.sav")],parent=self.root)
        if not source:return
        initial=Path(source).stem[:16]
        name=simpledialog.askstring(
            "Public world name",
            "Enter the exact name players will search in the Public tab (maximum 16 characters, case-sensitive):",
            initialvalue=initial,parent=self.root,
        )
        if name is None:return
        try:name=validate_world_name(name)
        except Exception as exc:
            messagebox.showerror("World import",str(exc),parent=self.root);return
        self.run_stopped_task(
            "Importing world",profile,import_world,profile.root,profile.save_dir,Path(source),self._backup_dir(profile),name+".sav",
            on_success=lambda p:self._notify_backup(profile,"World imported",f"{p.name}\n\nSearch the Public tab for: {p.stem}"),refresh="Worlds"
        )

    def rename_world_ui(self):
        profile=self._selected(); world=self._selected_world()
        if not profile or not world:return
        name=simpledialog.askstring(
            "Rename public world",
            "Enter the exact case-sensitive name players will search (maximum 16 characters):",
            initialvalue=world.stem,parent=self.root,
        )
        if name is None:return
        try:name=validate_world_name(name)
        except Exception as exc:
            messagebox.showerror("Rename world",str(exc),parent=self.root);return
        self.run_stopped_task(
            "Renaming public world",profile,rename_world,profile.root,world,self._backup_dir(profile),name,
            on_success=lambda p:messagebox.showinfo("World renamed",f"Search the Public tab for this exact name:\n{p.stem}",parent=self.root),
            refresh="Worlds",
        )
    def clone_world_ui(self):
        world=self._selected_world()
        if not world:return
        name=simpledialog.askstring("Clone world","New world filename",initialvalue=world.stem+" Copy",parent=self.root)
        profile=self._selected()
        if name and profile:self.run_stopped_task("Cloning world",profile,clone_world,world,name,refresh="Worlds")
    def export_world_ui(self):
        world=self._selected_world()
        if not world:return
        target=filedialog.asksaveasfilename(initialfile=world.name,defaultextension=".sav",filetypes=[("Dragonwilds worlds","*.sav")],parent=self.root)
        if target:self.run_task("Exporting world",shutil.copy2,world,Path(target))
    def delete_world_ui(self):
        profile=self._selected();world=self._selected_world()
        if not profile or not world:return
        if messagebox.askyesno("Delete world",f"Back up and delete {world.name}?",parent=self.root):self.run_stopped_task("Deleting world",profile,delete_world,profile.root,world,self._backup_dir(profile),refresh="Worlds")

    # Backups -------------------------------------------------------------------
    def _build_backups(self,page):
        header=self._header(page,"Backups","Verified ZIP backups with SHA-256 sidecars and transactional restore.")
        self._button(header,"Create full backup",self.create_selected_backup,accent=True).pack(side="right")
        row=tk.Frame(page,bg=BG);row.pack(fill="x",padx=24,pady=(0,10))
        self._button(row,"Refresh",self.refresh_backups).pack(side="left");self._button(row,"Verify",self.verify_backup_ui).pack(side="left",padx=8);self._button(row,"Restore",self.restore_backup_ui).pack(side="left",padx=8)
        self.backup_tree=ttk.Treeview(page,columns=("name","size","modified","path"),show="headings")
        for col,title,width in (("name","Backup",260),("size","Size",100),("modified","Created",180),("path","Path",560)):
            self.backup_tree.heading(col,text=title);self.backup_tree.column(col,width=width,anchor="w")
        self.backup_tree.pack(fill="both",expand=True,padx=24,pady=(0,24))
    def create_selected_backup(self):
        profile=self._selected()
        if not profile:return
        sources=[p for p in [profile.save_dir,profile.config_dir,profile.root/"RSDragonwilds"/"Content"/"Paks"/"~mods",profile.root/"RSDragonwilds"/"Binaries"/"Win64"/"Mods"] if p.exists()]
        self.run_stopped_task("Creating verified backup",profile,create_backup,profile.root,self._backup_dir(profile),sources,"full",on_success=lambda p:self._notify_backup(profile,"Backup completed",p.name),refresh="Backups")
    def _notify_backup(self,profile,title,message):
        if profile.discord_webhook:self.runner.submit(send_webhook,profile.discord_webhook,title,f"**{profile.name}**: {message}")
    def refresh_backups(self):
        profile=self._selected()
        if not profile:return
        directory=self._backup_dir(profile)
        def scan():
            if not directory.exists(): return []
            rows=[]
            for path in directory.glob("*.zip"):
                stat=path.stat(); rows.append((path,path.name,stat.st_size,stat.st_mtime))
            return sorted(rows,key=lambda row:row[3],reverse=True)
        self.run_task("Scanning backups",scan,on_success=self._backups_loaded)
    def _backups_loaded(self,items):
        self.backup_tree.delete(*self.backup_tree.get_children())
        for p,name,size,modified in items:
            self.backup_tree.insert("","end",iid=str(p),values=(name,f"{size/1024/1024:.2f} MB",time.strftime("%Y-%m-%d %H:%M",time.localtime(modified)),str(p)))
    def _selected_backup(self):
        sel=self.backup_tree.selection();return Path(sel[0]) if sel else None
    def verify_backup_ui(self):
        archive=self._selected_backup()
        if archive:self.run_task("Verifying backup",verify_backup,archive,on_success=lambda r:messagebox.showinfo("Backup verification",r[1],parent=self.root) if r[0] else messagebox.showerror("Backup verification",r[1],parent=self.root))
    def restore_backup_ui(self):
        profile=self._selected();archive=self._selected_backup()
        if not profile or not archive:return
        if messagebox.askyesno("Restore backup","The current server will be safety-backed up first. Continue?",parent=self.root):self.run_stopped_task("Restoring backup",profile,restore_backup,profile.root,archive,self._backup_dir(profile),refresh="Backups")

    # Mods ----------------------------------------------------------------------
    def _build_mods(self,page):
        self._header(page,"Nexus Mods — Submission Candidate","Official Nexus API submission candidate. Personal API keys are accepted only for reviewer/testing use; no website scraping, background polling, or free-download bypass is used.")
        connect=self._card(page,fill="x",padx=24,pady=(0,12))
        tk.Label(connect,text="Nexus API key",fg=MUTED,bg=PANEL).pack(side="left",padx=(14,8),pady=12)
        self.nexus_key_var=tk.StringVar(value="")
        self.nexus_key_entry=tk.Entry(connect,textvariable=self.nexus_key_var,show="•",bg=PANEL_2,fg=TEXT,insertbackground=TEXT,relief="flat",width=42)
        self.nexus_key_entry.pack(side="left",padx=8,pady=10,ipady=5)
        self._button(connect,"Connect",self.connect_nexus,accent=True).pack(side="left",padx=8)
        self._button(connect,"Run review test",self.run_nexus_review_test).pack(side="left",padx=4)
        self._button(connect,"Export review report",self.export_nexus_review_report).pack(side="left",padx=4)
        self.nexus_account_label=tk.Label(connect,text="Loading saved connection…",fg=MUTED,bg=PANEL);self.nexus_account_label.pack(side="left",padx=10)
        self.runner.submit(load_secret,"nexus_api_key",on_success=self._nexus_secret_loaded,on_error=lambda exc:self.nexus_account_label.configure(text="Not connected",fg=MUTED))
        searchbar=tk.Frame(page,bg=BG);searchbar.pack(fill="x",padx=24,pady=(0,10))
        self.mod_search=tk.StringVar();tk.Entry(searchbar,textvariable=self.mod_search,bg=PANEL_2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Segoe UI",10)).pack(side="left",fill="x",expand=True,ipady=6)
        self.mod_sort=tk.StringVar(value="downloads");ttk.Combobox(searchbar,textvariable=self.mod_sort,values=("downloads","endorsements","updatedAt","createdAt","name"),state="readonly",width=16).pack(side="left",padx=8)
        self._button(searchbar,"Search",lambda:self.load_mods(reset=True),accent=True).pack(side="left")
        self._button(searchbar,"Previous",self.prev_mod_page).pack(side="left",padx=(12,4));self._button(searchbar,"Next",self.next_mod_page).pack(side="left",padx=4)
        self.mod_page_label=tk.Label(searchbar,text="0 mods",fg=MUTED,bg=BG);self.mod_page_label.pack(side="right",padx=10)
        container=tk.Frame(page,bg=BG);container.pack(fill="both",expand=True,padx=24,pady=(0,24))
        self.mod_canvas=tk.Canvas(container,bg=BG,highlightthickness=0);scroll=ttk.Scrollbar(container,orient="vertical",command=self.mod_canvas.yview)
        self.mod_cards=tk.Frame(self.mod_canvas,bg=BG);self.mod_cards.bind("<Configure>",lambda e:self.mod_canvas.configure(scrollregion=self.mod_canvas.bbox("all")))
        self.mod_canvas.create_window((0,0),window=self.mod_cards,anchor="nw",tags="cards");self.mod_canvas.configure(yscrollcommand=scroll.set)
        self.mod_canvas.bind("<Configure>",lambda e:self.mod_canvas.itemconfigure("cards",width=e.width))
        self.mod_canvas.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y")

    def _nexus_secret_loaded(self,key):
        self.nexus_key_var.set(key or "")
        self.nexus_account_label.configure(text="Saved key loaded — connect to validate" if key else "Not connected",fg=MUTED)
    def connect_nexus(self):
        key=self.nexus_key_var.get().strip()
        if not key:
            messagebox.showinfo("Nexus Mods","Enter a Nexus API key first.",parent=self.root); return
        def validate_and_store():
            data=NexusClient(key, cache_dir=self.paths["cache"] / "nexus").validate(); save_secret("nexus_api_key",key); return data
        self.run_task("Connecting to Nexus Mods",validate_and_store,on_success=lambda data:self._nexus_connected(data))
    def _nexus_connected(self,data):
        name=data.get("name") or data.get("email") or "Connected account"
        tier = "Premium" if data.get("is_premium") else "Free account"
        self.nexus_account_label.configure(text=f"Connected: {name} • {tier}",fg=GREEN)
        self.load_mods(reset=True)

    def run_nexus_review_test(self):
        key=self.nexus_key_var.get().strip()
        if not key:
            messagebox.showinfo("Nexus review test","Enter and connect a personal Nexus API key first.",parent=self.root);return
        client=NexusClient(key, cache_dir=self.paths["cache"] / "nexus")
        def work():
            result=client.review_probe()
            report=client.export_review_report(self.paths["diagnostics"] / "NEXUS_REVIEW_RUNTIME_REPORT.json")
            return result,report
        def success(payload):
            result,report=payload
            tier="Premium" if result.get("is_premium") else "Free account"
            capability=str(result.get("download_capability") or "not_tested").replace("_"," ")
            limits=result.get("rate_limits") or {}
            quota=f"daily {limits.get('daily_remaining','?')} / hourly {limits.get('hourly_remaining','?')}" if limits else "not supplied by Nexus"
            messagebox.showinfo(
                "Nexus review test passed",
                f"Account validation: passed\nAccount tier: {tier}\nSample mod ID: {result.get('sample_mod_id') or 'none'}\nSample file count: {result.get('sample_file_count',0)}\nDownload capability: {capability}\nAPI remaining: {quota}\n\nRedacted report:\n{report}",
                parent=self.root,
            )
        self.run_task("Running Nexus reviewer API test",work,on_success=success)

    def export_nexus_review_report(self):
        key=self.nexus_key_var.get().strip()
        client=NexusClient(key, cache_dir=self.paths["cache"] / "nexus")
        target=self.paths["diagnostics"] / "NEXUS_REVIEW_RUNTIME_REPORT.json"
        try:
            client.export_review_report(target)
        except Exception as exc:
            messagebox.showerror("Nexus review report",str(exc),parent=self.root);return
        messagebox.showinfo("Nexus review report",f"A redacted Nexus API usage report was written to:\n{target}",parent=self.root)

    def load_mods(self,reset=False):
        if reset:self.mod_offset=0
        key=self.nexus_key_var.get().strip();client=NexusClient(key, cache_dir=self.paths["cache"] / "nexus");profile=self._selected()
        offset=self.mod_offset; count=self.mod_page_size; search=self.mod_search.get(); sort=self.mod_sort.get()
        self.mod_page_label.configure(text="Loading mods…")
        def load_page():
            records,total=client.catalog_page(offset,count,search,sort)
            manifests=load_manifests(profile.root) if profile else {}
            return records,total,manifests,client.last_catalog_source,client.last_catalog_note,client.rate_limit_snapshot()
        self.run_task("Loading Nexus mod catalog",load_page,on_success=self._mods_loaded,on_error=self._mods_failed)
    def _mods_failed(self, exc):
        self.mod_total = 0
        self.mod_records = []
        self.mod_page_label.configure(text="Catalog error")
        self.nexus_account_label.configure(text="Connected account — catalog unavailable", fg=RED)
        self._mod_render_generation += 1
        for child in self.mod_cards.winfo_children():
            child.destroy()
        card = self._card(self.mod_cards, fill="x", padx=2, pady=8)
        tk.Label(card, text="Nexus connected, but its mod catalog request failed", fg=RED, bg=PANEL, font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=16, pady=(16, 6))
        error_text = str(exc)
        tk.Label(card, text=error_text, fg=TEXT, bg=PANEL, wraplength=940, justify="left", anchor="w").pack(fill="x", padx=16, pady=(0, 12))
        tk.Label(card, text="Your API key validated successfully. Retry the catalog, open the official Dragonwilds Nexus page, or copy this exact error for diagnostics.", fg=MUTED, bg=PANEL, wraplength=940, justify="left", anchor="w").pack(fill="x", padx=16, pady=(0, 12))
        buttons = tk.Frame(card, bg=PANEL); buttons.pack(anchor="w", padx=16, pady=(0, 16))
        self._button(buttons, "Retry catalog", lambda: self.load_mods(reset=True), accent=True).pack(side="left")
        self._button(buttons, "Open Nexus Dragonwilds", lambda: webbrowser.open(NEXUS_GAME_URL)).pack(side="left", padx=8)
        def copy_error():
            self.root.clipboard_clear(); self.root.clipboard_append(error_text); self.root.update_idletasks()
        self._button(buttons, "Copy exact error", copy_error).pack(side="left")

    def _mods_loaded(self,result):
        # Accept the legacy 3-item result used by older UI tests/plugins while
        # preferring the new source-aware 5-item catalog result.
        if len(result) == 3:
            records, total, manifests = result
            source, note, rate_limits = "catalog", "", {}
        elif len(result) == 5:
            records, total, manifests, source, note = result
            rate_limits = {}
        else:
            records, total, manifests, source, note, rate_limits = result[:6]
        self.mod_records=records;self.mod_total=total;self.installed_manifests=manifests
        range_text=f"{self.mod_offset+1 if total else 0}-{min(self.mod_offset+len(records),total)} of {total}"
        source_text=f" • {source}" if source else ""
        self.mod_page_label.configure(text=range_text+source_text)
        if source:
            detail=f" ({note})" if note else ""
            quota = ""
            if isinstance(rate_limits, dict):
                daily = rate_limits.get("daily_remaining")
                hourly = rate_limits.get("hourly_remaining")
                if daily not in (None, "") or hourly not in (None, ""):
                    quota = f" • API remaining: daily {daily if daily not in (None, '') else '?'} / hourly {hourly if hourly not in (None, '') else '?'}"
            self.nexus_account_label.configure(text=f"Connected • catalog: {source}{detail}{quota}",fg=GREEN)
        self._mod_render_generation += 1
        generation = self._mod_render_generation
        for child in self.mod_cards.winfo_children():child.destroy()
        self.mod_photo_refs.clear()
        if not records:
            card=self._card(self.mod_cards,fill="x",padx=2,pady=8)
            tk.Label(card,text="No Dragonwilds mods were returned for this page.",fg=TEXT,bg=PANEL,font=("Segoe UI Semibold",12)).pack(anchor="w",padx=16,pady=(16,5))
            tk.Label(card,text="Try clearing the search, returning to the first page, or opening the Nexus game page. The status bar will show any API error instead of leaving this area blank.",fg=MUTED,bg=PANEL,wraplength=900,justify="left").pack(anchor="w",padx=16,pady=(0,10))
            self._button(card,"Open Dragonwilds on Nexus",lambda:webbrowser.open(NEXUS_GAME_URL)).pack(anchor="w",padx=16,pady=(0,16))
            return
        self._render_mod_batch(records, 0, generation)
    def _render_mod_batch(self, records, index, generation):
        # Ignore callbacks from older catalog searches/pages so obsolete card
        # batches cannot pile up and consume the Tk message thread.
        if generation != self._mod_render_generation or index >= len(records):
            return
        for record in records[index:index+4]:
            self._create_mod_card(record)
        self.root.after(10, lambda: self._render_mod_batch(records, index + 4, generation))
    def _create_mod_card(self,record:ModRecord):
        card=self._card(self.mod_cards,fill="x",padx=2,pady=5)
        icon=tk.Label(card,text="…",width=10,height=5,bg=PANEL_2,fg=MUTED,font=("Segoe UI",10));icon.pack(side="left",padx=12,pady=12)
        info=tk.Frame(card,bg=PANEL);info.pack(side="left",fill="both",expand=True,pady=10)
        tk.Label(info,text=record.name,fg=TEXT,bg=PANEL,font=("Segoe UI Semibold",12),anchor="w").pack(fill="x")
        tk.Label(info,text=f"{record.author} • {record.category} • v{record.version} • {record.downloads:,} downloads",fg=ACCENT,bg=PANEL,font=("Segoe UI",8),anchor="w").pack(fill="x",pady=(2,4))
        tk.Label(info,text=record.summary,wraplength=720,justify="left",fg=MUTED,bg=PANEL,font=("Segoe UI",9),anchor="w").pack(fill="x")
        buttons=tk.Frame(card,bg=PANEL);buttons.pack(side="right",padx=12)
        self._button(buttons,"Install",lambda r=record:self.install_nexus_mod(r),accent=True).pack(pady=(10,4))
        if str(record.mod_id) in self.installed_manifests:self._button(buttons,"Uninstall",lambda r=record:self.uninstall_nexus_mod(r),danger=True).pack(pady=4)
        if record.picture_url:
            self.runner.submit(self.image_service.fetch_png,record.picture_url,on_success=lambda path,r=record,w=icon:self._set_mod_icon(r,w,path))
    def _set_mod_icon(self,record,widget,path):
        if not path or not widget.winfo_exists():return
        try:
            photo=tk.PhotoImage(file=str(path));scale=max(1,max(photo.width()//72,photo.height()//72));photo=photo.subsample(scale,scale);self.mod_photo_refs[record.mod_id]=photo;widget.configure(image=photo,text="",width=84,height=72)
        except tk.TclError:widget.configure(text="No image")
    def next_mod_page(self):
        if self.mod_offset+self.mod_page_size<self.mod_total:self.mod_offset+=self.mod_page_size;self.load_mods()
    def prev_mod_page(self):
        if self.mod_offset>0:self.mod_offset=max(0,self.mod_offset-self.mod_page_size);self.load_mods()
    def _nexus_file_id(self, item: dict) -> int:
        return int(item.get("fileId") or item.get("file_id") or item.get("id") or 0)

    def _safe_download_name(self, chosen: dict, mod_id: int) -> str:
        name=str(chosen.get("file_name") or chosen.get("name") or f"mod-{mod_id}.zip")
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)

    def install_nexus_mod(self,record):
        profile=self._selected()
        if not profile:
            messagebox.showinfo("Mods","Select a server first.",parent=self.root);return
        client=NexusClient(self.nexus_key_var.get().strip(), cache_dir=self.paths["cache"] / "nexus")
        def work():
            if self.server_service.is_running(profile):
                raise RuntimeError("Stop this server before installing or updating mods.")
            files=client.mod_files(record.mod_id)
            chosen=choose_install_file(files)
            file_id=self._nexus_file_id(chosen)
            try:
                links=client.download_links(record.mod_id,file_id)
            except NexusRegistrationRequired as exc:
                return {"mode":"approval_required","message":str(exc),"chosen":chosen}
            if not links:
                raise NexusError("Nexus did not provide a download link.")
            url=links[0].get("URI") or links[0].get("uri")
            if not url:
                raise NexusError("Nexus download link was empty.")
            archive=self.paths["downloads"]/self._safe_download_name(chosen,record.mod_id)
            client.download(url,archive)
            installed=install_mod(profile.root,self._backup_dir(profile),archive,record.mod_id,record.name,str(chosen.get("version") or record.version))
            return {"mode":"installed","result":installed}
        self.run_task(
            f"Installing {record.name}",work,
            on_success=lambda result:self._nexus_install_ready(profile,record,result),
            refresh="Mods",
        )

    def _nexus_install_ready(self, profile, record, result):
        if isinstance(result, dict) and result.get("mode") == "approval_required":
            messagebox.showinfo(
                "Nexus authorization pending",
                result.get("message") or (
                    "This test build intentionally stops before any unsupported free-account direct download. "
                    "The included Nexus registration request asks Nexus Mods for an approved in-manager authorization/download mechanism."
                ),
                parent=self.root,
            )
            return
        self._notify_backup(profile,"Mod installed",record.name)

    def uninstall_nexus_mod(self,record):
        profile=self._selected()
        if not profile:return
        if messagebox.askyesno("Uninstall mod",f"Back up and uninstall {record.name}?",parent=self.root):self.run_stopped_task(f"Uninstalling {record.name}",profile,uninstall_mod,profile.root,self._backup_dir(profile),record.mod_id,refresh="Mods")

    # Discord -------------------------------------------------------------------
    def _build_discord(self,page):
        self._header(page,"Discord","Webhook notifications for server starts, stops, backups, world imports, and mod installations.")
        card=self._card(page,fill="x",padx=24,pady=(0,14));tk.Label(card,text="Discord webhook URL",fg=MUTED,bg=PANEL).pack(anchor="w",padx=18,pady=(18,6))
        self.discord_var=tk.StringVar();tk.Entry(card,textvariable=self.discord_var,show="•",bg=PANEL_2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Segoe UI",10)).pack(fill="x",padx=18,ipady=7)
        row=tk.Frame(card,bg=PANEL);row.pack(fill="x",padx=18,pady=18)
        self._button(row,"Save webhook",self.save_discord,accent=True).pack(side="left");self._button(row,"Send test",self.test_discord).pack(side="left",padx=8)
        tk.Label(page,text="Secrets are protected with Windows DPAPI on Windows and are never included in exported profiles.",fg=MUTED,bg=BG,font=("Segoe UI",9)).pack(anchor="w",padx=24)
    def save_discord(self):
        profile=self._selected()
        if not profile:return
        profile.discord_webhook=self.discord_var.get().strip();self.run_task("Saving Discord settings",self.store.save,self.profiles)
    def test_discord(self):
        url=self.discord_var.get().strip()
        if url:self.run_task("Sending Discord test",send_webhook,url,"Dragonwilds manager connected","Discord notifications are working.")

    # Logs/settings --------------------------------------------------------------
    def _build_logs(self,page):
        header=self._header(page,"Logs","Manager and selected-server logs are loaded on demand, never during startup.")
        self._button(header,"Refresh logs",self.refresh_logs,accent=True).pack(side="right")
        self.logs_text=tk.Text(page,bg=PANEL,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Consolas",9),wrap="none");self.logs_text.pack(fill="both",expand=True,padx=24,pady=(0,24))
    def refresh_logs(self):
        profile=self._selected();paths=[self.paths["logs"]/"manager.log"]
        if profile:paths.append(profile.root/"ManagerLogs"/"server-console.log")
        def read():
            parts=[]
            for p in paths:
                if p.exists():parts.append(f"===== {p} =====\n"+p.read_text(encoding="utf-8",errors="replace")[-250000:])
            return "\n\n".join(parts) or "No logs yet."
        self.run_task("Loading logs",read,on_success=lambda text:self._set_logs(text))
    def _set_logs(self,text):self.logs_text.delete("1.0","end");self.logs_text.insert("1.0",text)

    def _build_settings(self,page):
        self._header(page,"Settings","Local data, diagnostics, and safe reset controls.")
        card=self._card(page,fill="x",padx=24,pady=(0,12));tk.Label(card,text=f"Data directory\n{self.paths['root']}",justify="left",fg=TEXT,bg=PANEL,font=("Consolas",10)).pack(anchor="w",padx=18,pady=18)
        row=tk.Frame(page,bg=BG);row.pack(fill="x",padx=24)
        self._button(row,"Open data folder",lambda:self._open_path(self.paths["root"])).pack(side="left")
        self._button(row,"Export safe profiles",self.export_profiles).pack(side="left",padx=8)
        self._button(row,"Export Nexus review report",self.export_nexus_review_report).pack(side="left",padx=8)
    def _open_path(self,path):
        if os.name=="nt":os.startfile(path)
        elif sys.platform=="darwin":__import__("subprocess").Popen(["open",str(path)])
        else:__import__("subprocess").Popen(["xdg-open",str(path)])
    def export_profiles(self):
        target=filedialog.asksaveasfilename(defaultextension=".json",filetypes=[("JSON","*.json")],parent=self.root)
        if target:
            payload=json.dumps([p.to_dict(include_secrets=False) for p in self.profiles],indent=2)
            self.run_task("Exporting safe profiles",Path(target).write_text,payload,encoding="utf-8")

    def _refresh_page(self,name):
        if name=="Dashboard":self._refresh_dashboard()
        elif name=="Servers":self._refresh_servers()
        elif name=="Configuration":self.refresh_configs()
        elif name=="Worlds":self.refresh_worlds()
        elif name=="Backups":self.refresh_backups()
        elif name=="Discord" and hasattr(self,"discord_var"):
            profile=self._selected();self.discord_var.set(profile.discord_webhook if profile else "")
        elif name=="Mods" and hasattr(self,"mod_cards") and self.nexus_key_var.get().strip():self.load_mods()
        elif name=="Logs":self.refresh_logs()

    def close(self):
        if self._closing:return
        self._closing=True;self.set_status("Saving manager state…",True)
        snapshot=list(self.profiles)
        def finish(_=None):
            self.runner.close();self.root.destroy()
        self.runner.submit(self.store.save,snapshot,on_success=finish,on_error=lambda exc:finish())
        self.root.after(1500,lambda:finish() if self.root.winfo_exists() else None)
