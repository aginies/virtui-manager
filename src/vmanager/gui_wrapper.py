#!/usr/bin/env python3
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi
import yaml

# Add the directory containing the 'vmanager' package to sys.path
# This allows running the gui_wrapper.py directly from the vmanager directory.
script_dir = os.path.dirname(os.path.abspath(__file__))
vmanager_package_parent_dir = os.path.abspath(os.path.join(script_dir, ".."))
if vmanager_package_parent_dir not in sys.path:
    sys.path.insert(0, vmanager_package_parent_dir)

# Require GTK 3.0, Gdk 3.0 and Vte 2.91
try:
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("Vte", "2.91")
except ValueError as e:
    print(f"Error: Missing required libraries. {e}")
    sys.exit(1)

from gi.repository import Gdk, GLib, Gtk, Pango, Vte

from vmanager.utils import is_tmux_available


def is_running_under_flatpak():
    return "FLATPAK_ID" in os.environ


# Constants
DEFAULT_WINDOW_WIDTH = 1200
DEFAULT_WINDOW_HEIGHT = 800
MIN_WINDOW_WIDTH = 800
MIN_WINDOW_HEIGHT = 600
DEFAULT_FONT_SIZE = 12
TERMINAL_COLS = 92
TERMINAL_ROWS = 34
TERMINAL_SCROLLBACK = 10000
FONT_SIZES = [8, 10, 12, 14, 16, 18, 20, 24]


class VirtuiWrapper(Gtk.Window):
    CONFIG_DIR = Path.home() / ".config" / "virtui-manager"
    CONFIG_FILE = CONFIG_DIR / "gui-config.yaml"

    def __init__(self):
        super().__init__(title="VirtUI Manager Console")

        self.config = self.load_gui_config()

        width = self.config.get("width", DEFAULT_WINDOW_WIDTH)
        height = self.config.get("height", DEFAULT_WINDOW_HEIGHT)
        self.set_default_size(width, height)
        self.set_size_request(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        # Get system monospace font as default fallback
        system_font = "Monospace"
        system_size = DEFAULT_FONT_SIZE
        settings = Gtk.Settings.get_default()
        try:
            font_string = settings.get_property("gtk-monospace-font-name")
            if font_string:
                font_desc = Pango.FontDescription(font_string)
                system_font = font_desc.get_family()
                size = font_desc.get_size()
                if size > 0:
                    system_size = size // Pango.SCALE
        except TypeError:
            pass

        self.font_name = self.config.get("font_name", system_font)
        self.current_font_size = self.config.get("font_size", system_size)

        # Track child PIDs for cleanup
        self.terminal_pids = {}  # {terminal: pid}
        self.cleanup_in_progress = False

        # Dictionary to store tab data: terminal -> { 'page': ScrolledWindow, 'label': Label }
        self.tabs = {}

        # Main layout
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # Header Bar
        header_bar = Gtk.HeaderBar()
        header_bar.set_show_close_button(True)
        header_bar.set_title("VirtUI Manager GUI")
        self.set_titlebar(header_bar)

        # Settings Menu Button
        icon_menu = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        settings_button = Gtk.MenuButton()
        settings_button.set_image(icon_menu)
        header_bar.pack_start(settings_button)

        # Help Button
        help_btn = Gtk.Button.new_from_icon_name("help-browser-symbolic", Gtk.IconSize.BUTTON)
        help_btn.set_tooltip_text("Keyboard Shortcuts")
        help_btn.connect("clicked", self.on_help_clicked)
        header_bar.pack_start(help_btn)

        # Documentation Button
        doc_btn = Gtk.Button.new_from_icon_name("help-contents-symbolic", Gtk.IconSize.BUTTON)
        doc_btn.set_tooltip_text("Online Manual")
        doc_btn.connect("clicked", self.on_doc_clicked)
        header_bar.pack_start(doc_btn)

        settings_menu = Gtk.Menu()
        settings_button.set_popup(settings_menu)

        # Font Size submenu
        font_size_item = Gtk.MenuItem(label="Font Size")
        settings_menu.append(font_size_item)

        font_size_menu = Gtk.Menu()
        font_size_item.set_submenu(font_size_menu)

        group_font = None
        for size in FONT_SIZES:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group_font, f"{size}pt")
            if not group_font:
                group_font = item
            if size == self.current_font_size:
                item.set_active(True)
            item.connect("activate", self.on_font_size_selected, size)
            font_size_menu.append(item)

        # Separator
        font_size_menu.append(Gtk.SeparatorMenuItem())

        # Custom Font
        custom_font_item = Gtk.MenuItem(label="Custom Font...")
        custom_font_item.connect("activate", self.on_custom_font_selected)
        font_size_menu.append(custom_font_item)

        # Separator
        settings_menu.append(Gtk.SeparatorMenuItem())

        # New Tab - VManager
        new_vmanager_item = Gtk.MenuItem(label="New VirtUI Manager Tab")
        new_vmanager_item.connect("activate", self.on_new_vmanager_tab)
        settings_menu.append(new_vmanager_item)

        # New Tab - Command Line
        new_cmd_item = Gtk.MenuItem(label="New VirtUI Command Line Tab")
        new_cmd_item.connect("activate", self.on_new_cmd_tab)
        settings_menu.append(new_cmd_item)

        # New Tab - Log
        new_log_item = Gtk.MenuItem(label="New Log Tab")
        new_log_item.connect("activate", self.on_new_log_tab)
        settings_menu.append(new_log_item)

        settings_menu.show_all()

        # --- Search Bar ---
        self.search_bar = Gtk.SearchBar()
        self.search_bar.set_show_close_button(True)

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("activate", self.on_search_next)
        search_box.pack_start(self.search_entry, True, True, 0)

        btn_prev = Gtk.Button.new_from_icon_name("go-up-symbolic", Gtk.IconSize.MENU)
        btn_prev.set_tooltip_text("Find Previous")
        btn_prev.connect("clicked", self.on_search_prev)
        search_box.pack_start(btn_prev, False, False, 0)

        btn_next = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.MENU)
        btn_next.set_tooltip_text("Find Next")
        btn_next.connect("clicked", self.on_search_next)
        search_box.pack_start(btn_next, False, False, 0)

        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.add(search_box)
        vbox.pack_start(self.search_bar, False, False, 0)

        # Notebook for Tabs
        self.notebook = Gtk.Notebook()
        self.notebook.connect("switch-page", self.on_tab_switched)
        vbox.pack_start(self.notebook, True, True, 0)

        # Tab 1: Virtui Manager
        self.on_new_vmanager_tab(None)
        # Tab 2: Command Line
        self.on_new_cmd_tab(None)
        # Tab 3: Log
        self.on_new_log_tab(None)
        # Go to first tab
        self.notebook.set_current_page(0)

        # Connect window events for cleanup
        self.connect("delete-event", self.on_window_delete)
        self.connect("key-press-event", self.on_key_press)
        self.connect("destroy", self.on_destroy)

    def on_doc_clicked(self, widget):
        url = "https://aginies.github.io/virtui-manager/manual/"
        try:
            Gtk.show_uri_on_window(self, url, Gdk.CURRENT_TIME)
        except Exception as e:
            print(f"Error opening URL {url}: {e}")

    def on_help_clicked(self, widget):
        shortcuts = [
            ("Ctrl + Page Up", "Switch to Previous Tab"),
            ("Ctrl + Page Down", "Switch to Next Tab"),
            ("Ctrl + t", "New Virtui Manager Tab"),
            ("Ctrl + T", "New Command Line Tab"),
            ("Ctrl + Shift + c", "Copy selection to clipboard"),
            ("Ctrl + Shift + v", "Paste from clipboard"),
            ("Ctrl + w", "Close Current Tab"),
            ("Ctrl + f", "Toggle Search Bar"),
            ("Ctrl + + / =", "Increase Font Size"),
            ("Ctrl + -", "Decrease Font Size"),
            ("Ctrl + Scroll", "Zoom In/Out"),
            ("Esc", "Close Search Bar (if active)"),
        ]

        msg_text = "\n".join([f"<b>{keys}</b>: {desc}" for keys, desc in shortcuts])

        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Keyboard Shortcuts",
        )
        dialog.format_secondary_markup(msg_text)

        dialog.run()
        dialog.destroy()

    def load_gui_config(self):
        try:
            if self.CONFIG_FILE.exists():
                with open(self.CONFIG_FILE) as f:
                    return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error loading config: {e}")
        return {}

    def save_gui_config(self):
        try:
            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            width, height = self.get_size()
            config = {
                "font_name": self.font_name,
                "font_size": self.current_font_size,
                "width": max(width, MIN_WINDOW_WIDTH),
                "height": max(height, MIN_WINDOW_HEIGHT),
            }

            with open(self.CONFIG_FILE, "w") as f:
                yaml.dump(config, f, default_flow_style=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def on_new_vmanager_tab(self, widget):
        session_name = None
        if is_running_under_flatpak():
            tmux_bin = "/app/bin/tmux"
        else:
            tmux_bin = "tmux"

        if is_tmux_available():
            session_name = f"vmanager-{int(time.time())}"
            cmd = [
                tmux_bin,
                "new-session",
                "-s",
                session_name,
                sys.executable,
                "-m",
                "vmanager.vmanager",
            ]
        else:
            # Fallback to running without tmux if not available
            cmd = [sys.executable, "-m", "vmanager.wrapper"]
        self.create_tab("Virtui Manager", cmd, session_name=session_name, allow_copy_paste=False)

    def on_new_cmd_tab(self, widget):
        cmd_cli = [sys.executable, "-u", "-m", "vmanager.vmanager_cmd"]
        self.create_tab("Command Line", cmd_cli)

    def on_new_log_tab(self, widget):
        log_path = self._get_log_path()

        # Ensure log file exists to prevent tail from failing immediately if file is missing
        if not log_path.exists():
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.touch()
            except Exception as e:
                print(f"Error creating log file: {e}")

        cmd = ["tail", "-f", str(log_path)]
        self.create_tab("Log", cmd, fixed_title=True)

    def _get_log_path(self):
        try:
            from vmanager.config import get_log_path

            return get_log_path()
        except ImportError:
            return Path.home() / ".cache" / "virtui-manager" / "vm_manager.log"

    def set_font_size(self, size):
        """Sets the font size and applies it."""
        if size < 6:
            size = 6
        if size > 72:
            size = 72
        self.current_font_size = size
        self._apply_font_to_all_terminals()
        self.save_gui_config()

    def on_key_press(self, widget, event):
        # Check if Ctrl is pressed
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        keyname = Gdk.keyval_name(event.keyval)

        if ctrl:
            if keyname == "Page_Up":
                self.notebook.prev_page()
                return True
            elif keyname == "Page_Down":
                self.notebook.next_page()
                return True
            elif keyname == "t":
                self.on_new_vmanager_tab(None)
                return True
            elif keyname == "T":
                self.on_new_cmd_tab(None)
                return True
            elif keyname == "C":
                term = self.get_current_terminal()
                if term and self.tabs.get(term, {}).get("allow_copy_paste", True):
                    term.copy_clipboard()
                    return True
                return False
            elif keyname == "V":
                term = self.get_current_terminal()
                if term and self.tabs.get(term, {}).get("allow_copy_paste", True):
                    term.paste_clipboard()
                    return True
                return False
            elif keyname == "f":
                self.toggle_search()
                return True
            elif keyname == "w":
                term = self.get_current_terminal()
                if term:
                    self.on_close_tab(None, term)
                return True
            elif keyname in ["plus", "equal", "KP_Add"]:
                self.set_font_size(self.current_font_size + 1)
                return True
            elif keyname in ["minus", "KP_Subtract"]:
                self.set_font_size(self.current_font_size - 1)
                return True

        # Handle Escape to close search if active
        if self.search_bar.get_search_mode() and keyname == "Escape":
            self.search_bar.set_search_mode(False)
            return True

        return False

    def toggle_search(self):
        mode = self.search_bar.get_search_mode()
        self.search_bar.set_search_mode(not mode)
        if not mode:
            self.search_entry.grab_focus()

    def get_current_terminal(self):
        page_num = self.notebook.get_current_page()
        page = self.notebook.get_nth_page(page_num)
        for term, data in self.tabs.items():
            if data["page"] == page:
                return term
        return None

    def on_search_changed(self, entry):
        text = entry.get_text()
        term = self.get_current_terminal()
        if not term:
            return

        if not text:
            term.search_set_regex(None, 0)
            term.unselect_all()
            return

        try:
            regex = Vte.Regex.new_for_search(text, -1, 0)
            term.search_set_regex(regex, 0)
            self.on_search_next(None)
        except Exception as e:
            print(f"Search regex error: {e}")

    def on_search_next(self, widget):
        term = self.get_current_terminal()
        if term:
            term.search_find_next()

    def on_search_prev(self, widget):
        term = self.get_current_terminal()
        if term:
            term.search_find_previous()

    def on_tab_switched(self, notebook, page, page_num):
        if self.search_bar.get_search_mode():
            self.on_search_changed(self.search_entry)

    def create_tab(
        self, title, command, fixed_title=False, session_name=None, allow_copy_paste=True
    ):
        terminal = Vte.Terminal()
        terminal.set_size(TERMINAL_COLS, TERMINAL_ROWS)
        terminal.set_scrollback_lines(TERMINAL_SCROLLBACK)

        self._apply_font_to_terminal(terminal)

        # Connect button press for context menu
        terminal.connect("button-press-event", self.on_terminal_button_press)
        # Connect scroll event for zooming
        terminal.connect("scroll-event", self.on_terminal_scroll)

        # Pass the terminal itself as user_data to find which tab exited
        terminal.connect("child-exited", self.on_child_exited)
        if not fixed_title:
            terminal.connect("window-title-changed", self.on_window_title_changed)

        # Add terminal to a ScrolledWindow to handle scrolling
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.add(terminal)

        # Custom tab label with close button
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        label = Gtk.Label(label=title)
        tab_box.pack_start(label, True, True, 0)

        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        # Pass terminal to identify the tab to remove
        close_btn.connect("clicked", self.on_close_tab, terminal)
        tab_box.pack_start(close_btn, False, False, 0)
        tab_box.show_all()

        self.notebook.append_page(scrolled_window, tab_box)
        self.notebook.set_tab_reorderable(scrolled_window, True)

        # Store tab info
        self.tabs[terminal] = {
            "page": scrolled_window,
            "label": label,
            "session_name": session_name,
            "allow_copy_paste": allow_copy_paste,
        }

        self.spawn_process(terminal, command)

        # Ensure the new tab content is visible
        scrolled_window.show_all()
        # Switch to the new tab
        self.notebook.set_current_page(-1)
        return terminal

    def on_terminal_scroll(self, widget, event):
        """Handle scroll events for zooming with Ctrl + Scroll."""
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            if event.direction == Gdk.ScrollDirection.UP:
                self.set_font_size(self.current_font_size + 1)
                return True  # Consume event
            elif event.direction == Gdk.ScrollDirection.DOWN:
                self.set_font_size(self.current_font_size - 1)
                return True  # Consume event
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                if event.delta_y < 0:
                    self.set_font_size(self.current_font_size + 1)
                elif event.delta_y > 0:
                    self.set_font_size(self.current_font_size - 1)
                return True
        return False

    def on_terminal_button_press(self, widget, event):
        """Handle mouse button press on terminal for context menu."""
        if event.button == 3:  # Right click
            if not self.tabs.get(widget, {}).get("allow_copy_paste", True):
                return False

            menu = Gtk.Menu()

            # Copy
            copy_item = Gtk.MenuItem(label="Copy")
            copy_item.connect("activate", lambda x: widget.copy_clipboard())
            if not widget.get_has_selection():
                copy_item.set_sensitive(False)
            menu.append(copy_item)

            # Paste
            paste_item = Gtk.MenuItem(label="Paste")
            paste_item.connect("activate", lambda x: widget.paste_clipboard())
            menu.append(paste_item)

            menu.show_all()
            menu.popup(None, None, None, None, event.button, event.time)
            return True
        return False

    def kill_tmux_session(self, session_name):
        if is_running_under_flatpak():
            tmux_bin = "/app/bin/tmux"
        else:
            tmux_bin = "tmux"

        try:
            subprocess.run(
                [tmux_bin, "kill-session", "-t", session_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"Killed tmux session: {session_name}")
        except Exception as e:
            print(f"Error killing tmux session {session_name}: {e}")

    def cleanup_terminal(self, terminal):
        """
        Properly cleanup a terminal and its child process.
        Sends SIGTERM to the child process (and process group) and waits for graceful shutdown.
        Also kills associated tmux session if any.
        """
        # Retrieve session name before potential early return
        session_name = None
        if terminal in self.tabs:
            session_name = self.tabs[terminal].get("session_name")

        if terminal in self.terminal_pids:
            pid = self.terminal_pids[terminal]
            if pid is not None:
                try:
                    # Check if process is still alive
                    os.kill(pid, 0)

                    # Try to get process group ID to kill the whole tree
                    try:
                        pgid = os.getpgid(pid)
                    except OSError:
                        pgid = None

                    # Send SIGTERM
                    print(f"Sending SIGTERM to PID {pid} (PGID {pgid})")
                    if pgid:
                        os.killpg(pgid, signal.SIGTERM)
                    else:
                        os.kill(pid, signal.SIGTERM)

                    # Wait up to 0.5 seconds for graceful shutdown
                    timeout = 0.5
                    interval = 0.05
                    elapsed = 0
                    while elapsed < timeout:
                        try:
                            os.kill(pid, 0)  # Check if still alive
                            time.sleep(interval)
                            elapsed += interval
                        except OSError:
                            # Process terminated
                            print(f"PID {pid} terminated gracefully")
                            break

                    # If still alive after timeout, force kill
                    try:
                        os.kill(pid, 0)
                        print(f"PID {pid} didn't terminate, sending SIGKILL")
                        if pgid:
                            os.killpg(pgid, signal.SIGKILL)
                        else:
                            os.kill(pid, signal.SIGKILL)
                        time.sleep(0.05)
                    except OSError:
                        pass  # Already dead

                except OSError:
                    # Process already dead
                    pass
                except Exception as e:
                    print(f"Error cleaning up PID {pid}: {e}")
                finally:
                    if terminal in self.terminal_pids:
                        del self.terminal_pids[terminal]

        # Kill tmux session if needed
        if session_name:
            self.kill_tmux_session(session_name)

    def on_close_tab(self, button, terminal):
        """Handle closing a single tab with proper cleanup."""
        # Set wait cursor
        window = self.get_window()
        if window:
            display = window.get_display()
            cursor = Gdk.Cursor.new_from_name(display, "wait")
            window.set_cursor(cursor)
            # Force UI update to show the cursor change immediately
            while Gtk.events_pending():
                Gtk.main_iteration()

        try:
            if terminal in self.tabs:
                self.cleanup_terminal(terminal)
                page = self.tabs[terminal]["page"]
                page_num = self.notebook.page_num(page)

                if page_num != -1:
                    self.notebook.remove_page(page_num)

                del self.tabs[terminal]

                if self.notebook.get_n_pages() == 0:
                    self.destroy()
        finally:
            # Restore cursor if window still exists
            if window:
                window.set_cursor(None)

    def _apply_font_to_terminal(self, terminal):
        """Applies the current font settings to a single terminal."""
        font_desc = Pango.FontDescription(f"{self.font_name} {self.current_font_size}")
        terminal.set_font(font_desc)

    def _apply_font_to_all_terminals(self):
        """Applies the current font settings to all open terminals."""
        for terminal in self.tabs:
            self._apply_font_to_terminal(terminal)

    def on_font_size_selected(self, item, size):
        if item.get_active():
            self.set_font_size(size)

    def on_custom_font_selected(self, widget):
        dialog = Gtk.FontChooserDialog(title="Select Font", parent=self)

        # Set current font in dialog
        current_desc = Pango.FontDescription(f"{self.font_name} {self.current_font_size}")
        dialog.set_font_desc(current_desc)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            font_desc = dialog.get_font_desc()
            if font_desc:
                self.font_name = font_desc.get_family()
                self.current_font_size = font_desc.get_size() // Pango.SCALE
                self._apply_font_to_all_terminals()
                self.save_gui_config()

        dialog.destroy()

    def on_window_delete(self, widget, event):
        """
        Handle window close button (X) - cleanup all terminals before closing.
        Always returns False to allow GTK to proceed with destroy.
        """
        if self.cleanup_in_progress:
            return False  # Allow close if cleanup already done

        # Change cursor to wait to indicate cleanup in progress
        window = widget.get_window()
        if window:
            display = window.get_display()
            cursor = Gdk.Cursor.new_from_name(display, "wait")
            window.set_cursor(cursor)
            # Force UI update to show the cursor change immediately
            while Gtk.events_pending():
                Gtk.main_iteration()

        # Start cleanup
        self.cleanup_in_progress = True
        self.cleanup_all_terminals()

        # Allow window to close after cleanup
        return False

    def cleanup_all_terminals(self):
        """Cleanup all terminal processes before exiting."""
        if not self.terminal_pids:
            return

        print(f"Cleaning up {len(self.terminal_pids)} terminal process(es)...")

        # Create a copy of the items to avoid dictionary size change during iteration
        terminals_to_cleanup = list(self.terminal_pids.keys())

        threads = []
        for terminal in terminals_to_cleanup:
            thread = threading.Thread(target=self.cleanup_terminal, args=(terminal,))
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        print("All terminals cleaned up")

    def on_destroy(self, widget):
        """Handle window destruction - final cleanup."""
        if not self.cleanup_in_progress:
            self.cleanup_all_terminals()

        # Save window size before exiting
        width, height = self.get_size()
        self.config["width"] = width
        self.config["height"] = height
        self.save_gui_config()

        Gtk.main_quit()

    def cleanup(self):
        """Explicit cleanup method that can be called before destroy."""
        if not self.cleanup_in_progress:
            self.cleanup_in_progress = True
            self.cleanup_all_terminals()
            self.save_gui_config()

    def spawn_process(self, terminal, cmd):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))

        env = os.environ.copy()

        # Determine source directory for PYTHONPATH
        # 1. Check for VIRTUI_SRC_DIR environment variable
        src_dir = env.get("VIRTUI_SRC_DIR")

        # 2. Fallback to local source tree if not set and 'src' exists relative to script
        if not src_dir:
            # this script_dir is src/vmanager
            possible_src_dir = os.path.dirname(script_dir)  # this would be src/
            # Check if this looks like a source tree (has vmanager package)
            if os.path.isdir(os.path.join(possible_src_dir, "vmanager")):
                src_dir = possible_src_dir

        # 3. Apply to PYTHONPATH if a source directory was identified
        if src_dir:
            current_pythonpath = env.get("PYTHONPATH", "")
            if src_dir not in current_pythonpath:
                env["PYTHONPATH"] = (
                    f"{src_dir}:{current_pythonpath}" if current_pythonpath else src_dir
                )

        # Convert env to list of strings "KEY=VALUE" for spawn_async
        envv = [f"{k}={v}" for k, v in env.items()]

        try:
            # Vte.Terminal.spawn_async(pty_flags, working_directory, argv, envv, spawn_flags, child_setup, child_setup_data, timeout, cancellable, callback, user_data)
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                project_root,  # Working directory changed to project_root
                cmd,  # Command arguments
                envv,  # Environment
                GLib.SpawnFlags.DEFAULT,
                None,  # child_setup
                None,  # child_setup_data
                -1,  # timeout
                None,  # cancellable
                self.on_spawn_complete,  # callback to get PID
                terminal,  # user_data (pass terminal to callback)
            )
        except Exception as e:
            error_msg = f"Error spawning application: {e}\n"
            terminal.feed(error_msg.encode("utf-8"))
            print(error_msg)

    def on_spawn_complete(self, terminal, pid, error, user_data):
        """Callback when process spawning completes - store the PID for cleanup."""
        if error:
            print(f"Error spawning process: {error}")
            return

        # user_data is the terminal widget we passed
        self.terminal_pids[user_data] = pid
        print(f"Spawned process with PID: {pid}")

    def on_window_title_changed(self, terminal):
        title = terminal.get_property("window-title")
        if not title:
            return

        if terminal in self.tabs:
            self.tabs[terminal]["label"].set_text(title)

    def on_child_exited(self, terminal, status):
        """Handle child process exit."""
        print(f"Child exited with status: {status}")
        if terminal in self.terminal_pids:
            del self.terminal_pids[terminal]

        if terminal in self.tabs:
            page = self.tabs[terminal]["page"]
            page_num = self.notebook.page_num(page)

            if page_num != -1:
                self.notebook.remove_page(page_num)

            del self.tabs[terminal]

        if self.notebook.get_n_pages() == 0:
            self.destroy()


def signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT gracefully."""
    print(f"\nReceived signal {signum}, shutting down gracefully...")
    Gtk.main_quit()


def main():
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    app = VirtuiWrapper()
    app.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
