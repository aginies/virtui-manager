#!/usr/bin/env python3
import sys
import os
import shutil
import time
from pathlib import Path
import gi
import yaml

# Require GTK 3.0 and Vte 2.91
try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('Vte', '2.91')
except ValueError as e:
    print(f"Error: Missing required libraries. {e}")
    sys.exit(1)

from gi.repository import Gtk, Vte, GLib, Pango, Gdk

def is_running_under_flatpak():
    return 'FLATPAK_ID' in os.environ

def check_tmux():
    try:
        if shutil.which("tmux") is not None:
            return True
    except Exception as e:
        return False

# Constants
DEFAULT_WINDOW_WIDTH = 1200
DEFAULT_WINDOW_HEIGHT = 1024
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

        # Dictionary to store tab data: terminal -> { 'page': ScrolledWindow, 'label': Label }
        self.tabs = {}

        # Main layout
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # Header Bar
        header_bar = Gtk.HeaderBar()
        header_bar.set_show_close_button(True)
        header_bar.set_title("VirtUI Manager")
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
            ("Ctrl + w", "Close Current Tab"),
            ("Ctrl + f", "Toggle Search Bar"),
            ("Ctrl + + / =", "Increase Font Size"),
            ("Ctrl + -", "Decrease Font Size"),
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
                with open(self.CONFIG_FILE, 'r') as f:
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
                "width": width,
                "height": height
            }

            with open(self.CONFIG_FILE, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def on_destroy(self, widget):
        self.save_gui_config()
        Gtk.main_quit()

    def on_new_vmanager_tab(self, widget):
        if is_running_under_flatpak():
            tmux_bin = "/app/bin/tmux"
        else:
            tmux_bin = "tmux"
        if check_tmux():
            session_name = f"vmanager-{int(time.time())}"
            cmd = [tmux_bin, "new-session", "-s", session_name, sys.executable, "-m", "vmanager.vmanager"]
        else:
            # Fallback to running without tmux if not available
            cmd = [sys.executable, "-m", "vmanager.wrapper"]
        self.create_tab("Virtui Manager", cmd)

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
        if size < 6: size = 6
        if size > 72: size = 72
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
            if data['page'] == page:
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

    def create_tab(self, title, command, fixed_title=False):
        terminal = Vte.Terminal()
        terminal.set_size(TERMINAL_COLS, TERMINAL_ROWS)
        terminal.set_scrollback_lines(TERMINAL_SCROLLBACK)

        self._apply_font_to_terminal(terminal)

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
            'page': scrolled_window,
            'label': label
        }

        self.spawn_process(terminal, command)

        # Ensure the new tab content is visible
        scrolled_window.show_all()
        # Switch to the new tab
        self.notebook.set_current_page(-1)

    def on_close_tab(self, button, terminal):
        if terminal in self.tabs:
            page = self.tabs[terminal]['page']
            page_num = self.notebook.page_num(page)

            if page_num != -1:
                self.notebook.remove_page(page_num)

            del self.tabs[terminal]

            if self.notebook.get_n_pages() == 0:
                self.destroy()

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

    def spawn_process(self, terminal, cmd):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(script_dir, '..', '..'))

        env = os.environ.copy()

        # Determine source directory for PYTHONPATH
        # 1. Check for VIRTUI_SRC_DIR environment variable
        src_dir = env.get("VIRTUI_SRC_DIR")

        # 2. Fallback to local source tree if not set and 'src' exists relative to script
        if not src_dir:
            # this script_dir is src/vmanager
            possible_src_dir = os.path.dirname(script_dir) # this would be src/
            # Check if this looks like a source tree (has vmanager package)
            if os.path.isdir(os.path.join(possible_src_dir, "vmanager")):
                src_dir = possible_src_dir

        # 3. Apply to PYTHONPATH if a source directory was identified
        if src_dir:
            current_pythonpath = env.get("PYTHONPATH", "")
            if src_dir not in current_pythonpath:
                env["PYTHONPATH"] = f"{src_dir}:{current_pythonpath}" if current_pythonpath else src_dir

        # Convert env to list of strings "KEY=VALUE" for spawn_async
        envv = [f"{k}={v}" for k, v in env.items()]

        try:
            # Vte.Terminal.spawn_async(pty_flags, working_directory, argv, envv, spawn_flags, child_setup, child_setup_data, timeout, cancellable, callback, user_data)
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                project_root, # Working directory changed to project_root
                cmd,         # Command arguments
                envv,        # Environment
                GLib.SpawnFlags.DEFAULT,
                None,        # child_setup
                None,        # child_setup_data
                -1,          # timeout
                None,        # cancellable
                None,        # callback
                None         # user_data
            )
        except Exception as e:
            error_msg = f"Error spawning application: {e}\n"
            terminal.feed(error_msg.encode('utf-8'))
            print(error_msg)
    def on_window_title_changed(self, terminal):
        title = terminal.get_property("window-title")
        if not title:
            return

        if terminal in self.tabs:
            self.tabs[terminal]['label'].set_text(title)

    def on_child_exited(self, terminal, status):
        print(f"Child exited with status: {status}")

        if terminal in self.tabs:
            page = self.tabs[terminal]['page']
            page_num = self.notebook.page_num(page)

            if page_num != -1:
                self.notebook.remove_page(page_num)

            del self.tabs[terminal]

        if self.notebook.get_n_pages() == 0:
            self.destroy()
def main():
    app = VirtuiWrapper()
    app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
