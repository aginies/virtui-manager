"""
Main Window Builder

Builds the main viewer window with header bar, tabs, and all UI components.
"""

from typing import Optional, Callable, Dict, Any

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .menus import (
    build_settings_menu,
    build_power_menu,
    build_keys_menu,
    build_clipboard_menu,
)
from .console_tab import ConsoleTab
from .snapshot_tab import SnapshotTab
from .usb_tab import USBTab


class MainWindowBuilder:
    """
    Builds the main viewer window and all its components.

    Separates UI construction from business logic, making it easier to test
    and maintain the window structure.
    """

    def __init__(
        self,
        application,
        domain,
        conn,
        domain_name: str,
        uri: str,
        attach: bool,
        is_fullscreen: bool,
        show_logs: bool,
        # Display settings
        scaling_enabled: bool,
        smoothing_enabled: bool,
        lossy_encoding_enabled: bool,
        view_only_enabled: bool,
        vnc_depth: int,
        # Boot settings
        boot_devices: list[tuple[str, str]],
        current_boot_device: Optional[str],
        # Callbacks
        log_callback: Optional[Callable[[str], None]] = None,
        notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None,
        reconnect_callback: Optional[Callable[[], None]] = None,
    ):
        """
        Initialize the main window builder.

        Args:
            application: Gtk.Application instance
            domain: libvirt domain object
            conn: libvirt connection object
            domain_name: Name of the VM
            uri: Connection URI
            attach: Whether using attach mode
            is_fullscreen: Initial fullscreen state
            show_logs: Whether to show logs tab
            scaling_enabled: Initial scaling state
            smoothing_enabled: Initial smoothing state
            lossy_encoding_enabled: Initial lossy encoding state
            view_only_enabled: Initial view-only state
            vnc_depth: Initial VNC color depth
            boot_devices: List of (device_id, label) for boot selection
            current_boot_device: Initial boot device ID
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
            reconnect_callback: Function to call for reconnection after snapshot restore
        """
        self.application = application
        self.domain = domain
        self.conn = conn
        self.domain_name = domain_name
        self.uri = uri
        self.attach = attach
        self.is_fullscreen = is_fullscreen
        self.show_logs = show_logs

        # Display settings
        self.scaling_enabled = scaling_enabled
        self.smoothing_enabled = smoothing_enabled
        self.lossy_encoding_enabled = lossy_encoding_enabled
        self.view_only_enabled = view_only_enabled
        self.vnc_depth = vnc_depth

        # Boot settings
        self.boot_devices = boot_devices
        self.current_boot_device = current_boot_device

        # Callbacks
        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None
        self.reconnect = reconnect_callback if reconnect_callback else lambda: None

        # UI components (will be set during build)
        self.window: Optional[Gtk.ApplicationWindow] = None
        self.main_box: Optional[Gtk.Box] = None
        self.info_bar: Optional[Gtk.InfoBar] = None
        self.info_bar_label: Optional[Gtk.Label] = None
        self.notebook: Optional[Gtk.Notebook] = None
        self.view_container: Optional[Gtk.Box] = None
        self.log_view: Optional[Gtk.TextView] = None
        self.log_buffer: Optional[Gtk.TextBuffer] = None
        self.log_scroll: Optional[Gtk.ScrolledWindow] = None

        # Header bar widgets
        self.fs_button: Optional[Gtk.ToggleButton] = None
        self.logs_button: Optional[Gtk.ToggleButton] = None
        self.depth_settings_box: Optional[Gtk.Box] = None
        self.lossy_check: Optional[Gtk.CheckButton] = None
        self.boot_combo: Optional[Gtk.ComboBoxText] = None
        self.power_buttons: Dict[str, Gtk.ModelButton] = {}

        # Tab components
        self.console_tab_instance: Optional[ConsoleTab] = None
        self.snapshot_tab_instance: Optional[SnapshotTab] = None
        self.usb_tab_instance: Optional[USBTab] = None

    def build_window(self, handlers: Dict[str, Any]) -> Gtk.ApplicationWindow:
        """
        Build the complete main window.

        Args:
            handlers: Dictionary of event handler callbacks

        Returns:
            The main application window
        """
        # Determine title
        title = f"{self.domain_name} - Virtui Manager Viewer"
        subtitle = self.uri
        if self.attach:
            subtitle += " (Attached)"

        # Main Window
        self.window = Gtk.ApplicationWindow(application=self.application, title=title)
        self.window.set_default_size(1024, 768)

        # Build header bar
        header = self._build_header_bar(title, subtitle, handlers)
        self.window.set_titlebar(header)

        # Main layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.window.add(self.main_box)

        # Info bar for notifications
        self._build_info_bar()
        self.main_box.pack_start(self.info_bar, False, False, 0)

        # Tabs
        self._build_tabs(handlers)
        self.main_box.pack_start(self.notebook, True, True, 0)

        # Connect window signals
        self.window.connect("key-press-event", handlers.get("on_key_press"))
        self.window.connect("destroy", handlers.get("on_destroy"))

        # Apply initial fullscreen state
        if self.is_fullscreen:
            self.window.fullscreen()

        return self.window

    def _build_header_bar(
        self,
        title: str,
        subtitle: str,
        handlers: Dict[str, Any]
    ) -> Gtk.HeaderBar:
        """Build the header bar with all buttons and menus."""
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(title)
        header.set_subtitle(subtitle)

        # Keyboard shortcut hint (left side)
        shortcut_label = Gtk.Label()
        shortcut_label.set_markup("<span foreground='#666666'>Ctrl+Alt to release</span>")
        shortcut_label.set_tooltip_text("Press Ctrl+Alt to release grabbed keyboard/mouse")
        header.pack_start(shortcut_label)

        # Settings Menu
        settings_button, self.depth_settings_box, self.lossy_check, self.boot_combo = build_settings_menu(
            scaling_enabled=self.scaling_enabled,
            smoothing_enabled=self.smoothing_enabled,
            lossy_encoding_enabled=self.lossy_encoding_enabled,
            view_only_enabled=self.view_only_enabled,
            vnc_depth=self.vnc_depth,
            boot_devices=self.boot_devices,
            current_boot_device=self.current_boot_device,
            on_scaling_toggled=handlers.get("on_scaling_toggled"),
            on_smoothing_toggled=handlers.get("on_smoothing_toggled"),
            on_lossy_toggled=handlers.get("on_lossy_toggled"),
            on_view_only_toggled=handlers.get("on_view_only_toggled"),
            on_depth_changed=handlers.get("on_depth_changed"),
            on_boot_device_changed=handlers.get("on_boot_device_changed"),
            on_menu_show=handlers.get("on_settings_menu_show"),
        )
        header.pack_end(settings_button)

        # Power Menu
        power_button, self.power_buttons = build_power_menu(
            on_start=handlers.get("on_power_start"),
            on_pause=handlers.get("on_power_pause"),
            on_resume=handlers.get("on_power_resume"),
            on_hibernate=handlers.get("on_power_hibernate"),
            on_shutdown=handlers.get("on_power_shutdown"),
            on_reboot=handlers.get("on_power_reboot"),
            on_destroy=handlers.get("on_power_destroy"),
            on_menu_show=handlers.get("on_power_menu_show"),
        )
        header.pack_end(power_button)

        # Send Keys Menu
        keys_button = build_keys_menu(
            on_send_key=handlers.get("on_send_key")
        )
        header.pack_end(keys_button)

        # Clipboard Menu
        clip_button = build_clipboard_menu(
            on_type_clipboard=handlers.get("on_type_clipboard"),
        )
        header.pack_end(clip_button)

        # Screenshot Button
        screenshot_button = Gtk.Button()
        icon_screenshot = Gtk.Image.new_from_icon_name("camera-photo-symbolic", Gtk.IconSize.BUTTON)
        screenshot_button.set_image(icon_screenshot)
        screenshot_button.set_tooltip_text("Take Screenshot")
        screenshot_button.connect("clicked", handlers.get("on_screenshot_clicked"))
        header.pack_end(screenshot_button)

        # Reconnect Button
        reconnect_button = Gtk.Button()
        icon_reconnect = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        reconnect_button.set_image(icon_reconnect)
        reconnect_button.set_tooltip_text("Reconnect Display")
        reconnect_button.connect("clicked", handlers.get("on_reconnect_clicked"))
        header.pack_end(reconnect_button)

        # Fullscreen Button
        self.fs_button = Gtk.ToggleButton()
        icon_fs = Gtk.Image.new_from_icon_name("view-fullscreen-symbolic", Gtk.IconSize.BUTTON)
        self.fs_button.set_image(icon_fs)
        self.fs_button.set_tooltip_text("Toggle Fullscreen")
        self.fs_button.set_active(self.is_fullscreen)
        self.fs_button.connect("toggled", handlers.get("on_fs_button_toggled"))
        header.pack_end(self.fs_button)

        # Logs Toggle Button
        self.logs_button = Gtk.ToggleButton()
        icon_logs = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic", Gtk.IconSize.BUTTON)
        self.logs_button.set_image(icon_logs)
        self.logs_button.set_tooltip_text("Toggle Logs & Events")
        self.logs_button.set_active(self.show_logs)
        self.logs_button.connect("toggled", handlers.get("on_logs_toggled"))
        header.pack_end(self.logs_button)

        return header

    def _build_info_bar(self):
        """Build the notification info bar."""
        self.info_bar = Gtk.InfoBar()
        self.info_bar.set_revealed(False)
        self.info_bar.set_show_close_button(True)
        self.info_bar.connect("response", lambda bar, resp: bar.set_revealed(False))

        content = self.info_bar.get_content_area()
        self.info_bar_label = Gtk.Label()
        self.info_bar_label.set_line_wrap(True)
        content.add(self.info_bar_label)

    def _build_tabs(self, handlers: Dict[str, Any]):
        """Build all notebook tabs."""
        self.notebook = Gtk.Notebook()

        # Tab 1: Display
        self.display_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.notebook.append_page(self.display_tab, Gtk.Label(label="Display"))
        self.view_container = self.display_tab

        # Tab 2: Snapshots
        self.snapshot_tab_instance = SnapshotTab(
            domain=self.domain,
            window=self.window,
            log_callback=self.log,
            notification_callback=self.notify,
            reconnect_callback=self.reconnect,
        )
        snapshot_tab_widget = self.snapshot_tab_instance.build_tab()
        self.notebook.append_page(snapshot_tab_widget, Gtk.Label(label="Snapshots"))

        # Tab 3: USB Devices
        self.usb_tab_instance = USBTab(
            domain=self.domain,
            conn=self.conn,
            window=self.window,
            log_callback=self.log,
            notification_callback=self.notify,
        )
        usb_tab_widget = self.usb_tab_instance.build_tab()
        self.notebook.append_page(usb_tab_widget, Gtk.Label(label="USB Devices"))

        # Tab 4: Serial Console
        self.console_tab_instance = ConsoleTab(
            domain=self.domain,
            conn=self.conn,
            log_callback=self.log,
            notification_callback=self.notify,
        )
        console_tab_widget = self.console_tab_instance.build_tab()
        self.notebook.append_page(console_tab_widget, Gtk.Label(label="Serial Console"))

        # Tab 5: Logs & Events
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()

        self.log_scroll = Gtk.ScrolledWindow()
        self.log_scroll.add(self.log_view)
        self.notebook.append_page(self.log_scroll, Gtk.Label(label="Logs & Events"))

        # Connect tab switch handler
        self.notebook.connect("switch-page", handlers.get("on_notebook_switch_page"))

    def get_view_container(self) -> Gtk.Box:
        """Get the display tab container for adding display widgets."""
        return self.view_container

    def get_log_buffer(self) -> Gtk.TextBuffer:
        """Get the log text buffer."""
        return self.log_buffer

    def get_info_bar(self) -> Gtk.InfoBar:
        """Get the info bar widget."""
        return self.info_bar

    def get_info_bar_label(self) -> Gtk.Label:
        """Get the info bar label widget."""
        return self.info_bar_label

    def get_power_buttons(self) -> Dict[str, Gtk.ModelButton]:
        """Get the power menu buttons."""
        return self.power_buttons

    def get_depth_settings_box(self) -> Gtk.Box:
        """Get the VNC depth settings box."""
        return self.depth_settings_box

    def get_lossy_check(self) -> Gtk.CheckButton:
        """Get the lossy encoding checkbox."""
        return self.lossy_check

    def get_boot_combo(self) -> Gtk.ComboBoxText:
        """Get the boot device selector combo box."""
        return self.boot_combo

    def get_fullscreen_button(self) -> Gtk.ToggleButton:
        """Get the fullscreen toggle button."""
        return self.fs_button

    def get_logs_button(self) -> Gtk.ToggleButton:
        """Get the logs toggle button."""
        return self.logs_button

    def get_notebook(self) -> Gtk.Notebook:
        """Get the notebook widget."""
        return self.notebook

    def get_snapshot_tab(self) -> SnapshotTab:
        """Get the snapshot tab instance."""
        return self.snapshot_tab_instance

    def get_usb_tab(self) -> USBTab:
        """Get the USB tab instance."""
        return self.usb_tab_instance

    def get_console_tab(self) -> ConsoleTab:
        """Get the console tab instance."""
        return self.console_tab_instance

    def update_logs_visibility(self, show: bool):
        """
        Update visibility of the Logs & Events tab and other tabs.

        When logs are enabled, all tabs are shown with tabs visible.
        When logs are disabled, only the Display tab is shown with tabs hidden.

        Args:
            show: Whether to show the logs/extra tabs
        """
        if not self.notebook:
            return

        # Page numbers: 0=Display, 1=Snapshots, 2=USB, 3=Console, 4=Logs
        snapshots_page_num = 1
        usb_page_num = 2
        console_page_num = 3
        logs_page_num = 4

        if show:
            # Show all tabs
            self.notebook.get_nth_page(logs_page_num).show()
            self.notebook.get_nth_page(snapshots_page_num).show()
            self.notebook.get_nth_page(usb_page_num).show()
            self.notebook.get_nth_page(console_page_num).show()
            self.notebook.set_show_tabs(True)
        else:
            # Hide extra tabs, show only Display
            self.notebook.get_nth_page(logs_page_num).hide()
            self.notebook.get_nth_page(snapshots_page_num).hide()
            self.notebook.get_nth_page(usb_page_num).hide()
            self.notebook.get_nth_page(console_page_num).hide()
            self.notebook.set_show_tabs(False)

        # Always switch to Display tab when toggling
        self.notebook.set_current_page(0)

    def populate_snapshots(self):
        """Populate snapshots in the snapshot tab."""
        if self.snapshot_tab_instance:
            self.snapshot_tab_instance.populate_snapshots()

    def populate_usb_lists(self):
        """Populate USB device lists in the USB tab."""
        if self.usb_tab_instance:
            self.usb_tab_instance.populate_usb_lists()

    def update_snapshot_restore_button(self):
        """Update snapshot restore button sensitivity based on VM state."""
        if self.snapshot_tab_instance:
            self.snapshot_tab_instance.update_restore_button_sensitivity()

    def disconnect_console(self):
        """Disconnect the serial console."""
        if self.console_tab_instance:
            self.console_tab_instance.disconnect()


__all__ = ['MainWindowBuilder']
