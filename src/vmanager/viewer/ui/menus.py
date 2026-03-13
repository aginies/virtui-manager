"""
Menu Builders

Provides builder functions for creating various popup menus used in the viewer.
"""

from typing import Optional, Callable, Dict, Any

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk


def build_settings_menu(
    scaling_enabled: bool,
    smoothing_enabled: bool,
    lossy_encoding_enabled: bool,
    view_only_enabled: bool,
    vnc_depth: int,
    boot_devices: list[tuple[str, str]],
    current_boot_device: Optional[str],
    on_scaling_toggled: Callable,
    on_smoothing_toggled: Callable,
    on_lossy_toggled: Callable,
    on_view_only_toggled: Callable,
    on_depth_changed: Callable,
    on_boot_device_changed: Callable,
    on_menu_show: Optional[Callable] = None,
) -> tuple[Gtk.MenuButton, Gtk.Box, Gtk.CheckButton, Gtk.ComboBoxText]:
    """
    Build the settings menu with display options and boot order.

    Args:
        scaling_enabled: Initial scaling state
        smoothing_enabled: Initial smoothing state
        lossy_encoding_enabled: Initial lossy encoding state
        view_only_enabled: Initial view-only state
        vnc_depth: Initial VNC color depth
        boot_devices: List of (device_id, label) for boot selection
        current_boot_device: Initial boot device ID
        on_scaling_toggled: Callback for scaling toggle
        on_smoothing_toggled: Callback for smoothing toggle
        on_lossy_toggled: Callback for lossy encoding toggle
        on_view_only_toggled: Callback for view-only toggle
        on_depth_changed: Callback for depth change
        on_boot_device_changed: Callback for boot order change
        on_menu_show: Optional callback when menu is shown

    Returns:
        Tuple of (menu_button, depth_settings_box, lossy_check, boot_combo)
    """
    settings_button = Gtk.MenuButton()
    icon_settings = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
    settings_button.set_image(icon_settings)
    settings_button.set_tooltip_text("Settings")

    settings_popover = Gtk.Popover()
    if on_menu_show:
        settings_popover.connect("show", on_menu_show)
    vbox_settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    vbox_settings.set_margin_top(10)
    vbox_settings.set_margin_bottom(10)
    vbox_settings.set_margin_start(10)
    vbox_settings.set_margin_end(10)

    # Scaling Checkbox
    scaling_check = Gtk.CheckButton(label="Scaling (Resize)")
    scaling_check.set_active(scaling_enabled)
    scaling_check.connect("toggled", on_scaling_toggled)
    vbox_settings.pack_start(scaling_check, False, False, 0)

    # Smoothing Checkbox
    smoothing_check = Gtk.CheckButton(label="Smoothing (Interpolation)")
    smoothing_check.set_active(smoothing_enabled)
    smoothing_check.connect("toggled", on_smoothing_toggled)
    vbox_settings.pack_start(smoothing_check, False, False, 0)

    # Lossy Encoding Checkbox
    lossy_check = Gtk.CheckButton(label="Lossy Compression (JPEG)")
    lossy_check.set_active(lossy_encoding_enabled)
    lossy_check.connect("toggled", on_lossy_toggled)
    vbox_settings.pack_start(lossy_check, False, False, 0)

    # View Only Checkbox
    view_only_check = Gtk.CheckButton(label="View Only Mode")
    view_only_check.set_active(view_only_enabled)
    view_only_check.connect("toggled", on_view_only_toggled)
    vbox_settings.pack_start(view_only_check, False, False, 0)

    vbox_settings.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

    # Color Depth Selector
    depth_settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    depth_label = Gtk.Label(label="Color Depth:")
    depth_settings_box.pack_start(depth_label, False, False, 0)

    depth_combo = Gtk.ComboBoxText()
    depth_combo.append("0", "Default")
    depth_combo.append("8", "8-bit")
    depth_combo.append("16", "16-bit")
    depth_combo.append("24", "24-bit")
    depth_combo.set_active_id(str(vnc_depth))
    depth_combo.connect("changed", on_depth_changed)
    depth_settings_box.pack_start(depth_combo, True, True, 0)
    vbox_settings.pack_start(depth_settings_box, False, False, 0)

    # Boot Device Selector
    boot_settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    boot_label = Gtk.Label(label="First Boot:")
    boot_settings_box.pack_start(boot_label, False, False, 0)

    boot_combo = Gtk.ComboBoxText()
    for dev_id, label in boot_devices:
        boot_combo.append(dev_id, label)
    
    if current_boot_device:
        boot_combo.set_active_id(current_boot_device)
    elif boot_devices:
        boot_combo.set_active(0)

    boot_combo.connect("changed", on_boot_device_changed)
    boot_settings_box.pack_start(boot_combo, True, True, 0)
    vbox_settings.pack_start(boot_settings_box, False, False, 0)

    vbox_settings.show_all()
    settings_popover.add(vbox_settings)
    settings_button.set_popover(settings_popover)

    return settings_button, depth_settings_box, lossy_check, boot_combo



def build_power_menu(
    on_start: Callable,
    on_pause: Callable,
    on_resume: Callable,
    on_hibernate: Callable,
    on_shutdown: Callable,
    on_reboot: Callable,
    on_destroy: Callable,
    on_menu_show: Optional[Callable] = None,
) -> tuple[Gtk.MenuButton, Dict[str, Gtk.ModelButton]]:
    """
    Build the VM power control menu.

    Args:
        on_start: Callback for start action
        on_pause: Callback for pause action
        on_resume: Callback for resume action
        on_hibernate: Callback for hibernate action
        on_shutdown: Callback for graceful shutdown
        on_reboot: Callback for reboot action
        on_destroy: Callback for force power off
        on_menu_show: Optional callback when menu is shown

    Returns:
        Tuple of (menu_button, dict of power buttons)
    """
    power_button = Gtk.MenuButton()
    icon_power = Gtk.Image.new_from_icon_name("system-shutdown-symbolic", Gtk.IconSize.BUTTON)
    power_button.set_image(icon_power)
    power_button.set_tooltip_text("VM Power Control")

    power_popover = Gtk.Popover()
    power_button.set_popover(power_popover)

    if on_menu_show:
        power_popover.connect("show", on_menu_show)

    vbox_power = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    power_buttons = {}
    power_actions = [
        ("Start", "media-playback-start-symbolic", on_start),
        ("Pause", "media-playback-pause-symbolic", on_pause),
        ("Resume", "media-playback-start-symbolic", on_resume),
        ("Hibernate", "media-record-symbolic", on_hibernate),
        ("Graceful Shutdown", "system-shutdown-symbolic", on_shutdown),
        ("Reboot", "system-reboot-symbolic", on_reboot),
        ("Force Power Off", "system-shutdown-symbolic", on_destroy),
    ]

    for label, icon_name, callback in power_actions:
        btn = Gtk.ModelButton()
        btn.set_label(label)
        btn.connect("clicked", callback, power_popover)
        vbox_power.pack_start(btn, False, False, 0)
        power_buttons[label] = btn

    vbox_power.show_all()
    power_popover.add(vbox_power)

    return power_button, power_buttons


def build_keys_menu(on_send_key: Callable) -> Gtk.MenuButton:
    """
    Build the send keys menu.

    Args:
        on_send_key: Callback for sending key combinations

    Returns:
        The menu button widget
    """
    keys_button = Gtk.MenuButton()
    icon_keys = Gtk.Image.new_from_icon_name("input-keyboard-symbolic", Gtk.IconSize.BUTTON)
    keys_button.set_image(icon_keys)
    keys_button.set_tooltip_text("Send Key")

    keys_popover = Gtk.Popover()
    vbox_keys = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    key_combinations = [
        ("Ctrl+Alt+Del", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_Delete]),
        ("Ctrl+Alt+Backspace", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_BackSpace]),
        (
            "Shift+Ctrl+Alt+Esc",
            [Gdk.KEY_Shift_L, Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_Escape],
        ),
        ("Ctrl+Alt+F1", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F1]),
        ("Ctrl+Alt+F2", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F2]),
        ("Ctrl+Alt+F3", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F3]),
        ("Ctrl+Alt+F4", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F4]),
        ("Ctrl+Alt+F5", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F5]),
        ("Ctrl+Alt+F6", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F6]),
        ("Ctrl+Alt+F7", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F7]),
        ("Ctrl+Alt+F8", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F8]),
        ("Ctrl+Alt+F9", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F9]),
        ("Ctrl+Alt+F10", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F10]),
        ("PrintScreen", [Gdk.KEY_Print]),
    ]

    for label, keys in key_combinations:
        btn = Gtk.ModelButton()
        btn.set_label(label)
        btn.connect("clicked", on_send_key, keys, keys_popover)
        vbox_keys.pack_start(btn, False, False, 0)

    vbox_keys.show_all()
    keys_popover.add(vbox_keys)
    keys_button.set_popover(keys_popover)

    return keys_button


def build_clipboard_menu(
    on_type_clipboard: Callable,
    on_push_clipboard: Optional[Callable] = None,
    on_pull_clipboard: Optional[Callable] = None,
) -> Gtk.MenuButton:
    """
    Build the clipboard actions menu.

    Args:
        on_type_clipboard: Callback for typing clipboard as keystrokes
        on_push_clipboard: Optional callback for pushing to guest (currently unused)
        on_pull_clipboard: Optional callback for pulling from guest (currently unused)

    Returns:
        The menu button widget
    """
    clip_button = Gtk.MenuButton()
    icon_clip = Gtk.Image.new_from_icon_name("edit-paste-symbolic", Gtk.IconSize.BUTTON)
    clip_button.set_image(icon_clip)
    clip_button.set_tooltip_text("Clipboard Actions")

    clip_popover = Gtk.Popover()
    vbox_clip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    # Type Clipboard Option
    btn_type_clip = Gtk.ModelButton()
    btn_type_clip.set_label("Type Clipboard (as keys)")
    btn_type_clip.connect("clicked", on_type_clipboard, clip_popover)
    vbox_clip.pack_start(btn_type_clip, False, False, 0)

    # Manual Pull (currently commented out in original - not adding to menu)
    # btn_pull_clip = Gtk.ModelButton()
    # btn_pull_clip.set_label("Pull Guest Clipboard to Host")
    # if on_pull_clipboard:
    #     btn_pull_clip.connect("clicked", on_pull_clipboard, clip_popover)
    # vbox_clip.pack_start(btn_pull_clip, False, False, 0)

    # Manual Push (currently commented out in original - not adding to menu)
    # btn_push_clip = Gtk.ModelButton()
    # btn_push_clip.set_label("Push Host Clipboard to Guest")
    # if on_push_clipboard:
    #     btn_push_clip.connect("clicked", on_push_clipboard, clip_popover)
    # vbox_clip.pack_start(btn_push_clip, False, False, 0)

    vbox_clip.show_all()
    clip_popover.add(vbox_clip)
    clip_button.set_popover(clip_popover)

    return clip_button


__all__ = [
    'build_settings_menu',
    'build_power_menu',
    'build_keys_menu',
    'build_clipboard_menu',
]
