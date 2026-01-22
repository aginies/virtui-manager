#!/usr/bin/env python3
"""
Simple remote viewer
"""
#import os
#os.environ['GDK_BACKEND'] = 'x11'
import argparse
import sys
import xml.etree.ElementTree as ET
import os
import json
import time
import subprocess
import socket
import threading
import libvirt
import gi
from . import vm_queries
from . import vm_actions
from . import libvirt_utils

gi.require_version('Gtk', '3.0')
gi.require_version('GtkVnc', '2.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')

try:
    gi.require_version('SpiceClientGtk', '3.0')
    gi.require_version('SpiceClientGLib', '2.0')
    from gi.repository import SpiceClientGtk, SpiceClientGLib
    SPICE_AVAILABLE = True
except (ValueError, ImportError):
    SPICE_AVAILABLE = False

from gi.repository import Gtk, Gdk, GtkVnc, GLib, GdkPixbuf

class RemoteViewer(Gtk.Application):
    def __init__(self, uri, domain_name, uuid, verbose, password=None, show_logs=False, attach=False, wait=False, direct=False):
        super().__init__(application_id=None)
        self.uri = uri
        self.domain_name = domain_name
        self.uuid = uuid
        self.original_domain_uuid = None
        self.verbose = verbose
        self.password = password
        self.show_logs = show_logs
        self.attach = attach
        self.wait_for_vm = wait
        self.direct_connection = direct
        self.conn = None
        self._pending_password = None
        self.domain = None
        self.window = None
        self.list_window = None
        self.is_fullscreen = False
        self.scaling_enabled = False
        self.smoothing_enabled = True
        self.lossy_encoding_enabled = False
        self.view_only_enabled = False
        self.vnc_depth = 0
        self.display_widget = None # VNC or SPICE widget
        self.spice_session = None
        self.protocol = None # 'vnc' or 'spice'
        self.reconnect_pending = False
        self.fs_button = None
        self.info_bar = None
        self.info_bar_label = None
        self.events_registered = False
        self.snapshots_store = None
        self.snapshots_tree_view = None
        self.attached_usb_store = None
        self.attached_usb_tree_view = None
        self.host_usb_store = None
        self.host_usb_tree_view = None
        self.attach_usb_button = None
        self.detach_usb_button = None
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.clipboard_update_in_progress = False
        self.last_clipboard_content = None
        self.ssh_tunnel_process = None
        self.ssh_tunnel_local_port = None
        self.ssh_tunnel_active = False
        self.ssh_gateway = None
        self.ssh_gateway_port = None
        self.notification_timeout_id = None

    def show_error_dialog(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self.window if self.window else None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Error"
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def show_notification(self, message, message_type=Gtk.MessageType.INFO):
        if self.info_bar and self.info_bar_label:
            self.info_bar.set_message_type(message_type)
            self.info_bar_label.set_text(message)
            self.info_bar.set_revealed(True)

            # Cancel previous timeout if it exists
            if self.notification_timeout_id:
                GLib.source_remove(self.notification_timeout_id)
                self.notification_timeout_id = None

            # Set new timeout to hide after 5 seconds
            self.notification_timeout_id = GLib.timeout_add_seconds(5, self._hide_notification)

        elif message_type == Gtk.MessageType.ERROR:
            # Fallback if no window/infobar yet
            self.show_error_dialog(message)
        else:
            if self.verbose:
                print(f"Notification ({message_type}): {message}")

    def on_info_bar_response(self, bar, response):
        bar.set_revealed(False)

    def _hide_notification(self):
        if self.info_bar:
            self.info_bar.set_revealed(False)
        self.notification_timeout_id = None
        return False

    def log_message(self, message):
        timestamp = time.strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}\n"

        if self.verbose:
            print(full_msg.strip())

        if hasattr(self, 'log_buffer') and self.log_buffer:
            GLib.idle_add(self._append_log_safe, full_msg)

    def _append_log_safe(self, text):
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, text)
        # Auto-scroll
        mark = self.log_buffer.create_mark(None, end_iter, False)
        self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        return False

    def _libvirt_event_tick(self):
        try:
            libvirt.virEventRunDefaultImpl()
        except Exception:
            # If no events or error, just ignore
            pass
        return True # Continue calling

    def _find_free_port(self):
        """Find a free local port for SSH tunnel"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def _parse_ssh_uri(self):
        """Parse qemu+ssh URI to extract SSH gateway and port
        Examples:
          qemu+ssh://user@host/system -> gateway: user@host, port: 22
          qemu+ssh://user@host:999/system -> gateway: user@host, port: 999
        """
        if not self.uri or 'qemu+ssh' not in self.uri:
            return None, None
        
        import re
        # Pattern: qemu+ssh://[user@]host[:port]/path
        match = re.search(r'qemu\+ssh://([^@]+@)?([^/:]+)(?::(\d+))?', self.uri)
        if not match:
            return None, None
        
        user_part = match.group(1) if match.group(1) else ""
        host = match.group(2)
        port = match.group(3) if match.group(3) else "22"
        
        gateway = f"{user_part}{host}"
        return gateway, port

    def setup_ssh_tunnel(self):
        """Setup SSH tunnel for qemu+ssh:// connections"""
        if not self.uri or 'qemu+ssh' not in self.uri or self.direct_connection:
            return False

        try:
            # Parse SSH gateway from URI
            self.ssh_gateway, self.ssh_gateway_port = self._parse_ssh_uri()

            if not self.ssh_gateway:
                self.log_message("ERROR: Could not parse qemu+ssh URI")
                return False

            self.log_message(f"Detected remote SSH connection via {self.ssh_gateway}:{self.ssh_gateway_port}")

            # Find a free local port for the tunnel
            self.ssh_tunnel_local_port = self._find_free_port()

            self.log_message(f"SSH tunnel will use local port: {self.ssh_tunnel_local_port}")

            return True

        except Exception as e:
            self.log_message(f"ERROR: Failed to setup SSH tunnel: {e}")
            return False

    def start_ssh_tunnel(self, remote_host, remote_port):
        """Start the actual SSH tunnel process"""
        if not self.ssh_gateway or not self.ssh_tunnel_local_port:
            return False

        # Ensure any previous tunnel is stopped
        self.stop_ssh_tunnel()

        # SSH command: ssh -N -C -L local_port:remote_host:remote_port gateway -p gateway_port
        ssh_cmd = [
            'ssh', '-N', '-C', '-L',
            f'{self.ssh_tunnel_local_port}:{remote_host}:{remote_port}',
            self.ssh_gateway, '-p', self.ssh_gateway_port
        ]

        self.log_message(f"Starting SSH tunnel: {' '.join(ssh_cmd)}")
        self.ssh_tunnel_process = subprocess.Popen(ssh_cmd)
        return True

    def stop_ssh_tunnel(self, *args):
        """Terminate the SSH tunnel process if active."""
        if self.ssh_tunnel_process:
            self.log_message("Terminating SSH tunnel")
            self.ssh_tunnel_process.terminate()
            try:
                self.ssh_tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ssh_tunnel_process.kill()
            self.ssh_tunnel_process = None
            self.log_message("SSH tunnel terminated.")

    def register_domain_events(self):
        if not self.conn or not self.domain:
            return

        if self.events_registered:
            return

        try:
            # Lifecycle events
            self.conn.domainEventRegisterAny(
                self.domain, 
                libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                self._event_lifecycle_callback,
                None
            )

            # Graphics events
            self.conn.domainEventRegisterAny(
                self.domain,
                libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS,
                self._event_generic_callback,
                "Graphics"
            )

            # Reboot
            self.conn.domainEventRegisterAny(
                self.domain,
                libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                self._event_generic_callback,
                "Reboot"
            )

            # IO Error
            self.conn.domainEventRegisterAny(
                self.domain,
                libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR,
                self._event_generic_callback,
                "IO Error"
            )

            # Watchdog
            self.conn.domainEventRegisterAny(
                self.domain,
                libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG,
                self._event_generic_callback,
                "Watchdog"
            )

            # Start the event loop ticker
            GLib.timeout_add(100, self._libvirt_event_tick)
            self.events_registered = True
            self.log_message("Registered for libvirt domain events.")

        except Exception as e:
            self.log_message(f"Failed to register domain events: {e}")

    def _event_lifecycle_callback(self, conn, dom, event, detail, opaque):
        event_strs = {
            0: "Defined",
            1: "Undefined",
            2: "Started",
            3: "Suspended",
            4: "Resumed",
            5: "Stopped",
            6: "Shutdown",
            7: "PMSuspended",
            8: "Crashed"
        }
        event_type = event_strs.get(event, f"Unknown({event})")
        self.log_message(f"Event: Lifecycle - {event_type} (Detail: {detail})")

        if event_type == "Started":
            self.show_notification(f"VM '{dom.name()}' has started.", Gtk.MessageType.INFO)
            self.log_message("VM started, scheduling display connection...")
            # Schedule connection attempt (give some time for graphics to init)
            GLib.timeout_add(3000, self.connect_display)
        elif event_type == "Suspended":
            self.show_notification(f"VM '{dom.name()}' is suspended.", Gtk.MessageType.WARNING)
        elif event_type == "Resumed":
            self.show_notification(f"VM '{dom.name()}' has resumed.", Gtk.MessageType.INFO)
        elif event_type == "Stopped":
            self.show_notification(f"VM '{dom.name()}' has stopped.", Gtk.MessageType.ERROR)
        elif event_type == "Shutdown":
            self.show_notification(f"VM '{dom.name()}' has shut down.", Gtk.MessageType.INFO)
        elif event_type == "Crashed":
            self.show_notification(f"VM '{dom.name()}' has crashed.", Gtk.MessageType.ERROR)
        
        # Call this to update sensitivity of restore button if needed
        self.update_restore_button_sensitivity()


    def _event_generic_callback(self, conn, dom, *args):
        info = "Event"
        if args and isinstance(args[-1], str):
            info = args[-1]

        self.log_message(f"Event: {info} - {args[:-1]}")

    def get_config_path(self):
        config_dir = os.path.join(os.path.expanduser("~"), ".config", "virtui-manager")
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "remote-viewer-state.json")

    def load_state(self):
        try:
            with open(self.get_config_path(), 'r') as f:
                data = json.load(f)
                self.is_fullscreen = data.get("fullscreen", False)
                self.scaling_enabled = data.get("scaling", False)
                self.smoothing_enabled = data.get("smoothing", True)
                self.lossy_encoding_enabled = data.get("lossy_encoding", False)
                self.view_only_enabled = data.get("view_only", False)
                self.vnc_depth = data.get("vnc_depth", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            self.is_fullscreen = False
            self.scaling_enabled = False
            self.smoothing_enabled = True
            self.lossy_encoding_enabled = False
            self.view_only_enabled = False
            self.vnc_depth = 0

    def save_state(self):
        try:
            data = {
                "fullscreen": self.is_fullscreen,
                "scaling": self.scaling_enabled,
                "smoothing": self.smoothing_enabled,
                "lossy_encoding": self.lossy_encoding_enabled,
                "view_only": self.view_only_enabled,
                "vnc_depth": self.vnc_depth
            }
            with open(self.get_config_path(), 'w') as f:
                json.dump(data, f)
        except Exception as e:
            if self.verbose:
                print(f"Failed to save state: {e}")

    def do_activate(self):
        # Connection to libvirt
        try:
            if 'qemu+ssh' in self.uri and not self.direct_connection:
                self.setup_ssh_tunnel()
            self.conn = libvirt.open(self.uri)
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Error connecting to libvirt: {e}")
            sys.exit(1)
        except Exception as e:
            self.show_error_dialog(f"Connection error: {e}")
            sys.exit(1)

        if not self.domain_name and not self.uuid:
            self.show_vm_list()
        else:
            self.resolve_domain()
            self.show_viewer()

    def resolve_domain(self):
        try:
            if self.domain_name:
                self.domain = self.conn.lookupByName(self.domain_name)
            elif self.uuid:
                self.domain = self.conn.lookupByUUIDString(self.uuid)
             # Store the original domain UUID to prevent connecting to wrong VM
            if self.domain and not self.original_domain_uuid:
                self.original_domain_uuid = self.domain.UUIDString()
        except libvirt.libvirtError as e:
            self.log_message(f"Error finding domain: {e}")
            if self.verbose:
                print(f"Error finding domain: {e}")

    def show_vm_list(self):
        self.list_window = Gtk.Window(application=self, title="Select VM to Connect")
        self.list_window.set_default_size(400, 500)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_border_width(10)
        self.list_window.add(vbox)

        label = Gtk.Label(label="<b>Available VMs</b>", use_markup=True)
        vbox.pack_start(label, False, False, 0)

        # ListStore: Name, State, Protocol, Object
        store = Gtk.ListStore(str, str, str, object)

        try:
            # List defined domains (active and inactive)
            domains = self.conn.listAllDomains(0)
            for dom in domains:
                state_code = dom.info()[0]

                # Only show running (1) or paused (3) VMs
                if state_code not in [1, 3]:
                    continue

                state_str = "Running" if state_code == 1 else "Paused"

                # Detect protocol (VNC or SPICE)
                xml = dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
                proto = "Unknown"
                if "type='spice'" in xml:
                    proto = "SPICE"
                elif "type='vnc'" in xml:
                    proto = "VNC"

                store.append([dom.name(), state_str, proto, dom])
        except libvirt.libvirtError as e:
            print(f"Error listing domains: {e}")

        tree = Gtk.TreeView(model=store)

        renderer = Gtk.CellRendererText()
        col1 = Gtk.TreeViewColumn("Name", renderer, text=0)
        tree.append_column(col1)

        col2 = Gtk.TreeViewColumn("State", renderer, text=1)
        tree.append_column(col2)

        col3 = Gtk.TreeViewColumn("Protocol", renderer, text=2)
        tree.append_column(col3)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add(tree)
        vbox.pack_start(scroll, True, True, 0)

        connect_btn = Gtk.Button(label="Connect")
        connect_btn.connect("clicked", self.on_list_connect, tree)
        vbox.pack_start(connect_btn, False, False, 0)

        # Connect on double click
        tree.connect("row-activated", self.on_list_row_activated)

        self.list_window.show_all()

    def on_list_connect(self, btn, tree):
        selection = tree.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            self.domain = model[treeiter][3]
            self.domain_name = self.domain.name()
            self.original_domain_uuid = self.domain.UUIDString()
            self.list_window.destroy()
            self.show_viewer()

    def on_list_row_activated(self, tree, path, column):
        model = tree.get_model()
        treeiter = model.get_iter(path)
        if treeiter:
            self.domain = model[treeiter][3]
            self.domain_name = self.domain.name()
            self.list_window.destroy()
            self.show_viewer()

    def _wait_and_connect_cb(self):
        try:
            # Refresh domain info
            if self.original_domain_uuid:
                try:
                    self.domain = self.conn.lookupByUUIDString(self.original_domain_uuid)
                except libvirt.libvirtError:
                    pass # Keep using current domain object if lookup fails
            
            protocol, host, port, pwd = self.get_display_info()
            if not self.attach and (not host or not port):
                return True # Keep waiting
            
            self.show_notification("VM started! Connecting...", Gtk.MessageType.INFO)
            self.connect_display()
            return False
        except Exception as e:
            if self.verbose: print(f"Wait error: {e}")
            return True

    def show_viewer(self):
        # Allow viewer to start even if domain is not found
        # if not self.domain:
        #    self.show_error_dialog("No domain selected or domain not found.")
        #    return

        self.load_state()

        if self.domain:
            domain_name = self.domain.name()
        else:
            domain_name = self.domain_name or self.uuid or "Unknown VM"

        title = f"{domain_name} - Virtui Manager Viewer"
        subtitle = self.uri
        if self.attach:
            subtitle += " (Attached)"

        # Main Window (GTK3)
        self.window = Gtk.ApplicationWindow(application=self, title=title)
        self.window.set_default_size(1024, 768)

        # HeaderBar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(title)
        header.set_subtitle(subtitle)
        self.window.set_titlebar(header)

        # --- Settings Menu ---
        settings_button = Gtk.MenuButton()
        icon_settings = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        settings_button.set_image(icon_settings)
        settings_button.set_tooltip_text("Settings")

        settings_popover = Gtk.Popover()
        vbox_settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox_settings.set_margin_top(10)
        vbox_settings.set_margin_bottom(10)
        vbox_settings.set_margin_start(10)
        vbox_settings.set_margin_end(10)

        # Scaling Checkbox
        scaling_check = Gtk.CheckButton(label="Scaling (Resize)")
        scaling_check.set_active(self.scaling_enabled)
        scaling_check.connect("toggled", self.on_scaling_toggled)
        vbox_settings.pack_start(scaling_check, False, False, 0)

        # Smoothing Checkbox
        self.smoothing_check = Gtk.CheckButton(label="Smoothing (Interpolation)")
        self.smoothing_check.set_active(self.smoothing_enabled)
        self.smoothing_check.connect("toggled", self.on_smoothing_toggled)
        vbox_settings.pack_start(self.smoothing_check, False, False, 0)

        # Lossy Encoding Checkbox
        self.lossy_check = Gtk.CheckButton(label="Lossy Compression (JPEG)")
        self.lossy_check.set_active(self.lossy_encoding_enabled)
        self.lossy_check.connect("toggled", self.on_lossy_toggled)
        vbox_settings.pack_start(self.lossy_check, False, False, 0)

        # View Only Checkbox
        view_only_check = Gtk.CheckButton(label="View Only Mode")
        view_only_check.set_active(self.view_only_enabled)
        view_only_check.connect("toggled", self.on_view_only_toggled)
        vbox_settings.pack_start(view_only_check, False, False, 0)

        # Color Depth Selector
        self.depth_settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        depth_label = Gtk.Label(label="Color Depth:")
        self.depth_settings_box.pack_start(depth_label, False, False, 0)

        depth_combo = Gtk.ComboBoxText()
        depth_combo.append("0", "Default")
        depth_combo.append("8", "8-bit")
        depth_combo.append("16", "16-bit")
        depth_combo.append("24", "24-bit")
        depth_combo.set_active_id(str(self.vnc_depth))
        depth_combo.connect("changed", self.on_depth_changed)
        self.depth_settings_box.pack_start(depth_combo, True, True, 0)
        vbox_settings.pack_start(self.depth_settings_box, False, False, 0)

        vbox_settings.show_all()
        settings_popover.add(vbox_settings)
        settings_button.set_popover(settings_popover)
        header.pack_end(settings_button)

        # --- Power Menu ---
        power_button = Gtk.MenuButton()
        icon_power = Gtk.Image.new_from_icon_name("system-shutdown-symbolic", Gtk.IconSize.BUTTON)
        power_button.set_image(icon_power)
        power_button.set_tooltip_text("VM Power Control")

        power_popover = Gtk.Popover()
        power_button.set_popover(power_popover)
        power_popover.connect("show", self.on_power_menu_show)

        vbox_power = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.power_buttons = {}
        power_actions = [
            ("Start", "media-playback-start-symbolic", self.on_power_start),
            ("Pause", "media-playback-pause-symbolic", self.on_power_pause),
            ("Resume", "media-playback-start-symbolic", self.on_power_resume),
            ("Graceful Shutdown", "system-shutdown-symbolic", self.on_power_shutdown),
            ("Reboot", "system-reboot-symbolic", self.on_power_reboot),
            ("Force Power Off", "system-shutdown-symbolic", self.on_power_destroy),
        ]

        for label, icon_name, callback in power_actions:
            btn = Gtk.ModelButton()
            btn.set_label(label)
            btn.connect("clicked", callback, power_popover)
            vbox_power.pack_start(btn, False, False, 0)
            self.power_buttons[label] = btn

        vbox_power.show_all()
        power_popover.add(vbox_power)
        power_button.set_popover(power_popover)
        header.pack_end(power_button)

        # --- Send Keys Menu ---
        keys_button = Gtk.MenuButton()
        icon_keys = Gtk.Image.new_from_icon_name("input-keyboard-symbolic", Gtk.IconSize.BUTTON)
        keys_button.set_image(icon_keys)
        keys_button.set_tooltip_text("Send Key")

        keys_popover = Gtk.Popover()
        vbox_keys = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        key_combinations = [
            ("Ctrl+Alt+Del", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_Delete]),
            ("Ctrl+Alt+Backspace", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_BackSpace]),
            ("Ctrl+Alt+F1", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F1]),
            ("Ctrl+Alt+F2", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F2]),
            ("Ctrl+Alt+F3", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F3]),
            ("Ctrl+Alt+F7", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F7]),
            ("PrintScreen", [Gdk.KEY_Print]),
        ]

        for label, keys in key_combinations:
            btn = Gtk.ModelButton()
            btn.set_label(label)
            btn.connect("clicked", self.on_send_key, keys, keys_popover)
            vbox_keys.pack_start(btn, False, False, 0)

        vbox_keys.show_all()
        keys_popover.add(vbox_keys)
        keys_button.set_popover(keys_popover)
        header.pack_end(keys_button)

        # --- Clipboard Menu ---
        clip_button = Gtk.MenuButton()
        icon_clip = Gtk.Image.new_from_icon_name("edit-paste-symbolic", Gtk.IconSize.BUTTON)
        clip_button.set_image(icon_clip)
        clip_button.set_tooltip_text("Clipboard Actions")

        clip_popover = Gtk.Popover()
        vbox_clip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Type Clipboard Option
        btn_type_clip = Gtk.ModelButton()
        btn_type_clip.set_label("Type Clipboard (as keys)")
        btn_type_clip.connect("clicked", self.on_type_clipboard, clip_popover)
        vbox_clip.pack_start(btn_type_clip, False, False, 0)

        # Manual Pull
        btn_pull_clip = Gtk.ModelButton()
        btn_pull_clip.set_label("Pull Guest Clipboard to Host")
        btn_pull_clip.connect("clicked", self.on_pull_clipboard, clip_popover)
        #vbox_clip.pack_start(btn_pull_clip, False, False, 0)

        # Manual Push
        btn_push_clip = Gtk.ModelButton()
        btn_push_clip.set_label("Push Host Clipboard to Guest")
        btn_push_clip.connect("clicked", self.on_push_clipboard, clip_popover)
        #vbox_clip.pack_start(btn_push_clip, False, False, 0)

        vbox_clip.show_all()
        clip_popover.add(vbox_clip)
        clip_button.set_popover(clip_popover)
        header.pack_end(clip_button)

        # --- Screenshot Button ---
        screenshot_button = Gtk.Button()
        icon_screenshot = Gtk.Image.new_from_icon_name("camera-photo-symbolic", Gtk.IconSize.BUTTON)
        screenshot_button.set_image(icon_screenshot)
        screenshot_button.set_tooltip_text("Take Screenshot")
        screenshot_button.connect("clicked", self.on_screenshot_clicked)
        header.pack_end(screenshot_button)

        # --- Reconnect Button ---
        reconnect_button = Gtk.Button()
        icon_reconnect = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        reconnect_button.set_image(icon_reconnect)
        reconnect_button.set_tooltip_text("Reconnect Display")
        reconnect_button.connect("clicked", self.on_reconnect_clicked)
        header.pack_end(reconnect_button)

        # --- Fullscreen Button ---
        self.fs_button = Gtk.ToggleButton()
        icon_fs = Gtk.Image.new_from_icon_name("view-fullscreen-symbolic", Gtk.IconSize.BUTTON)
        self.fs_button.set_image(icon_fs)
        self.fs_button.set_tooltip_text("Toggle Fullscreen")
        self.fs_button.set_active(self.is_fullscreen)
        self.fs_button.connect("toggled", self.on_fs_button_toggled)
        header.pack_end(self.fs_button)

        # --- Logs Toggle Button ---
        self.logs_button = Gtk.ToggleButton()
        icon_logs = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic", Gtk.IconSize.BUTTON)
        self.logs_button.set_image(icon_logs)
        self.logs_button.set_tooltip_text("Toggle Logs & Events")
        self.logs_button.set_active(self.show_logs)
        self.logs_button.connect("toggled", self.on_logs_toggled)
        header.pack_end(self.logs_button)

        # Main Layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.window.add(self.main_box)

        # InfoBar for notifications
        self.info_bar = Gtk.InfoBar()
        self.info_bar.set_revealed(False)
        self.info_bar.set_show_close_button(True)
        self.info_bar.connect("response", self.on_info_bar_response)

        content = self.info_bar.get_content_area()
        self.info_bar_label = Gtk.Label()
        self.info_bar_label.set_line_wrap(True)
        content.add(self.info_bar_label)

        self.main_box.pack_start(self.info_bar, False, False, 0)

        # Tabs (Always created now)
        self.notebook = Gtk.Notebook()
        self.main_box.pack_start(self.notebook, True, True, 0)

        # Tab 1: Display
        self.display_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.notebook.append_page(self.display_tab, Gtk.Label(label="Display"))
        self.view_container = self.display_tab

        # Tab 2: Snapshots
        self.snapshots_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.snapshots_tab.set_border_width(10)
        self.notebook.append_page(self.snapshots_tab, Gtk.Label(label="Snapshots"))

        # Snapshot list store and treeview
        # name, description, creation_time, state, libvirt_snapshot_object
        self.snapshots_store = Gtk.ListStore(str, str, str, str, object)
        self.snapshots_tree_view = Gtk.TreeView(model=self.snapshots_store)

        self._add_snapshots_tree_columns()

        scroll_snapshots = Gtk.ScrolledWindow()
        scroll_snapshots.set_vexpand(True)
        scroll_snapshots.add(self.snapshots_tree_view)
        self.snapshots_tab.pack_start(scroll_snapshots, True, True, 0)

        # Snapshot actions buttons
        action_buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.snapshots_tab.pack_start(action_buttons_box, False, False, 0)
 
        self.create_snapshot_button = Gtk.Button(label="Create Snapshot")
        self.create_snapshot_button.connect("clicked", self.on_create_snapshot_clicked)
        action_buttons_box.pack_start(self.create_snapshot_button, True, True, 0)
 
        self.delete_snapshot_button = Gtk.Button(label="Delete Snapshot")
        self.delete_snapshot_button.connect("clicked", self.on_delete_snapshot_clicked)
        self.delete_snapshot_button.set_sensitive(False) # Initially insensitive
        action_buttons_box.pack_start(self.delete_snapshot_button, True, True, 0)
 
        self.restore_snapshot_button = Gtk.Button(label="Restore Snapshot")
        self.restore_snapshot_button.connect("clicked", self.on_restore_snapshot_clicked)
        self.restore_snapshot_button.set_sensitive(False) # Initially insensitive
        action_buttons_box.pack_start(self.restore_snapshot_button, True, True, 0)
 
        # Refresh button (keeping it separate or integrating as preferred)
        refresh_button = Gtk.Button(label="Refresh Snapshots")
        refresh_button.connect("clicked", self.on_refresh_snapshots_clicked)
        action_buttons_box.pack_start(refresh_button, True, True, 0)
 
        # Connect selection change to update button sensitivity
        selection = self.snapshots_tree_view.get_selection()
        selection.connect("changed", self.on_snapshots_selection_changed)

        # Tab 3: USB Devices
        self.usb_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.usb_tab.set_border_width(10)
        self.notebook.append_page(self.usb_tab, Gtk.Label(label="USB Devices"))

        # Attached USB Devices
        self.usb_tab.pack_start(Gtk.Label(label="<b>Attached USB Devices</b>", use_markup=True), False, False, 0)
        self.attached_usb_store = Gtk.ListStore(str, str, str, str, str) # vendor_id, product_id, vendor_name, product_name, description
        self.attached_usb_tree_view = Gtk.TreeView(model=self.attached_usb_store)
        self._add_usb_tree_columns(self.attached_usb_tree_view)
        scroll_attached_usb = Gtk.ScrolledWindow()
        scroll_attached_usb.set_vexpand(True)
        scroll_attached_usb.add(self.attached_usb_tree_view)
        self.usb_tab.pack_start(scroll_attached_usb, True, True, 0)

        # Host USB Devices
        self.usb_tab.pack_start(Gtk.Label(label="<b>Available Host USB Devices</b>", use_markup=True), False, False, 0)
        self.host_usb_store = Gtk.ListStore(str, str, str, str, str) # vendor_id, product_id, vendor_name, product_name, description
        self.host_usb_tree_view = Gtk.TreeView(model=self.host_usb_store)
        self._add_usb_tree_columns(self.host_usb_tree_view)
        scroll_host_usb = Gtk.ScrolledWindow()
        scroll_host_usb.set_vexpand(True)
        scroll_host_usb.add(self.host_usb_tree_view)
        self.usb_tab.pack_start(scroll_host_usb, True, True, 0)

        # USB Action Buttons
        usb_action_buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.usb_tab.pack_start(usb_action_buttons_box, False, False, 0)

        self.attach_usb_button = Gtk.Button(label="Attach USB Device")
        self.attach_usb_button.connect("clicked", self.on_attach_usb_clicked)
        self.attach_usb_button.set_sensitive(False)
        usb_action_buttons_box.pack_start(self.attach_usb_button, True, True, 0)

        self.detach_usb_button = Gtk.Button(label="Detach USB Device")
        self.detach_usb_button.connect("clicked", self.on_detach_usb_clicked)
        self.detach_usb_button.set_sensitive(False)
        usb_action_buttons_box.pack_start(self.detach_usb_button, True, True, 0)

        refresh_usb_button = Gtk.Button(label="Refresh USB Lists")
        refresh_usb_button.connect("clicked", self.on_refresh_usb_lists_clicked)
        usb_action_buttons_box.pack_start(refresh_usb_button, True, True, 0)

        # Connect selection changes for sensitivity
        self.attached_usb_tree_view.get_selection().connect("changed", self.on_attached_usb_selection_changed)
        self.host_usb_tree_view.get_selection().connect("changed", self.on_host_usb_selection_changed)
        # Connect tab-select signal to populate snapshots when the tab is switched to
        self.notebook.connect("switch-page", self.on_notebook_switch_page)

        # Tab 4: Logs & Events
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()

        self.log_scroll = Gtk.ScrolledWindow()
        self.log_scroll.add(self.log_view)

        # We append it, but might hide it
        self.notebook.append_page(self.log_scroll, Gtk.Label(label="Logs & Events"))

        self.update_logs_visibility()

        # Init display (VNC or SPICE)
        self.init_display()

        # Fullscreen management via key-press-event signal
        self.window.connect("key-press-event", self.on_key_press)
        # Ensure SSH tunnel is stopped if window is destroyed
        self.window.connect("destroy", self.stop_ssh_tunnel)

        # Apply initial fullscreen state
        if self.is_fullscreen:
            self.window.fullscreen()

        # Display
        self.window.show_all()
        # Hide info bar initially (show_all reveals it)
        self.info_bar.set_revealed(False)
        self.window.present()

        # Register Events
        self.register_domain_events()

        # Connection
        if self.wait_for_vm:
            protocol, host, port, pwd = self.get_display_info()
            if not self.attach and (not host or not port):
                self.show_notification("Waiting for VM to start...", Gtk.MessageType.INFO)
                GLib.timeout_add_seconds(3, self._wait_and_connect_cb)
                return
        else: # Check current state if not waiting for VM to start
            if self.domain:
                try:
                    state, _ = self.domain.state()
                    if state == libvirt.VIR_DOMAIN_PAUSED:
                        self.show_notification(f"VM '{self.domain.name()}' is paused.", Gtk.MessageType.WARNING)
                    elif state == libvirt.VIR_DOMAIN_RUNNING:
                        self.show_notification(f"VM '{self.domain.name()}' is running.", Gtk.MessageType.INFO)
                    elif state == libvirt.VIR_DOMAIN_SHUTOFF or state == libvirt.VIR_DOMAIN_SHUTDOWN:
                        self.show_notification(f"VM '{self.domain.name()}' is shut off.", Gtk.MessageType.INFO)
                except libvirt.libvirtError as e:
                    self.show_notification(f"Could not determine VM state: {e}", Gtk.MessageType.ERROR)
            else:
                 self.show_notification("VM domain not found/loaded.", Gtk.MessageType.WARNING)

        self.connect_display()

    def _setup_tunnel_if_needed(self, listen, port):
        # Stop any existing tunnel before potentially starting a new one.
        self.stop_ssh_tunnel()

        # If SSH tunnel is configured, setup tunnel for this specific port
        if self.ssh_gateway: # Removed `self.ssh_tunnel_process is None` as stop_ssh_tunnel handles it
            # Start tunnel to the actual remote host/port
            remote_host = listen if not self.direct_connection else None
            if listen == 'localhost' or listen == '0.0.0.0':
                # Extract remote host from libvirt URI
                import re
                match = re.search(r'qemu\+ssh://(?:[^@]+@)?([^/:]+)', self.uri)
                if match:
                    remote_host = match.group(1)

            if remote_host and not self.direct_connection:
                self.start_ssh_tunnel(remote_host, port)
            elif self.direct_connection:
                self.log_message("Direct connection mode: Skipping SSH tunnel")
 
    def get_display_info(self):
        """Retrieve connection info (protocol, host, port, password)"""
        if not self.domain:
            return None, None, None, None

        try:
            xml_desc = self.domain.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
            root = ET.fromstring(xml_desc)

            def get_graphics_info(g_node):
                if g_node is None: return None
                port = g_node.get('port')
                if not port or port == '-1':
                    port = g_node.get('tlsPort')
                
                listen = g_node.get('listen')
                if not listen or listen == '0.0.0.0':
                    listen = 'localhost'
                
                password = g_node.get('passwd')
                
                if port and port != '-1':
                    return listen, port, password
                return None

            # Check SPICE (only if client is available)
            if SPICE_AVAILABLE:
                info = get_graphics_info(root.find(".//graphics[@type='spice']"))
                if info:
                    listen, port, password = info
                    self._setup_tunnel_if_needed(listen, port)
                    return 'spice', listen, port, password

            # Check VNC
            info = get_graphics_info(root.find(".//graphics[@type='vnc']"))
            if info:
                listen, port, password = info
                self._setup_tunnel_if_needed(listen, port)
                return 'vnc', listen, port, password

        except Exception as e:
            msg = f"XML parse error: {e}"
            self.log_message(msg)
            if self.verbose:
                print(msg)
        return None, None, None, None

    def init_display(self):
        """Initialize the display widget based on available protocol"""
        # Cleanup existing display widget and container
        if self.display_widget:
            # Check for parent ScrolledWindow and remove it
            parent = self.display_widget.get_parent()
            if parent and isinstance(parent, Gtk.ScrolledWindow):
                if parent.get_parent() == self.view_container:
                     self.view_container.remove(parent)
                parent.destroy()
            elif parent == self.view_container:
                self.view_container.remove(self.display_widget)
            
            self.display_widget.destroy()
            self.display_widget = None

        # Disconnect previous clipboard handler if exists
        if hasattr(self, 'clipboard_handler_id') and self.clipboard_handler_id:
             if self.clipboard.handler_is_connected(self.clipboard_handler_id):
                 self.clipboard.disconnect(self.clipboard_handler_id)
             self.clipboard_handler_id = None

        protocol, host, port, password_required = self.get_display_info()
        self.protocol = protocol

        msg = f"Initializing display for protocol: {protocol}"
        self.log_message(msg)
        if self.verbose:
             print(msg)

        if protocol is None:
            self.log_message("No display protocol detected. Skipping display initialization for now.")
            return

        scroll = Gtk.ScrolledWindow()

        if protocol == 'spice' and SPICE_AVAILABLE:
            self.depth_settings_box.set_visible(False)
            self.lossy_check.set_visible(False)
            self.spice_session = SpiceClientGLib.Session()

            try:
                self.spice_gtk_session = SpiceClientGtk.GtkSession.get(self.spice_session)
                self.spice_gtk_session.set_property("auto-clipboard", True)
            except Exception as e:
                if self.verbose:
                    print(f"Failed to configure SPICE clipboard: {e}")

            self.display_widget = SpiceClientGtk.Display(session=self.spice_session)
            # SPICE specific configs?
            self.display_widget.set_property("scaling", self.scaling_enabled)
        else:
            # Default to VNC
            GLib.MainContext.default().iteration(False)
            self.depth_settings_box.set_visible(True)
            self.lossy_check.set_visible(True)
            self.protocol = 'vnc' # Fallback if spice not available
            self.vnc_display = GtkVnc.Display()
            self.display_widget = self.vnc_display

            self.vnc_display.set_pointer_local(True)
            self.vnc_display.set_scaling(self.scaling_enabled)
            self.vnc_display.set_smoothing(self.smoothing_enabled)
            self.vnc_display.set_keep_aspect_ratio(True)
            self.vnc_display.set_lossy_encoding(self.lossy_encoding_enabled)
            self.vnc_display.set_read_only(self.view_only_enabled)
            self._apply_vnc_depth()

            # Signals
            self.vnc_display.connect("vnc-disconnected", self.on_vnc_disconnected)
            self.vnc_display.connect("vnc-connected", self.on_vnc_connected)
            self.vnc_display.connect("vnc-auth-credential", self.on_vnc_auth_credential)
            self.vnc_display.connect("vnc-server-cut-text", self.on_vnc_server_cut_text)

            # Local clipboard -> VNC
            self.clipboard_handler_id = self.clipboard.connect("owner-change", self.on_clipboard_owner_change)

        scroll.add(self.display_widget)
        self.view_container.pack_start(scroll, True, True, 0)
        self.view_container.show_all()

    def connect_display(self, force=False, password=None):
        """Attempts connection"""
        # Refresh domain object to ensure we have the latest handle (especially for live XML)
        if self.original_domain_uuid and self.conn:
            try:
                # We need to refresh self.domain to ensure we are querying the running instance
                # which has the assigned VNC/SPICE port in its XML.
                self.domain = self.conn.lookupByUUIDString(self.original_domain_uuid)
                
                # Log state for debugging
                try:
                    state_str = vm_queries.get_status(self.domain)
                    self.log_message(f"Debug: Domain refreshed. Current State: {state_str}")
                except Exception as ex:
                    self.log_message(f"Debug: Could not get VM state: {ex}")

            except libvirt.libvirtError as e:
                self.log_message(f"Warning: Could not refresh domain object: {e}")

        # Safety check: ensure we're still connecting to the same VM
        if self.original_domain_uuid and self.domain:
            try:
                current_uuid = self.domain.UUIDString()
                self.log_message(f"INFO: {current_uuid}")
                if current_uuid != self.original_domain_uuid:
                    self.log_message(f"ERROR: Domain UUID mismatch! Expected {self.original_domain_uuid}, got {current_uuid}")
                    self.show_error_dialog(f"Security error: Domain UUID changed. Refusing to connect.")
                    return False
            except libvirt.libvirtError:
                pass

        protocol, host, port, xml_password_required = self.get_display_info()

        if not protocol:
            self.log_message("Connection skipped: No display protocol detected.")
            return False

        if not self.display_widget:
            self.log_message(f"Display widget not initialized, initializing now for {protocol}...")
            self.init_display()

        # If attaching, we don't strictly need host/port if libvirt handles the FD,
        # but we need to know the protocol (which init_display determined).
        if not self.attach and (not host or not port):
            if self.verbose:
                print("Display not ready (VM stopped?). Waiting.")
            return False

        if self.verbose:
            msg = f"Connecting to {protocol}"
            if self.attach:
                msg += " (via libvirt direct attach)"
            else:
                msg += f" at {host}:{port}"
            print(msg)

        # Priority: Command line password > XML password
        if self.password:
            password = self.password
            self.log_message("Debug: Using password from command line.")
        elif xml_password_required:
            password = xml_password_required
            self.log_message("Debug: Using password from VM XML.")
        else:
            # No password required - set to None to skip auth
            password = None
            self.log_message("Debug: No password provided via command line or found in VM XML.")

            password = xml_password_required

        self._pending_password = password

        try:
            if self.attach:
                # Direct attach using libvirt FD
                try:
                    # Request the FD for the first graphics device (0)
                    fd = self.domain.openGraphicsFD(0)
                except libvirt.libvirtError as e:
                    self.show_error_dialog(f"Failed to attach to graphics: {e}")
                    return True

                if self.protocol == 'spice' and SPICE_AVAILABLE:
                    self.spice_session.open_fd(fd)
                elif self.protocol == 'vnc':
                    if self.vnc_display.is_open() and force:
                         self.vnc_display.close()
                    # Ensure no pending reconnect
                    self.reconnect_pending = False
                    self._apply_vnc_depth()
                    self.vnc_display.open_fd(fd)

                return False

            # Standard Network Connection
            if self.protocol == 'spice' and SPICE_AVAILABLE:
                # Spice connection
                # Use tunneled connection if SSH tunnel is active and not in direct mode
                if self.ssh_gateway and self.ssh_tunnel_local_port and not self.direct_connection:
                    host = 'localhost'
                    port = self.ssh_tunnel_local_port
                    self.log_message(f"Using SSH tunnel: localhost:{port}")
                    # Give tunnel time to establish
                    time.sleep(2)
                uri = f"spice://{host}:{port}"
                self.log_message(f"Connecting to SPICE at {uri}")
                self.spice_session.set_property("uri", uri)
                if password:
                    self.spice_session.set_property("password", password)
                try:
                    self.spice_session.connect()
                except Exception as e:
                    error_msg = f"Failed to connect to SPICE server at {host}:{port}\n\nError: {e}"
                    self.log_message(f"ERROR: {error_msg}")
                    self.show_notification(error_msg)
                    return False

            else:
                # VNC connection
                # Use tunneled connection if SSH tunnel is active and not in direct mode
                if self.ssh_gateway and self.ssh_tunnel_local_port and not self.direct_connection:
                    host = 'localhost'
                    port = self.ssh_tunnel_local_port
                    self.log_message(f"Using SSH tunnel: localhost:{port}")
                    # Give tunnel time to establish
                    time.sleep(2)
                self.log_message(f"Connecting to VNC at {host}:{port}")
                if self.vnc_display.is_open():
                    if force:
                        if self.verbose: print("Forcing reconnection (closing first)...")
                        self.reconnect_pending = True
                        self.vnc_display.close()
                    return False

                # Ensure no pending reconnect if we are opening normally
                self.reconnect_pending = False
                # Re-apply depth setting before connecting to ensure it takes effect
                self._apply_vnc_depth()

                self.vnc_display.open_host(host, str(port))

                return False # Stop retrying
        except Exception as e:
            if self.verbose:
                print(f"Connection failed: {e}")
            self.show_notification(f"Connection failed: {e}", Gtk.MessageType.ERROR)
            return False

    def _prompt_for_password(self, protocol):
        dialog = Gtk.Dialog(
            title=f"{protocol.upper()} Password Required",
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)

        hbox = Gtk.Box(spacing=6)
        dialog.get_content_area().pack_start(hbox, True, True, 0)

        label = Gtk.Label(label=f"Enter password for {self.domain_name} ({protocol.upper()}):")
        hbox.pack_start(label, False, False, 0)

        password_entry = Gtk.Entry()
        password_entry.set_visibility(False)
        password_entry.set_invisible_char("*")
        hbox.pack_start(password_entry, True, True, 0)

        dialog.show_all()
        response = dialog.run()
        password = password_entry.get_text()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            return password
        return None

    def on_vnc_auth_credential(self, vnc, cred_list):
        if self.verbose:
            print(f"VNC Auth Credential requested")

        password = self._pending_password
        # If no password was set (XML has no passwd attribute), VNC shouldn't ask for auth
        # but if it does, prompt the user
        if password is None:
            # No password in XML, but VNC is asking - prompt user
            password = self._prompt_for_password("VNC")

        if password:
            self.vnc_display.set_credential(GtkVnc.DisplayCredential.PASSWORD, password)
        else:
            self.vnc_display.close()

    def on_vnc_connected(self, vnc):
        if self.verbose: print("VNC Connected")

    def on_vnc_disconnected(self, vnc):
        if self.verbose: print("VNC Disconnected")

        if self.reconnect_pending:
            if self.verbose: print("Pending reconnect detected, reconnecting in 1500ms...")
            self.reconnect_pending = False
            # Increase delay to 1500ms to ensure socket cleanup
            GLib.timeout_add(1500, self.connect_display)
            return

        self.check_shutdown()

    def on_vnc_server_cut_text(self, vnc, text):
        if self.verbose:
            print(f"VNC Server Cut Text: {len(text)} chars")

        if text != self.last_clipboard_content:
            self.last_clipboard_content = text
            self.log_message(f"Clipboard: Received {len(text)} characters from guest (VNC)")

            try:
                # Avoid triggering the owner-change signal loop via flag
                self.clipboard_update_in_progress = True
                self.clipboard.set_text(text, -1)
                self.clipboard.store()
            finally:
                self.clipboard_update_in_progress = False

            self.show_notification(f"Clipboard updated from guest ({len(text)} chars).")

    def on_clipboard_owner_change(self, clipboard, event):
        if self.clipboard_update_in_progress:
            return

        if self.protocol == 'vnc' and self.vnc_display and self.vnc_display.is_open():
            # Use async request to avoid blocking UI
            clipboard.request_text(self._on_clipboard_text_received)

    def _on_clipboard_text_received(self, clipboard, text):
        if not text:
            return

        if self.protocol == 'vnc' and self.vnc_display and self.vnc_display.is_open():
            if text != self.last_clipboard_content:
                self.last_clipboard_content = text
                if self.verbose:
                    print(f"Clipboard Owner Change: Sending {len(text)} chars to VNC")
                self.vnc_display.client_cut_text(text)
                self.log_message(f"Clipboard: Sent {len(text)} characters to guest (VNC)")

    def on_push_clipboard(self, button, popover):
        popover.popdown()
        text = self.clipboard.wait_for_text()
        if text:
            if self.protocol == 'vnc' and self.vnc_display and self.vnc_display.is_open():
                self.vnc_display.client_cut_text(text)
                self.log_message(f"Clipboard: Manually pushed {len(text)} characters to guest (VNC)")
            elif self.protocol == 'spice' and self.spice_gtk_session:
                # Spice auto-clipboard should handle it, but we can try to force it if needed
                self.log_message("Clipboard: SPICE auto-clipboard should sync automatically.")
        else:
            self.show_notification("Local clipboard is empty.", Gtk.MessageType.WARNING)

    def on_pull_clipboard(self, button, popover):
        popover.popdown()
        if self.last_clipboard_content:
            text = self.last_clipboard_content
            try:
                self.clipboard_update_in_progress = True
                self.clipboard.set_text(text, -1)
                self.clipboard.store()
            finally:
                self.clipboard_update_in_progress = False
            self.log_message(f"Clipboard: Manually pulled {len(text)} characters from cache to host")
            self.show_notification(f"Restored {len(text)} chars from guest clipboard cache.")
        else:
            self.show_notification("No clipboard content received from guest yet.", Gtk.MessageType.INFO)

    def check_shutdown(self):
        # Poll VM state for a few seconds to detect shutdown
        if self.original_domain_uuid and self.domain:
            try:
                current_uuid = self.domain.UUIDString()
                if current_uuid == self.original_domain_uuid:
                    GLib.timeout_add_seconds(1, self._check_shutdown_async, 0)
            except libvirt.libvirtError:
                self.show_notification("ERROR: Domain invalid", Gtk.MessageType.ERROR)
                self.quit()

    def _check_shutdown_async(self, counter):
        try:
            if not self.domain.isActive():
                if self.verbose: print("VM is shutdown. Exiting...")
                self.show_notification("VM has shut down. You can restart it from the Power menu.", Gtk.MessageType.INFO)
                return False
        except:
            self.quit()
            return False

        # Verify we're still checking the same domain
        try:
            if self.domain.UUIDString() != self.original_domain_uuid:
                self.log_message("ERROR: Domain UUID changed during reconnect check!")
                return False
        except libvirt.libvirtError:
            self.quit()
            return False

        if counter < 10: # Try for 10 seconds
            GLib.timeout_add_seconds(1, self._check_shutdown_async, counter + 1)
            return False # Don't repeat *this* specific call scheduler

        if self.verbose:
            print("VM still active after disconnect (Reboot or Network issue?). Reconnecting...")

        # Auto-reconnect if VM is still running
        self.connect_display()
        return False

    def on_key_press(self, widget, event):
        if (event.keyval == Gdk.KEY_f or event.keyval == Gdk.KEY_F) and (event.state & Gdk.ModifierType.CONTROL_MASK):
            self.fs_button.set_active(not self.fs_button.get_active())
            return True
        return False

    def on_fs_button_toggled(self, button):
        self.is_fullscreen = button.get_active()
        if self.is_fullscreen:
            self.window.fullscreen()
        else:
            self.window.unfullscreen()
        self.save_state()

    def on_scaling_toggled(self, button):
        self.scaling_enabled = button.get_active()
        if self.protocol == 'vnc' and self.vnc_display:
            self.vnc_display.set_scaling(self.scaling_enabled)
        elif self.protocol == 'spice' and self.display_widget:
            self.display_widget.set_property("scaling", self.scaling_enabled)
        self.save_state()

    def on_smoothing_toggled(self, button):
        self.smoothing_enabled = button.get_active()
        if self.protocol == 'vnc' and self.vnc_display:
            self.vnc_display.set_smoothing(self.smoothing_enabled)
        self.save_state()

    def on_lossy_toggled(self, button):
        self.lossy_encoding_enabled = button.get_active()
        if self.protocol == 'vnc' and self.vnc_display:
            self.vnc_display.set_lossy_encoding(self.lossy_encoding_enabled)
        self.save_state()

    def on_logs_toggled(self, button):
        self.show_logs = button.get_active()
        self.update_logs_visibility()

    def update_logs_visibility(self):
        if not hasattr(self, 'notebook'):
            return

        # Page numbers: 0=Display, 1=Snapshots, 2=USB Devices, 3=Logs & Events
        snapshots_page_num = 1
        usb_page_num = 2
        logs_page_num = 3

        if self.show_logs:
            self.notebook.get_nth_page(logs_page_num).show()
            self.notebook.get_nth_page(snapshots_page_num).show() # Show snapshots too if logs are visible
            self.notebook.get_nth_page(usb_page_num).show() # Show USB tab
            self.notebook.set_show_tabs(True)
        else:
            self.notebook.get_nth_page(logs_page_num).hide()
            self.notebook.get_nth_page(snapshots_page_num).hide()
            self.notebook.get_nth_page(usb_page_num).hide() # Hide USB tab
            self.notebook.set_show_tabs(False)
            
        # Always default to Display tab when toggling or at startup
        self.notebook.set_current_page(0)

    def on_view_only_toggled(self, button):
        self.view_only_enabled = button.get_active()
        if self.protocol == 'vnc' and self.vnc_display:
            self.vnc_display.set_read_only(self.view_only_enabled)
        self.save_state()

    def on_reconnect_clicked(self, button):
        if self.protocol == 'vnc' and self.vnc_display:
            # Force disconnect and reconnect (will go through on_vnc_disconnected -> connect_display)
            self.connect_display(force=True)
        elif self.protocol == 'spice' and self.spice_session:
            if self.spice_session.is_connected():
                self.spice_session.disconnect()
                # Schedule reconnection after disconnect
                GLib.timeout_add(1500, self.connect_display)
            else:
                self.connect_display()


    def on_power_menu_show(self, popover):
        try:
            if self.domain:
                state, reason = self.domain.state()
            else:
                state = libvirt.VIR_DOMAIN_NOSTATE
        except libvirt.libvirtError:
            state = libvirt.VIR_DOMAIN_NOSTATE # Assume unknown if error

        # All buttons initially insensitive
        for btn in self.power_buttons.values():
            btn.set_sensitive(False)

        if state == libvirt.VIR_DOMAIN_RUNNING:
            self.power_buttons["Start"].set_sensitive(False)
            self.power_buttons["Pause"].set_sensitive(True)
            self.power_buttons["Resume"].set_sensitive(False)
            self.power_buttons["Graceful Shutdown"].set_sensitive(True)
            self.power_buttons["Reboot"].set_sensitive(True)
            self.power_buttons["Force Power Off"].set_sensitive(True)
        elif state == libvirt.VIR_DOMAIN_PAUSED:
            self.power_buttons["Start"].set_sensitive(False)
            self.power_buttons["Pause"].set_sensitive(False)
            self.power_buttons["Resume"].set_sensitive(True)
            self.power_buttons["Graceful Shutdown"].set_sensitive(True)
            self.power_buttons["Reboot"].set_sensitive(True)
            self.power_buttons["Force Power Off"].set_sensitive(True)
        elif state == libvirt.VIR_DOMAIN_SHUTOFF or state == libvirt.VIR_DOMAIN_SHUTDOWN:
            self.power_buttons["Start"].set_sensitive(True)
            self.power_buttons["Pause"].set_sensitive(False)
            self.power_buttons["Resume"].set_sensitive(False)
            self.power_buttons["Graceful Shutdown"].set_sensitive(False)
            self.power_buttons["Reboot"].set_sensitive(False)
            self.power_buttons["Force Power Off"].set_sensitive(False)
        else: # NOSTATE, BLOCKED, CRASHED, PMSUSPENDED, etc. or unknown
            self.power_buttons["Start"].set_sensitive(True) # Allow start if in uncertain state
            self.power_buttons["Force Power Off"].set_sensitive(True) # Always allow force off as a last resort


    def on_depth_changed(self, combo):
        depth_str = combo.get_active_id()
        if depth_str:
            self.vnc_depth = int(depth_str)
            if self.protocol == 'vnc' and self.vnc_display:
                self._apply_vnc_depth()
                if self.vnc_display.is_open():
                    # Ask user if they want to reconnect
                    dialog = Gtk.MessageDialog(
                        transient_for=self.window,
                        flags=0,
                        message_type=Gtk.MessageType.QUESTION,
                        buttons=Gtk.ButtonsType.YES_NO,
                        text="Reconnect required"
                    )
                    dialog.format_secondary_text(
                        "Changing color depth requires a reconnection. Reconnect now?"
                    )
                    response = dialog.run()
                    dialog.destroy()

                    if response == Gtk.ResponseType.YES:
                        self.connect_display(force=True)

            self.save_state()

    def _apply_vnc_depth(self):
        # Map integer depth to GtkVnc enum
        depth_enum = GtkVnc.DisplayDepthColor.DEFAULT
        if self.vnc_depth == 24:
            depth_enum = GtkVnc.DisplayDepthColor.FULL
        elif self.vnc_depth == 16:
            depth_enum = GtkVnc.DisplayDepthColor.MEDIUM
        elif self.vnc_depth == 8:
            depth_enum = GtkVnc.DisplayDepthColor.LOW

        self.vnc_display.set_depth(depth_enum)

    def _apply_vnc_depth(self):
        # Map integer depth to GtkVnc enum
        depth_enum = GtkVnc.DisplayDepthColor.DEFAULT
        if self.vnc_depth == 24:
            depth_enum = GtkVnc.DisplayDepthColor.FULL
        elif self.vnc_depth == 16:
            depth_enum = GtkVnc.DisplayDepthColor.MEDIUM
        elif self.vnc_depth == 8:
            depth_enum = GtkVnc.DisplayDepthColor.LOW

        self.vnc_display.set_depth(depth_enum)

    def _add_snapshots_tree_columns(self):
        columns = [
            ("Name", 0),
            ("Description", 1),
            ("Creation Time", 2),
            ("State", 3)
        ]
        for title, col_id in columns:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=col_id)
            self.snapshots_tree_view.append_column(column)

    def _populate_snapshots_list(self):
        self.snapshots_store.clear()
        if self.domain:
            snapshots = vm_queries.get_vm_snapshots(self.domain)
            # Sort by creation time to ensure newest first is displayed top
            snapshots.sort(key=lambda x: x.get('creation_time', ''), reverse=True)
            for snap in snapshots:
                self.snapshots_store.append([
                    snap.get("name", "N/A"),
                    snap.get("description", ""),
                    snap.get("creation_time", "N/A"),
                    snap.get("state", "N/A"),
                    snap.get("snapshot_object")
                ])
        else:
            self.show_notification("No VM domain available to list snapshots.", Gtk.MessageType.WARNING)

    def on_refresh_snapshots_clicked(self, button):
        self._populate_snapshots_list()
        self.show_notification("Snapshots list refreshed.", Gtk.MessageType.INFO)

    def on_notebook_switch_page(self, notebook, page, page_num):
        # Only populate when switching to the specific tab
        if page_num == 1: # Snapshots tab
            self._populate_snapshots_list()
        elif page_num == 2: # USB Devices tab
            self._populate_usb_lists()

    def on_send_key(self, button, keys, popover):
        if self.protocol == 'vnc' and self.vnc_display:
            self.vnc_display.send_keys(keys)
        elif self.protocol == 'spice' and self.display_widget:
            # Spice send keys implementation would go here
            # Need to map GDK keys to Spice scancodes or use generic input
            if self.verbose: print("Send keys not fully implemented for SPICE yet")
            pass
        popover.popdown()

    def on_type_clipboard(self, button, popover):
        popover.popdown()
        self.clipboard.request_text(self._on_type_clipboard_received)

    def _on_type_clipboard_received(self, clipboard, text):
        if not text:
            return

        if self.protocol == 'vnc' and self.vnc_display and self.vnc_display.is_open():
            if self.verbose:
                print(f"Typing clipboard: {len(text)} chars")

            for char in text:
                try:
                    keyval = Gdk.unicode_to_keyval(ord(char))
                    self.vnc_display.send_keys([keyval])
                    # Small delay might be needed for some guests, but let's try fast first
                    # time.sleep(0.01) # Avoid blocking main thread; use GLib timeout if strictly needed
                except Exception as e:
                    if self.verbose:
                        print(f"Failed to type char '{char}': {e}")

    def on_screenshot_clicked(self, button):
        pixbuf = None
        if self.protocol == 'vnc' and self.vnc_display:
            pixbuf = self.vnc_display.get_pixbuf()
        elif self.protocol == 'spice' and self.display_widget:
            try:
                pixbuf = self.display_widget.get_pixbuf()
            except:
                pass

        if not pixbuf:
            self.show_notification("Error: Could not capture screen")
            return

        dialog = Gtk.FileChooserDialog(
            title="Save Screenshot",
            parent=self.window,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.ACCEPT
        )

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        dialog.set_current_name(f"screenshot-{timestamp}.png")

        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
            try:
                pixbuf.savev(filename, "png", [], [])
                self.show_notification(f"Screenshot saved to {filename}")
                if self.verbose:
                    print(f"Screenshot saved to {filename}")
            except Exception as e:
                self.show_error_dialog(f"Error saving screenshot: {e}")

        dialog.destroy()

    def on_power_start(self, button, popover):
        popover.popdown()
        try:
        # Refresh domain object to ensure validity, but verify it's the same VM
            if self.original_domain_uuid:
                self.domain = self.conn.lookupByUUIDString(self.original_domain_uuid)
            elif self.domain_name:
                self.domain = self.conn.lookupByName(self.domain_name)
            elif self.uuid:
                self.domain = self.conn.lookupByUUIDString(self.uuid)
            else:
                raise libvirt.libvirtError("No domain identifier available")

            self.domain.create()
            GLib.timeout_add_seconds(5, self.connect_display)
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Start error: {e}")

    def on_power_pause(self, button, popover):
        popover.popdown()
        try:
            self.domain.suspend()
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Pause error: {e}")

    def on_power_resume(self, button, popover):
        popover.popdown()
        try:
            self.domain.resume()
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Resume error: {e}")

    def on_power_shutdown(self, button, popover):
        popover.popdown()
        try:
            self.domain.shutdown()
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Shutdown error: {e}")

    def on_power_reboot(self, button, popover):
        popover.popdown()
        try:
            self.domain.reboot(0)
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Reboot error: {e}")

    def on_power_destroy(self, button, popover):
        popover.popdown()
        try:
            self.domain.destroy()
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Destroy error: {e}")

    def on_snapshots_selection_changed(self, selection):
        model, treeiter = selection.get_selected()
        has_selection = (treeiter is not None)
        self.delete_snapshot_button.set_sensitive(has_selection)
        self.update_restore_button_sensitivity()

    def update_restore_button_sensitivity(self):
        selection = self.snapshots_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        is_vm_active = self.domain.isActive() if self.domain else True # Assume active if no domain
        
        # Restore button is sensitive only if a snapshot is selected AND the VM is NOT active
        self.restore_snapshot_button.set_sensitive(treeiter is not None and not is_vm_active)

    def on_create_snapshot_clicked(self, button):
        dialog = Gtk.Dialog(
            title="Create Snapshot",
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)

        content_area = dialog.get_content_area()
        grid = Gtk.Grid(row_spacing=5, column_spacing=5, margin=10)
        content_area.add(grid)

        grid.attach(Gtk.Label(label="Snapshot Name:"), 0, 0, 1, 1)
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Required")
        grid.attach(name_entry, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Description:"), 0, 1, 1, 1)
        description_entry = Gtk.Entry()
        description_entry.set_placeholder_text("Optional")
        grid.attach(description_entry, 1, 1, 1, 1)

        quiesce_check = Gtk.CheckButton(label="Quiesce VM (Requires QEMU Guest Agent)")
        grid.attach(quiesce_check, 0, 2, 2, 1)

        grid.show_all()

        response = dialog.run()
        snapshot_name = name_entry.get_text().strip()
        snapshot_description = description_entry.get_text().strip()
        quiesce = quiesce_check.get_active()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            if not snapshot_name:
                self.show_notification("Snapshot name is required.", Gtk.MessageType.ERROR)
                return

            self._show_wait_dialog(f"Creating snapshot '{snapshot_name}'...")
            
            # Function to run in a separate thread
            def _create_snapshot_thread():
                try:
                    vm_actions.create_vm_snapshot(self.domain, snapshot_name, snapshot_description, quiesce)
                    GLib.idle_add(self.show_notification, f"Snapshot '{snapshot_name}' created successfully.", Gtk.MessageType.INFO)
                except libvirt.libvirtError as e:
                    GLib.idle_add(self.show_notification, f"Failed to create snapshot: {e}", Gtk.MessageType.ERROR)
                except Exception as e:
                    GLib.idle_add(self.show_notification, f"An unexpected error occurred: {e}", Gtk.MessageType.ERROR)
                finally:
                    GLib.idle_add(self._hide_wait_dialog)
                    GLib.idle_add(self._populate_snapshots_list) # Refresh list on main thread
            
            threading.Thread(target=_create_snapshot_thread).start()

    def on_delete_snapshot_clicked(self, button):
        selection = self.snapshots_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            snapshot_name = model[treeiter][0] # Name is at index 0

            dialog = Gtk.MessageDialog(
                transient_for=self.window,
                flags=0,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Confirm Snapshot Deletion"
            )
            dialog.format_secondary_text(f"Are you sure you want to delete snapshot '{snapshot_name}'?")
            response = dialog.run()
            dialog.destroy()

            if response == Gtk.ResponseType.YES:
                self._show_wait_dialog(f"Deleting snapshot '{snapshot_name}'...")
                
                # Function to run in a separate thread
                def _delete_snapshot_thread():
                    try:
                        vm_actions.delete_vm_snapshot(self.domain, snapshot_name)
                        GLib.idle_add(self.show_notification, f"Snapshot '{snapshot_name}' deleted successfully.", Gtk.MessageType.INFO)
                    except libvirt.libvirtError as e:
                        GLib.idle_add(self.show_notification, f"Failed to delete snapshot: {e}", Gtk.MessageType.ERROR)
                    except Exception as e:
                        GLib.idle_add(self.show_notification, f"An unexpected error occurred: {e}", Gtk.MessageType.ERROR)
                    finally:
                        GLib.idle_add(self._hide_wait_dialog)
                        GLib.idle_add(self._populate_snapshots_list) # Refresh list on main thread
                
                threading.Thread(target=_delete_snapshot_thread).start()
        else:
            self.show_notification("No snapshot selected for deletion.", Gtk.MessageType.WARNING)

    def on_restore_snapshot_clicked(self, button):
        selection = self.snapshots_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            snapshot_name = model[treeiter][0] # Name is at index 0

            if self.domain and self.domain.isActive():
                self.show_notification("Cannot restore snapshot while VM is running. Please stop the VM first.", Gtk.MessageType.ERROR)
                return

            dialog = Gtk.MessageDialog(
                transient_for=self.window,
                flags=0,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Confirm Snapshot Restore"
            )
            dialog.format_secondary_text(f"Are you sure you want to restore to snapshot '{snapshot_name}'? "
                                        "Any unsaved work in the current VM state will be lost.")
            response = dialog.run()
            dialog.destroy()

            if response == Gtk.ResponseType.YES:
                self._show_wait_dialog(f"Restoring VM to snapshot '{snapshot_name}'...")
                
                # Function to run in a separate thread
                def _restore_snapshot_thread():
                    try:
                        vm_actions.restore_vm_snapshot(self.domain, snapshot_name)
                        GLib.idle_add(self.show_notification, f"VM restored to snapshot '{snapshot_name}' successfully.", Gtk.MessageType.INFO)
                        GLib.idle_add(self.connect_display) # Reconnect display after successful restore
                    except libvirt.libvirtError as e:
                        GLib.idle_add(self.show_notification, f"Failed to restore snapshot: {e}", Gtk.MessageType.ERROR)
                    except Exception as e:
                        GLib.idle_add(self.show_notification, f"An unexpected error occurred: {e}", Gtk.MessageType.ERROR)
                    finally:
                        GLib.idle_add(self._hide_wait_dialog)
                        GLib.idle_add(self._populate_snapshots_list) # Refresh list on main thread
                
                threading.Thread(target=_restore_snapshot_thread).start()



    def _add_usb_tree_columns(self, tree_view):
        columns = [
            ("Description", 4), # Index of the description column in ListStore
            ("Vendor ID", 0),
            ("Product ID", 1)
        ]
        for title, col_id in columns:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=col_id)
            tree_view.append_column(column)



    def _populate_usb_lists(self):
        self.attached_usb_store.clear()
        self.host_usb_store.clear()

        if not self.domain:
            self.show_notification("No VM domain available for USB actions.", Gtk.MessageType.WARNING)
            return

        # Populate attached USB devices
        try:
            domain_xml = self.domain.XMLDesc(0)
            root = ET.fromstring(domain_xml)
            attached_devices = vm_queries.get_attached_usb_devices(root)
            for dev in attached_devices:
                self.attached_usb_store.append([
                    dev.get("vendor_id", "N/A"),
                    dev.get("product_id", "N/A"),
                    "", "", # Vendor/Product Name not directly in attached_usb_devices from vm_queries
                    f"{dev.get('vendor_id', '')}:{dev.get('product_id', '')}"
                ])
        except libvirt.libvirtError as e:
            self.show_notification(f"Failed to get attached USB devices: {e}", Gtk.MessageType.ERROR)
        except Exception as e:
            self.show_notification(f"Error getting attached USB devices: {e}", Gtk.MessageType.ERROR)

        # Populate host USB devices
        try:
            host_devices = libvirt_utils.get_host_usb_devices(self.conn)
            for dev in host_devices:
                self.host_usb_store.append([
                    dev.get("vendor_id", "N/A"),
                    dev.get("product_id", "N/A"),
                    dev.get("vendor_name", ""),
                    dev.get("product_name", ""),
                    dev.get("description", "N/A")
                ])
        except libvirt.libvirtError as e:
            self.show_notification(f"Failed to get host USB devices: {e}", Gtk.MessageType.ERROR)
        except Exception as e:
            self.show_notification(f"Error getting host USB devices: {e}", Gtk.MessageType.ERROR)
        
        # Update button sensitivities after populating lists
        self.on_attached_usb_selection_changed(self.attached_usb_tree_view.get_selection())
        self.on_host_usb_selection_changed(self.host_usb_tree_view.get_selection())


    def on_refresh_usb_lists_clicked(self, button):
        self._populate_usb_lists()
        self.show_notification("USB device lists refreshed.", Gtk.MessageType.INFO)

    def on_attached_usb_selection_changed(self, selection):
        model, treeiter = selection.get_selected()
        self.detach_usb_button.set_sensitive(treeiter is not None and self.domain and self.domain.isActive())
        # If an attached device is selected, deselect host device to avoid conflict
        if treeiter:
            self.host_usb_tree_view.get_selection().unselect_all()

    def on_host_usb_selection_changed(self, selection):
        model, treeiter = selection.get_selected()
        self.attach_usb_button.set_sensitive(treeiter is not None and self.domain and self.domain.isActive())
        # If a host device is selected, deselect attached device to avoid conflict
        if treeiter:
            self.attached_usb_tree_view.get_selection().unselect_all()

    def on_attach_usb_clicked(self, button):
        self.log_message("on_attach_usb_clicked called.")
        selection = self.host_usb_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            vendor_id = model[treeiter][0]
            product_id = model[treeiter][1]
            description = model[treeiter][4]
            self.log_message(f"Selected host USB: {description} ({vendor_id}:{product_id})")

            self._show_wait_dialog(f"Attaching USB device '{description}'...")
            self.log_message("Wait dialog shown.")
            
            def _attach_usb_thread():
                self.log_message(f"Attempting to attach USB in thread: {description}")
                try:
                    vm_actions.attach_usb_device(self.domain, vendor_id, product_id)
                    self.log_message(f"USB device {description} attached successfully by vm_actions.")
                    GLib.idle_add(self.show_notification, f"USB device '{description}' attached successfully.", Gtk.MessageType.INFO)
                except libvirt.libvirtError as e:
                    self.log_message(f"libvirtError attaching USB: {e}")
                    print(f"ERROR (libvirt): Failed to attach USB device: {e}", file=sys.stderr) # Direct print for debug
                    GLib.idle_add(self.show_notification, f"Failed to attach USB device: {e}", Gtk.MessageType.ERROR)
                except Exception as e:
                    self.log_message(f"Generic error attaching USB: {e}")
                    print(f"ERROR (generic): An unexpected error occurred during USB attach: {e}", file=sys.stderr) # Direct print for debug
                    GLib.idle_add(self.show_notification, f"An unexpected error occurred: {e}", Gtk.MessageType.ERROR)
                finally:
                    self.log_message("Attaching USB thread finished. Hiding wait dialog and refreshing lists.")
                    GLib.idle_add(self._hide_wait_dialog)
                    GLib.idle_add(self._populate_usb_lists) # Refresh lists on main thread
            
            threading.Thread(target=_attach_usb_thread).start()
        else:
            self.show_notification("No host USB device selected for attachment.", Gtk.MessageType.WARNING)
            self.log_message("No host USB device selected.")

    def on_detach_usb_clicked(self, button):
        self.log_message("on_detach_usb_clicked called.")
        selection = self.attached_usb_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            vendor_id = model[treeiter][0]
            product_id = model[treeiter][1]
            description = model[treeiter][4]
            self.log_message(f"Selected attached USB: {description} ({vendor_id}:{product_id})")

            self._show_wait_dialog(f"Detaching USB device '{description}'...")
            self.log_message("Wait dialog shown.")
            
            def _detach_usb_thread():
                self.log_message(f"Attempting to detach USB in thread: {description}")
                try:
                    vm_actions.detach_usb_device(self.domain, vendor_id, product_id)
                    self.log_message(f"USB device {description} detached successfully by vm_actions.")
                    GLib.idle_add(self.show_notification, f"USB device '{description}' detached successfully.", Gtk.MessageType.INFO)
                except libvirt.libvirtError as e:
                    self.log_message(f"libvirtError detaching USB: {e}")
                    print(f"ERROR (libvirt): Failed to detach USB device: {e}", file=sys.stderr) # Direct print for debug
                    GLib.idle_add(self.show_notification, f"Failed to detach USB device: {e}", Gtk.MessageType.ERROR)
                except Exception as e:
                    self.log_message(f"Generic error detaching USB: {e}")
                    print(f"ERROR (generic): An unexpected error occurred during USB detach: {e}", file=sys.stderr) # Direct print for debug
                    GLib.idle_add(self.show_notification, f"An unexpected error occurred: {e}", Gtk.MessageType.ERROR)
                finally:
                    self.log_message("Detaching USB thread finished. Hiding wait dialog and refreshing lists.")
                    GLib.idle_add(self._hide_wait_dialog)
                    GLib.idle_add(self._populate_usb_lists) # Refresh lists on main thread
            
            threading.Thread(target=_detach_usb_thread).start()
        else:
            self.show_notification("No attached USB device selected for detachment.", Gtk.MessageType.WARNING)
            self.log_message("No attached USB device selected.")

    def _show_wait_dialog(self, message):
        self.wait_dialog = Gtk.Dialog(
            title="Please Wait",
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        self.wait_dialog.set_default_size(250, 100)
        self.wait_dialog.set_resizable(False)
        self.wait_dialog.set_decorated(False) # Hide title bar etc.

        content_area = self.wait_dialog.get_content_area()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        content_area.add(vbox)

        spinner = Gtk.Spinner()
        spinner.props.active = True
        spinner.set_size_request(30, 30) # Make spinner a bit larger
        vbox.pack_start(spinner, False, False, 0)

        label = Gtk.Label(label=message)
        vbox.pack_start(label, False, False, 0)

        vbox.show_all()
        self.wait_dialog.show()
        self.wait_dialog.present()
        # Ensure UI updates
        while Gtk.events_pending():
            Gtk.main_iteration()

    def _hide_wait_dialog(self):
        if hasattr(self, 'wait_dialog') and self.wait_dialog:
            self.wait_dialog.destroy()
            self.wait_dialog = None
            # Ensure UI updates
            while Gtk.events_pending():
                Gtk.main_iteration()
    def do_shutdown(self):
        """Cleanup SSH tunnel on application shutdown"""
        self.stop_ssh_tunnel() # Call the unified cleanup method
        Gtk.Application.do_shutdown(self)

def main():
    try:
        libvirt.virEventRegisterDefaultImpl()
    except Exception as e:
        print(f"Warning: Failed to register libvirt event implementation: {e}")

    parser = argparse.ArgumentParser(description='Simple Remote Viewer for VMs with VNC/SPICE (GTK3)')
    parser.add_argument('-c', '--connect', dest='uri', required=True, help='libvirt URI connexion (ie: qemu:///system)')

    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('--domain-name', help='Virtual Machine name')
    group.add_argument('--uuid', help='Virtual Machine UUID')

    parser.add_argument('--password', help='VNC/SPICE Password')
    parser.add_argument('--verbose', action='store_true', help='Verbose mode')
    parser.add_argument('--logs', action='store_true', help='Enable Logs & Events tab')
    parser.add_argument('-a', '--attach', action='store_true', help='Attach to the local display using libvirt')
    parser.add_argument('-w', '--wait', action='store_true', help='Wait for VM to start')
    parser.add_argument('--direct', action='store_true', help='Direct connection (disable SSH tunneling)')

    args = parser.parse_args()

    app = RemoteViewer(args.uri, args.domain_name, args.uuid, args.verbose, args.password, show_logs=args.logs, attach=args.attach, wait=args.wait, direct=args.direct)
    try:
        app.run([sys.argv[0]])
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
