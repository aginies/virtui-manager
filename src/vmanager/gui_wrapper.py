#!/usr/bin/env python3
import sys
import os
import gi

# Require GTK 3.0 and Vte 2.91
try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('Vte', '2.91')
except ValueError as e:
    print(f"Error: Missing required libraries. {e}")
    sys.exit(1)

from gi.repository import Gtk, Vte, GLib, Pango

class VirtuiWrapper(Gtk.Window):
    def __init__(self):
        super().__init__(title="Virtui Manager Console")
        
        self.set_default_size(1200, 1024)
        self.terminal = Vte.Terminal()
        self.terminal.set_size(92, 34)
        
        font_desc = Pango.FontDescription("Monospace 12")
        self.terminal.set_font(font_desc)
        
        self.terminal.set_scrollback_lines(10000)
        
        self.terminal.connect("child-exited", self.on_child_exited)
        
        # Add terminal to a ScrolledWindow to handle scrolling
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.add(self.terminal)
        
        self.add(scrolled_window)
        
        self.connect("destroy", Gtk.main_quit)
        self.spawn_app()

    def spawn_app(self):
        # python3 -m vmanager.wrapper
        cmd = [sys.executable, "-m", "vmanager.wrapper"]
        env = os.environ.copy()
        
        # Calculate src directory relative to this script
        # This script is in src/vmanager/gui_wrapper.py
        script_dir = os.path.dirname(os.path.realpath(__file__))
        src_dir = os.path.dirname(script_dir)
        
        current_pythonpath = env.get("PYTHONPATH", "")
        if src_dir not in current_pythonpath:
            env["PYTHONPATH"] = f"{src_dir}:{current_pythonpath}" if current_pythonpath else src_dir
            
        # Convert env to list of strings "KEY=VALUE" for spawn_async
        envv = [f"{k}={v}" for k, v in env.items()]
        
        try:
            # Vte.Terminal.spawn_async(pty_flags, working_directory, argv, envv, spawn_flags, child_setup, child_setup_data, timeout, cancellable, callback, user_data)
            self.terminal.spawn_async(
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
            self.terminal.feed(error_msg.encode('utf-8'))
            print(error_msg)

    def on_child_exited(self, terminal, status):
        print(f"Child exited with status: {status}")
        Gtk.main_quit()

def main():
    app = VirtuiWrapper()
    app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
