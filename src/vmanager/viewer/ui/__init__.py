"""
UI Components Package

Contains all GTK3 UI building components for the remote viewer.
"""

from .console_tab import ConsoleTab
from .snapshot_tab import SnapshotTab
from .usb_tab import USBTab
from .main_window import MainWindowBuilder
from .menus import (
    build_settings_menu,
    build_boot_menu,
    build_power_menu,
    build_keys_menu,
    build_clipboard_menu,
)

__all__ = [
    'ConsoleTab',
    'SnapshotTab',
    'USBTab',
    'MainWindowBuilder',
    'build_settings_menu',
    'build_boot_menu',
    'build_power_menu',
    'build_keys_menu',
    'build_clipboard_menu',
]
