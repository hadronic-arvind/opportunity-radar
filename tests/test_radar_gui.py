"""Desktop safety and an opt-in rendered journey using a disposable workspace."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from radar.gui import _source_link, main
from radar.store import Store


class SourceNavigationTests(unittest.TestCase):
    def test_source_navigation_requires_a_public_https_destination(self):
        public = "https://careers.example.org/openings/34"
        self.assertEqual(_source_link(public), public)
        for value in (
            "javascript:alert(1)",
            "file:///tmp/private",
            "http://example.org/",
            "https://example.org:1234/",
            "https://guest@example.org/",
            "https://@example.org/",
            "https://localhost./",
            "https://device.local/",
            "https://device.internal/",
            "https://127.0.0.1/",
            "https://127.1/",
            "https://0177.0.0.1/",
            "https://[::1]/",
            "https://[::ffff:127.0.0.1]/",
            "https://224.0.0.1/",
            "https://example.org\\@localhost/",
        ):
            with self.subTest(value=value):
                self.assertIsNone(_source_link(value))


@unittest.skipUnless(
    os.environ.get("RADAR_TEST_DESKTOP") == "1", "requires a desktop with Tk 8.6+"
)
class DesktopJourneyTests(unittest.TestCase):
    def test_explore_save_and_return_to_shortlist(self):
        import tkinter as tk
        from tkinter import ttk

        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)

        def widget(parent, kind, label=None):
            return next(
                child
                for child in descendants(parent)
                if isinstance(child, kind)
                and (label is None or child.cget("text") == label)
            )

        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "workspace.sqlite3")
            create = tk.Tk
            failures = []

            def create_test_window():
                root = create()

                def fail(error):
                    failures.append(error)
                    root.destroy()

                root.report_callback_exception = lambda _kind, error, _traceback: fail(
                    error
                )

                def await_state(description, condition, next_action):
                    deadline = time.monotonic() + 4

                    def check():
                        if condition():
                            next_action()
                        elif time.monotonic() < deadline:
                            root.after(25, check)
                        else:
                            fail(AssertionError(f"Did not reach {description}"))

                    root.after(25, check)

                def read_detail():
                    return widget(root, tk.Text).get("1.0", "end")

                def load_examples():
                    widget(root, ttk.Button, "Data").invoke()
                    widget(root, ttk.Button, "Load examples").invoke()
                    await_state(
                        "example results",
                        lambda: len(tree().get_children()) == 8,
                        search,
                    )

                def tree():
                    return widget(root, ttk.Treeview)

                def search():
                    entry = widget(root, ttk.Entry)
                    entry.focus_force()
                    for letter in "museum":
                        entry.event_generate("<KeyPress>", keysym=letter)
                        entry.event_generate("<KeyRelease>", keysym=letter)
                    self.assertEqual(entry.get(), "museum")
                    await_state(
                        "typed search result",
                        lambda: len(tree().get_children()) == 1,
                        track,
                    )

                def track():
                    (opportunity,) = tree().get_children()
                    tree().selection_set(opportunity)
                    tree().event_generate("<<TreeviewSelect>>", when="now")
                    self.assertIn("Museum Education Assistant", read_detail())
                    self.assertIn("Fictional sample", read_detail())
                    widget(root, ttk.Button, "Track…").invoke()
                    dialog = widget(root, tk.Toplevel)
                    widget(dialog, ttk.Combobox).set("Plan to apply")
                    widget(dialog, ttk.Checkbutton).invoke()
                    widget(dialog, tk.Text).insert("1.0", "Ask about training.")
                    widget(dialog, ttk.Button, "Save decision").invoke()
                    (saved,) = store.search(scope="tracked")
                    self.assertEqual(
                        (saved["id"], saved["status"], saved["note"]),
                        (opportunity, "apply", "Ask about training."),
                    )
                    self.assertTrue(saved["saved"])
                    widget(root, ttk.Entry).delete(0, "end")
                    widget(root, ttk.Button, "Shortlist").invoke()
                    root.geometry("720x520")
                    await_state(
                        "shortlist detail",
                        lambda: "Ask about training." in read_detail(),
                        finish,
                    )

                def finish():
                    root.update_idletasks()
                    for name in (
                        "Track…",
                        "Open source",
                        "Reload",
                        "Cancel collection",
                    ):
                        button = widget(root, ttk.Button, name)
                        self.assertTrue(button.winfo_ismapped(), name)
                        self.assertLessEqual(
                            button.winfo_rootx() + button.winfo_width(),
                            root.winfo_rootx() + root.winfo_width(),
                            name,
                        )
                        self.assertLessEqual(
                            button.winfo_rooty() + button.winfo_height(),
                            root.winfo_rooty() + root.winfo_height(),
                            name,
                        )
                    root.tk.call(root.protocol("WM_DELETE_WINDOW"))

                await_state(
                    "empty workspace",
                    lambda: "Find your first opportunity" in read_detail(),
                    load_examples,
                )
                return root

            with patch.object(tk, "Tk", create_test_window):
                code = main(store)
            if failures:
                raise failures[0]
            self.assertEqual(code, 0)
