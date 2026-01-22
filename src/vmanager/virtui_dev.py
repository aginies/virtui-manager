#!/usr/bin/env python3
"""
Development entry point for VirtUI Manager.
Allows running the application directly from the source code without installation.

Usage:
    python3 virtui_dev.py [mode] [args...]

Modes:
    tui       Run the Textual User Interface (default)
    cli       Run the Command Line Interface
    viewer    Run the Remote Viewer (GTK3)

Examples:
    python3 virtui_dev.py
    python3 virtui_dev.py cli
    python3 virtui_dev.py viewer --connect qemu:///system --domain-name MyVM
"""
import sys
import os
import argparse

def setup_path():
    """
    Add the 'src' directory to sys.path.
    This enables importing 'vmanager' as a package, which is required for
    relative imports (e.g., 'from .config import ...') to work correctly.
    """
    # Get the directory containing this script (src/vmanager)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level to get the 'src' directory
    src_dir = os.path.dirname(current_dir)
    
    if src_dir not in sys.path:
        # Insert at the beginning to ensure local source takes precedence
        sys.path.insert(0, src_dir)

def run_tui():
    from vmanager import vmanager
    if hasattr(vmanager, 'main'):
        vmanager.main()
    else:
        print("Error: vmanager module has no 'main' function.")

def run_cli():
    from vmanager import vmanager_cmd
    if hasattr(vmanager_cmd, 'main'):
        vmanager_cmd.main()
    else:
        print("Error: vmanager_cmd module has no 'main' function.")

def run_viewer():
    from vmanager import remote_viewer
    if hasattr(remote_viewer, 'main'):
        remote_viewer.main()
    else:
        print("Error: remote_viewer module has no 'main' function.")

def run_viewer_gtk4():
    from vmanager import remote_viewer_gtk4
    if hasattr(remote_viewer_gtk4, 'main'):
        remote_viewer_gtk4.main()
    else:
        print("Error: remote_viewer_gtk4 module has no 'main' function.")

if __name__ == "__main__":
    setup_path()
    
    # Pre-parse arguments to determine the mode
    # We use parse_known_args because the remaining arguments should be passed 
    # to the actual application (e.g. viewer args)
    parser = argparse.ArgumentParser(description="VirtUI Manager Dev Tool", add_help=False)
    parser.add_argument("mode", nargs="?", choices=["tui", "cli", "viewer"], default="tui", help="Application mode to run")
    
    # Check if the first argument is one of our modes
    if len(sys.argv) > 1 and sys.argv[1] in ["tui", "cli", "viewer"]:
        mode = sys.argv[1]
        # Remove the mode argument so the app doesn't see it
        sys.argv.pop(1)
    else:
        mode = "tui"

    # Execute the requested mode
    try:
        if mode == "tui":
            run_tui()
        elif mode == "cli":
            run_cli()
        elif mode == "viewer":
            run_viewer()
    except ImportError as e:
        print(f"Import Error: {e}")
        print("Ensure you are running this script from the source directory structure.")
        sys.exit(1)
    except Exception as e:
        print(f"Application Error: {e}")
        sys.exit(1)
