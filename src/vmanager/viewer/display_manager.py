"""
Display Manager

Handles VNC and SPICE display protocol initialization and connection management.
"""

import re
import xml.etree.ElementTree as ET
from typing import Optional, Tuple, Callable

import gi
import libvirt

gi.require_version("Gtk", "3.0")
gi.require_version("GtkVnc", "2.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gtk, GtkVnc, GLib, GObject

# Try to import SPICE libraries
try:
    gi.require_version("SpiceClientGtk", "3.0")
    gi.require_version("SpiceClientGLib", "2.0")
    from gi.repository import SpiceClientGLib, SpiceClientGtk
    SPICE_AVAILABLE = True
except (ValueError, ImportError):
    SPICE_AVAILABLE = False

from .constants import SSH_TUNNEL_CONNECT_DELAY_MS, RECONNECT_DELAY_MS


class DisplaySettings:
    """Container for display settings."""

    def __init__(self,
                 scaling_enabled: bool = False,
                 smoothing_enabled: bool = True,
                 lossy_encoding_enabled: bool = False,
                 view_only_enabled: bool = False,
                 vnc_depth: int = 0):
        self.scaling_enabled = scaling_enabled
        self.smoothing_enabled = smoothing_enabled
        self.lossy_encoding_enabled = lossy_encoding_enabled
        self.view_only_enabled = view_only_enabled
        self.vnc_depth = vnc_depth


class DisplayManager:
    """
    Manages VNC and SPICE display protocols.

    Handles display initialization, connection management, and protocol-specific settings.
    """

    def __init__(self,
                 log_callback: Optional[Callable[[str], None]] = None,
                 notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None,
                 error_dialog_callback: Optional[Callable[[str], None]] = None,
                 disconnect_callback: Optional[Callable[[], None]] = None,
                 reconnect_callback: Optional[Callable[[], None]] = None,
                 verbose: bool = False):
        """
        Initialize the display manager.

        Args:
            log_callback: Function to call for logging messages
            notification_callback: Function to call for user notifications
            error_dialog_callback: Function to call for error dialogs
            disconnect_callback: Function to call when display disconnects (checks shutdown)
            reconnect_callback: Function to call to reconnect display
            verbose: Whether to print verbose output
        """
        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None
        self.show_error_dialog = error_dialog_callback if error_dialog_callback else lambda msg: None
        self.on_disconnect = disconnect_callback if disconnect_callback else lambda: None
        self.on_reconnect = reconnect_callback if reconnect_callback else lambda: None
        self.verbose = verbose

        # Display widgets
        self.display_widget: Optional[Gtk.Widget] = None
        self.vnc_display: Optional[GtkVnc.Display] = None
        self.spice_session: Optional = None
        self.spice_gtk_session: Optional = None

        # Protocol state
        self.protocol: Optional[str] = None  # 'vnc' or 'spice'
        self.reconnect_pending: bool = False
        self._pending_password: Optional[str] = None

        # Display settings
        self.settings = DisplaySettings()

        # UI elements (set externally)
        self.view_container: Optional[Gtk.Box] = None
        self.window: Optional[Gtk.Window] = None
        self.no_display_label: Optional[Gtk.Label] = None

        # UI widgets for settings visibility
        self.depth_settings_box: Optional[Gtk.Box] = None
        self.lossy_check: Optional[Gtk.CheckButton] = None

        # Clipboard handler ID (for VNC)
        self.clipboard_handler_id: Optional[int] = None

    def set_view_container(self, container: Gtk.Box):
        """Set the container where the display will be added."""
        self.view_container = container

    def set_window(self, window: Gtk.Window):
        """Set the main window for password dialogs."""
        self.window = window

    def set_ui_elements(self, depth_box: Gtk.Box, lossy_check: Gtk.CheckButton):
        """Set UI elements that need protocol-specific visibility control."""
        self.depth_settings_box = depth_box
        self.lossy_check = lossy_check

    def get_display_info(self, domain, ssh_tunnel_manager=None) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Retrieve connection info (protocol, host, port, password) from domain XML.

        Args:
            domain: libvirt domain object
            ssh_tunnel_manager: SSH tunnel manager for setting up tunnels if needed

        Returns:
            Tuple of (protocol, host, port, password)
            password will be None if no password is set in VM XML
        """
        if not domain:
            return None, None, None, None

        try:
            xml_desc = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
            root = ET.fromstring(xml_desc)

            def get_graphics_info(g_node):
                if g_node is None:
                    return None
                port = g_node.get("port")
                if not port or port == "-1":
                    port = g_node.get("tlsPort")

                listen = g_node.get("listen")
                if not listen or listen == "0.0.0.0":
                    listen = "localhost"

                password = g_node.get("passwd")  # Actual password from XML

                if port and port != "-1":
                    return listen, port, password
                return None

            # Check SPICE (only if client is available)
            if SPICE_AVAILABLE:
                info = get_graphics_info(root.find(".//graphics[@type='spice']"))
                if info:
                    listen, port, password = info
                    if ssh_tunnel_manager:
                        self._setup_tunnel_if_needed(ssh_tunnel_manager, listen, port)
                    return "spice", listen, port, password

            # Check VNC
            info = get_graphics_info(root.find(".//graphics[@type='vnc']"))
            if info:
                listen, port, password = info
                if ssh_tunnel_manager:
                    self._setup_tunnel_if_needed(ssh_tunnel_manager, listen, port)
                return "vnc", listen, port, password

        except Exception as e:
            msg = f"XML parse error: {e}"
            self.log(msg)
            if self.verbose:
                print(msg)
        return None, None, None, None

    def _setup_tunnel_if_needed(self, ssh_tunnel_manager, listen: str, port: str):
        """Setup SSH tunnel if needed based on listen address."""
        # If SSH tunnel is configured, setup tunnel for this specific port
        if ssh_tunnel_manager and ssh_tunnel_manager.ssh_gateway:
            remote_host = listen
            if listen == "localhost" or listen == "0.0.0.0":
                # Extract remote host from libvirt URI
                match = re.search(r"qemu\+ssh://(?:[^@]+@)?([^/:]+)", ssh_tunnel_manager.ssh_gateway)
                if match:
                    remote_host = match.group(1)

            if remote_host:
                ssh_tunnel_manager.start(remote_host, int(port))

    def init_display(self, domain=None, ssh_tunnel_manager=None, view_only_handler=None, grab_handler=None) -> Optional[Gtk.Widget]:
        """
        Initialize the display widget based on detected protocol.

        Args:
            domain: libvirt domain to detect protocol from (optional)
            ssh_tunnel_manager: SSH tunnel manager for remote connections (optional)
            view_only_handler: Callback for VNC server cut text (clipboard)
            grab_handler: Callbacks for keyboard grab events

        Returns:
            The initialized display widget, or None if no protocol available
        """
        # Detect protocol if not already set and domain is provided
        if self.protocol is None and domain:
            protocol, _, _, _ = self.get_display_info(domain, ssh_tunnel_manager)
            if protocol:
                self.protocol = protocol
                self.log(f"Detected protocol: {protocol}")

        # Cleanup existing display widget
        if self.display_widget:
            parent = self.display_widget.get_parent()
            if parent and isinstance(parent, Gtk.ScrolledWindow):
                if parent.get_parent() == self.view_container:
                    self.view_container.remove(parent)
                parent.destroy()
            elif parent == self.view_container:
                self.view_container.remove(self.display_widget)

            self.display_widget.destroy()
            self.display_widget = None

        # Remove any existing placeholder
        self._remove_no_display_placeholder()

        # Disconnect previous clipboard handler if exists
        if self.clipboard_handler_id:
            # This will be handled by clipboard handler module
            pass

        self.log(f"Initializing display for protocol: {self.protocol}")

        if self.protocol is None:
            self.log("No display protocol detected. Skipping display initialization.")
            self._show_no_display_placeholder()
            return None

        scroll = Gtk.ScrolledWindow()

        if self.protocol == "spice" and SPICE_AVAILABLE:
            self._init_spice_display(grab_handler)
        else:
            self._init_vnc_display(view_only_handler, grab_handler)

        scroll.add(self.display_widget)
        self.view_container.pack_start(scroll, True, True, 0)
        self.view_container.show_all()

        return self.display_widget

    def _init_spice_display(self, grab_handler=None):
        """Initialize SPICE display widget."""
        if self.depth_settings_box:
            self.depth_settings_box.set_visible(False)
        if self.lossy_check:
            self.lossy_check.set_visible(False)

        self.spice_session = SpiceClientGLib.Session()

        # Connect SPICE session signals using GObject.Object.connect to avoid conflict with Session.connect()
        GObject.Object.connect(self.spice_session, "channel-new", self.on_spice_channel_new)

        try:
            self.spice_gtk_session = SpiceClientGtk.GtkSession.get(self.spice_session)
            self.spice_gtk_session.set_property("auto-clipboard", True)
        except Exception as e:
            if self.verbose:
                print(f"Failed to configure SPICE clipboard: {e}")

        self.display_widget = SpiceClientGtk.Display(session=self.spice_session)

        # SPICE specific configs
        self.display_widget.set_property("scaling", self.settings.scaling_enabled)
        self.display_widget.set_property("grab-keyboard", True)
        self.display_widget.set_property("grab-mouse", True)

        # Connect SPICE grab property notifications
        if grab_handler and hasattr(grab_handler, 'on_spice_grab_changed'):
            self.display_widget.connect("notify::grab-keyboard", grab_handler.on_spice_grab_changed)

    def _init_vnc_display(self, clipboard_handler=None, grab_handler=None):
        """Initialize VNC display widget."""
        GLib.MainContext.default().iteration(False)

        if self.depth_settings_box:
            self.depth_settings_box.set_visible(True)
        if self.lossy_check:
            self.lossy_check.set_visible(True)

        self.protocol = "vnc"  # Fallback if spice not available
        self.vnc_display = GtkVnc.Display()
        self.display_widget = self.vnc_display

        self.vnc_display.set_pointer_local(True)
        self.vnc_display.set_scaling(self.settings.scaling_enabled)
        self.vnc_display.set_smoothing(self.settings.smoothing_enabled)
        self.vnc_display.set_keep_aspect_ratio(True)
        self.vnc_display.set_lossy_encoding(self.settings.lossy_encoding_enabled)
        self.vnc_display.set_read_only(self.settings.view_only_enabled)
        self._apply_vnc_depth()

        # Enable keyboard grab
        self.vnc_display.set_keyboard_grab(True)
        self.vnc_display.set_pointer_grab(True)

        # Connect VNC signals
        self.vnc_display.connect("vnc-disconnected", self.on_vnc_disconnected)
        self.vnc_display.connect("vnc-connected", self.on_vnc_connected)
        self.vnc_display.connect("vnc-auth-credential", self.on_vnc_auth_credential)

        # Connect clipboard and grab handlers if provided
        if clipboard_handler:
            if hasattr(clipboard_handler, 'on_vnc_server_cut_text'):
                self.vnc_display.connect("vnc-server-cut-text", clipboard_handler.on_vnc_server_cut_text)
            elif hasattr(clipboard_handler, 'on_server_cut_text'):
                self.vnc_display.connect("vnc-server-cut-text", clipboard_handler.on_server_cut_text)
        if grab_handler:
            if hasattr(grab_handler, 'on_keyboard_grab_activated'):
                self.vnc_display.connect("vnc-grab-keyboard", grab_handler.on_keyboard_grab_activated)
            if hasattr(grab_handler, 'on_keyboard_grab_released'):
                self.vnc_display.connect("vnc-ungrab-keyboard", grab_handler.on_keyboard_grab_released)

    def _show_no_display_placeholder(self):
        """Show a placeholder message when no display protocol is available."""
        self._remove_no_display_placeholder()

        self.no_display_label = Gtk.Label()
        self.no_display_label.set_markup(
            "<span size='large'><b>No Display Available</b></span>\n\n"
            "The VM does not have an active graphics device (VNC or SPICE),\n"
            "or the VM is not currently running.\n\n"
            "Possible actions:\n"
            "  - Start the VM using the power menu\n"
            "  - Check that the VM has a graphics device configured\n"
            "  - Click Reconnect once the VM is running"
        )
        self.no_display_label.set_justify(Gtk.Justification.CENTER)
        self.no_display_label.set_valign(Gtk.Align.CENTER)
        self.no_display_label.set_halign(Gtk.Align.CENTER)
        self.no_display_label.set_vexpand(True)

        self.view_container.pack_start(self.no_display_label, True, True, 0)
        self.no_display_label.show()

    def _remove_no_display_placeholder(self):
        """Remove the no-display placeholder if it exists."""
        if self.no_display_label and self.no_display_label.get_parent():
            self.view_container.remove(self.no_display_label)
            self.no_display_label = None

    def connect(self, host: str, port: int, password: Optional[str] = None,
                attach: bool = False, domain=None, ssh_tunnel_manager=None,
                force: bool = False) -> bool:
        """
        Connect to the display server.

        Args:
            host: Host to connect to (None to auto-detect from domain)
            port: Port to connect to (None to auto-detect from domain)
            password: Password for authentication (optional)
            attach: Use libvirt attach mode instead of network connection
            domain: libvirt domain (required for attach mode or auto-detection)
            ssh_tunnel_manager: SSH tunnel manager for tunneled connections
            force: Force reconnection even if already connected

        Returns:
            True if connection initiated successfully
        """
        self._pending_password = password

        try:
            if attach:
                return self._connect_attach(domain, force)

            # Get display info if host/port not provided
            if host is None or port is None:
                if not domain:
                    self.log("Cannot connect: no domain provided for auto-detection")
                    return False

                protocol, detected_host, detected_port, xml_password = self.get_display_info(
                    domain, ssh_tunnel_manager
                )

                if not protocol:
                    self.log("No display protocol detected")
                    return False

                host = detected_host
                port = detected_port

                # Use detected password if none provided
                if password is None and xml_password:
                    password = xml_password

            # Standard network connection
            if self.protocol == "spice" and SPICE_AVAILABLE:
                return self._connect_spice(host, port, password, ssh_tunnel_manager)
            else:
                return self._connect_vnc(host, port, password, ssh_tunnel_manager, force)

        except Exception as e:
            if self.verbose:
                print(f"Connection failed: {e}")
            self.notify(f"Connection failed: {e}", Gtk.MessageType.ERROR)
            return False

    def _connect_attach(self, domain, force: bool) -> bool:
        """Connect using libvirt attach mode."""
        if not domain:
            return False

        try:
            fd = domain.openGraphicsFD(0)
        except libvirt.libvirtError as e:
            self.notify(f"Failed to attach to graphics: {e}", Gtk.MessageType.ERROR)
            return False

        if self.protocol == "spice" and SPICE_AVAILABLE:
            self.spice_session.open_fd(fd)
        elif self.protocol == "vnc":
            if self.vnc_display.is_open() and force:
                self.vnc_display.close()
            self.reconnect_pending = False
            self._apply_vnc_depth()
            self.vnc_display.open_fd(fd)

        return True

    def _connect_spice(self, host: str, port: int, password: Optional[str],
                      ssh_tunnel_manager=None) -> bool:
        """Connect to SPICE server."""
        # Use tunneled connection if SSH tunnel is active
        if ssh_tunnel_manager and ssh_tunnel_manager.is_active():
            host = "localhost"
            port = ssh_tunnel_manager.get_local_port()
            self.log(f"Using SSH tunnel: localhost:{port}")
            # Delay connection to allow tunnel to establish
            GLib.timeout_add(SSH_TUNNEL_CONNECT_DELAY_MS,
                           lambda: self._do_spice_connect(host, port, password))
            return True

        self._do_spice_connect(host, port, password)
        return True

    def _connect_vnc(self, host: str, port: int, password: Optional[str],
                    ssh_tunnel_manager=None, force: bool = False) -> bool:
        """Connect to VNC server."""
        # Use tunneled connection if SSH tunnel is active
        if ssh_tunnel_manager and ssh_tunnel_manager.is_active():
            host = "localhost"
            port = ssh_tunnel_manager.get_local_port()
            self.log(f"Using SSH tunnel: localhost:{port}")
            # Delay connection to allow tunnel to establish
            GLib.timeout_add(SSH_TUNNEL_CONNECT_DELAY_MS,
                           lambda: self._do_vnc_connect(host, port, force))
            return True

        self._do_vnc_connect(host, port, force)
        return True

    def _do_spice_connect(self, host: str, port: int, password: Optional[str]):
        """Perform the actual SPICE connection (called directly or via GLib.timeout)."""
        try:
            uri = f"spice://{host}:{port}"
            self.log(f"Connecting to SPICE at {uri}")
            self.spice_session.set_property("uri", uri)
            if password:
                self.spice_session.set_property("password", password)
            self.spice_session.connect()
        except Exception as e:
            error_msg = f"Failed to connect to SPICE server at {host}:{port}\n\nError: {e}"
            self.log(f"ERROR: {error_msg}")
            self.notify(error_msg, Gtk.MessageType.ERROR)
        return False  # Don't repeat the timeout

    def _do_vnc_connect(self, host: str, port: int, force: bool = False):
        """Perform the actual VNC connection (called directly or via GLib.timeout)."""
        try:
            self.log(f"Connecting to VNC at {host}:{port}")
            if self.vnc_display.is_open():
                if force:
                    if self.verbose:
                        print("Forcing reconnection (closing first)...")
                    self.reconnect_pending = True
                    self.vnc_display.close()
                return False

            # Ensure no pending reconnect if opening normally
            self.reconnect_pending = False
            # Re-apply depth setting before connecting
            self._apply_vnc_depth()

            self.vnc_display.open_host(host, str(port))
        except Exception as e:
            error_msg = f"Failed to connect to VNC server at {host}:{port}\n\nError: {e}"
            self.log(f"ERROR: {error_msg}")
            self.notify(error_msg, Gtk.MessageType.ERROR)
        return False  # Don't repeat the timeout

    def _apply_vnc_depth(self):
        """Apply VNC color depth setting (internal)."""
        if not self.vnc_display:
            return

        depth_enum = GtkVnc.DisplayDepthColor.DEFAULT
        if self.settings.vnc_depth == 24:
            depth_enum = GtkVnc.DisplayDepthColor.FULL
        elif self.settings.vnc_depth == 16:
            depth_enum = GtkVnc.DisplayDepthColor.MEDIUM
        elif self.settings.vnc_depth == 8:
            depth_enum = GtkVnc.DisplayDepthColor.LOW

        self.vnc_display.set_depth(depth_enum)

    def apply_vnc_depth(self):
        """Public method to apply VNC color depth setting."""
        self._apply_vnc_depth()

    def apply_settings(self, settings: DisplaySettings):
        """Apply display settings to the active connection."""
        self.settings = settings

        if self.protocol == "vnc" and self.vnc_display:
            self.vnc_display.set_scaling(settings.scaling_enabled)
            self.vnc_display.set_smoothing(settings.smoothing_enabled)
            self.vnc_display.set_lossy_encoding(settings.lossy_encoding_enabled)
            self.vnc_display.set_read_only(settings.view_only_enabled)
            self._apply_vnc_depth()
        elif self.protocol == "spice" and self.display_widget:
            self.display_widget.set_property("scaling", settings.scaling_enabled)

    # VNC Signal Handlers
    def on_vnc_auth_credential(self, vnc, cred_list):
        """Handle VNC authentication request."""
        if self.verbose:
            print("VNC Auth Credential requested")

        password = self._pending_password
        if password is None:
            password = self._prompt_for_password("VNC")

        if password:
            self.vnc_display.set_credential(GtkVnc.DisplayCredential.PASSWORD, password)
        else:
            self.vnc_display.close()

    def on_vnc_connected(self, vnc):
        """Handle VNC connected event."""
        if self.verbose:
            print("VNC Connected")

    def on_vnc_disconnected(self, vnc):
        """Handle VNC disconnected event."""
        if self.verbose:
            print("VNC Disconnected")

        if self.reconnect_pending:
            if self.verbose:
                print("Pending reconnect detected, reconnecting in 500ms...")
            self.reconnect_pending = False
            # Delay reconnection briefly
            def do_reconnect():
                self.on_reconnect()
                return False  # Don't repeat the timeout
            GLib.timeout_add(RECONNECT_DELAY_MS, do_reconnect)
            return

        # Call disconnect callback to check if VM shut down or needs reconnect
        self.on_disconnect()

    def on_spice_channel_new(self, session, channel):
        """Handle SPICE new channel event."""
        if self.verbose:
            print(f"SPICE channel created: {channel}")
        # Connect to channel events to detect disconnections
        GObject.Object.connect(channel, "channel-event", self.on_spice_channel_event)

    def on_spice_channel_event(self, channel, event):
        """Handle SPICE channel events."""
        if self.verbose:
            print(f"SPICE channel event: {event}")

        # SPICE_CHANNEL_OPENED = 0
        # SPICE_CHANNEL_SWITCHING = 1
        # SPICE_CHANNEL_CLOSED = 2
        # SPICE_CHANNEL_ERROR_CONNECT = 3
        # SPICE_CHANNEL_ERROR_TLS = 4
        # SPICE_CHANNEL_ERROR_LINK = 5
        # SPICE_CHANNEL_ERROR_AUTH = 6
        # SPICE_CHANNEL_ERROR_IO = 7

        if event == SpiceClientGLib.ChannelEvent.OPENED:
            if self.verbose:
                print("SPICE channel opened")
        elif event == SpiceClientGLib.ChannelEvent.CLOSED:
            if self.verbose:
                print("SPICE channel closed")
        elif event in [SpiceClientGLib.ChannelEvent.ERROR_CONNECT,
                       SpiceClientGLib.ChannelEvent.ERROR_TLS,
                       SpiceClientGLib.ChannelEvent.ERROR_LINK,
                       SpiceClientGLib.ChannelEvent.ERROR_AUTH,
                       SpiceClientGLib.ChannelEvent.ERROR_IO]:
            if self.verbose:
                print(f"SPICE channel error: {event}")

    def _prompt_for_password(self, protocol: str) -> Optional[str]:
        """Prompt user for password via dialog."""
        dialog = Gtk.Dialog(
            title=f"{protocol.upper()} Password Required",
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )

        hbox = Gtk.Box(spacing=6)
        dialog.get_content_area().pack_start(hbox, True, True, 0)

        label = Gtk.Label(label=f"Enter password for {protocol.upper()}:")
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

    def disconnect(self):
        """Disconnect from display server."""
        if self.protocol == "vnc" and self.vnc_display:
            if self.vnc_display.is_open():
                self.vnc_display.close()
        elif self.protocol == "spice" and self.spice_session:
            if self.spice_session.is_connected():
                self.spice_session.disconnect()

    def is_connected(self) -> bool:
        """Check if display is connected."""
        if self.protocol == "vnc" and self.vnc_display:
            return self.vnc_display.is_open()
        elif self.protocol == "spice" and self.spice_session:
            return self.spice_session.is_connected()
        return False


__all__ = ['DisplayManager', 'DisplaySettings', 'SPICE_AVAILABLE']
