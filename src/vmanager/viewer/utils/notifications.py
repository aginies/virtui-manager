"""
Notification and Logging Manager

Handles all user notifications, error dialogs, and logging functionality.
"""

import time
from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from ...utils import sanitize_sensitive_data
from ..constants import NOTIFICATION_TIMEOUT_SECONDS


class NotificationManager:
    """Manages notifications, dialogs, and logging for the remote viewer."""

    def __init__(self, verbose=False):
        """
        Initialize the notification manager.

        Args:
            verbose: Whether to print verbose output to console
        """
        self.verbose = verbose

        # GTK widgets (set after window is created)
        self.window = None
        self.info_bar = None
        self.info_bar_label = None
        self.log_buffer = None
        self.log_view = None

        # State
        self.notification_timeout_id = None

    def set_window(self, window: Gtk.Window):
        """Set the main window for error dialogs."""
        self.window = window

    def set_info_bar(self, info_bar: Gtk.InfoBar, label: Gtk.Label):
        """
        Set the InfoBar widgets for notifications.

        Args:
            info_bar: The InfoBar widget
            label: The label inside the InfoBar
        """
        self.info_bar = info_bar
        self.info_bar_label = label

    def set_log_widgets(self, log_buffer: Gtk.TextBuffer, log_view: Gtk.TextView):
        """
        Set the logging widgets.

        Args:
            log_buffer: The text buffer for logs
            log_view: The text view for logs
        """
        self.log_buffer = log_buffer
        self.log_view = log_view

    def show_error_dialog(self, message: str):
        """
        Show an error dialog to the user.

        Args:
            message: Error message to display (will be sanitized)
        """
        dialog = Gtk.MessageDialog(
            transient_for=self.window if self.window else None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Error",
        )
        dialog.format_secondary_text(self._sanitize_error_message(message))
        dialog.run()
        dialog.destroy()

    def show_notification(self, message: str, message_type=Gtk.MessageType.INFO):
        """
        Show a temporary notification in the InfoBar.

        Args:
            message: Message to display
            message_type: Type of message (INFO, WARNING, ERROR, etc.)
        """
        if self.info_bar and self.info_bar_label:
            self.info_bar.set_message_type(message_type)
            self.info_bar_label.set_text(message)
            self.info_bar.set_revealed(True)

            # Cancel previous timeout if it exists
            if self.notification_timeout_id:
                GLib.source_remove(self.notification_timeout_id)
                self.notification_timeout_id = None

            # Set new timeout to hide after configured duration
            self.notification_timeout_id = GLib.timeout_add_seconds(
                NOTIFICATION_TIMEOUT_SECONDS, self._hide_notification
            )

        elif message_type == Gtk.MessageType.ERROR:
            # Fallback if no window/infobar yet
            self.show_error_dialog(message)
        else:
            if self.verbose:
                print(self._sanitize_verbose_output(f"Notification ({message_type}): {message}"))

    def on_info_bar_response(self, bar, response):
        """Handle InfoBar close button click."""
        bar.set_revealed(False)

    def _hide_notification(self):
        """Hide the notification InfoBar (callback)."""
        if self.info_bar:
            self.info_bar.set_revealed(False)
        self.notification_timeout_id = None
        return False

    def log_message(self, message: str):
        """
        Log a message to the log buffer and optionally to console.

        Args:
            message: Message to log (will be sanitized)
        """
        # Best-effort sanitization to avoid logging sensitive data
        safe_message = self._sanitize_log_message(message)

        timestamp = time.strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {safe_message}\n"

        if self.verbose:
            print(self._sanitize_verbose_output(full_msg.strip()))

        if self.log_buffer:
            GLib.idle_add(self._append_log_safe, full_msg)

    def _append_log_safe(self, text: str):
        """
        Append text to log buffer safely (runs on main thread).

        Args:
            text: Text to append

        Returns:
            False to remove the GLib callback
        """
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, text)

        # Auto-scroll to the end
        if self.log_view:
            mark = self.log_buffer.create_mark(None, end_iter, False)
            self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)

        return False

    def _sanitize_log_message(self, message: str) -> str:
        """
        Sanitize log messages to remove sensitive data.

        Args:
            message: Message to sanitize

        Returns:
            Sanitized message
        """
        return sanitize_sensitive_data(message)

    def _sanitize_verbose_output(self, message: str) -> str:
        """
        Sanitize verbose output to remove sensitive data.

        Args:
            message: Message to sanitize

        Returns:
            Sanitized message
        """
        return sanitize_sensitive_data(message)

    def _sanitize_error_message(self, message: str) -> str:
        """
        Sanitize error messages to remove sensitive data.

        Args:
            message: Message to sanitize

        Returns:
            Sanitized message
        """
        return sanitize_sensitive_data(message)


__all__ = ['NotificationManager']
