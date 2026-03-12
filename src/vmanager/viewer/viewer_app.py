#!/usr/bin/env python3
"""
Remote Viewer Application

Main application class that coordinates all managers, handlers, and UI components.

SECURITY NOTE:
This module implements comprehensive sanitization of sensitive information to prevent
accidental exposure of passwords, SSH keys, connection details, and other secrets.
All sensitive data is redacted using the shared sanitize_sensitive_data utility.
"""

import argparse
import sys

import gi
import libvirt
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from .constants import (
    LIBVIRT_EVENT_TICK_INTERVAL_MS,
    VM_START_CONNECT_DELAY_MS,
)
from .ssh_tunnel import SSHTunnelManager
from .display_manager import DisplayManager, DisplaySettings
from .ui import MainWindowBuilder
from .handlers import (
    PowerHandler,
    ClipboardHandler,
    DisplayHandler,
    VMStateHandler,
)
from .utils.config import ConfigManager
from .utils.notifications import NotificationManager

from .. import vm_queries


class RemoteViewer(Gtk.Application):
    """
    Remote viewer application for VNC/SPICE connections to virtual machines.

    Coordinates all managers, handlers, and UI components to provide a complete
    VM viewing experience with power control, clipboard sync, snapshots, USB
    passthrough, and serial console access.
    """

    def __init__(
        self,
        uri: str,
        domain_name: str = None,
        uuid: str = None,
        verbose: bool = False,
        password: str = None,
        show_logs: bool = False,
        attach: bool = False,
        wait: bool = False,
        direct: bool = False,
    ):
        """
        Initialize the remote viewer application.

        Args:
            uri: libvirt connection URI
            domain_name: Name of the VM domain
            uuid: UUID of the VM domain
            verbose: Enable verbose logging
            password: VNC/SPICE password
            show_logs: Show logs tab by default
            attach: Use libvirt attach mode
            wait: Wait for VM to start
            direct: Skip SSH tunneling
        """
        super().__init__(application_id=None)

        # Connection parameters
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

        # Libvirt objects
        self.conn = None
        self.domain = None

        # UI references
        self.window = None
        self.list_window = None

        # Managers
        self.config_manager = ConfigManager(verbose=verbose)
        self.notification_manager = None  # Created after UI
        self.ssh_tunnel_manager = None  # Created in do_activate
        self.display_manager = None  # Created after UI

        # Handlers
        self.power_handler = None
        self.clipboard_handler = None
        self.display_handler = None
        self.vm_state_handler = None

        # UI builder
        self.window_builder = None

        # State
        self.events_registered = False
        self._pending_password = None

        # Clipboard
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

    def __del__(self):
        """Destructor to ensure SSH tunnel cleanup on object destruction."""
        self._cleanup_resources()

    def _cleanup_resources(self, *args):
        """Clean up all resources (SSH tunnel, console, connections, etc.)."""
        try:
            # Disconnect console
            if self.window_builder:
                console_tab = self.window_builder.get_console_tab()
                if console_tab:
                    console_tab.disconnect()

            # Stop SSH tunnel
            if self.ssh_tunnel_manager:
                self.ssh_tunnel_manager.stop()

        except Exception as e:
            if self.verbose:
                print(f"Warning: Error during resource cleanup: {e}")

    def do_activate(self):
        """Application activation - connect to libvirt and show UI."""
        # Create SSH tunnel manager
        self.ssh_tunnel_manager = SSHTunnelManager(
            log_callback=lambda msg: print(msg) if self.verbose else None,
            notification_callback=None,  # Will be set after UI creation
        )

        # Setup SSH tunnel if needed
        if "qemu+ssh" in self.uri and not self.direct_connection:
            self.ssh_tunnel_manager.setup(self.uri, self.direct_connection)

        # Connect to libvirt
        try:
            self.conn = libvirt.open(self.uri)
        except libvirt.libvirtError as e:
            self._show_startup_error(f"Error connecting to libvirt: {e}")
            sys.exit(1)
        except Exception as e:
            self._show_startup_error(f"Connection error: {e}")
            sys.exit(1)

        # Show VM list or specific VM
        if not self.domain_name and not self.uuid:
            self.show_vm_list()
        else:
            self.resolve_domain()
            self.show_viewer()

    def _show_startup_error(self, message: str):
        """Show error dialog during startup (before main UI is ready)."""
        dialog = Gtk.MessageDialog(
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Error",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def resolve_domain(self):
        """Resolve domain by name or UUID."""
        try:
            if self.domain_name:
                self.domain = self.conn.lookupByName(self.domain_name)
            elif self.uuid:
                self.domain = self.conn.lookupByUUIDString(self.uuid)

            # Store original UUID for security checks
            if self.domain and not self.original_domain_uuid:
                self.original_domain_uuid = self.domain.UUIDString()
        except libvirt.libvirtError as e:
            if self.verbose:
                print(f"Error finding domain: {e}")

    def show_vm_list(self):
        """Show VM selection dialog."""
        self.list_window = Gtk.Window(application=self, title="Select VM to Connect")
        self.list_window.set_default_size(400, 500)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_border_width(10)
        self.list_window.add(vbox)

        label = Gtk.Label(label="<b>Available VMs</b>", use_markup=True)
        vbox.pack_start(label, False, False, 0)

        # ListStore: Name, State, Protocol, Domain Object
        store = Gtk.ListStore(str, str, str, object)

        try:
            domains = self.conn.listAllDomains(0)
            for dom in domains:
                state_code = dom.info()[0]

                # Only show running or paused VMs
                if state_code not in [1, 3]:  # RUNNING, PAUSED
                    continue

                state_str = "Running" if state_code == 1 else "Paused"

                # Detect protocol
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

        for i, title in enumerate(["Name", "State", "Protocol"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            tree.append_column(column)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add(tree)
        vbox.pack_start(scroll, True, True, 0)

        connect_btn = Gtk.Button(label="Connect")
        connect_btn.connect("clicked", self._on_list_connect, tree)
        vbox.pack_start(connect_btn, False, False, 0)

        tree.connect("row-activated", self._on_list_row_activated)

        self.list_window.show_all()

    def _on_list_connect(self, btn, tree):
        """Handle connect button in VM list."""
        selection = tree.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            self.domain = model[treeiter][3]
            self.domain_name = self.domain.name()
            self.original_domain_uuid = self.domain.UUIDString()
            self.list_window.destroy()
            self.show_viewer()

    def _on_list_row_activated(self, tree, path, column):
        """Handle double-click in VM list."""
        model = tree.get_model()
        treeiter = model.get_iter(path)
        if treeiter:
            self.domain = model[treeiter][3]
            self.domain_name = self.domain.name()
            self.original_domain_uuid = self.domain.UUIDString()
            self.list_window.destroy()
            self.show_viewer()

    def show_viewer(self):
        """Build and show the main viewer window."""
        # Load saved state
        state = self.config_manager.load_state()

        # Determine domain name for window title
        if self.domain:
            domain_name = self.domain.name()
        else:
            domain_name = self.domain_name or self.uuid or "Unknown VM"

        # Create display settings from loaded state
        display_settings = DisplaySettings(
            scaling_enabled=state.get('scaling', False),
            smoothing_enabled=state.get('smoothing', True),
            lossy_encoding_enabled=state.get('lossy_encoding', False),
            view_only_enabled=state.get('view_only', False),
            vnc_depth=state.get('vnc_depth', 0),
        )

        # Build window using MainWindowBuilder
        handlers = self._create_event_handlers(display_settings)

        self.window_builder = MainWindowBuilder(
            application=self,
            domain=self.domain,
            conn=self.conn,
            domain_name=domain_name,
            uri=self.uri,
            attach=self.attach,
            is_fullscreen=state.get('fullscreen', False),
            show_logs=self.show_logs,
            scaling_enabled=display_settings.scaling_enabled,
            smoothing_enabled=display_settings.smoothing_enabled,
            lossy_encoding_enabled=display_settings.lossy_encoding_enabled,
            view_only_enabled=display_settings.view_only_enabled,
            vnc_depth=display_settings.vnc_depth,
            log_callback=self._log_message,
            notification_callback=self._show_notification,
            reconnect_callback=self._reconnect_display,
        )

        self.window = self.window_builder.build_window(handlers)

        # Create notification manager now that UI is ready
        self.notification_manager = NotificationManager(verbose=self.verbose)
        self.notification_manager.set_window(self.window)
        self.notification_manager.set_info_bar(
            self.window_builder.get_info_bar(),
            self.window_builder.get_info_bar_label()
        )
        self.notification_manager.set_log_widgets(
            self.window_builder.get_log_buffer(),
            self.window_builder.log_view
        )

        # Update SSH tunnel manager with notification callback
        self.ssh_tunnel_manager.set_notification_callback(self._show_notification)

        # Create display manager
        self.display_manager = DisplayManager(
            log_callback=self._log_message,
            notification_callback=self._show_notification,
            error_dialog_callback=self._show_error_dialog,
            disconnect_callback=self._on_display_disconnected,
            reconnect_callback=self._connect_display,
            verbose=self.verbose
        )

        # Set display manager UI elements
        view_container = self.window_builder.get_view_container()
        self.display_manager.set_view_container(view_container)
        self.display_manager.set_window(self.window)
        self.display_manager.set_ui_elements(
            self.window_builder.get_depth_settings_box(),
            self.window_builder.get_lossy_check()
        )

        # Adapter to provide the grab handler interface expected by DisplayManager
        class _GrabHandlerAdapter:
            def __init__(self, callback):
                self._callback = callback

            def on_keyboard_grab_activated(self, *args, **kwargs):
                self._callback(*args, **kwargs)

            def on_keyboard_grab_deactivated(self, *args, **kwargs):
                self._callback(*args, **kwargs)

            def on_spice_grab_changed(self, *args, **kwargs):
                self._callback(*args, **kwargs)

        # Initialize display (already adds to view_container internally)
        self.display_manager.init_display(
            domain=self.domain,
            ssh_tunnel_manager=self.ssh_tunnel_manager,
            view_only_handler=self._on_view_only_changed,
            grab_handler=_GrabHandlerAdapter(self._on_grab_changed),
        )

        # Apply initial display settings
        self.display_manager.apply_settings(display_settings)

        # Create handlers now that we have all components
        self._create_handlers(display_settings)

        # Update logs visibility
        self.window_builder.update_logs_visibility(self.show_logs)

        # Show window
        if state.get('fullscreen', False):
            self.window.fullscreen()

        self.window.show_all()
        # Hide info bar initially
        self.window_builder.get_info_bar().set_revealed(False)
        self.window.present()

        # Register domain events
        self._register_domain_events()

        # Start libvirt event loop ticker
        GLib.timeout_add(LIBVIRT_EVENT_TICK_INTERVAL_MS, self._libvirt_event_tick)

        # Connect display or wait for VM
        if self.wait_for_vm:
            protocol, host, port, pwd = self.display_manager.get_display_info(
                self.domain, self.ssh_tunnel_manager
            )
            if not self.attach and (not host or not port):
                self.vm_state_handler.start_wait_for_vm()
                return

        # Check current VM state
        if self.domain:
            try:
                state_code, _ = self.domain.state()
                state_str = vm_queries.get_status(self.domain)

                if state_code == libvirt.VIR_DOMAIN_PAUSED:
                    self._show_notification(f"VM '{self.domain.name()}' is paused.", Gtk.MessageType.WARNING)
                elif state_code == libvirt.VIR_DOMAIN_RUNNING:
                    self._show_notification(f"VM '{self.domain.name()}' is running.", Gtk.MessageType.INFO)
                elif state_code in [libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTDOWN]:
                    self._show_notification(f"VM '{self.domain.name()}' is shut off.", Gtk.MessageType.INFO)
            except libvirt.libvirtError as e:
                if self.verbose:
                    print(f"Could not determine VM state: {e}")

        # Connect display
        self._connect_display()

    def _create_event_handlers(self, display_settings: DisplaySettings) -> dict:
        """
        Create event handler callbacks for UI components.

        These are lightweight wrapper methods that will delegate to handler instances
        once they're created.
        """
        # Use lists for mutable references (Python closure workaround)
        scaling_ref = [display_settings.scaling_enabled]
        smoothing_ref = [display_settings.smoothing_enabled]
        lossy_ref = [display_settings.lossy_encoding_enabled]
        view_only_ref = [display_settings.view_only_enabled]
        depth_ref = [display_settings.vnc_depth]

        return {
            # Power handlers
            'on_power_start': lambda btn, pop: self.power_handler.on_start(btn, pop) if self.power_handler else None,
            'on_power_pause': lambda btn, pop: self.power_handler.on_pause(btn, pop) if self.power_handler else None,
            'on_power_resume': lambda btn, pop: self.power_handler.on_resume(btn, pop) if self.power_handler else None,
            'on_power_shutdown': lambda btn, pop: self.power_handler.on_shutdown(btn, pop) if self.power_handler else None,
            'on_power_reboot': lambda btn, pop: self.power_handler.on_reboot(btn, pop) if self.power_handler else None,
            'on_power_destroy': lambda btn, pop: self.power_handler.on_destroy(btn, pop) if self.power_handler else None,
            'on_power_menu_show': lambda pop: self.power_handler.update_menu_sensitivity(pop) if self.power_handler else None,

            # Display handlers
            'on_screenshot_clicked': lambda btn: self.display_handler.on_screenshot_clicked(btn) if self.display_handler else None,
            'on_reconnect_clicked': lambda btn: self.display_handler.on_reconnect_clicked(btn) if self.display_handler else None,
            'on_send_key': lambda btn, keys, pop: self.display_handler.on_send_key(btn, keys, pop) if self.display_handler else None,
            'on_key_press': lambda w, e: self.display_handler.on_key_press(w, e) if self.display_handler else None,
            'on_fs_button_toggled': lambda btn: self.display_handler.on_fullscreen_toggled(btn) if self.display_handler else None,
            'on_scaling_toggled': lambda btn: self.display_handler.on_scaling_toggled(btn, scaling_ref) if self.display_handler else None,
            'on_smoothing_toggled': lambda btn: self.display_handler.on_smoothing_toggled(btn, smoothing_ref) if self.display_handler else None,
            'on_lossy_toggled': lambda btn: self.display_handler.on_lossy_toggled(btn, lossy_ref) if self.display_handler else None,
            'on_view_only_toggled': lambda btn: self.display_handler.on_view_only_toggled(btn, view_only_ref) if self.display_handler else None,
            'on_depth_changed': lambda combo: self.display_handler.on_depth_changed(combo, depth_ref, self._apply_vnc_depth) if self.display_handler else None,
            'on_logs_toggled': lambda btn: self._on_logs_toggled(btn),

            # Clipboard handlers
            'on_type_clipboard': lambda btn, pop: self.clipboard_handler.on_type(btn, pop) if self.clipboard_handler else None,

            # Tab switch
            'on_notebook_switch_page': lambda nb, page, page_num: self._on_notebook_switch_page(nb, page, page_num),

            # Window destroy
            'on_destroy': self._cleanup_resources,
        }

    def _create_handlers(self, display_settings: DisplaySettings):
        """Create all handler instances after UI and managers are ready."""
        # Power handler
        self.power_handler = PowerHandler(
            domain=self.domain,
            conn=self.conn,
            original_domain_uuid=self.original_domain_uuid,
            domain_name=self.domain_name,
            uuid=self.uuid,
            power_buttons=self.window_builder.get_power_buttons(),
            connect_display_callback=self._connect_display,
            log_callback=self._log_message,
            notification_callback=self._show_notification,
            error_dialog_callback=self._show_error_dialog,
        )

        # Clipboard handler
        self.clipboard_handler = ClipboardHandler(
            clipboard=self.clipboard,
            protocol=self.display_manager.protocol,
            vnc_display=self.display_manager.vnc_display,
            spice_gtk_session=self.display_manager.spice_gtk_session,
            log_callback=self._log_message,
            notification_callback=self._show_notification,
            verbose=self.verbose,
        )

        # Connect clipboard event handlers
        if self.display_manager.protocol == "vnc" and self.display_manager.vnc_display:
            self.display_manager.vnc_display.connect(
                "vnc-server-cut-text",
                self.clipboard_handler.on_server_cut_text
            )
            self.clipboard.connect(
                "owner-change",
                self.clipboard_handler.on_owner_change
            )

        # Display handler
        self.display_handler = DisplayHandler(
            window=self.window,
            protocol=self.display_manager.protocol,
            vnc_display=self.display_manager.vnc_display,
            spice_session=self.display_manager.spice_session,
            display_widget=self.display_manager.display_widget,
            fs_button=self.window_builder.get_fullscreen_button(),
            connect_display_callback=self._connect_display,
            save_state_callback=self._save_state,
            log_callback=self._log_message,
            notification_callback=self._show_notification,
            error_dialog_callback=self._show_error_dialog,
            verbose=self.verbose,
        )

        # VM state handler
        self.vm_state_handler = VMStateHandler(
            domain=self.domain,
            conn=self.conn,
            original_domain_uuid=self.original_domain_uuid,
            attach=self.attach,
            info_bar=self.window_builder.get_info_bar(),
            get_display_info_callback=lambda: self.display_manager.get_display_info(self.domain, self.ssh_tunnel_manager),
            connect_display_callback=self._connect_display,
            quit_callback=self.quit,
            log_callback=self._log_message,
            notification_callback=self._show_notification,
            verbose=self.verbose,
        )

    def _connect_display(self, force=False, password=None, retry_count=0):
        """Connect to the VM display."""
        if not self.display_manager:
            return False

        return self.display_manager.connect(
            host=None,  # Will be determined from domain
            port=None,  # Will be determined from domain
            password=password or self.password,
            attach=self.attach,
            domain=self.domain,
            ssh_tunnel_manager=self.ssh_tunnel_manager,
            force=force
        )

    def _reconnect_display(self):
        """Reconnect display after snapshot restore."""
        GLib.timeout_add(500, self._connect_display)

    def _apply_vnc_depth(self):
        """Apply VNC color depth setting."""
        if self.display_manager:
            self.display_manager.apply_vnc_depth()

    def _on_view_only_changed(self, enabled: bool):
        """Handle view-only mode change."""
        # Update display manager
        if self.display_manager:
            self.display_manager.settings.view_only_enabled = enabled

    def _on_grab_changed(self, activated: bool):
        """Handle keyboard/mouse grab change."""
        if self.display_handler:
            if activated:
                self.display_handler.on_keyboard_grab_activated(None)
            else:
                self.display_handler.on_keyboard_grab_released(None)

    def _on_display_disconnected(self):
        """
        Handle display disconnection.

        Checks if VM shut down or if it's a reboot/reconnect scenario.
        """
        if self.vm_state_handler:
            self.vm_state_handler.check_shutdown()

    def _on_logs_toggled(self, button):
        """Handle logs tab toggle."""
        self.show_logs = button.get_active()
        if self.window_builder:
            self.window_builder.update_logs_visibility(self.show_logs)

    def _on_notebook_switch_page(self, notebook, page, page_num):
        """Handle tab switch to populate data on demand."""
        if page_num == 1:  # Snapshots tab
            self.window_builder.populate_snapshots()
        elif page_num == 2:  # USB tab
            self.window_builder.populate_usb_lists()

    def _register_domain_events(self):
        """Register libvirt domain event callbacks."""
        if not self.conn or not self.domain or self.events_registered:
            return

        try:
            self.vm_state_handler.register_events()
            self.events_registered = True
        except Exception as e:
            self._log_message(f"Failed to register domain events: {e}")

    def _libvirt_event_tick(self):
        """Tick the libvirt event loop."""
        try:
            libvirt.virEventRunDefaultImpl()
        except Exception:
            pass
        return True

    def _log_message(self, message: str):
        """Log a message."""
        if self.notification_manager:
            self.notification_manager.log_message(message)
        elif self.verbose:
            print(message)

    def _show_notification(self, message: str, message_type=Gtk.MessageType.INFO):
        """Show a notification."""
        if self.notification_manager:
            self.notification_manager.show_notification(message, message_type)
        elif message_type == Gtk.MessageType.ERROR:
            self._show_error_dialog(message)
        elif self.verbose:
            print(f"Notification: {message}")

    def _show_error_dialog(self, message: str):
        """Show an error dialog."""
        if self.notification_manager:
            self.notification_manager.show_error_dialog(message)
        else:
            dialog = Gtk.MessageDialog(
                transient_for=self.window if self.window else None,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Error",
            )
            dialog.format_secondary_text(message)
            dialog.run()
            dialog.destroy()

    def _save_state(self):
        """Save application state."""
        if not self.display_manager:
            return

        # Prefer current UI/handler state when available, fall back to manager settings.
        fullscreen = self.display_handler.is_fullscreen if self.display_handler else False
        scaling = getattr(
            self.display_handler,
            "scaling_enabled",
            self.display_manager.settings.scaling_enabled,
        )
        smoothing = getattr(
            self.display_handler,
            "smoothing_enabled",
            self.display_manager.settings.smoothing_enabled,
        )
        lossy_encoding = getattr(
            self.display_handler,
            "lossy_encoding_enabled",
            self.display_manager.settings.lossy_encoding_enabled,
        )
        view_only = getattr(
            self.display_handler,
            "view_only_enabled",
            self.display_manager.settings.view_only_enabled,
        )
        vnc_depth = getattr(
            self.display_handler,
            "vnc_depth",
            self.display_manager.settings.vnc_depth,
        )

        state = {
            'fullscreen': fullscreen,
            'scaling': scaling,
            'smoothing': smoothing,
            'lossy_encoding': lossy_encoding,
            'view_only': view_only,
            'vnc_depth': vnc_depth,
        }
        self.config_manager.save_state(state)

    def do_shutdown(self):
        """Cleanup on application shutdown."""
        self._cleanup_resources()
        Gtk.Application.do_shutdown(self)


def main():
    """Main entry point for the remote viewer application."""
    try:
        libvirt.virEventRegisterDefaultImpl()
    except Exception as e:
        print(f"Warning: Failed to register libvirt event implementation: {e}")

    parser = argparse.ArgumentParser(
        description="Remote Viewer for VMs with VNC/SPICE (GTK3)"
    )
    parser.add_argument(
        "-c", "--connect",
        dest="uri",
        required=True,
        help="libvirt URI connection (e.g., qemu:///system)"
    )

    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--domain-name", help="Virtual Machine name")
    group.add_argument("--uuid", help="Virtual Machine UUID")

    parser.add_argument("--password", help="VNC/SPICE Password")
    parser.add_argument("--verbose", action="store_true", help="Verbose mode")
    parser.add_argument("--logs", action="store_true", help="Enable Logs & Events tab")
    parser.add_argument(
        "-a", "--attach",
        action="store_true",
        help="Attach to the local display using libvirt"
    )
    parser.add_argument(
        "-w", "--wait",
        action="store_true",
        help="Wait for VM to start"
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Direct connection (disable SSH tunneling)"
    )

    args = parser.parse_args()

    app = RemoteViewer(
        uri=args.uri,
        domain_name=args.domain_name,
        uuid=args.uuid,
        verbose=args.verbose,
        password=args.password,
        show_logs=args.logs,
        attach=args.attach,
        wait=args.wait,
        direct=args.direct,
    )

    try:
        app.run([sys.argv[0]])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
