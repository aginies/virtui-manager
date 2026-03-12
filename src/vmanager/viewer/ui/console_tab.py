"""
Serial Console Tab

Provides serial console access to VMs for text-based interaction.
"""

import libvirt
from typing import Optional, Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib


class ConsoleTab:
    """
    Manages the serial console tab UI and functionality.

    Provides text-based access to VM serial console for troubleshooting and headless access.
    """

    def __init__(self, domain, conn,
                 log_callback: Optional[Callable[[str], None]] = None,
                 notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None):
        """
        Initialize the console tab.

        Args:
            domain: libvirt domain object
            conn: libvirt connection object
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
        """
        self.domain = domain
        self.conn = conn
        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None

        # Console state
        self.console_stream: Optional[libvirt.virStream] = None
        self.console_connected: bool = False

        # UI elements (created in build_tab)
        self.console_view: Optional[Gtk.TextView] = None
        self.console_buffer: Optional[Gtk.TextBuffer] = None
        self.console_input: Optional[Gtk.Entry] = None
        self.console_connect_button: Optional[Gtk.Button] = None
        self.console_disconnect_button: Optional[Gtk.Button] = None

    def build_tab(self) -> Gtk.Box:
        """
        Build the serial console tab UI.

        Returns:
            The tab container widget
        """
        tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tab.set_border_width(10)

        # Console output
        self.console_view = Gtk.TextView()
        self.console_view.set_editable(False)
        self.console_view.set_monospace(True)
        self.console_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.console_buffer = self.console_view.get_buffer()

        console_scroll = Gtk.ScrolledWindow()
        console_scroll.set_vexpand(True)
        console_scroll.add(self.console_view)
        tab.pack_start(console_scroll, True, True, 0)

        # Console input area
        console_input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab.pack_start(console_input_box, False, False, 0)

        input_label = Gtk.Label(label="Input:")
        console_input_box.pack_start(input_label, False, False, 0)

        self.console_input = Gtk.Entry()
        self.console_input.set_placeholder_text("Type command and press Enter...")
        self.console_input.connect("activate", self.on_console_input_activate)
        console_input_box.pack_start(self.console_input, True, True, 0)

        # Console control buttons
        console_buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab.pack_start(console_buttons_box, False, False, 0)

        self.console_connect_button = Gtk.Button(label="Connect to Console")
        self.console_connect_button.connect("clicked", self.on_console_connect_clicked)
        console_buttons_box.pack_start(self.console_connect_button, True, True, 0)

        self.console_disconnect_button = Gtk.Button(label="Disconnect Console")
        self.console_disconnect_button.connect("clicked", self.on_console_disconnect_clicked)
        self.console_disconnect_button.set_sensitive(False)
        console_buttons_box.pack_start(self.console_disconnect_button, True, True, 0)

        console_clear_button = Gtk.Button(label="Clear Output")
        console_clear_button.connect("clicked", self.on_console_clear_clicked)
        console_buttons_box.pack_start(console_clear_button, True, True, 0)

        console_help_button = Gtk.Button(label="Help")
        console_help_button.connect("clicked", self.on_console_help_clicked)
        console_buttons_box.pack_start(console_help_button, True, True, 0)

        return tab

    def on_console_connect_clicked(self, button):
        """Connect to the serial console."""
        if not self.domain:
            self.notify("No domain available for console connection.", Gtk.MessageType.ERROR)
            return

        if self.console_connected:
            self.notify("Console already connected.", Gtk.MessageType.INFO)
            return

        try:
            self.log("Connecting to serial console...")
            # Try common console names
            console_names = ["serial0", "console.0", "console"]

            for console_name in console_names:
                try:
                    self.console_stream = self.conn.newStream(libvirt.VIR_STREAM_NONBLOCK)
                    self.domain.openConsole(console_name, self.console_stream, 0)
                    self.log(f"Connected to console: {console_name}")
                    break
                except libvirt.libvirtError as e:
                    if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                        continue
                    else:
                        raise

            if not self.console_stream:
                raise libvirt.libvirtError("No serial console device found")

            # Set up stream callbacks
            self.console_stream.eventAddCallback(
                libvirt.VIR_STREAM_EVENT_READABLE, self._console_stream_callback, None
            )

            self.console_connected = True
            self.console_connect_button.set_sensitive(False)
            self.console_disconnect_button.set_sensitive(True)
            self.console_input.set_sensitive(True)
            self.notify("Serial console connected.", Gtk.MessageType.INFO)

            # Start receiving data
            GLib.timeout_add(100, self._console_receive_data)

        except libvirt.libvirtError as e:
            self.log(f"Failed to connect to console: {e}")
            self.notify(f"Failed to connect to console: {e}", Gtk.MessageType.ERROR)
            if self.console_stream:
                try:
                    self.console_stream.finish()
                except:
                    pass
                self.console_stream = None

    def on_console_disconnect_clicked(self, button):
        """Disconnect from the serial console."""
        if not self.console_connected:
            return

        try:
            if self.console_stream:
                self.console_stream.eventRemoveCallback()
                self.console_stream.finish()
                self.console_stream = None

            self.console_connected = False
            self.console_connect_button.set_sensitive(True)
            self.console_disconnect_button.set_sensitive(False)
            self.console_input.set_sensitive(False)
            self.log("Serial console disconnected.")
            self.notify("Serial console disconnected.", Gtk.MessageType.INFO)

        except Exception as e:
            self.log(f"Error disconnecting console: {e}")

    def on_console_clear_clicked(self, button):
        """Clear the console output buffer."""
        if self.console_buffer:
            self.console_buffer.set_text("")

    def on_console_input_activate(self, entry):
        """Handle Enter key in console input."""
        if not self.console_connected or not self.console_stream:
            self.notify("Console not connected.", Gtk.MessageType.WARNING)
            return

        text = entry.get_text()
        if not text:
            return

        try:
            # Send text with newline
            data = (text + "\n").encode("utf-8")
            self.console_stream.send(data)
            entry.set_text("")  # Clear input

            # Echo to console output
            self._append_console_text(f"> {text}\n")

        except libvirt.libvirtError as e:
            self.log(f"Failed to send to console: {e}")
            self.notify(f"Failed to send to console: {e}", Gtk.MessageType.ERROR)

    def _console_stream_callback(self, stream, events, opaque):
        """Callback for console stream events."""
        # This is called by libvirt when data is available
        return

    def _console_receive_data(self):
        """Periodically receive data from console stream."""
        if not self.console_connected or not self.console_stream:
            return False  # Stop the timer

        try:
            # Try to receive data
            data = self.console_stream.recv(4096)
            if data:
                text = data.decode("utf-8", errors="replace")
                self._append_console_text(text)
        except libvirt.libvirtError as e:
            # Check if it's just no data available
            if "no data available" not in str(e).lower():
                self.log(f"Console receive error: {e}")
        except Exception as e:
            self.log(f"Console receive error: {e}")

        # Continue receiving
        return self.console_connected

    def _append_console_text(self, text: str):
        """Append text to console buffer and auto-scroll."""
        if not self.console_buffer:
            return

        end_iter = self.console_buffer.get_end_iter()
        self.console_buffer.insert(end_iter, text)

        # Auto-scroll to bottom
        mark = self.console_buffer.create_mark(None, end_iter, False)
        self.console_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)

    def on_console_help_clicked(self, button):
        """Show help dialog for serial console configuration."""
        help_text = """SERIAL CONSOLE CONFIGURATION GUIDE

The serial console allows text-based access to your VM, useful for troubleshooting,
viewing boot messages, and accessing headless VMs.

═══════════════════════════════════════════════════════════════

REQUIREMENTS

1. VM must have a serial device configured (usually already present)
2. Guest OS must redirect output to serial port
3. Getty service must run on serial port for interactive login

═══════════════════════════════════════════════════════════════

CONFIGURATION STEPS

For Linux VMs (systemd-based: RHEL, CentOS, Fedora, Debian, Ubuntu, openSUSE):

1. ENABLE SERIAL GETTY (for interactive login prompt):

   Inside the VM, run:

   sudo systemctl enable serial-getty@ttyS0.service
   sudo systemctl start serial-getty@ttyS0.service

2. ENABLE BOOT MESSAGES (to see boot output):

   Edit /etc/default/grub and add/modify:

   GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200n8"
   GRUB_TERMINAL="console serial"
   GRUB_SERIAL_COMMAND="serial --speed=115200 --unit=0 --word=8 --parity=no --stop=1"

   Then update grub:

   sudo grub2-mkconfig -o /boot/grub2/grub.cfg  # RHEL/CentOS/Fedora
   sudo update-grub                              # Debian/Ubuntu

3. REBOOT the VM for changes to take effect

═══════════════════════════════════════════════════════════════

TROUBLESHOOTING

• No output: Check that serial device exists in VM XML
• No login prompt: Ensure serial-getty service is enabled
• Garbled text: Check baud rate matches (115200 is standard)
• Cannot type: Press Enter a few times to wake up getty

═══════════════════════════════════════════════════════════════

VM XML CONFIGURATION

The VM should have a serial device like this in its XML:

<serial type='pty'>
  <target type='isa-serial' port='0'>
    <model name='isa-serial'/>
  </target>
</serial>
<console type='pty'>
  <target type='serial' port='0'/>
</console>

Use 'virsh edit <vm-name>' to check/modify if needed.
"""

        dialog = Gtk.Dialog(
            title="Serial Console Configuration Help",
            parent=None,  # Will be set by caller
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(700, 600)

        content_area = dialog.get_content_area()
        content_area.set_border_width(10)

        # Scrollable window for help text
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        content_area.pack_start(scroll, True, True, 0)

        # TextView with help content
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.set_left_margin(10)
        text_view.set_right_margin(10)
        text_view.set_top_margin(10)
        text_view.set_bottom_margin(10)
        text_view.set_monospace(True)

        buffer = text_view.get_buffer()
        buffer.set_text(help_text)
        scroll.add(text_view)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def disconnect(self):
        """Clean up console connection."""
        if self.console_connected:
            self.on_console_disconnect_clicked(None)


__all__ = ['ConsoleTab']
