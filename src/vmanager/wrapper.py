import sys

def main():
    """Entry point for the TUI application."""
    try:
        from vmanager import vmanager
        if hasattr(vmanager, 'main'):
            vmanager.main()
        else:
            print("Error: vmanager module has no 'main' function.")
            sys.exit(1)
    except ImportError as e:
        print(f"Error importing vmanager: {e}")
        sys.exit(1)

def cmd_main():
    """Entry point for the command-line interface."""
    try:
        from vmanager import vmanager_cmd
        if hasattr(vmanager_cmd, 'main'):
            vmanager_cmd.main()
        else:
            print("Error: vmanager_cmd module has no 'main' function.")
            sys.exit(1)
    except ImportError as e:
        print(f"Error importing vmanager_cmd: {e}")
        sys.exit(1)

def remote_viewer_main():
    """Entry point for the remote viewer application (GTK3)."""
    try:
        from vmanager import remote_viewer
        if hasattr(remote_viewer, 'main'):
            remote_viewer.main()
        else:
            print("Error: remote_viewer module has no 'main' function.")
            sys.exit(1)
    except ImportError as e:
        print(f"Error importing remote_viewer: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
