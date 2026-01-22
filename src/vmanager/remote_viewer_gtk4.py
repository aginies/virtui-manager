#!/usr/bin/env python3
"""
Simple remote viewer (GTK4 Port)
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
import libvirt
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')

from gi.repository import Gtk, Gdk, GLib, Gio, GObject

# Attempt to load GtkVnc and SpiceClientGtk
# Note: Ensure you have GTK4-compatible versions of these libraries installed.
VNC_AVAILABLE = False
try:
    gi.require_version('GtkVnc', '2.0')
    from gi.repository import GtkVnc
    VNC_AVAILABLE = True
except Exception as e:
    print(f"Warning: GtkVnc not found or not compatible with GTK4: {e}")

SPICE_AVAILABLE = False
try:
    gi.require_version('SpiceClientGtk', '3.0')
    gi.require_version('SpiceClientGLib', '2.0')
    from gi.repository import SpiceClientGtk, SpiceClientGLib
    SPICE_AVAILABLE = True
except Exception as e:
    print(f"Warning: SpiceClientGtk not found or not compatible with GTK4: {e}")


class RemoteViewer(Gtk.Application):
    def __init__(self, uri, domain_name, uuid, verbose, password=None, show_logs=False, attach=False, wait=False):
        super().__init__(application_id="com.virtui.remoteviewer", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.uri = uri
        self.domain_name = domain_name
        self.uuid = uuid
        self.original_domain_uuid = None
        self.verbose = verbose
        self.password = password
        self.show_logs = show_logs
        self.attach = attach
        self.wait_for_vm = wait
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
        self.vnc_display = None
        self.protocol = None # 'vnc' or 'spice'
        self.reconnect_pending = False
        self.fs_button = None
        self.info_bar = None
        self.info_bar_label = None
        self.events_registered = False
        self.clipboard = Gdk.Display.get_default().get_clipboard()
        self.clipboard_update_in_progress = False
        self.last_clipboard_content = None
        self.ssh_tunnel_process = None
        self.ssh_tunnel_local_port = None
        self.ssh_tunnel_active = False
        self.ssh_gateway = None
        self.ssh_gateway_port = None
        
        # Connect to clipboard change signal
        self.clipboard.connect("changed", self.on_clipboard_owner_change)

    def show_error_dialog(self, message, quit_app=False):
        # In GTK4, Dialogs are async and don't block.
        # If quit_app is True, we quit when dialog closes.
        dialog = Gtk.MessageDialog(
            transient_for=self.window if self.window else None,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Error"
        )
        dialog.format_secondary_text(message)
        
        def on_response(dlg, res):
            dlg.destroy()
            if quit_app:
                self.quit()

        dialog.connect("response", on_response)
        dialog.present()

    def show_notification(self, message, message_type=Gtk.MessageType.INFO):
        if self.info_bar and self.info_bar_label:
            self.info_bar.set_message_type(message_type)
            self.info_bar_label.set_text(message)
            self.info_bar.set_visible(True)
        elif message_type == Gtk.MessageType.ERROR:
            # Fallback if no window/infobar yet
            self.show_error_dialog(message)
        else:
            if self.verbose:
                print(f"Notification ({message_type}): {message}")

    def on_info_bar_response(self, bar, response):
        bar.set_visible(False)

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
        """Parse qemu+ssh URI to extract SSH gateway and port"""
        if not self.uri or 'qemu+ssh' not in self.uri:
            return None, None
        
        import re
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
        if not self.uri or 'qemu+ssh' not in self.uri:
            return False

        try:
            self.ssh_gateway, self.ssh_gateway_port = self._parse_ssh_uri()

            if not self.ssh_gateway:
                self.log_message("ERROR: Could not parse qemu+ssh URI")
                return False

            self.log_message(f"Detected remote SSH connection via {self.ssh_gateway}:{self.ssh_gateway_port}")
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

        ssh_cmd = [
            'ssh', '-N', '-C', '-L',
            f'{self.ssh_tunnel_local_port}:{remote_host}:{remote_port}',
            self.ssh_gateway, '-p', self.ssh_gateway_port
        ]

        self.log_message(f"Starting SSH tunnel: {' '.join(ssh_cmd)}")
        self.ssh_tunnel_process = subprocess.Popen(ssh_cmd)
        return True

    def register_domain_events(self):
        if not self.conn or not self.domain:
            return

        if self.events_registered:
            return

        try:
            self.conn.domainEventRegisterAny(
                self.domain, 
                libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                self._event_lifecycle_callback,
                None
            )
            # ... (Other events omitted for brevity, logic same as before)
            self.conn.domainEventRegisterAny(self.domain, libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS, self._event_generic_callback, "Graphics")
            self.conn.domainEventRegisterAny(self.domain, libvirt.VIR_DOMAIN_EVENT_ID_REBOOT, self._event_generic_callback, "Reboot")
            self.conn.domainEventRegisterAny(self.domain, libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR, self._event_generic_callback, "IO Error")
            self.conn.domainEventRegisterAny(self.domain, libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG, self._event_generic_callback, "Watchdog")

            GLib.timeout_add(100, self._libvirt_event_tick)
            self.events_registered = True
            self.log_message("Registered for libvirt domain events.")

        except Exception as e:
            self.log_message(f"Failed to register domain events: {e}")

    def _event_lifecycle_callback(self, conn, dom, event, detail, opaque):
        event_strs = {
            0: "Defined", 1: "Undefined", 2: "Started", 3: "Suspended",
            4: "Resumed", 5: "Stopped", 6: "Shutdown", 7: "PMSuspended", 8: "Crashed"
        }
        event_type = event_strs.get(event, f"Unknown({event})")
        self.log_message(f"Event: Lifecycle - {event_type} (Detail: {detail})")

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
            pass # Use defaults

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
        try:
            if 'qemu+ssh' in self.uri:
                self.setup_ssh_tunnel()
            self.conn = libvirt.open(self.uri)
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Error connecting to libvirt: {e}", quit_app=True)
            return
        except Exception as e:
            self.show_error_dialog(f"Connection error: {e}", quit_app=True)
            return

        if not self.domain_name and not self.uuid:
            self.show_vm_list()
        else:
            self.resolve_domain()
            # If domain resolution failed, it might be handled async, but resolve_domain is sync here.
            # If self.domain is None, show_viewer will complain.
            if self.domain:
                 self.show_viewer()
            else:
                 # Error dialog shown in resolve_domain is not blocking, 
                 # so we might end up here.
                 pass

    def resolve_domain(self):
        try:
            if self.domain_name:
                self.domain = self.conn.lookupByName(self.domain_name)
            elif self.uuid:
                self.domain = self.conn.lookupByUUIDString(self.uuid)
            
            if self.domain and not self.original_domain_uuid:
                self.original_domain_uuid = self.domain.UUIDString()
        except libvirt.libvirtError as e:
            self.show_error_dialog(f"Error finding domain: {e}", quit_app=True)

    def show_vm_list(self):
        self.list_window = Gtk.Window(application=self, title="Select VM to Connect")
        self.list_window.set_default_size(400, 500)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)
        self.list_window.set_child(vbox)

        label = Gtk.Label(label="<b>Available VMs</b>", use_markup=True)
        vbox.append(label)

        store = Gtk.ListStore(str, str, str, object)
        try:
            domains = self.conn.listAllDomains(0)
            for dom in domains:
                state_code = dom.info()[0]
                if state_code not in [1, 3]: continue
                state_str = "Running" if state_code == 1 else "Paused"
                xml = dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
                proto = "Unknown"
                if "type='spice'" in xml: proto = "SPICE"
                elif "type='vnc'" in xml: proto = "VNC"
                store.append([dom.name(), state_str, proto, dom])
        except libvirt.libvirtError as e:
            print(f"Error listing domains: {e}")

        tree = Gtk.TreeView(model=store)
        renderer = Gtk.CellRendererText()
        tree.append_column(Gtk.TreeViewColumn("Name", renderer, text=0))
        tree.append_column(Gtk.TreeViewColumn("State", renderer, text=1))
        tree.append_column(Gtk.TreeViewColumn("Protocol", renderer, text=2))

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(tree)
        vbox.append(scroll)

        connect_btn = Gtk.Button(label="Connect")
        connect_btn.connect("clicked", self.on_list_connect, tree)
        vbox.append(connect_btn)

        tree.connect("row-activated", self.on_list_row_activated)
        self.list_window.present()

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
            self.original_domain_uuid = self.domain.UUIDString()
            self.list_window.destroy()
            self.show_viewer()

    def _wait_and_connect_cb(self):
        try:
            if self.original_domain_uuid:
                try:
                    self.domain = self.conn.lookupByUUIDString(self.original_domain_uuid)
                except libvirt.libvirtError:
                    pass
            
            protocol, host, port, pwd = self.get_display_info()
            if not self.attach and (not host or not port):
                return True
            
            self.show_notification("VM started! Connecting...", Gtk.MessageType.INFO)
            self.connect_display()
            return False
        except Exception as e:
            if self.verbose: print(f"Wait error: {e}")
            return True

    def show_viewer(self):
        if not self.domain:
            self.show_error_dialog("No domain selected.", quit_app=True)
            return

        self.load_state()

        domain_name = self.domain.name()
        title = f"{domain_name} - Virtui Manager Viewer"
        subtitle = self.uri
        if self.attach: subtitle += " (Attached)"
        
        # In GTK4, use Window title usually, subtitle is gone from HeaderBar
        # We can combine them
        full_title = f"{title} [{subtitle}]"

        self.window = Gtk.ApplicationWindow(application=self, title=full_title)
        self.window.set_default_size(800, 600)

        header = Gtk.HeaderBar()
        header.set_show_title_buttons(True)
        # header.set_title(title) # HeaderBar title is derived from Window usually
        self.window.set_titlebar(header)

        # Settings
        settings_button = Gtk.MenuButton()
        settings_button.set_icon_name("open-menu-symbolic")
        settings_button.set_tooltip_text("Settings")

        settings_popover = Gtk.Popover()
        vbox_settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox_settings.set_margin_top(10)
        vbox_settings.set_margin_bottom(10)
        vbox_settings.set_margin_start(10)
        vbox_settings.set_margin_end(10)

        # GTK4 CheckButton is different
        scaling_check = Gtk.CheckButton(label="Scaling (Resize)")
        scaling_check.set_active(self.scaling_enabled)
        scaling_check.connect("toggled", self.on_scaling_toggled)
        vbox_settings.append(scaling_check)

        self.smoothing_check = Gtk.CheckButton(label="Smoothing (Interpolation)")
        self.smoothing_check.set_active(self.smoothing_enabled)
        self.smoothing_check.connect("toggled", self.on_smoothing_toggled)
        vbox_settings.append(self.smoothing_check)

        self.lossy_check = Gtk.CheckButton(label="Lossy Compression (JPEG)")
        self.lossy_check.set_active(self.lossy_encoding_enabled)
        self.lossy_check.connect("toggled", self.on_lossy_toggled)
        vbox_settings.append(self.lossy_check)

        view_only_check = Gtk.CheckButton(label="View Only Mode")
        view_only_check.set_active(self.view_only_enabled)
        view_only_check.connect("toggled", self.on_view_only_toggled)
        vbox_settings.append(view_only_check)

        self.depth_settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        depth_label = Gtk.Label(label="Color Depth:")
        self.depth_settings_box.append(depth_label)

        depth_options = ["Default", "8-bit", "16-bit", "24-bit"]
        self.depth_values = [0, 8, 16, 24]
        
        depth_dropdown = Gtk.DropDown.new_from_strings(depth_options)
        try:
            initial_index = self.depth_values.index(self.vnc_depth)
            depth_dropdown.set_selected(initial_index)
        except ValueError:
            depth_dropdown.set_selected(0)
            
        depth_dropdown.connect("notify::selected", self.on_depth_changed)
        depth_dropdown.set_hexpand(True)
        self.depth_settings_box.append(depth_dropdown)
        vbox_settings.append(self.depth_settings_box)

        settings_popover.set_child(vbox_settings)
        settings_button.set_popover(settings_popover)
        header.pack_end(settings_button)

        # Power
        power_button = Gtk.MenuButton()
        power_button.set_icon_name("system-shutdown-symbolic")
        power_button.set_tooltip_text("VM Power Control")
        
        power_popover = Gtk.Popover()
        power_button.set_popover(power_popover)
        power_popover.connect("notify::visible", self.on_power_menu_show) # "show" signal is tricky in Popover, use property notify

        vbox_power = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.power_buttons = {}
        power_actions = [
            ("Start", self.on_power_start),
            ("Pause", self.on_power_pause),
            ("Resume", self.on_power_resume),
            ("Graceful Shutdown", self.on_power_shutdown),
            ("Reboot", self.on_power_reboot),
            ("Force Power Off", self.on_power_destroy),
        ]
        
        for label, callback in power_actions:
            # Gtk.ModelButton still exists in 4 but simpler to use Button with flat class?
            # ModelButton is preferred for menus
            btn = Gtk.Button(label=label)
            btn.set_has_frame(False) # Make it look like a menu item
            btn.set_halign(Gtk.Align.FILL)
            btn.connect("clicked", callback, power_popover)
            vbox_power.append(btn)
            self.power_buttons[label] = btn
        
        power_popover.set_child(vbox_power)
        header.pack_end(power_button)

        # Send Keys
        keys_button = Gtk.MenuButton()
        keys_button.set_icon_name("input-keyboard-symbolic")
        keys_button.set_tooltip_text("Send Key")
        keys_popover = Gtk.Popover()
        vbox_keys = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        key_combinations = [
            ("Ctrl+Alt+Del", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_Delete]),
            ("Ctrl+Alt+Backspace", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_BackSpace]),
            ("Ctrl+Alt+F1", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F1]),
            ("Ctrl+Alt+F2", [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F2]),
            ("PrintScreen", [Gdk.KEY_Print]),
        ]
        
        for label, keys in key_combinations:
            btn = Gtk.Button(label=label)
            btn.set_has_frame(False)
            btn.set_halign(Gtk.Align.FILL)
            btn.connect("clicked", self.on_send_key, keys, keys_popover)
            vbox_keys.append(btn)
        
        keys_popover.set_child(vbox_keys)
        keys_button.set_popover(keys_popover)
        header.pack_end(keys_button)

        # Clipboard
        clip_button = Gtk.MenuButton()
        clip_button.set_icon_name("edit-paste-symbolic")
        clip_button.set_tooltip_text("Clipboard")
        clip_popover = Gtk.Popover()
        vbox_clip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        btn_type_clip = Gtk.Button(label="Type Clipboard")
        btn_type_clip.set_has_frame(False)
        btn_type_clip.set_halign(Gtk.Align.FILL)
        btn_type_clip.connect("clicked", self.on_type_clipboard, clip_popover)
        vbox_clip.append(btn_type_clip)
        
        clip_popover.set_child(vbox_clip)
        clip_button.set_popover(clip_popover)
        header.pack_end(clip_button)

        # Screenshot
        screenshot_button = Gtk.Button()
        screenshot_button.set_icon_name("camera-photo-symbolic")
        screenshot_button.set_tooltip_text("Take Screenshot")
        screenshot_button.connect("clicked", self.on_screenshot_clicked)
        header.pack_end(screenshot_button)

        # Reconnect
        reconnect_button = Gtk.Button()
        reconnect_button.set_icon_name("view-refresh-symbolic")
        reconnect_button.set_tooltip_text("Reconnect")
        reconnect_button.connect("clicked", self.on_reconnect_clicked)
        header.pack_end(reconnect_button)

        # Fullscreen
        self.fs_button = Gtk.ToggleButton()
        self.fs_button.set_icon_name("view-fullscreen-symbolic")
        self.fs_button.set_tooltip_text("Fullscreen")
        self.fs_button.set_active(self.is_fullscreen)
        self.fs_button.connect("toggled", self.on_fs_button_toggled)
        header.pack_end(self.fs_button)

        # Logs
        self.logs_button = Gtk.ToggleButton()
        self.logs_button.set_icon_name("utilities-terminal-symbolic")
        self.logs_button.set_active(self.show_logs)
        self.logs_button.connect("toggled", self.on_logs_toggled)
        header.pack_end(self.logs_button)

        # Main Layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.window.set_child(self.main_box)

        # InfoBar
        self.info_bar = Gtk.InfoBar()
        self.info_bar.set_visible(False)
        self.info_bar.props.show_close_button = True
        self.info_bar.connect("response", self.on_info_bar_response)
        
        self.info_bar_label = Gtk.Label()
        self.info_bar_label.set_wrap(True)
        self.info_bar.add_child(self.info_bar_label)
        self.main_box.append(self.info_bar)

        # Notebook
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.main_box.append(self.notebook)

        # Tab 1
        self.display_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.notebook.append_page(self.display_tab, Gtk.Label(label="Display"))
        self.view_container = self.display_tab

        # Tab 2
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()
        self.log_scroll = Gtk.ScrolledWindow()
        self.log_scroll.set_child(self.log_view)
        self.log_scroll.set_vexpand(True)
        self.log_scroll.set_hexpand(True)
        self.notebook.append_page(self.log_scroll, Gtk.Label(label="Logs"))
        
        self.update_logs_visibility()
        self.init_display()

        # Key Events
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.window.add_controller(key_controller)

        if self.is_fullscreen:
            self.window.fullscreen()

        self.window.present()
        self.register_domain_events()

        if self.wait_for_vm:
            protocol, host, port, pwd = self.get_display_info()
            if not self.attach and (not host or not port):
                self.show_notification("Waiting for VM to start...", Gtk.MessageType.INFO)
                GLib.timeout_add_seconds(2, self._wait_and_connect_cb)
                return

        self.connect_display()

    def _setup_tunnel_if_needed(self, listen, port):
        if self.ssh_gateway and self.ssh_tunnel_process is None:
            remote_host = listen
            if listen == 'localhost' or listen == '0.0.0.0':
                import re
                match = re.search(r'qemu\+ssh://(?:[^@]+@)?([^/:]+)', self.uri)
                if match:
                    remote_host = match.group(1)
            self.start_ssh_tunnel(remote_host, port)

    def get_display_info(self):
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

            if SPICE_AVAILABLE:
                info = get_graphics_info(root.find(".//graphics[@type='spice']"))
                if info:
                    listen, port, password = info
                    self._setup_tunnel_if_needed(listen, port)
                    return 'spice', listen, port, password

            info = get_graphics_info(root.find(".//graphics[@type='vnc']"))
            if info:
                listen, port, password = info
                self._setup_tunnel_if_needed(listen, port)
                return 'vnc', listen, port, password

        except Exception as e:
            self.log_message(f"XML parse error: {e}")
        return None, None, None, None

    def init_display(self):
        # Cleanup
        if self.display_widget:
            # We need to find the scrolled window parent if it exists and remove it
            # In GTK4 we can just unparent the widget if we have reference
            parent = self.display_widget.get_parent()
            if parent:
                 # If parent is scroll, remove scroll from view_container
                 if parent.get_parent() == self.view_container:
                     self.view_container.remove(parent)
                 else:
                     # Just remove widget
                     parent.remove(self.display_widget)
            self.display_widget = None

        protocol, host, port, password_required = self.get_display_info()
        self.protocol = protocol
        
        msg = f"Initializing display for protocol: {protocol}"
        self.log_message(msg)

        scroll = Gtk.ScrolledWindow()

        if protocol == 'spice' and SPICE_AVAILABLE:
            self.depth_settings_box.set_visible(False)
            self.lossy_check.set_visible(False)
            self.spice_session = SpiceClientGLib.Session()
            try:
                # SpiceClientGtk session retrieval might need adjustment for GTK4
                # Or it works if library is updated.
                pass
            except Exception:
                pass
            
            # Note: SpiceClientGtk.Display in GTK3 inherits GtkWidget. 
            # If it's not ported to GTK4, this line will fail or segfault.
            self.display_widget = SpiceClientGtk.Display(session=self.spice_session)
            self.display_widget.set_property("scaling", self.scaling_enabled)
        
        elif protocol == 'vnc' and VNC_AVAILABLE:
            self.depth_settings_box.set_visible(True)
            self.lossy_check.set_visible(True)
            self.protocol = 'vnc'
            
            # VNC Display
            self.vnc_display = GtkVnc.Display()
            self.display_widget = self.vnc_display

            # Configuration
            # Note: set_pointer_local might not be available or needed
            self.vnc_display.set_scaling(self.scaling_enabled)
            self.vnc_display.set_smoothing(self.smoothing_enabled)
            self.vnc_display.set_keep_aspect_ratio(True)
            self.vnc_display.set_lossy_encoding(self.lossy_encoding_enabled)
            self.vnc_display.set_read_only(self.view_only_enabled)
            self._apply_vnc_depth()

            self.vnc_display.connect("vnc-disconnected", self.on_vnc_disconnected)
            self.vnc_display.connect("vnc-connected", self.on_vnc_connected)
            self.vnc_display.connect("vnc-auth-credential", self.on_vnc_auth_credential)
            self.vnc_display.connect("vnc-server-cut-text", self.on_vnc_server_cut_text)
        
        else:
            self.log_message("No supported display protocol available (VNC/SPICE).")
            lbl = Gtk.Label(label="No supported protocol available.")
            scroll.set_child(lbl)
            self.view_container.append(scroll)
            return

        scroll.set_child(self.display_widget)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        self.view_container.append(scroll)

    def connect_display(self, force=False, password=None):
        if self.original_domain_uuid and self.domain:
            try:
                current_uuid = self.domain.UUIDString()
                if current_uuid != self.original_domain_uuid:
                    self.show_error_dialog(f"Security: Domain UUID changed.")
                    return False
            except libvirt.libvirtError:
                pass

        protocol, host, port, xml_password_required = self.get_display_info()

        if not self.attach and (not host or not port):
            return False

        if self.password: password = self.password
        elif xml_password_required: password = xml_password_required
        else: password = None

        self._pending_password = password

        try:
            if self.attach:
                fd = self.domain.openGraphicsFD(0)
                if self.protocol == 'spice' and SPICE_AVAILABLE:
                    self.spice_session.open_fd(fd)
                elif self.protocol == 'vnc' and VNC_AVAILABLE:
                    if self.vnc_display.is_open() and force: self.vnc_display.close()
                    self.reconnect_pending = False
                    self._apply_vnc_depth()
                    self.vnc_display.open_fd(fd)
                return False

            if self.protocol == 'spice' and SPICE_AVAILABLE:
                if self.ssh_gateway and self.ssh_tunnel_local_port:
                    host = 'localhost'
                    port = self.ssh_tunnel_local_port
                    time.sleep(1)
                uri = f"spice://{host}:{port}"
                self.spice_session.set_property("uri", uri)
                if password: self.spice_session.set_property("password", password)
                self.spice_session.connect()

            elif self.protocol == 'vnc' and VNC_AVAILABLE:
                if self.ssh_gateway and self.ssh_tunnel_local_port:
                    host = 'localhost'
                    port = self.ssh_tunnel_local_port
                    time.sleep(1)
                
                if self.vnc_display.is_open():
                    if force:
                        self.reconnect_pending = True
                        self.vnc_display.close()
                    return False

                self.reconnect_pending = False
                self._apply_vnc_depth()
                self.vnc_display.open_host(host, str(port))

                return False
        except Exception as e:
            self.show_notification(f"Connection failed: {e}", Gtk.MessageType.ERROR)
            return False

    def on_vnc_auth_credential(self, vnc, cred_list):
        # Async password prompt
        password = self._pending_password
        if password:
             self.vnc_display.set_credential(GtkVnc.DisplayCredential.PASSWORD, password)
        else:
             self._prompt_for_password("VNC")

    def _prompt_for_password(self, protocol):
        dialog = Gtk.Dialog(title=f"{protocol} Password", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        
        content = dialog.get_content_area()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)
        content.append(vbox)
        
        label = Gtk.Label(label=f"Enter password for {self.domain_name}:")
        vbox.append(label)
        
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_invisible_char("*")
        vbox.append(entry)
        
        def on_response(dlg, response):
            text = entry.get_text()
            dlg.destroy()
            if response == Gtk.ResponseType.OK:
                # Set credential on VNC
                if self.vnc_display:
                    self.vnc_display.set_credential(GtkVnc.DisplayCredential.PASSWORD, text)
            else:
                if self.vnc_display:
                    self.vnc_display.close()
        
        dialog.connect("response", on_response)
        dialog.present()

    def on_vnc_connected(self, vnc):
        if self.verbose: print("VNC Connected")

    def on_vnc_disconnected(self, vnc):
        if self.verbose: print("VNC Disconnected")
        if self.reconnect_pending:
            self.reconnect_pending = False
            GLib.timeout_add(500, self.connect_display)
            return
        self.check_shutdown()

    def on_vnc_server_cut_text(self, vnc, text):
        if text != self.last_clipboard_content:
            self.last_clipboard_content = text
            self.clipboard_update_in_progress = True
            self.clipboard.set_text(text)
            self.clipboard_update_in_progress = False
            self.show_notification(f"Clipboard received ({len(text)} chars)")

    def on_clipboard_owner_change(self, clipboard):
        if self.clipboard_update_in_progress: return
        clipboard.read_text_async(None, self._on_clipboard_text_received)

    def _on_clipboard_text_received(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text and text != self.last_clipboard_content:
                self.last_clipboard_content = text
                if self.protocol == 'vnc' and self.vnc_display and self.vnc_display.is_open():
                     self.vnc_display.client_cut_text(text)
        except Exception:
            pass

    def check_shutdown(self):
        if self.original_domain_uuid and self.domain:
            GLib.timeout_add_seconds(1, self._check_shutdown_async, 0)

    def _check_shutdown_async(self, counter):
        try:
            if not self.domain.isActive():
                self.show_notification("VM has shut down.", Gtk.MessageType.INFO)
                return False
        except:
            self.quit()
            return False
            
        if counter < 10:
            GLib.timeout_add_seconds(1, self._check_shutdown_async, counter + 1)
            return False
            
        self.connect_display()
        return False

    def on_key_pressed(self, controller, keyval, keycode, state):
        # Handle Fullscreen toggle Ctrl+F
        if (keyval == Gdk.KEY_f or keyval == Gdk.KEY_F) and (state & Gdk.ModifierType.CONTROL_MASK):
            self.fs_button.set_active(not self.fs_button.get_active())
            return True
        return False

    def on_fs_button_toggled(self, button):
        self.is_fullscreen = button.get_active()
        if self.is_fullscreen: self.window.fullscreen()
        else: self.window.unfullscreen()
        self.save_state()

    def on_scaling_toggled(self, button):
        self.scaling_enabled = button.get_active()
        if self.protocol == 'vnc' and self.display_widget:
            self.display_widget.set_scaling(self.scaling_enabled)
        self.save_state()

    def on_smoothing_toggled(self, button):
        self.smoothing_enabled = button.get_active()
        if self.protocol == 'vnc' and self.display_widget:
            self.display_widget.set_smoothing(self.smoothing_enabled)
        self.save_state()

    def on_lossy_toggled(self, button):
        self.lossy_encoding_enabled = button.get_active()
        if self.protocol == 'vnc' and self.display_widget:
            self.display_widget.set_lossy_encoding(self.lossy_encoding_enabled)
        self.save_state()

    def on_logs_toggled(self, button):
        self.show_logs = button.get_active()
        self.update_logs_visibility()

    def update_logs_visibility(self):
        if hasattr(self, 'notebook'):
            if self.show_logs: self.notebook.get_nth_page(1).set_visible(True)
            else: self.notebook.get_nth_page(1).set_visible(False)

    def on_view_only_toggled(self, button):
        self.view_only_enabled = button.get_active()
        if self.protocol == 'vnc' and self.display_widget:
            self.display_widget.set_read_only(self.view_only_enabled)
        self.save_state()

    def on_reconnect_clicked(self, button):
        if self.protocol == 'vnc' and self.display_widget:
            self.connect_display(force=True)

    def on_power_menu_show(self, popover, pspec):
        if not popover.get_visible(): return
        
        try:
            state, reason = self.domain.state()
        except libvirt.libvirtError:
            state = libvirt.VIR_DOMAIN_NOSTATE

        # Enable/Disable buttons based on state
        for btn in self.power_buttons.values(): btn.set_sensitive(False)

        if state == libvirt.VIR_DOMAIN_RUNNING:
            self.power_buttons["Pause"].set_sensitive(True)
            self.power_buttons["Graceful Shutdown"].set_sensitive(True)
            self.power_buttons["Reboot"].set_sensitive(True)
            self.power_buttons["Force Power Off"].set_sensitive(True)
        elif state == libvirt.VIR_DOMAIN_PAUSED:
            self.power_buttons["Resume"].set_sensitive(True)
            self.power_buttons["Graceful Shutdown"].set_sensitive(True)
            self.power_buttons["Reboot"].set_sensitive(True)
            self.power_buttons["Force Power Off"].set_sensitive(True)
        elif state == libvirt.VIR_DOMAIN_SHUTOFF:
            self.power_buttons["Start"].set_sensitive(True)
        else:
            self.power_buttons["Start"].set_sensitive(True)
            self.power_buttons["Force Power Off"].set_sensitive(True)

    def on_depth_changed(self, dropdown, pspec):
        index = dropdown.get_selected()
        if index >= 0 and index < len(self.depth_values):
            self.vnc_depth = self.depth_values[index]
            if self.protocol == 'vnc' and self.vnc_display:
                self._apply_vnc_depth()
                # Reconnect dialog async
                def on_resp(dlg, res):
                    dlg.destroy()
                    if res == Gtk.ResponseType.YES:
                        self.connect_display(force=True)
                
                dlg = Gtk.MessageDialog(transient_for=self.window, modal=True, 
                                        message_type=Gtk.MessageType.QUESTION, 
                                        buttons=Gtk.ButtonsType.YES_NO, 
                                        text="Reconnect required")
                dlg.format_secondary_text("Changing color depth requires a reconnection. Reconnect now?")
                dlg.connect("response", on_resp)
                dlg.present()
            self.save_state()

    def _apply_vnc_depth(self):
        depth_enum = GtkVnc.DisplayDepthColor.DEFAULT
        if self.vnc_depth == 24: depth_enum = GtkVnc.DisplayDepthColor.FULL
        elif self.vnc_depth == 16: depth_enum = GtkVnc.DisplayDepthColor.MEDIUM
        elif self.vnc_depth == 8: depth_enum = GtkVnc.DisplayDepthColor.LOW
        self.vnc_display.set_depth(depth_enum)

    def on_send_key(self, button, keys, popover):
        popover.popdown()
        if self.protocol == 'vnc' and self.display_widget:
            self.display_widget.send_keys(keys)

    def on_type_clipboard(self, button, popover):
        popover.popdown()
        self.clipboard.read_text_async(None, self._on_type_clipboard_received_for_typing)

    def _on_type_clipboard_received_for_typing(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text and self.protocol == 'vnc' and self.display_widget:
                 for char in text:
                    keyval = Gdk.unicode_to_keyval(ord(char))
                    self.display_widget.send_keys([keyval])
        except Exception:
            pass

    def on_screenshot_clicked(self, button):
        # GTK4 Screenshot is harder. VNC widget might have get_pixbuf (returns GdkPixbuf).
        # But GdkPixbuf is same in GTK3/4.
        pixbuf = None
        if self.protocol == 'vnc' and self.display_widget:
            pixbuf = self.display_widget.get_pixbuf()
        
        if not pixbuf:
            self.show_notification("Could not capture screen")
            return

        # Use async file dialog or custom implementation
        # Gtk.FileChooserNative is good for GTK4
        dialog = Gtk.FileChooserNative(title="Save Screenshot", transient_for=self.window, action=Gtk.FileChooserAction.SAVE)
        
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        dialog.set_current_name(f"screenshot-{timestamp}.png")
        
        def on_response(d, res):
            if res == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                path = f.get_path()
                try:
                    pixbuf.savev(path, "png", [], [])
                    self.show_notification(f"Screenshot saved to {path}")
                except Exception as e:
                    self.show_error_dialog(f"Error saving: {e}")
            d.destroy()

        dialog.connect("response", on_response)
        dialog.show()

    def on_power_start(self, button, popover):
        popover.popdown()
        try:
             self.domain.create()
             GLib.timeout_add_seconds(2, self.connect_display)
        except Exception as e: self.show_error_dialog(f"Error: {e}")

    def on_power_pause(self, button, popover):
        popover.popdown()
        try: self.domain.suspend()
        except Exception as e: self.show_error_dialog(f"Error: {e}")

    def on_power_resume(self, button, popover):
        popover.popdown()
        try: self.domain.resume()
        except Exception as e: self.show_error_dialog(f"Error: {e}")

    def on_power_shutdown(self, button, popover):
        popover.popdown()
        try: self.domain.shutdown()
        except Exception as e: self.show_error_dialog(f"Error: {e}")

    def on_power_reboot(self, button, popover):
        popover.popdown()
        try: self.domain.reboot(0)
        except Exception as e: self.show_error_dialog(f"Error: {e}")

    def on_power_destroy(self, button, popover):
        popover.popdown()
        try: self.domain.destroy()
        except Exception as e: self.show_error_dialog(f"Error: {e}")

    def do_shutdown(self):
        if self.ssh_tunnel_process:
            self.ssh_tunnel_process.terminate()
        Gtk.Application.do_shutdown(self)

def main():
    try:
        libvirt.virEventRegisterDefaultImpl()
    except Exception as e:
        print(f"Warning: Failed to register libvirt event implementation: {e}")

    parser = argparse.ArgumentParser(description='Simple Remote Viewer (GTK4)')
    parser.add_argument('-c', '--connect', dest='uri', required=True, help='libvirt URI')
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('--domain-name', help='VM name')
    group.add_argument('--uuid', help='VM UUID')
    parser.add_argument('--password', help='VNC/SPICE Password')
    parser.add_argument('--verbose', action='store_true', help='Verbose mode')
    parser.add_argument('--logs', action='store_true', help='Enable Logs')
    parser.add_argument('-a', '--attach', action='store_true', help='Attach directly')
    parser.add_argument('-w', '--wait', action='store_true', help='Wait for VM')

    args = parser.parse_args()
    app = RemoteViewer(args.uri, args.domain_name, args.uuid, args.verbose, args.password, show_logs=args.logs, attach=args.attach, wait=args.wait)
    # Pass only program name to run(), as we handled args manually.
    # Otherwise GApplication might complain about unknown options.
    app.run([sys.argv[0]])

if __name__ == '__main__':
    main()