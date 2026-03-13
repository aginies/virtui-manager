"""
Power Control Handlers

Handles VM power state changes (start, pause, resume, shutdown, reboot, destroy).
"""

from typing import Optional, Callable, Dict

import gi
import libvirt
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

import threading


class PowerHandler:
    """
    Manages VM power control operations.

    Handles user actions for controlling VM power state through the power menu.
    """

    def __init__(
        self,
        domain,
        conn,
        original_domain_uuid: Optional[str],
        domain_name: Optional[str],
        uuid: Optional[str],
        power_buttons: Dict[str, Gtk.ModelButton],
        connect_display_callback: Callable,
        log_callback: Optional[Callable[[str], None]] = None,
        notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None,
        error_dialog_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize the power handler.

        Args:
            domain: libvirt domain object
            conn: libvirt connection object
            original_domain_uuid: UUID of the original domain
            domain_name: Name of the domain
            uuid: UUID string
            power_buttons: Dictionary of power control buttons
            connect_display_callback: Callback to reconnect display
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
            error_dialog_callback: Function to call for error dialogs
        """
        self.domain = domain
        self.conn = conn
        self.original_domain_uuid = original_domain_uuid
        self.domain_name = domain_name
        self.uuid = uuid
        self.power_buttons = power_buttons
        self.connect_display = connect_display_callback

        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None
        self.show_error_dialog = error_dialog_callback if error_dialog_callback else lambda msg: None

    def update_menu_sensitivity(self, popover):
        """
        Update power menu button sensitivity based on VM state.

        Called when power menu is shown to enable/disable actions appropriately.

        Args:
            popover: The power menu popover
        """
        try:
            if self.domain:
                state, reason = self.domain.state()
            else:
                state = libvirt.VIR_DOMAIN_NOSTATE
        except libvirt.libvirtError:
            state = libvirt.VIR_DOMAIN_NOSTATE

        # All buttons initially insensitive
        for btn in self.power_buttons.values():
            btn.set_sensitive(False)

        if state == libvirt.VIR_DOMAIN_RUNNING:
            self.power_buttons["Start"].set_sensitive(False)
            self.power_buttons["Pause"].set_sensitive(True)
            self.power_buttons["Resume"].set_sensitive(False)
            self.power_buttons["Hibernate"].set_sensitive(True)
            self.power_buttons["Graceful Shutdown"].set_sensitive(True)
            self.power_buttons["Reboot"].set_sensitive(True)
            self.power_buttons["Force Power Off"].set_sensitive(True)
        elif state == libvirt.VIR_DOMAIN_PAUSED:
            self.power_buttons["Start"].set_sensitive(False)
            self.power_buttons["Pause"].set_sensitive(False)
            self.power_buttons["Resume"].set_sensitive(True)
            self.power_buttons["Hibernate"].set_sensitive(False)
            self.power_buttons["Graceful Shutdown"].set_sensitive(True)
            self.power_buttons["Reboot"].set_sensitive(True)
            self.power_buttons["Force Power Off"].set_sensitive(True)
        elif state == libvirt.VIR_DOMAIN_SHUTOFF or state == libvirt.VIR_DOMAIN_SHUTDOWN:
            self.power_buttons["Start"].set_sensitive(True)
            self.power_buttons["Pause"].set_sensitive(False)
            self.power_buttons["Resume"].set_sensitive(False)
            self.power_buttons["Hibernate"].set_sensitive(False)
            self.power_buttons["Graceful Shutdown"].set_sensitive(False)
            self.power_buttons["Reboot"].set_sensitive(False)
            self.power_buttons["Force Power Off"].set_sensitive(False)
        else:  # NOSTATE, BLOCKED, CRASHED, PMSUSPENDED, etc.
            self.power_buttons["Start"].set_sensitive(True)
            self.power_buttons["Hibernate"].set_sensitive(False)
            self.power_buttons["Force Power Off"].set_sensitive(True)

    def on_start(self, button, popover):
        """Start the VM."""
        popover.popdown()
        try:
            # Refresh domain object to ensure validity
            if self.original_domain_uuid:
                self.domain = self.conn.lookupByUUIDString(self.original_domain_uuid)
            elif self.domain_name:
                self.domain = self.conn.lookupByName(self.domain_name)
            elif self.uuid:
                self.domain = self.conn.lookupByUUIDString(self.uuid)
            else:
                raise libvirt.libvirtError("No domain identifier available")

            self.domain.create()
            self.log("VM started successfully")
            # Don't schedule connection here - lifecycle event will trigger it
        except libvirt.libvirtError as e:
            self.log(f"Start error: {e}")
            self.show_error_dialog(f"Start error: {e}")

    def on_pause(self, button, popover):
        """Pause (suspend) the VM."""
        popover.popdown()
        try:
            self.domain.suspend()
            self.log("VM paused successfully")
        except libvirt.libvirtError as e:
            self.log(f"Pause error: {e}")
            self.show_error_dialog(f"Pause error: {e}")

    def on_resume(self, button, popover):
        """Resume the VM from paused state."""
        popover.popdown()

        def do_resume():
            try:
                self.domain.resume()
                GLib.idle_add(self.log, "VM resumed successfully")
            except libvirt.libvirtError as e:
                GLib.idle_add(self.log, f"Resume error: {e}")
                GLib.idle_add(self.show_error_dialog, f"Resume error: {e}")

        # Run in thread to avoid blocking UI
        threading.Thread(target=do_resume, daemon=True).start()

    def on_hibernate(self, button, popover):
        """Hibernate the VM."""
        popover.popdown()
        try:
            # We need to import hibernate_vm from ..vm_actions
            # but it is easier to just call it if we pass it or if we use the domain
            # The user requested to use hibernate_vm from @src/vmanager/vm_actions.py
            from ...vm_actions import hibernate_vm
            hibernate_vm(self.domain)
            self.log("VM hibernation initiated")
        except libvirt.libvirtError as e:
            self.log(f"Hibernate error: {e}")
            self.show_error_dialog(f"Hibernate error: {e}")
        except Exception as e:
            self.log(f"Hibernate error: {e}")
            self.show_error_dialog(f"Hibernate error: {e}")

    def on_shutdown(self, button, popover):
        """Gracefully shutdown the VM."""
        popover.popdown()
        try:
            self.domain.shutdown()
            self.log("Graceful shutdown initiated")
        except libvirt.libvirtError as e:
            self.log(f"Shutdown error: {e}")
            self.show_error_dialog(f"Shutdown error: {e}")

    def on_reboot(self, button, popover):
        """Reboot the VM."""
        popover.popdown()
        try:
            self.domain.reboot(0)
            self.log("VM reboot initiated")
        except libvirt.libvirtError as e:
            self.log(f"Reboot error: {e}")
            self.show_error_dialog(f"Reboot error: {e}")

    def on_destroy(self, button, popover):
        """Force power off the VM."""
        popover.popdown()
        try:
            self.domain.destroy()
            self.log("VM force powered off")
        except libvirt.libvirtError as e:
            self.log(f"Force power off error: {e}")
            self.show_error_dialog(f"Destroy error: {e}")


__all__ = ['PowerHandler']
