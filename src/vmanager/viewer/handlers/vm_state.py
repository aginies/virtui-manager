"""
VM State Handlers

Handles VM state monitoring, waiting for VM to start, and shutdown detection.
"""

import time
from typing import Optional, Callable

import gi
import libvirt
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from ..constants import VM_WAIT_TIMEOUT_SECONDS, VM_WAIT_CHECK_INTERVAL_SECONDS


class VMStateHandler:
    """
    Manages VM state monitoring and waiting operations.

    Handles waiting for VM to start with timeout, checking for VM shutdown,
    and domain event callbacks.
    """

    def __init__(
        self,
        domain,
        conn,
        original_domain_uuid: Optional[str],
        attach: bool,
        info_bar: Gtk.InfoBar,
        get_display_info_callback: Callable,
        connect_display_callback: Callable,
        quit_callback: Callable,
        log_callback: Optional[Callable[[str], None]] = None,
        notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None,
        verbose: bool = False,
    ):
        """
        Initialize the VM state handler.

        Args:
            domain: libvirt domain object
            conn: libvirt connection object
            original_domain_uuid: UUID of the original domain
            attach: Whether using attach mode
            info_bar: InfoBar for displaying status messages
            get_display_info_callback: Callback to get display connection info
            connect_display_callback: Callback to connect display
            quit_callback: Callback to quit application
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
            verbose: Whether to print verbose output
        """
        self.domain = domain
        self.conn = conn
        self.original_domain_uuid = original_domain_uuid
        self.attach = attach
        self.info_bar = info_bar
        self.get_display_info = get_display_info_callback
        self.connect_display = connect_display_callback
        self.quit = quit_callback
        self.verbose = verbose

        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None

        # Wait state
        self.wait_timeout_id: Optional[int] = None
        self.wait_start_time: Optional[float] = None
        self.wait_cancel_button: Optional[Gtk.Button] = None
        self.wait_max_seconds: int = VM_WAIT_TIMEOUT_SECONDS

        # Shutdown check state
        self.shutdown_check_timeout_id: Optional[int] = None

        # Lifecycle event reconnection state
        self.lifecycle_reconnect_pending: bool = False

    def start_wait_for_vm(self):
        """Start waiting for VM with timeout and cancel button."""
        self.wait_start_time = time.time()

        # Add cancel button to info bar action area
        if self.info_bar:
            action_area = self.info_bar.get_action_area()
            if action_area:
                self.wait_cancel_button = Gtk.Button(label="Cancel")
                self.wait_cancel_button.connect("clicked", self._on_cancel_wait)
                action_area.pack_start(self.wait_cancel_button, False, False, 0)
                self.wait_cancel_button.show()

        mins, secs = divmod(self.wait_max_seconds, 60)
        self.notify(
            f"Waiting for VM to start... ({mins}m {secs}s remaining)",
            Gtk.MessageType.INFO
        )

        self.wait_timeout_id = GLib.timeout_add_seconds(
            VM_WAIT_CHECK_INTERVAL_SECONDS,
            self._wait_and_connect_cb
        )

    def _wait_and_connect_cb(self):
        """
        Callback for waiting for VM to start.

        Returns:
            True to keep waiting, False to stop
        """
        # Check if wait was cancelled
        if self.wait_timeout_id is None:
            self._cleanup_wait_ui()
            return False

        # Check for timeout
        if self.wait_start_time:
            elapsed = time.time() - self.wait_start_time
            remaining = self.wait_max_seconds - elapsed

            if elapsed >= self.wait_max_seconds:
                self.log("Wait timeout reached. Stopping wait for VM.")
                self.notify(
                    f"Timed out waiting for VM to start after {self.wait_max_seconds} seconds.",
                    Gtk.MessageType.WARNING
                )
                self._cleanup_wait_ui()
                return False

            # Update notification with remaining time
            mins, secs = divmod(int(remaining), 60)
            if mins > 0:
                time_str = f"{mins}m {secs}s"
            else:
                time_str = f"{secs}s"
            self.notify(
                f"Waiting for VM to start... ({time_str} remaining)",
                Gtk.MessageType.INFO
            )

        try:
            # Refresh domain info
            if self.original_domain_uuid:
                try:
                    self.domain = self.conn.lookupByUUIDString(self.original_domain_uuid)
                except libvirt.libvirtError:
                    pass  # Keep using current domain object

            protocol, host, port, pwd = self.get_display_info()
            if not self.attach and (not host or not port):
                return True  # Keep waiting

            self.notify("VM started! Connecting...", Gtk.MessageType.INFO)
            self._cleanup_wait_ui()
            self.connect_display()
            return False
        except Exception as e:
            if self.verbose:
                print(f"Wait error: {e}")
            return True

    def _on_cancel_wait(self, button):
        """Handle cancel button click during wait."""
        self.log("Wait for VM cancelled by user.")
        if self.wait_timeout_id:
            GLib.source_remove(self.wait_timeout_id)
            self.wait_timeout_id = None
        self._cleanup_wait_ui()
        self.notify("Wait for VM cancelled.", Gtk.MessageType.INFO)

    def _cleanup_wait_ui(self):
        """Remove the cancel button from the info bar."""
        self.wait_timeout_id = None
        self.wait_start_time = None
        if self.wait_cancel_button and self.wait_cancel_button.get_parent():
            self.wait_cancel_button.get_parent().remove(self.wait_cancel_button)
            self.wait_cancel_button = None

    def check_shutdown(self):
        """Poll VM state for a few seconds to detect shutdown."""
        if self.original_domain_uuid and self.domain:
            try:
                current_uuid = self.domain.UUIDString()
                if current_uuid == self.original_domain_uuid:
                    # Cancel any existing shutdown check
                    if self.shutdown_check_timeout_id:
                        GLib.source_remove(self.shutdown_check_timeout_id)
                    self.shutdown_check_timeout_id = GLib.timeout_add_seconds(1, self._check_shutdown_async, 0)
            except libvirt.libvirtError:
                self.notify("ERROR: Domain invalid", Gtk.MessageType.ERROR)
                self.quit()

    def _check_shutdown_async(self, counter):
        """
        Async callback to check if VM has shut down.

        Args:
            counter: Number of checks performed

        Returns:
            False to stop the timeout
        """
        try:
            if not self.domain.isActive():
                if self.verbose:
                    print("VM is shutdown. Exiting...")
                self.notify(
                    "VM has shut down. You can restart it from the Power menu.",
                    Gtk.MessageType.INFO
                )
                self.shutdown_check_timeout_id = None
                return False
        except:
            self.shutdown_check_timeout_id = None
            self.quit()
            return False

        # Verify we're still checking the same domain
        try:
            if self.domain.UUIDString() != self.original_domain_uuid:
                self.log("ERROR: Domain UUID changed during reconnect check!")
                self.shutdown_check_timeout_id = None
                return False
        except libvirt.libvirtError:
            self.shutdown_check_timeout_id = None
            self.quit()
            return False

        if counter < 10:  # Try for 10 seconds
            self.shutdown_check_timeout_id = GLib.timeout_add_seconds(1, self._check_shutdown_async, counter + 1)
            return False

        # Check VM state - only reconnect if RUNNING, not if PAUSED
        try:
            state, reason = self.domain.state()
            # VIR_DOMAIN_RUNNING = 1, VIR_DOMAIN_PAUSED = 3
            if state == libvirt.VIR_DOMAIN_PAUSED:
                if self.verbose:
                    print("VM is paused, not reconnecting. Will reconnect on resume.")
                self.shutdown_check_timeout_id = None
                return False
            elif state == libvirt.VIR_DOMAIN_RUNNING:
                if self.verbose:
                    print("VM still running after disconnect (Reboot or Network issue?). Reconnecting...")
                # Auto-reconnect if VM is still running
                self.shutdown_check_timeout_id = None
                self.connect_display()
                return False
        except:
            pass

        self.shutdown_check_timeout_id = None
        return False

    def lifecycle_callback(self, conn, dom, event, detail, opaque):
        """
        Libvirt lifecycle event callback.

        Args:
            conn: libvirt connection
            dom: domain object
            event: lifecycle event type
            detail: event detail
            opaque: user data
        """
        # Verify we're monitoring the correct domain
        try:
            if dom.UUIDString() != self.original_domain_uuid:
                return
        except libvirt.libvirtError:
            return

        if event == libvirt.VIR_DOMAIN_EVENT_STARTED:
            self.log("VM lifecycle event: Started")
            if self.verbose:
                print("Domain started event received")
            self.notify("VM started", Gtk.MessageType.INFO)

            # Cancel shutdown check if running (we know VM is started)
            if self.shutdown_check_timeout_id:
                GLib.source_remove(self.shutdown_check_timeout_id)
                self.shutdown_check_timeout_id = None

            # Reconnect display when VM starts (prevent duplicates)
            if not self.lifecycle_reconnect_pending:
                self.lifecycle_reconnect_pending = True
                def do_start_reconnect():
                    self.lifecycle_reconnect_pending = False
                    self.connect_display()
                    return False
                GLib.timeout_add(1000, do_start_reconnect)

        elif event == libvirt.VIR_DOMAIN_EVENT_STOPPED:
            self.log("VM lifecycle event: Stopped")
            if self.verbose:
                print("Domain stopped event received")
            self.notify("VM stopped", Gtk.MessageType.ERROR)

        elif event == libvirt.VIR_DOMAIN_EVENT_SUSPENDED:
            self.log("VM lifecycle event: Suspended/Paused")
            if self.verbose:
                print("Domain suspended event received")
            self.notify("VM paused", Gtk.MessageType.WARNING)

        elif event == libvirt.VIR_DOMAIN_EVENT_RESUMED:
            self.log("VM lifecycle event: Resumed")
            if self.verbose:
                print("Domain resumed event received")
            self.notify("VM resumed", Gtk.MessageType.INFO)
            # Note: Do NOT reconnect on RESUME - the connection should still be active

    def reboot_callback(self, conn, dom, opaque):
        """
        Libvirt reboot event callback.

        Args:
            conn: libvirt connection
            dom: domain object
            opaque: user data
        """
        try:
            if dom.UUIDString() != self.original_domain_uuid:
                return
        except libvirt.libvirtError:
            return

        self.log("VM reboot event received")
        if self.verbose:
            print("Domain reboot event received")

    def register_events(self):
        """Register libvirt domain event callbacks."""
        if not self.domain or not self.conn:
            return

        try:
            # Register lifecycle events
            self.conn.domainEventRegisterAny(
                self.domain,
                libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                self.lifecycle_callback,
                None
            )

            # Register reboot events
            self.conn.domainEventRegisterAny(
                self.domain,
                libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                self.reboot_callback,
                None
            )

            self.log("Domain event callbacks registered")
        except libvirt.libvirtError as e:
            self.log(f"Failed to register domain events: {e}")
            if self.verbose:
                print(f"Event registration error: {e}")


__all__ = ['VMStateHandler']
