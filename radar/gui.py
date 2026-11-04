"""Desktop workspace over the same on-device services as the command line."""

import csv
import io
import ipaddress
import json
import queue
import re
import shlex
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit


PAGE_SIZE = 40
NO_GOAL = "No goal lens"
STATUSES = {
    "new": "New",
    "reviewed": "Reviewed",
    "apply": "Plan to apply",
    "applied": "Applied",
    "skip": "Dismissed",
}
COLORS = {
    "navy": "#10242D",
    "ink": "#142D37",
    "muted": "#46606B",
    "surface": "#FFFFFF",
    "paper": "#F2F6F6",
    "line": "#CEDBDE",
    "teal": "#075E56",
    "mint": "#C8EAE1",
}


def _label(value):
    return str(value).replace("_", " ").capitalize()


def _display(value, depth=0):
    if value is None or value == "" or value == [] or value == {}:
        return "Unknown"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        return "\n".join(
            f"{_label(key)}: {_display(item, depth + 1)}" for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(
            ("• " if depth < 3 else "") + _display(item, depth + 1) for item in value
        )
    return str(value)


def _source_link(url):
    from .store import safe_url

    try:
        url = safe_url(url)
        if not url:
            return None
        parsed = urlsplit(url)
        host = parsed.hostname.rstrip(".")
        if (
            parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or "%" in host
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.encode("idna").decode("ascii").lower().split(".")
            if (
                len(labels) < 2
                or labels[-1] in {"localhost", "local", "internal"}
                or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1])
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                    for label in labels
                )
            ):
                return None
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                return None
    except ValueError:
        return None
    return url


def _format_phrases(values):
    stream = io.StringIO()
    csv.writer(stream).writerow(values)
    return stream.getvalue().rstrip("\r\n")


def main(store):
    """Open a local window. Importing this module never initializes Tk."""
    try:
        import tkinter as tk
        from tkinter import filedialog, font, messagebox, ttk
    except ImportError as exc:
        print(
            f"Desktop setup requires a Python installation with Tk: {exc}",
            file=sys.stderr,
        )
        return 1
    if tk.TkVersion < 8.6:
        print(
            "Desktop setup requires Tk 8.6 or newer. On macOS, use a Python installation that bundles modern Tk.",
            file=sys.stderr,
        )
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Could not open a desktop window: {exc}", file=sys.stderr)
        return 1

    from . import app, recovery
    from .collection import load_catalog

    class Workspace:
        def __init__(self):
            self.scope = "all"
            self.query = tk.StringVar(root)
            self.goal = tk.StringVar(root, NO_GOAL)
            self.goal_names = {}
            self.kind = tk.StringVar(root, "Any kind")
            self.position = tk.StringVar(root)
            self.banner = tk.StringVar(
                root, "Ready • Your workspace stays on this device"
            )
            self.heading = tk.StringVar(root, "Explore")
            self.description = tk.StringVar(
                root,
                "Search your entire collection. Add a goal when you want tailored results.",
            )
            self.compare_label = tk.StringVar(root, "Compare (0)")
            self.offset = 0
            self.selected = None
            self.selected_item = None
            self.selected_goal = None
            self.pins = []
            self.rows = {}
            self.jobs = {}
            self.closed = False
            self.closing = False
            self.pending_search = None
            self.generation = 0
            self.queued_search = None
            self.collect_cancel = None
            self.configure_style()
            self.layout()
            self.refresh_goals()
            self.schedule_search(reset=True)

        def configure_style(self):
            root.configure(background=COLORS["paper"])
            self.style = ttk.Style(root)
            if "clam" in self.style.theme_names():
                self.style.theme_use("clam")
            self.body_font = font.nametofont("TkDefaultFont").copy()
            self.body_font.configure(size=12)
            self.title_font = self.body_font.copy()
            self.title_font.configure(size=24, weight="bold")
            self.section_font = self.body_font.copy()
            self.section_font.configure(size=14, weight="bold")
            self.small_font = self.body_font.copy()
            self.small_font.configure(size=10)
            self.style.configure(
                ".",
                font=self.body_font,
                background=COLORS["paper"],
                foreground=COLORS["ink"],
            )
            self.style.configure(
                "TButton",
                padding=(9, 6),
                width=0,
                relief="flat",
                bordercolor=COLORS["line"],
                lightcolor=COLORS["line"],
                darkcolor=COLORS["line"],
                background=COLORS["surface"],
                focuscolor=COLORS["teal"],
            )
            self.style.map("TButton", foreground=[("disabled", COLORS["muted"])])
            self.style.configure(
                "Accent.TButton",
                background=COLORS["teal"],
                foreground=COLORS["surface"],
                bordercolor=COLORS["teal"],
                lightcolor=COLORS["teal"],
                darkcolor=COLORS["teal"],
                focuscolor=COLORS["surface"],
            )
            self.style.map(
                "Accent.TButton",
                background=[("active", "#084B47"), ("pressed", COLORS["navy"])],
            )
            self.style.configure("Top.TFrame", background=COLORS["navy"])
            self.style.configure(
                "Brand.TLabel",
                background=COLORS["navy"],
                foreground=COLORS["surface"],
                font=self.section_font,
            )
            self.style.configure(
                "Private.TLabel",
                background=COLORS["navy"],
                foreground=COLORS["mint"],
                font=self.small_font,
            )
            self.style.configure("Title.TLabel", font=self.title_font)
            self.style.configure("Muted.TLabel", foreground=COLORS["muted"])
            self.style.configure(
                "Caption.TLabel", foreground=COLORS["muted"], font=self.small_font
            )
            self.style.configure("Section.TLabel", font=self.section_font)
            self.style.configure("Card.TFrame", background=COLORS["surface"])
            self.style.configure(
                "Treeview",
                rowheight=30,
                fieldbackground=COLORS["surface"],
                background=COLORS["surface"],
            )
            self.style.map(
                "Treeview",
                background=[("selected", COLORS["teal"])],
                foreground=[("selected", COLORS["surface"])],
            )
            self.style.configure(
                "Treeview.Heading",
                background=COLORS["paper"],
                foreground=COLORS["muted"],
                padding=(6, 6),
            )
            self.style.configure("TCombobox", padding=(5, 5))
            self.style.configure("TEntry", padding=(6, 6))

        def layout(self):
            root.title("Opportunity Radar • Local workspace")
            root.geometry("1140x760")
            root.minsize(720, 520)
            root.protocol("WM_DELETE_WINDOW", self.close)
            root.bind("<Control-f>", self.focus_search)
            root.bind("<F5>", self.reload_workspace)
            if sys.platform == "darwin":
                root.bind("<Command-f>", self.focus_search)
            top = ttk.Frame(root, style="Top.TFrame", padding=(20, 11))
            top.pack(fill="x")
            ttk.Label(top, text="Opportunity Radar", style="Brand.TLabel").pack(
                side="left"
            )
            ttk.Label(top, text="LOCAL WORKSPACE", style="Private.TLabel").pack(
                side="right"
            )

            controls = ttk.Frame(root, padding=(18, 12, 18, 5))
            controls.pack(fill="x")
            self.tabs = {}
            for value, title in (
                ("all", "Explore"),
                ("recommended", "For a goal"),
                ("tracked", "Shortlist"),
            ):
                self.tabs[value] = ttk.Button(
                    controls,
                    text=title,
                    command=lambda value=value: self.set_scope(value),
                )
                self.tabs[value].pack(side="left", padx=(0, 6))
            self.tabs["all"].configure(style="Accent.TButton")
            for label, callback in (
                ("Data", self.data_dialog),
                ("Sources", self.sources_dialog),
                ("Goals", self.goals_dialog),
            ):
                ttk.Button(controls, text=label, command=callback).pack(
                    side="right", padx=(6, 0)
                )

            heading = ttk.Frame(root, padding=(20, 3, 20, 10))
            heading.pack(fill="x")
            ttk.Label(heading, textvariable=self.heading, style="Title.TLabel").pack(
                anchor="w"
            )
            ttk.Label(
                heading,
                textvariable=self.description,
                style="Muted.TLabel",
                wraplength=660,
            ).pack(anchor="w")

            filters = ttk.Frame(root, padding=(20, 1, 20, 9))
            filters.pack(fill="x")
            filters.columnconfigure(0, weight=1)
            ttk.Label(
                filters, text="Search your collection", style="Caption.TLabel"
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(filters, text="Goal lens", style="Caption.TLabel").grid(
                row=0, column=1, sticky="w", padx=(9, 0)
            )
            ttk.Label(filters, text="Opportunity kind", style="Caption.TLabel").grid(
                row=0, column=2, sticky="w", padx=(9, 0)
            )
            self.query_entry = ttk.Entry(filters, textvariable=self.query, width=26)
            self.query_entry.grid(row=1, column=0, sticky="ew")
            self.query_entry.bind("<Escape>", self.clear_search)
            self.query.trace_add("write", lambda *_: self.schedule_search(reset=True))
            self.goal_picker = ttk.Combobox(
                filters, textvariable=self.goal, state="readonly", width=17
            )
            self.goal_picker.grid(row=1, column=1, sticky="ew", padx=(9, 0))
            self.goal_picker.bind("<<ComboboxSelected>>", self.change_goal)
            self.kind_picker = ttk.Combobox(
                filters, textvariable=self.kind, state="readonly", width=18
            )
            self.kind_picker.configure(
                values=(
                    "Any kind",
                    "job",
                    "internship",
                    "apprenticeship",
                    "fellowship",
                    "grant",
                    "research_program",
                    "research",
                    "postdoc",
                    "training",
                    "competition",
                    "hackathon",
                    "scholarship",
                    "event",
                    "opportunity",
                    "other",
                )
            )
            self.kind_picker.grid(row=1, column=2, sticky="ew", padx=(9, 0))
            self.kind_picker.bind(
                "<<ComboboxSelected>>", lambda _: self.schedule_search(reset=True)
            )

            panes = ttk.Panedwindow(root, orient="horizontal")
            panes.pack(fill="both", expand=True, padx=20, pady=(1, 7))
            left = ttk.Frame(panes, padding=(0, 0, 7, 0))
            right = ttk.Frame(panes, padding=(7, 0, 0, 0))
            panes.add(left, weight=2)
            panes.add(right, weight=3)

            result_area = ttk.Frame(left)
            result_area.pack(fill="both", expand=True)
            self.tree = ttk.Treeview(
                result_area,
                columns=("organization",),
                show="tree headings",
                selectmode="browse",
                height=8,
            )
            self.tree.heading("#0", text="Opportunity", anchor="w")
            self.tree.heading("organization", text="Organization", anchor="w")
            self.tree.column("#0", width=220, minwidth=108, stretch=True)
            self.tree.column("organization", width=115, minwidth=64, stretch=True)
            self.tree.pack(side="left", fill="both", expand=True)
            scroll = ttk.Scrollbar(
                result_area, orient="vertical", command=self.tree.yview
            )
            scroll.pack(side="right", fill="y")
            self.tree.configure(yscrollcommand=scroll.set)
            self.tree.bind("<<TreeviewSelect>>", self.select_result)
            self.tree.bind("<Return>", lambda _: self.detail.focus_set())

            paging = ttk.Frame(left, padding=(0, 6, 0, 0))
            paging.pack(fill="x")
            self.prev_button = ttk.Button(
                paging, text="Previous", command=lambda: self.change_page(-PAGE_SIZE)
            )
            self.next_button = ttk.Button(
                paging, text="Next", command=lambda: self.change_page(PAGE_SIZE)
            )
            self.prev_button.pack(side="left")
            self.next_button.pack(side="right")
            ttk.Label(paging, textvariable=self.position, style="Caption.TLabel").pack(
                padx=5
            )

            self.detail = self.text_panel(right)
            actions = ttk.Frame(right, padding=(0, 6, 0, 0))
            actions.pack(fill="x")
            self.source_button = ttk.Button(
                actions, text="Open source", command=self.open_selected
            )
            actions.columnconfigure((0, 1), weight=1)
            self.source_button.grid(
                row=0, column=0, sticky="ew", padx=(0, 5), pady=(0, 4)
            )
            self.pin_button = ttk.Button(actions, text="Pin", command=self.toggle_pin)
            self.pin_button.grid(row=0, column=1, sticky="ew", pady=(0, 4))
            ttk.Button(
                actions, textvariable=self.compare_label, command=self.compare_dialog
            ).grid(row=1, column=0, sticky="ew", padx=(0, 5))
            self.review_button = ttk.Button(
                actions,
                text="Track…",
                style="Accent.TButton",
                command=self.track_dialog,
            )
            self.review_button.grid(row=1, column=1, sticky="ew")
            self.enable_detail_actions(False)

            bottom = ttk.Frame(root, padding=(20, 2, 20, 10))
            bottom.pack(side="bottom", fill="x", before=panes)
            ttk.Label(
                bottom, textvariable=self.banner, style="Muted.TLabel", wraplength=550
            ).pack(side="left")
            self.cancel_button = ttk.Button(
                bottom,
                text="Cancel collection",
                command=self.cancel_collection,
                state="disabled",
            )
            self.cancel_button.pack(side="right")
            ttk.Button(bottom, text="Reload", command=self.reload_workspace).pack(
                side="right", padx=(0, 6)
            )
            self.query_entry.focus_set()

        def text_panel(self, parent):
            holder = ttk.Frame(parent, style="Card.TFrame")
            holder.pack(fill="both", expand=True)
            scroll = ttk.Scrollbar(holder, orient="vertical")
            scroll.pack(side="right", fill="y")
            content = tk.Text(
                holder,
                wrap="word",
                height=8,
                width=24,
                padx=14,
                pady=11,
                foreground=COLORS["ink"],
                background=COLORS["surface"],
                borderwidth=0,
                highlightthickness=2,
                highlightcolor=COLORS["teal"],
                highlightbackground=COLORS["line"],
                font=self.body_font,
                yscrollcommand=scroll.set,
                spacing1=1,
                spacing3=3,
                takefocus=True,
            )
            content.tag_configure("title", font=self.title_font, spacing3=6)
            content.tag_configure("heading", font=self.section_font, spacing1=10)
            content.tag_configure("quiet", foreground=COLORS["muted"])
            content.tag_configure("teal", foreground=COLORS["teal"])
            content.configure(state="disabled")
            content.bind("<Tab>", lambda _: self.focus_relative(content, 1))
            content.bind("<Shift-Tab>", lambda _: self.focus_relative(content, -1))
            content.pack(side="left", fill="both", expand=True)
            scroll.configure(command=content.yview)
            return content

        def focus_relative(self, widget, direction):
            destination = (
                widget.tk_focusNext() if direction == 1 else widget.tk_focusPrev()
            )
            if destination is not None:
                destination.focus_set()
            return "break"

        def write_text(self, widget, sections):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            for text, tag in sections:
                widget.insert("end", text + "\n", tag)
            widget.configure(state="disabled")
            widget.yview_moveto(0)

        def focus_search(self, _):
            self.query_entry.focus_set()
            self.query_entry.selection_range(0, "end")
            return "break"

        def clear_search(self, _):
            self.query.set("")
            return "break"

        def active_goal(self):
            return self.goal_names.get(self.goal.get())

        def change_goal(self, _=None):
            if self.selected and self.selected_goal != self.active_goal():
                self.show_item(self.selected)
            self.schedule_search(reset=True)

        def reload_workspace(self, _=None):
            try:
                self.refresh_goals()
                if self.selected:
                    position = self.detail.yview()[0]
                    self.show_item(self.selected)
                    self.detail.yview_moveto(position)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.banner.set(f"Unable to reload workspace. {exc}")
                return
            self.schedule_search(reset=False)

        def refresh_goals(self):
            active = self.active_goal()
            names = [item["name"] for item in store.goals()]
            self.goal_names = {f"Goal: {name}": name for name in names}
            self.goal_picker.configure(values=(NO_GOAL, *self.goal_names))
            self.goal.set(f"Goal: {active}" if active in names else NO_GOAL)

        def set_scope(self, scope):
            if scope != self.scope:
                self.clear_selection()
            self.scope = scope
            headings = {
                "all": (
                    "Explore",
                    "Search your entire collection. Add a goal when you want tailored results.",
                ),
                "recommended": (
                    "For a goal",
                    "Review why a listing surfaced for the selected goal and what still needs checking.",
                ),
                "tracked": (
                    "Shortlist",
                    "Return to saved, planned, applied and dismissed opportunities, with your notes.",
                ),
            }
            self.heading.set(headings[scope][0])
            self.description.set(headings[scope][1])
            for name, button in self.tabs.items():
                button.configure(style="Accent.TButton" if name == scope else "TButton")
            if scope == "recommended" and not self.active_goal():
                first = next(iter(self.goal_names), None)
                if first:
                    self.goal.set(first)
            self.change_goal()

        def change_page(self, delta):
            self.offset = max(0, self.offset + delta)
            self.clear_selection()
            self.schedule_search(reset=False)

        def clear_selection(self):
            self.selected = None
            self.selected_item = None
            self.selected_goal = None
            self.rows.clear()
            if self.tree.selection():
                self.tree.selection_remove(*self.tree.selection())
            self.enable_detail_actions(False)

        def schedule_search(self, *, reset):
            if self.closing:
                return
            if reset:
                self.offset = 0
            self.generation += 1
            if self.pending_search is not None:
                root.after_cancel(self.pending_search)
            self.pending_search = root.after(140, self.start_search)

        def start_search(self):
            self.pending_search = None
            kind = self.kind.get()
            params = {
                "query": self.query.get(),
                "goal": self.active_goal(),
                "scope": self.scope,
                "kind": None if kind == "Any kind" else kind,
                "limit": PAGE_SIZE + 1,
                "offset": self.offset,
            }
            version = self.generation
            if "search" in self.jobs:
                self.queued_search = (version, params)
                return
            self.launch_search(version, params)

        def launch_search(self, version, params):
            self.position.set("Searching…")

            def finished(items, error):
                if not self.closing and version == self.generation:
                    if error:
                        self.position.set("Search failed")
                        self.banner.set(f"Search failed. {error}")
                    else:
                        self.show_results(items, params["offset"])
                if self.queued_search is not None and not self.closing:
                    queued, self.queued_search = self.queued_search, None
                    self.launch_search(*queued)

            if params["scope"] == "recommended" and params["goal"] is None:
                finished([], None)
            else:
                self.run("search", lambda: store.search(**params), finished)

        def show_results(self, items, offset):
            has_more = len(items) > PAGE_SIZE
            items = items[:PAGE_SIZE]
            self.prev_button.configure(state="normal" if offset else "disabled")
            self.next_button.configure(state="normal" if has_more else "disabled")
            self.position.set(
                f"{offset + 1}-{offset + len(items)}" if items else "0 results"
            )
            if self.tree.get_children():
                self.tree.delete(*self.tree.get_children())
            self.rows = {item["id"]: item for item in items}
            for item in items:
                name = ("★ " if item.get("saved") else "") + item["title"]
                self.tree.insert(
                    "",
                    "end",
                    iid=item["id"],
                    text=name,
                    values=(item.get("organization") or "Unknown",),
                )
            if self.selected in self.rows:
                self.tree.selection_set(self.selected)
                self.tree.see(self.selected)
            elif items and self.selected is None:
                item = items[0]["id"]
                self.show_item(item)
                self.tree.selection_set(item)
                self.tree.focus(item)
            elif not items and self.selected is None:
                self.empty_detail()

        def empty_detail(self):
            if self.scope == "recommended" and not self.active_goal():
                title, copy = (
                    "Start with one goal",
                    "Open Goals and describe what you want to do or learn. Explore stays available without a goal.",
                )
            elif self.scope == "tracked":
                title, copy = (
                    "Your shortlist is empty",
                    "Find a listing in Explore, then select Track to save it or set a decision.",
                )
            elif self.query.get() or self.kind.get() != "Any kind":
                title, copy = (
                    "No results for these filters",
                    "Try a shorter phrase or choose Any kind. Open Sources to check what has been collected.",
                )
            else:
                title, copy = (
                    "Find your first opportunity",
                    "Open Data to load a small fictional collection offline, or open Sources to collect listings from a public board.",
                )
            self.write_text(self.detail, [(title, "title"), (copy, "quiet")])

        def select_result(self, _):
            if self.tree.selection():
                item = self.tree.selection()[0]
                if item in self.rows and (
                    item != self.selected or self.selected_goal != self.active_goal()
                ):
                    self.show_item(item)

        def detail_sections(self, item, *, brief=False):
            kinds = item.get("kinds") or []
            organization = item.get("organization") or "Organization unknown"
            location = item.get("location") or "Location unknown"
            observations = item.get("observations") or []
            fictional = observations and all(
                row.get("source_id") == "sample" for row in observations
            )
            availability = (
                "Fictional sample"
                if fictional
                else _label(item.get("availability") or "unknown")
            )
            status = STATUSES.get(item.get("status"), "New")
            sections = [
                (item.get("title") or "Untitled", "title"),
                (f"{organization}  ·  {location}", "quiet"),
                (
                    f"{availability}  ·  {status}"
                    + ("  ·  Saved" if item.get("saved") else ""),
                    "teal",
                ),
                (", ".join(_label(kind) for kind in kinds) or "Kind unknown", "quiet"),
                (
                    f"Apply by {item.get('deadline_at') or 'unknown'}  ·  Posted {item.get('posted_at') or 'unknown'}",
                    "quiet",
                ),
            ]
            brief_groups = item.get("brief") or {}
            for label, evidence in brief_groups.items():
                if evidence:
                    sections.append((label, "heading"))
                    for source in evidence:
                        details = source.get("source_id") or "Source unknown"
                        if source.get("conditional"):
                            details += " · Conditional wording"
                        if source.get("negative_language"):
                            details += " · Negative wording"
                        sections.extend(
                            [
                                (f"“{source['quote']}”", ""),
                                (details, "quiet"),
                            ]
                        )
            if not brief_groups.get("Activities") and not brief:
                sections.extend(
                    [
                        ("Listing text", "heading"),
                        (_display(item.get("description")), ""),
                    ]
                )
            match = item.get("match")
            eligibility = (match or {}).get("eligibility", {})
            sections.extend(
                [
                    ("Eligibility", "heading"),
                    (_label(eligibility.get("state", "unknown")), "quiet"),
                ]
            )
            for evidence in eligibility.get("evidence", []):
                sections.append(
                    (f"“{evidence['quote']}”\n{evidence.get('url', '')}", "")
                )
            if match is not None:
                sections.append(("Goal evidence", "heading"))
                decision = (
                    "Recommended"
                    if match.get("recommended")
                    else "Outside recommendations for this goal"
                )
                sections.append(
                    (f"{decision} · Fit score {match.get('score', 0)}", "teal")
                )
                for rule in match.get("rules", []):
                    label = _label(rule["rule"])
                    term = rule["term"]
                    if rule.get("quote"):
                        evidence = f"“{rule['quote']}”"
                        if rule.get("url"):
                            evidence += f"\n{rule['url']}"
                    else:
                        evidence = "No supporting passage found."
                    sections.append((f"{label} · {term}\n{evidence}", ""))
            for key, label in (
                ("requirements", "Stated requirements"),
                ("note", "Your note"),
            ):
                if key in item and (key != "note" or item[key]):
                    sections.extend([(label, "heading"), (_display(item[key]), "")])
            if not brief and brief_groups.get("Activities"):
                sections.extend(
                    [
                        ("Full listing text", "heading"),
                        (_display(item.get("description")), ""),
                    ]
                )
            sections.append(("Source observations", "heading"))
            for index, row in enumerate(observations):
                sections.extend(
                    self.observation_sections(row, include_text=not brief and index > 0)
                )
            if not observations:
                sections.append(("Unknown", "quiet"))
            if not brief:
                history = item.get("history") or []
                if history:
                    sections.append(("Earlier source observations", "heading"))
                    for row in history:
                        sections.extend(
                            self.observation_sections(row, include_text=False)
                        )
                sections.extend([("Record ID", "heading"), (item["id"], "quiet")])
            return sections

        def observation_sections(self, row, *, include_text):
            name = row.get("source_name") or row.get("source_id") or "Unknown publisher"
            date = row.get("observed_at") or "Observation date unknown"
            facts = {
                "listing_link": row.get("url"),
                "deadline": row.get("deadline_at"),
                "publisher_evidence": row.get("authority"),
                "provenance": row.get("evidence"),
            }
            if include_text:
                facts.update(
                    description=row.get("description"),
                    requirements=row.get("requirements"),
                )
            return [(f"{name} · {date}", "teal"), (_display(facts), "")]

        def show_item(self, item_id):
            active_goal = self.active_goal()
            try:
                item = store.inspect(item_id, goal=active_goal)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.banner.set(f"Unable to read this opportunity. {exc}")
                return
            if not item:
                return
            self.selected = item_id
            self.selected_item = item
            self.selected_goal = active_goal
            self.write_text(self.detail, self.detail_sections(item))
            self.enable_detail_actions(True)
            self.pin_button.configure(text="Unpin" if item_id in self.pins else "Pin")

        def enable_detail_actions(self, enabled):
            state = "normal" if enabled else "disabled"
            self.review_button.configure(state=state)
            self.pin_button.configure(state=state)
            url = (
                _source_link(self.selected_item.get("url"))
                if self.selected_item
                else None
            )
            self.source_button.configure(
                state="normal" if enabled and url else "disabled"
            )

        def open_selected(self):
            if self.selected_item is None:
                return
            url = _source_link(self.selected_item.get("url"))
            if url:
                try:
                    webbrowser.open(url)
                except (OSError, webbrowser.Error) as exc:
                    self.banner.set(f"Unable to open the source. {exc}")

        def toggle_pin(self):
            if self.selected in self.pins:
                self.pins.remove(self.selected)
            elif len(self.pins) < 3:
                self.pins.append(self.selected)
            else:
                self.banner.set(
                    "Three opportunities are pinned. Unpin one to add another."
                )
                return
            self.pin_button.configure(
                text="Unpin" if self.selected in self.pins else "Pin"
            )
            self.compare_label.set(f"Compare ({len(self.pins)})")

        def dialog(self, title, subtitle, size="650x520"):
            window = tk.Toplevel(root)
            window.title(title)
            window.transient(root)
            window.geometry(size)
            window.minsize(420, 330)
            window.bind("<Escape>", lambda _: window.destroy())
            frame = ttk.Frame(window, padding=(16, 12))
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w")
            ttk.Label(frame, text=subtitle, style="Muted.TLabel", wraplength=490).pack(
                anchor="w", pady=(4, 10)
            )
            return window, frame

        def compare_dialog(self):
            if len(self.pins) < 2:
                self.banner.set(
                    "Pin two or three opportunities from Explore or your shortlist to compare them."
                )
                return
            try:
                items = app.compare(store, self.pins, goal=self.active_goal())
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.banner.set(f"Unable to compare opportunities. {exc}")
                return
            _, frame = self.dialog(
                "Compare opportunities",
                "Compare source facts, unknowns, goal evidence and your notes. Pins are for this session.",
                "1050x610",
            )
            split = ttk.Panedwindow(frame, orient="horizontal")
            split.pack(fill="both", expand=True)
            if isinstance(items, dict):
                items = items.get("items", items.get("opportunities", []))
            for item in items:
                column = ttk.Frame(split, padding=(0, 0, 5, 0))
                split.add(column, weight=1)
                panel = self.text_panel(column)
                self.write_text(panel, self.detail_sections(item, brief=True))

        def track_dialog(self):
            item = self.selected_item
            if not item:
                return
            window, frame = self.dialog("Track opportunity", item["title"], "560x435")
            original_status = STATUSES.get(item.get("status"), STATUSES["new"])
            status = tk.StringVar(window, original_status)
            saved = tk.BooleanVar(window, bool(item.get("saved")))
            controls = ttk.Frame(frame)
            controls.pack(fill="x")
            ttk.Label(controls, text="Decision").pack(side="left", padx=(0, 7))
            choice = ttk.Combobox(
                controls,
                state="readonly",
                textvariable=status,
                values=tuple(STATUSES.values()),
            )
            choice.pack(side="left")
            ttk.Checkbutton(controls, text="Saved to shortlist", variable=saved).pack(
                side="left", padx=(10, 0)
            )
            ttk.Label(frame, text="Private note or next action").pack(
                anchor="w", pady=(12, 5)
            )
            note = tk.Text(frame, width=45, height=7, wrap="word", font=self.body_font)
            note.pack(fill="both", expand=True)
            note.insert("1.0", item.get("note") or "")
            note.bind("<Tab>", lambda _: self.focus_relative(note, 1))
            note.bind("<Shift-Tab>", lambda _: self.focus_relative(note, -1))
            error = tk.StringVar(window)
            ttk.Label(
                frame, textvariable=error, style="Muted.TLabel", wraplength=490
            ).pack(anchor="w", pady=(4, 0))

            def save():
                new_status = next(
                    key for key, label in STATUSES.items() if label == status.get()
                )
                new_note = note.get("1.0", "end-1c")
                kwargs = {}
                if status.get() != original_status:
                    kwargs["status"] = new_status
                if saved.get() != bool(item.get("saved")):
                    kwargs["saved"] = saved.get()
                if new_note != (item.get("note") or ""):
                    kwargs["note"] = new_note
                try:
                    if kwargs:
                        store.track(item["id"], **kwargs)
                except (ValueError, OSError, sqlite3.Error) as exc:
                    error.set(str(exc))
                    return
                window.destroy()
                self.banner.set("Shortlist updated on this device.")
                if self.selected == item["id"]:
                    self.show_item(item["id"])
                self.schedule_search(reset=False)

            ttk.Button(
                frame, text="Save decision", style="Accent.TButton", command=save
            ).pack(anchor="e", pady=(7, 0))
            window.grab_set()
            choice.focus_set()

        def goals_dialog(self):
            window, frame = self.dialog(
                "Search goals",
                "Goal lenses rank the same collection independently. Explore always shows direct search results.",
                "680x680",
            )
            selection = ttk.Frame(frame)
            selection.pack(fill="x")
            saved_names = tuple(item["name"] for item in store.goals())
            picker = ttk.Combobox(
                selection, values=saved_names, state="readonly", width=24
            )
            picker.pack(side="left", fill="x", expand=True)
            fields = {}
            error = tk.StringVar(window)
            descriptions = (
                ("name", "Goal name"),
                (
                    "objective",
                    "What do you want to do, gain or learn? (optional context)",
                ),
                ("prefer", "Prefer these activities, subjects or benefits"),
                ("require", "Require evidence of these phrases for recommendations"),
                ("avoid", "Avoid these duties or conditions"),
                ("explore", "Explore these related interests"),
                (
                    "facts",
                    "Optional eligibility facts (JSON object with true or false values)",
                ),
            )

            def fill(name=None):
                goal = next(
                    (goal for goal in store.goals() if goal["name"] == name), None
                )
                picker.set(name or "")
                for key, variable in fields.items():
                    value = (
                        (goal["name"] if key == "name" else goal["rules"].get(key, ""))
                        if goal
                        else ""
                    )
                    if isinstance(value, list):
                        value = _format_phrases(value)
                    elif isinstance(value, dict):
                        value = json.dumps(value, ensure_ascii=False)
                    variable.set(value)
                error.set("")

            ttk.Button(selection, text="New goal", command=lambda: fill()).pack(
                side="left", padx=(8, 0)
            )
            picker.bind("<<ComboboxSelected>>", lambda _: fill(picker.get()))
            for key, label in descriptions:
                fields[key] = tk.StringVar(window)
                ttk.Label(frame, text=label).pack(anchor="w", pady=(6, 2))
                ttk.Entry(frame, textvariable=fields[key]).pack(fill="x")
            ttk.Label(
                frame,
                text="Separate phrases with commas. Use quotes to keep a comma inside a phrase. Facts stay local and only apply to conditions the publisher evidence supports.",
                style="Caption.TLabel",
                wraplength=490,
            ).pack(anchor="w", pady=(7, 0))
            ttk.Label(
                frame, textvariable=error, style="Muted.TLabel", wraplength=490
            ).pack(anchor="w", pady=(6, 0))
            controls = ttk.Frame(frame)
            controls.pack(fill="x", pady=(8, 0))

            def update_view(name, *, select_goal=True):
                names = tuple(item["name"] for item in store.goals())
                picker.configure(values=names)
                actual_name = next(
                    (value for value in names if value.casefold() == name.casefold()),
                    None,
                )
                fill(actual_name)
                self.refresh_goals()
                if select_goal and picker.get():
                    self.goal.set(f"Goal: {picker.get()}")
                self.schedule_search(reset=True)
                if self.selected:
                    self.show_item(self.selected)

            def save():
                name = fields["name"].get().strip()
                try:
                    rules = {
                        key: [
                            part.strip()
                            for part in next(
                                csv.reader(
                                    [fields[key].get()],
                                    skipinitialspace=True,
                                    strict=True,
                                )
                            )
                            if part.strip()
                        ]
                        for key in ("prefer", "require", "avoid", "explore")
                    }
                    rules["objective"] = fields["objective"].get().strip()
                    if fields["facts"].get().strip():
                        rules["facts"] = json.loads(fields["facts"].get())
                    store.put_goal(name, rules)
                except (ValueError, OSError, sqlite3.Error, csv.Error) as exc:
                    error.set(str(exc))
                    return
                update_view(name)
                error.set(
                    "Saved on this device. Editing a name creates or updates a goal under that name."
                )

            def undo():
                name = picker.get()
                if not name:
                    error.set("Choose a saved goal first.")
                    return
                try:
                    store.undo_goal(name)
                except (ValueError, OSError, sqlite3.Error) as exc:
                    error.set(str(exc))
                    return
                update_view(name)
                error.set("Last goal edit undone.")

            def import_pack():
                from .profiles import import_profiles, preview_profiles

                path = filedialog.askopenfilename(
                    parent=window,
                    title="Choose a profile pack",
                    filetypes=[("JSON", "*.json"), ("All files", "*")],
                )
                if not path:
                    return
                try:
                    preview = preview_profiles(store, path=path)
                    names = [profile["name"] for profile in preview["profiles"]]
                    detail = ", ".join(names[:6])
                    if len(names) > 6:
                        detail += f", and {len(names) - 6} more"
                    if not messagebox.askyesno(
                        "Import profiles",
                        f"Import {detail}?\n\n"
                        f"{len(preview['conflicts'])} saved profiles with matching names will be updated.",
                        parent=window,
                    ):
                        return
                    result = import_profiles(store, path=path, expected_revision=preview["revision"])
                except (ValueError, OSError, sqlite3.Error) as exc:
                    error.set(str(exc))
                    return
                update_view(
                    result.get("active_profile") or names[0],
                    select_goal=result.get("active_profile") is not None,
                )
                if "active_profile" in result and result["active_profile"] is None:
                    self.goal.set(NO_GOAL)
                    self.change_goal()
                error.set(f"Imported {len(names)} profiles.")

            ttk.Button(controls, text="Undo last edit", command=undo).pack(side="left")
            ttk.Button(controls, text="Import profiles…", command=import_pack).pack(
                side="left", padx=(8, 0)
            )
            ttk.Button(
                controls, text="Save goal", style="Accent.TButton", command=save
            ).pack(side="right")
            if self.active_goal() in saved_names:
                fill(self.active_goal())
            elif saved_names:
                fill(saved_names[0])
            picker.focus_set()

        def sources_dialog(self):
            window, frame = self.dialog(
                "Public sources",
                "Review sources before collecting. A collection fetches one public source with a time limit you choose.",
                "900x660",
            )
            controls = ttk.Frame(frame)
            controls.pack(fill="x", pady=(0, 7))
            query = tk.StringVar(window)
            ttk.Label(controls, text="Filter sources").pack(side="left", padx=(0, 6))
            entry = ttk.Entry(controls, textvariable=query)
            entry.pack(side="left", fill="x", expand=True)
            ttk.Button(
                controls,
                text="Add public source…",
                command=lambda: self.add_source_dialog(reload),
            ).pack(side="right", padx=(8, 0))
            source_list = ttk.Frame(frame)
            source_list.pack(fill="both", expand=True)
            tree = ttk.Treeview(
                source_list,
                columns=("kind", "coverage"),
                show="tree headings",
                selectmode="browse",
                height=7,
            )
            for column, label, width in (
                ("#0", "Source", 215),
                ("kind", "Adapter", 100),
                ("coverage", "Collection", 150),
            ):
                tree.heading(column, text=label, anchor="w")
                tree.column(column, width=width, minwidth=65, stretch=column == "#0")
            tree.pack(side="left", fill="both", expand=True)
            scroll = ttk.Scrollbar(source_list, command=tree.yview)
            scroll.pack(side="right", fill="y")
            tree.configure(yscrollcommand=scroll.set)
            details = self.text_panel(frame)
            controls = ttk.Frame(frame)
            controls.pack(fill="x", pady=(6, 0))
            ttk.Label(controls, text="Time limit (seconds)").pack(
                side="left", padx=(0, 5)
            )
            seconds = ttk.Entry(controls, width=6)
            seconds.insert(0, "15")
            seconds.pack(side="left")
            ttk.Button(controls, text="Run history", command=self.receipts_dialog).pack(
                side="right", padx=(6, 0)
            )
            sources = {}
            local = {}

            def selected():
                return sources.get(tree.selection()[0]) if tree.selection() else None

            def show(_=None):
                source = selected()
                if source:
                    local_source = local.get(source["id"])
                    collectable = source["kind"] not in {"legacy", "manual", "sample"}
                    safe_fields = (
                        "id",
                        "kind",
                        "url",
                        "regions",
                        "opportunity_types",
                        "last_state",
                        "last_finished_at",
                        "last_message",
                        "state",
                        "message",
                        "last_receipt",
                    )
                    self.write_text(
                        details,
                        [
                            (source["name"], "heading"),
                            (
                                _display(
                                    {
                                        key: source[key]
                                        for key in safe_fields
                                        if key in source
                                    }
                                ),
                                "",
                            ),
                            (
                                "Local data source"
                                if not collectable
                                else "Configured for collection"
                                if local_source and local_source.get("enabled")
                                else "Select Enable or Collect to opt in.",
                                "quiet",
                            ),
                        ],
                    )
                    enable.configure(
                        text="Disable"
                        if local_source and local_source.get("enabled")
                        else "Enable",
                        state="normal" if collectable else "disabled",
                    )
                    run_button.configure(state="normal" if collectable else "disabled")
                else:
                    self.write_text(
                        details,
                        [
                            ("Choose a source", "heading"),
                            (
                                "Uncollected sources are available to explore from the public catalog.",
                                "quiet",
                            ),
                        ],
                    )
                    enable.configure(state="disabled")
                    run_button.configure(state="disabled")

            def reload():
                previous = tree.selection()
                local.clear()
                local.update({source["id"]: source for source in store.sources()})
                sources.clear()
                sources.update({source["id"]: source for source in load_catalog()})
                sources.update(local)
                if tree.get_children():
                    tree.delete(*tree.get_children())
                needle = query.get().casefold().strip()
                ordered = sorted(
                    sources.values(),
                    key=lambda source: (
                        source["id"] not in local,
                        source["name"].casefold(),
                    ),
                )
                for source in ordered:
                    source_id = source["id"]
                    searchable = " ".join(
                        str(source.get(key, ""))
                        for key in (
                            "id",
                            "name",
                            "kind",
                            "regions",
                            "opportunity_types",
                        )
                    ).casefold()
                    if needle and needle not in searchable:
                        continue
                    configured = local.get(source_id)
                    state = (
                        "Local import"
                        if source["kind"] in {"legacy", "manual", "sample"}
                        else "Enabled"
                        if configured and configured.get("enabled")
                        else "Disabled"
                        if configured
                        else "Catalog"
                    )
                    tree.insert(
                        "",
                        "end",
                        iid=source_id,
                        text=source["name"],
                        values=(_label(source["kind"]), state),
                    )
                if previous and tree.exists(previous[0]):
                    tree.selection_set(previous[0])
                show()

            def configure(enabled=None):
                source = selected()
                if source is None or source["kind"] in {"legacy", "manual", "sample"}:
                    return False
                if enabled is None:
                    enabled = not bool(local.get(source["id"], {}).get("enabled"))
                try:
                    store.add_source({**source, "enabled": enabled})
                except (ValueError, OSError, sqlite3.Error) as exc:
                    messagebox.showerror(
                        "Source was not updated", str(exc), parent=window
                    )
                    return False
                reload()
                return True

            def collect():
                source = selected()
                if source is None:
                    return
                if "work" in self.jobs:
                    messagebox.showinfo(
                        "Work in progress",
                        "Wait for the current operation to finish before collecting.",
                        parent=window,
                    )
                    return
                try:
                    budget = int(seconds.get())
                    if not 1 <= budget <= 60:
                        raise ValueError
                except ValueError:
                    messagebox.showerror(
                        "Check the time limit",
                        "Enter a whole number from 1 through 60.",
                        parent=window,
                    )
                    return
                if not local.get(source["id"], {}).get("enabled") and not configure(
                    True
                ):
                    return
                self.collect_cancel = threading.Event()
                cancel = self.collect_cancel
                self.cancel_button.configure(state="normal")

                def done(receipt):
                    if window.winfo_exists():
                        reload()
                    self.schedule_search(reset=False)
                    state = _label(receipt.get("state") or "finished")
                    message = (
                        receipt.get("message") or f"{receipt.get('seen', 0)} observed"
                    )
                    return f"{source['name']} · {state} · {message}"

                self.background(
                    f"Collecting {source['name']} · up to {budget} seconds",
                    lambda: app.refresh(store, source["id"], budget, cancel=cancel),
                    done,
                    always=lambda: (
                        cancel_here.configure(state="disabled")
                        if window.winfo_exists()
                        else None
                    ),
                )
                cancel_here.configure(state="normal")

            enable = ttk.Button(controls, text="Enable", command=configure)
            enable.pack(side="right", padx=(6, 0))
            run_button = ttk.Button(
                controls, text="Collect", style="Accent.TButton", command=collect
            )
            run_button.pack(side="right")
            work_status = ttk.Frame(frame, padding=(0, 6, 0, 0))
            work_status.pack(fill="x")
            ttk.Label(
                work_status,
                textvariable=self.banner,
                wraplength=500,
                style="Caption.TLabel",
            ).pack(side="left", fill="x", expand=True)
            cancel_here = ttk.Button(
                work_status,
                text="Cancel collection",
                command=self.cancel_collection,
                state="normal" if self.collect_cancel else "disabled",
            )
            cancel_here.pack(side="right")
            tree.bind("<<TreeviewSelect>>", show)
            tree.bind("<Return>", lambda _: details.focus_set())
            query.trace_add(
                "write", lambda *_: reload() if window.winfo_exists() else None
            )
            reload()
            entry.focus_set()

        def add_source_dialog(self, updated):
            from .collection import CATALOG_ONLY, SUPPORTED_KINDS

            window, frame = self.dialog(
                "Add a public source",
                "Use a public HTTPS URL. Hosted job boards also need their board slug. Adding a source makes no network request.",
                "600x510",
            )
            fields = {}
            for key, title in (
                ("name", "Display name"),
                ("id", "Source ID (lowercase, hyphens allowed)"),
                ("url", "Public HTTPS URL"),
                ("kind", "Adapter"),
                ("board", "Board slug, if applicable"),
            ):
                ttk.Label(frame, text=title).pack(anchor="w", pady=(5, 2))
                variable = fields[key] = tk.StringVar(window)
                if key == "kind":
                    variable.set("syndication")
                    ttk.Combobox(
                        frame,
                        state="readonly",
                        textvariable=variable,
                        values=sorted(SUPPORTED_KINDS - CATALOG_ONLY),
                    ).pack(fill="x")
                else:
                    field = ttk.Entry(frame, textvariable=variable)
                    field.pack(fill="x")
                    if key == "name":
                        field.focus_set()
            error = tk.StringVar(window)
            ttk.Label(
                frame, textvariable=error, style="Muted.TLabel", wraplength=470
            ).pack(anchor="w", pady=(5, 0))

            def save():
                data = {
                    key: field.get().strip()
                    for key, field in fields.items()
                    if field.get().strip()
                }
                try:
                    store.add_source({**data, "enabled": True})
                except (ValueError, OSError, sqlite3.Error) as exc:
                    error.set(str(exc))
                    return
                window.destroy()
                updated()

            ttk.Button(
                frame, text="Save source", style="Accent.TButton", command=save
            ).pack(anchor="e", pady=(8, 0))
            window.grab_set()

        def receipts_dialog(self):
            _, frame = self.dialog(
                "Collection history",
                "Receipts distinguish completed, partial, canceled and failed refreshes.",
                "710x560",
            )
            panel = self.text_panel(frame)
            try:
                receipts = store.receipts(limit=30)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.write_text(
                    panel,
                    [("Unable to read collection history", "heading"), (str(exc), "")],
                )
                return
            sections = []
            for receipt in receipts:
                sections.append(
                    (
                        f"{receipt['source_id']}  ·  {_label(receipt['state'])}",
                        "heading",
                    )
                )
                sections.append(
                    (
                        _display(
                            {
                                key: value
                                for key, value in receipt.items()
                                if key not in {"id", "source_id", "state"}
                            }
                        ),
                        "",
                    )
                )
            self.write_text(panel, sections or [("No collections yet", "heading")])

        def data_dialog(self):
            window, frame = self.dialog(
                "Workspace data",
                "Manage local listings and personal decisions. Backups contain your private data.",
                "670x555",
            )

            def perform(label, action, *, replacement=False):
                if "work" in self.jobs:
                    messagebox.showinfo(
                        "Work in progress",
                        "Wait for the current operation to finish.",
                        parent=window,
                    )
                    return

                def done(result):
                    self.refresh_goals()
                    if replacement:
                        self.clear_selection()
                        self.pins.clear()
                        self.compare_label.set("Compare (0)")
                    self.schedule_search(reset=replacement)
                    return label + " complete."

                self.background(label, action, done)
                window.destroy()

            def load_example():
                perform("Loading fictional examples", lambda: app.seed(store))

            def import_listings():
                path = filedialog.askopenfilename(
                    parent=window,
                    title="Choose opportunity JSON",
                    filetypes=[("JSON", "*.json"), ("All files", "*")],
                )
                if path:
                    perform(
                        "Importing listings", lambda: app.import_file(store, Path(path))
                    )

            def make_backup():
                path = filedialog.asksaveasfilename(
                    parent=window,
                    title="Create a private backup",
                    initialfile="opportunity-radar-backup.sqlite3",
                )
                if path:
                    perform(
                        "Creating backup", lambda: recovery.backup(store, Path(path))
                    )

            def restore_backup():
                path = filedialog.askopenfilename(
                    parent=window, title="Choose a verified Radar backup"
                )
                if path and messagebox.askyesno(
                    "Restore workspace",
                    "Restore this backup over the current workspace? Create a backup of the current data first if you want to keep it.",
                    parent=window,
                ):
                    perform(
                        "Restoring backup",
                        lambda: recovery.restore(store, Path(path)),
                        replacement=True,
                    )

            def migrate():
                path = filedialog.askopenfilename(
                    parent=window,
                    title="Choose a disposable copy of an older Radar database",
                )
                if not path:
                    return
                choose_config = messagebox.askyesnocancel(
                    "Legacy configuration",
                    "Choose a separate copy of the older configuration folder? Select No to use the config folder beside the copied data folder, if present.",
                    parent=window,
                )
                if choose_config is None:
                    return
                config_dir = None
                if choose_config:
                    folder = filedialog.askdirectory(
                        parent=window,
                        title="Choose the copied legacy configuration folder",
                    )
                    if not folder:
                        return
                    config_dir = Path(folder)
                perform(
                    "Importing legacy workspace",
                    lambda: recovery.migrate(store, Path(path), config_dir=config_dir),
                )

            for label, copy, callback in (
                (
                    "Load examples",
                    "Try a small, clearly fictional collection offline.",
                    load_example,
                ),
                (
                    "Import listings…",
                    "Read a local JSON listing file without a network request.",
                    import_listings,
                ),
                (
                    "Create backup…",
                    "Save an owner-only snapshot with goals and tracking.",
                    make_backup,
                ),
                (
                    "Restore backup…",
                    "Verify a snapshot, then replace the current workspace.",
                    restore_backup,
                ),
                (
                    "Import old data…",
                    "Import a copy of an older Opportunity Radar database.",
                    migrate,
                ),
                (
                    "Schedule help",
                    "Use your system scheduler for finite collection runs.",
                    self.schedule_dialog,
                ),
            ):
                row = ttk.Frame(frame, padding=(0, 5))
                row.pack(fill="x")
                button = ttk.Button(row, text=label, command=callback, width=19)
                button.pack(side="left", padx=(0, 9))
                ttk.Label(row, text=copy, wraplength=370, style="Muted.TLabel").pack(
                    side="left", fill="x", expand=True
                )
            ttk.Label(
                frame,
                text=f"Workspace file · {store.path}",
                style="Caption.TLabel",
                wraplength=590,
            ).pack(anchor="w", pady=(10, 0))

        def schedule_dialog(self):
            _, frame = self.dialog(
                "Schedule a collection",
                "Use the operating system scheduler to run a time-limited command. The desktop app can be closed.",
                "670x420",
            )
            panel = self.text_panel(frame)
            command = shlex.join(
                [
                    sys.executable,
                    "-m",
                    "radar",
                    "--workspace",
                    str(store.path),
                    "collect",
                    "--seconds",
                    "15",
                    "--max-sources",
                    "3",
                ]
            )
            self.write_text(
                panel,
                [
                    ("One-shot collection", "heading"),
                    (
                        "Enable a source in Sources, then run the collection command from the project folder. Run it manually once before setting up a schedule.",
                        "",
                    ),
                    (command, "teal"),
                    (
                        "Use absolute paths and the same workspace as the desktop. Review the result in Sources → Run history.",
                        "",
                    ),
                    ("The app runs no periodic collection by itself.", "quiet"),
                ],
            )

        def run(self, name, action, callback):
            if name in self.jobs or self.closing:
                return
            result = queue.SimpleQueue()

            def perform():
                try:
                    result.put((action(), None))
                except Exception as exc:
                    result.put((None, exc))

            worker = threading.Thread(target=perform, name=f"radar-{name}")
            self.jobs[name] = worker
            worker.start()

            def check():
                if worker.is_alive():
                    root.after(50, check)
                    return
                worker.join()
                del self.jobs[name]
                value, error = result.get_nowait()
                if not self.closing:
                    callback(value, error)
                elif not self.jobs:
                    self.finish_close()

            root.after(30, check)

        def background(self, label, action, finished, *, always=None):
            self.banner.set(label + "…")

            def done(result, error):
                self.collect_cancel = None
                self.cancel_button.configure(state="disabled")
                if always:
                    always()
                if error:
                    self.banner.set(f"{label} failed. {error}")
                else:
                    self.banner.set(finished(result))

            self.run("work", action, done)

        def cancel_collection(self):
            if self.collect_cancel is not None:
                self.collect_cancel.set()
                self.banner.set("Cancel requested. Waiting for collection to exit…")
                self.cancel_button.configure(state="disabled")

        def close(self):
            if self.closing:
                return
            self.closing = True
            if self.pending_search is not None:
                root.after_cancel(self.pending_search)
                self.pending_search = None
            self.queued_search = None
            if self.jobs:
                self.cancel_collection()
                self.banner.set("Closing when active work reaches a safe stop…")
            else:
                self.finish_close()

        def finish_close(self):
            if not self.closed:
                self.closed = True
                root.destroy()

    try:
        Workspace()
        root.mainloop()
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Unable to open the local workspace: {exc}", file=sys.stderr)
        return 1
    return 0
