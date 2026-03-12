"""
Clipboard Handlers

Handles clipboard synchronization between host and guest VM.
"""

from typing import Optional, Callable

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkVnc", "2.0")
from gi.repository import Gtk, Gdk

try:
    from gi.repository import GtkVnc
    VNC_AVAILABLE = True
except ImportError:
    VNC_AVAILABLE = False


class ClipboardHandler:
    """
    Manages clipboard synchronization between host and guest.

    Handles clipboard events and provides manual push/pull/type operations.
    """

    def __init__(
        self,
        clipboard: Gtk.Clipboard,
        protocol: str,
        vnc_display,
        spice_gtk_session,
        log_callback: Optional[Callable[[str], None]] = None,
        notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None,
        verbose: bool = False,
    ):
        """
        Initialize the clipboard handler.

        Args:
            clipboard: GTK clipboard object
            protocol: Display protocol ('vnc' or 'spice')
            vnc_display: VNC display widget (if using VNC)
            spice_gtk_session: SPICE GTK session (if using SPICE)
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
            verbose: Whether to print verbose output
        """
        self.clipboard = clipboard
        self.protocol = protocol
        self.vnc_display = vnc_display
        self.spice_gtk_session = spice_gtk_session
        self.verbose = verbose

        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None

        # Clipboard state
        self.last_clipboard_content: Optional[str] = None
        self.clipboard_update_in_progress: bool = False

    def on_server_cut_text(self, vnc, text):
        """
        Handle text received from guest via VNC.

        Called when guest copies text to its clipboard.

        Args:
            vnc: VNC display widget
            text: Text from guest clipboard
        """
        if self.verbose:
            print(f"VNC Server Cut Text: {len(text)} chars")

        if text != self.last_clipboard_content:
            self.last_clipboard_content = text
            self.log(f"Clipboard: Received {len(text)} characters from guest (VNC)")

            try:
                # Avoid triggering owner-change signal loop
                self.clipboard_update_in_progress = True
                self.clipboard.set_text(text, -1)
                self.clipboard.store()
            finally:
                self.clipboard_update_in_progress = False

            self.notify(f"Clipboard updated from guest ({len(text)} chars).", Gtk.MessageType.INFO)

    def on_owner_change(self, clipboard, event):
        """
        Handle host clipboard changes.

        Called when the host clipboard content changes. Sends to guest if VNC.

        Args:
            clipboard: GTK clipboard object
            event: Clipboard change event
        """
        if self.clipboard_update_in_progress:
            return

        if self.protocol == "vnc" and self.vnc_display and self.vnc_display.is_open():
            # Use async request to avoid blocking UI
            clipboard.request_text(self._on_clipboard_text_received)

    def _on_clipboard_text_received(self, clipboard, text):
        """
        Async callback when clipboard text is retrieved.

        Args:
            clipboard: GTK clipboard object
            text: Text from clipboard
        """
        if not text:
            return

        if self.protocol == "vnc" and self.vnc_display and self.vnc_display.is_open():
            if text != self.last_clipboard_content:
                self.last_clipboard_content = text
                if self.verbose:
                    print(f"Clipboard Owner Change: Sending {len(text)} chars to VNC")
                self.vnc_display.client_cut_text(text)
                self.log(f"Clipboard: Sent {len(text)} characters to guest (VNC)")

    def on_push(self, button, popover):
        """
        Manually push host clipboard to guest.

        Args:
            button: Button that triggered the action
            popover: Popover menu to close
        """
        popover.popdown()

        def on_clipboard_received(clipboard, text):
            """Async callback when clipboard text is retrieved."""
            if text:
                if self.protocol == "vnc" and self.vnc_display and self.vnc_display.is_open():
                    self.vnc_display.client_cut_text(text)
                    self.log(f"Clipboard: Manually pushed {len(text)} characters to guest (VNC)")
                elif self.protocol == "spice" and self.spice_gtk_session:
                    self.log("Clipboard: SPICE auto-clipboard should sync automatically.")
            else:
                self.notify("Local clipboard is empty.", Gtk.MessageType.WARNING)

        # Non-blocking async clipboard request
        self.clipboard.request_text(on_clipboard_received)

    def on_pull(self, button, popover):
        """
        Manually pull guest clipboard to host.

        Args:
            button: Button that triggered the action
            popover: Popover menu to close
        """
        popover.popdown()
        if self.last_clipboard_content:
            text = self.last_clipboard_content
            try:
                self.clipboard_update_in_progress = True
                self.clipboard.set_text(text, -1)
                self.clipboard.store()
            finally:
                self.clipboard_update_in_progress = False
            self.log(f"Clipboard: Manually pulled {len(text)} characters from cache to host")
            self.notify(f"Restored {len(text)} chars from guest clipboard cache.", Gtk.MessageType.INFO)
        else:
            self.notify("No clipboard content received from guest yet.", Gtk.MessageType.INFO)

    def on_type(self, button, popover):
        """
        Type clipboard content as keystrokes to guest.

        Useful when clipboard sync doesn't work or for special scenarios.

        Args:
            button: Button that triggered the action
            popover: Popover menu to close
        """
        popover.popdown()
        self.clipboard.request_text(self._on_type_clipboard_received)

    def _on_type_clipboard_received(self, clipboard, text):
        """
        Async callback for typing clipboard content.

        Args:
            clipboard: GTK clipboard object
            text: Text to type
        """
        if not text:
            return

        if self.protocol == "vnc" and self.vnc_display and self.vnc_display.is_open():
            if self.verbose:
                print(f"Typing clipboard: {len(text)} chars")

            for char in text:
                try:
                    keyval = Gdk.unicode_to_keyval(ord(char))
                    self.vnc_display.send_keys([keyval])
                except Exception as e:
                    if self.verbose:
                        print(f"Failed to type char '{char}': {e}")

            self.log(f"Clipboard: Typed {len(text)} characters as keystrokes")
            self.notify(f"Typed {len(text)} characters to guest.", Gtk.MessageType.INFO)


__all__ = ['ClipboardHandler']
