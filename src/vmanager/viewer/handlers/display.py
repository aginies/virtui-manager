"""
Display Event Handlers

Handles display-related events like screenshots, reconnect, send keys, fullscreen, etc.
"""

import time
from typing import Optional, Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

try:
    from gi.repository import SpiceClientGtk
    SPICE_AVAILABLE = True
except ImportError:
    SPICE_AVAILABLE = False


class DisplayHandler:
    """
    Manages display-related user interactions.

    Handles screenshot capture, reconnection, key sending, fullscreen toggle,
    display settings changes, and keyboard/mouse grab events.
    """

    def __init__(
        self,
        window: Gtk.Window,
        protocol: str,
        vnc_display,
        spice_session,
        display_widget,
        fs_button: Gtk.ToggleButton,
        connect_display_callback: Callable,
        save_state_callback: Callable,
        log_callback: Optional[Callable[[str], None]] = None,
        notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None,
        error_dialog_callback: Optional[Callable[[str], None]] = None,
        verbose: bool = False,
    ):
        """
        Initialize the display handler.

        Args:
            window: Main application window
            protocol: Display protocol ('vnc' or 'spice')
            vnc_display: VNC display widget (if using VNC)
            spice_session: SPICE session (if using SPICE)
            display_widget: Current display widget
            fs_button: Fullscreen toggle button
            connect_display_callback: Callback to reconnect display
            save_state_callback: Callback to save application state
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
            error_dialog_callback: Function to call for error dialogs
            verbose: Whether to print verbose output
        """
        self.window = window
        self.protocol = protocol
        self.vnc_display = vnc_display
        self.spice_session = spice_session
        self.display_widget = display_widget
        self.fs_button = fs_button
        self.connect_display = connect_display_callback
        self.save_state = save_state_callback
        self.verbose = verbose

        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None
        self.show_error_dialog = error_dialog_callback if error_dialog_callback else lambda msg: None

        # Display state
        self.is_fullscreen: bool = False
        self.grab_active: bool = False
        self.grab_status_label: Optional[Gtk.Label] = None

    def on_screenshot_clicked(self, button):
        """Capture and save a screenshot of the display."""
        pixbuf = None
        if self.protocol == "vnc" and self.vnc_display:
            pixbuf = self.vnc_display.get_pixbuf()
        elif self.protocol == "spice" and self.display_widget:
            try:
                pixbuf = self.display_widget.get_pixbuf()
            except:
                pass

        if not pixbuf:
            self.notify("Error: Could not capture screen", Gtk.MessageType.ERROR)
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
                self.notify(f"Screenshot saved to {filename}", Gtk.MessageType.INFO)
                self.log(f"Screenshot saved to {filename}")
                if self.verbose:
                    print(f"Screenshot saved to {filename}")
            except Exception as e:
                self.log(f"Error saving screenshot: {e}")
                self.show_error_dialog(f"Error saving screenshot: {e}")

        dialog.destroy()

    def on_reconnect_clicked(self, button):
        """Reconnect the display."""
        self.log("Reconnect button clicked")
        if self.protocol == "vnc" and self.vnc_display:
            self.connect_display(force=True, retry_count=0)
        elif self.protocol == "spice" and self.spice_session:
            # Try to disconnect (will fail silently if not connected)
            try:
                self.spice_session.disconnect()
                # Wait before reconnecting
                GLib.timeout_add(800, lambda: self.connect_display(retry_count=0))
            except:
                # Not connected, just reconnect
                self.connect_display(retry_count=0)

    def on_send_key(self, button, keys, popover):
        """
        Send key combination to the guest VM.

        Args:
            button: Button that triggered the action
            keys: List of key values to send
            popover: Popover menu to close
        """
        if self.protocol == "vnc" and self.vnc_display:
            self.vnc_display.send_keys(keys)
            self.log(f"Sent key combination via VNC: {[hex(k) for k in keys]}")
        elif self.protocol == "spice" and self.display_widget and SPICE_AVAILABLE:
            try:
                self.display_widget.send_keys(keys, SpiceClientGtk.DisplayKeyEvent.CLICK)
                self.log(f"Sent key combination via SPICE: {[hex(k) for k in keys]}")
            except Exception as e:
                self.log(f"ERROR: Failed to send keys via SPICE: {e}")
                if self.verbose:
                    print(f"SPICE send_keys error: {e}")
        popover.popdown()

    def on_key_press(self, widget, event):
        """
        Handle keyboard shortcuts.

        Args:
            widget: Widget that received the event
            event: Key press event

        Returns:
            True if event was handled, False otherwise
        """
        # Ctrl+F for fullscreen toggle
        if (event.keyval == Gdk.KEY_f or event.keyval == Gdk.KEY_F) and \
           (event.state & Gdk.ModifierType.CONTROL_MASK):
            self.fs_button.set_active(not self.fs_button.get_active())
            return True
        return False

    def on_fullscreen_toggled(self, button):
        """Toggle fullscreen mode."""
        self.is_fullscreen = button.get_active()
        if self.is_fullscreen:
            self.window.fullscreen()
        else:
            self.window.unfullscreen()
        self.save_state()

    def on_scaling_toggled(self, button, scaling_enabled_ref):
        """
        Handle scaling toggle.

        Args:
            button: Toggle button
            scaling_enabled_ref: List containing [scaling_enabled] (mutable reference)
        """
        scaling_enabled_ref[0] = button.get_active()
        if self.protocol == "vnc" and self.vnc_display:
            self.vnc_display.set_scaling(scaling_enabled_ref[0])
        elif self.protocol == "spice" and self.display_widget:
            self.display_widget.set_property("scaling", scaling_enabled_ref[0])
        self.save_state()

    def on_smoothing_toggled(self, button, smoothing_enabled_ref):
        """
        Handle smoothing toggle.

        Args:
            button: Toggle button
            smoothing_enabled_ref: List containing [smoothing_enabled] (mutable reference)
        """
        smoothing_enabled_ref[0] = button.get_active()
        if self.protocol == "vnc" and self.vnc_display:
            self.vnc_display.set_smoothing(smoothing_enabled_ref[0])
        self.save_state()

    def on_lossy_toggled(self, button, lossy_enabled_ref):
        """
        Handle lossy encoding toggle.

        Args:
            button: Toggle button
            lossy_enabled_ref: List containing [lossy_encoding_enabled] (mutable reference)
        """
        lossy_enabled_ref[0] = button.get_active()
        if self.protocol == "vnc" and self.vnc_display:
            self.vnc_display.set_lossy_encoding(lossy_enabled_ref[0])
        self.save_state()

    def on_view_only_toggled(self, button, view_only_ref):
        """
        Handle view-only mode toggle.

        Args:
            button: Toggle button
            view_only_ref: List containing [view_only_enabled] (mutable reference)
        """
        view_only_ref[0] = button.get_active()
        if self.protocol == "vnc" and self.vnc_display:
            self.vnc_display.set_read_only(view_only_ref[0])
        self.save_state()

    def on_depth_changed(self, combo, vnc_depth_ref, apply_vnc_depth_callback):
        """
        Handle VNC color depth change.

        Args:
            combo: ComboBox widget
            vnc_depth_ref: List containing [vnc_depth] (mutable reference)
            apply_vnc_depth_callback: Callback to apply depth setting
        """
        depth_str = combo.get_active_id()
        if depth_str:
            vnc_depth_ref[0] = int(depth_str)
            if self.protocol == "vnc" and self.vnc_display:
                apply_vnc_depth_callback()
                if self.vnc_display.is_open():
                    # Ask user if they want to reconnect
                    dialog = Gtk.MessageDialog(
                        transient_for=self.window,
                        flags=0,
                        message_type=Gtk.MessageType.QUESTION,
                        buttons=Gtk.ButtonsType.YES_NO,
                        text="Reconnect required",
                    )
                    dialog.format_secondary_text(
                        "Changing color depth requires a reconnection. Reconnect now?"
                    )
                    response = dialog.run()
                    dialog.destroy()

                    if response == Gtk.ResponseType.YES:
                        self.connect_display(force=True)

            self.save_state()

    def on_keyboard_grab_activated(self, widget):
        """Called when keyboard/mouse grab is activated."""
        self.grab_active = True
        if self.grab_status_label:
            self.grab_status_label.show()

    def on_keyboard_grab_released(self, widget):
        """Called when keyboard/mouse grab is released."""
        self.grab_active = False
        if self.grab_status_label:
            self.grab_status_label.hide()

    def on_spice_grab_changed(self, widget, pspec):
        """Called when SPICE grab-keyboard property changes."""
        is_grabbed = widget.get_property("grab-keyboard")
        if is_grabbed:
            self.on_keyboard_grab_activated(widget)
        else:
            self.on_keyboard_grab_released(widget)


__all__ = ['DisplayHandler']
