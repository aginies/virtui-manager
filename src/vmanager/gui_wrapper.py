#!/usr/bin/env python3
import sys
import os
import shutil
import gi

# Require GTK 3.0 and Vte 2.91
try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('Vte', '2.91')
except ValueError as e:
    print(f"Error: Missing required libraries. {e}")
    sys.exit(1)

from gi.repository import Gtk, Vte, GLib, Pango, Gdk

def check_tmux():
    try:
        if shutil.which("tmux") is not None:
            return True
    except Exception as e:
        return False

class VirtuiWrapper(Gtk.Window):
    def __init__(self):
        super().__init__(title="Virtui Manager Console")

        self.set_default_size(1200, 1024)

        # Get system monospace font
        settings = Gtk.Settings.get_default()
        font_string = None
        try:
            font_string = settings.get_property("gtk-monospace-font-name")
        except TypeError:
            pass # Property not available on this GTK version

        if font_string:
            font_desc = Pango.FontDescription(font_string)
            self.font_name = font_desc.get_family()
            size = font_desc.get_size()
            if size > 0:
                self.current_font_size = size // Pango.SCALE
            else:
                self.current_font_size = 12
        else:
            self.font_name = "Monospace"
            self.current_font_size = 12

        self.terminals = []

        # Main layout
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # Header Bar
        header_bar = Gtk.HeaderBar()
        header_bar.set_show_close_button(True)
        header_bar.set_title("Virtui Manager Console")
        self.set_titlebar(header_bar)

        # Settings Menu Button
        icon_menu = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        settings_button = Gtk.MenuButton()
        settings_button.set_image(icon_menu)
        header_bar.pack_end(settings_button)

        settings_menu = Gtk.Menu()
        settings_button.set_popup(settings_menu)

        # Font Size submenu
        font_size_item = Gtk.MenuItem(label="Font Size")
        settings_menu.append(font_size_item)

        font_size_menu = Gtk.Menu()
        font_size_item.set_submenu(font_size_menu)

        group_font = None
        for size in [8, 10, 12, 14, 16, 18, 20, 24]:
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
        new_vmanager_item = Gtk.MenuItem(label="New VManager Tab")
        new_vmanager_item.connect("activate", self.on_new_vmanager_tab)
        settings_menu.append(new_vmanager_item)

        # New Tab - Command Line
        new_cmd_item = Gtk.MenuItem(label="New Command Line Tab")
        new_cmd_item.connect("activate", self.on_new_cmd_tab)
        settings_menu.append(new_cmd_item)

        settings_menu.show_all()

        # Notebook for Tabs
        self.notebook = Gtk.Notebook()
        vbox.pack_start(self.notebook, True, True, 0)

        # Tab 1: Virtui Manager
        self.on_new_vmanager_tab(None)

        # Tab 2: Command Line
        self.on_new_cmd_tab(None)

        self.connect("key-press-event", self.on_key_press)
        self.connect("destroy", Gtk.main_quit)

    def on_new_vmanager_tab(self, widget):
        cmd_wrapper = [sys.executable, "-m", "vmanager.wrapper"]
        if check_tmux():
            cmd_wrapper = [sys.executable, "-m", "vmanager.wrapper"]
            if check_tmux():
                pass

        cmd = [sys.executable, "-m", "vmanager.wrapper"]
        self.create_tab("Virtui Manager", cmd, is_main_app=(len(self.terminals)==0))

    def on_new_cmd_tab(self, widget):
        cmd_cli = [sys.executable, "-m", "vmanager.vmanager_cmd"]
        self.create_tab("Command Line", cmd_cli, is_main_app=False)

    def on_key_press(self, widget, event):
        # Check if Ctrl is pressed
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK

        if ctrl:
            keyname = Gdk.keyval_name(event.keyval)
            if keyname == "Page_Up":
                self.notebook.prev_page()
                return True
            elif keyname == "Page_Down":
                self.notebook.next_page()
                return True
        return False

    def create_tab(self, title, command, is_main_app=False):
        terminal = Vte.Terminal()
        terminal.set_size(92, 34)
        terminal.set_scrollback_lines(10000)

        font_desc = Pango.FontDescription(f"{self.font_name} {self.current_font_size}")
        terminal.set_font(font_desc)

        # Pass the terminal itself as user_data to find which tab exited
        terminal.connect("child-exited", self.on_child_exited)
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
        # Pass scrolled_window to identify the page to remove
        close_btn.connect("clicked", self.on_close_tab, scrolled_window)
        tab_box.pack_start(close_btn, False, False, 0)
        tab_box.show_all()

        self.notebook.append_page(scrolled_window, tab_box)
        self.notebook.set_tab_reorderable(scrolled_window, True)
        self.terminals.append(terminal)

        self.spawn_process(terminal, command)

        # Ensure the new tab content is visible
        scrolled_window.show_all()
        # Switch to the new tab
        self.notebook.set_current_page(-1)

    def on_close_tab(self, button, page):
        page_num = self.notebook.page_num(page)
        if page_num != -1:
            # Find the terminal associated with this page
            # page is the ScrolledWindow, terminal is its child
            terminal = page.get_child()
            if terminal in self.terminals:
                self.terminals.remove(terminal)

            self.notebook.remove_page(page_num)

            if self.notebook.get_n_pages() == 0:
                Gtk.main_quit()

    def on_font_size_selected(self, item, size):
        if item.get_active():
            self.current_font_size = size
            font_desc = Pango.FontDescription(f"{self.font_name} {size}")
            for terminal in self.terminals:
                terminal.set_font(font_desc)

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
                for terminal in self.terminals:
                    terminal.set_font(font_desc)

        dialog.destroy()

    def spawn_process(self, terminal, cmd):
        # Calculate src directory relative to this script
        # This script is in src/vmanager/gui_wrapper.py
        script_dir = os.path.dirname(os.path.realpath(__file__))
        src_dir = os.path.dirname(script_dir)

        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        if src_dir not in current_pythonpath:
            env["PYTHONPATH"] = f"{src_dir}:{current_pythonpath}" if current_pythonpath else src_dir

        # Convert env to list of strings "KEY=VALUE" for spawn_async
        envv = [f"{k}={v}" for k, v in env.items()]

        try:
            # Vte.Terminal.spawn_async(pty_flags, working_directory, argv, envv, spawn_flags, child_setup, child_setup_data, timeout, cancellable, callback, user_data)
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                os.getcwd(), # Working directory
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
        title = terminal.get_window_title()
        if not title:
            return

        # Find the page containing this terminal
        parent = terminal.get_parent() # ScrolledWindow
        page_num = self.notebook.page_num(parent)

        if page_num != -1:
            tab_box = self.notebook.get_tab_label(parent)
            if tab_box:
                children = tab_box.get_children()
                if children and isinstance(children[0], Gtk.Label):
                    children[0].set_text(title)

    def on_child_exited(self, terminal, status):
        print(f"Child exited with status: {status}")

        # Remove terminal from list
        if terminal in self.terminals:
            self.terminals.remove(terminal)

        # Find the page containing this terminal
        parent = terminal.get_parent() # ScrolledWindow
        page_num = self.notebook.page_num(parent)

        if page_num != -1:
            self.notebook.remove_page(page_num)

        if self.notebook.get_n_pages() == 0:
            Gtk.main_quit()

def main():
    app = VirtuiWrapper()
    app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
