"""
USB Devices Tab

Provides USB device passthrough functionality for VMs.
"""

import sys
import threading
import xml.etree.ElementTree as ET
from typing import Optional, Callable

import gi
import libvirt
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from ... import vm_actions, vm_queries, libvirt_utils


class USBTab:
    """
    Manages the USB devices tab UI and functionality.

    Provides USB device attach/detach for VM passthrough.
    """

    def __init__(self, domain, conn, window=None,
                 log_callback: Optional[Callable[[str], None]] = None,
                 notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None):
        """
        Initialize the USB tab.

        Args:
            domain: libvirt domain object
            conn: libvirt connection object
            window: Main window (for dialog parent)
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
        """
        self.domain = domain
        self.conn = conn
        self.window = window
        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None

        # UI elements
        self.attached_usb_store: Optional[Gtk.ListStore] = None
        self.attached_usb_tree_view: Optional[Gtk.TreeView] = None
        self.host_usb_store: Optional[Gtk.ListStore] = None
        self.host_usb_tree_view: Optional[Gtk.TreeView] = None
        self.attach_usb_button: Optional[Gtk.Button] = None
        self.detach_usb_button: Optional[Gtk.Button] = None

        # Wait dialog
        self.wait_dialog: Optional[Gtk.Dialog] = None

    def build_tab(self) -> Gtk.Box:
        """
        Build the USB devices tab UI.

        Returns:
            The tab container widget
        """
        tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tab.set_border_width(10)

        # Attached USB Devices section
        tab.pack_start(
            Gtk.Label(label="<b>Attached USB Devices</b>", use_markup=True),
            False, False, 0
        )

        # ListStore: vendor_id, product_id, vendor_name, product_name, description
        self.attached_usb_store = Gtk.ListStore(str, str, str, str, str)
        self.attached_usb_tree_view = Gtk.TreeView(model=self.attached_usb_store)
        self._add_usb_tree_columns(self.attached_usb_tree_view)

        scroll_attached_usb = Gtk.ScrolledWindow()
        scroll_attached_usb.set_vexpand(True)
        scroll_attached_usb.add(self.attached_usb_tree_view)
        tab.pack_start(scroll_attached_usb, True, True, 0)

        # Host USB Devices section
        tab.pack_start(
            Gtk.Label(label="<b>Available Host USB Devices</b>", use_markup=True),
            False, False, 0
        )

        self.host_usb_store = Gtk.ListStore(str, str, str, str, str)
        self.host_usb_tree_view = Gtk.TreeView(model=self.host_usb_store)
        self._add_usb_tree_columns(self.host_usb_tree_view)

        scroll_host_usb = Gtk.ScrolledWindow()
        scroll_host_usb.set_vexpand(True)
        scroll_host_usb.add(self.host_usb_tree_view)
        tab.pack_start(scroll_host_usb, True, True, 0)

        # USB Action Buttons
        usb_action_buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab.pack_start(usb_action_buttons_box, False, False, 0)

        self.attach_usb_button = Gtk.Button(label="Attach USB Device")
        self.attach_usb_button.connect("clicked", self.on_attach_usb_clicked)
        self.attach_usb_button.set_sensitive(False)
        usb_action_buttons_box.pack_start(self.attach_usb_button, True, True, 0)

        self.detach_usb_button = Gtk.Button(label="Detach USB Device")
        self.detach_usb_button.connect("clicked", self.on_detach_usb_clicked)
        self.detach_usb_button.set_sensitive(False)
        usb_action_buttons_box.pack_start(self.detach_usb_button, True, True, 0)

        refresh_usb_button = Gtk.Button(label="Refresh USB Lists")
        refresh_usb_button.connect("clicked", self.on_refresh_usb_lists_clicked)
        usb_action_buttons_box.pack_start(refresh_usb_button, True, True, 0)

        # Connect selection change handlers
        self.attached_usb_tree_view.get_selection().connect(
            "changed", self.on_attached_usb_selection_changed
        )
        self.host_usb_tree_view.get_selection().connect(
            "changed", self.on_host_usb_selection_changed
        )

        return tab

    def _add_usb_tree_columns(self, tree_view: Gtk.TreeView):
        """Add columns to the USB device tree view."""
        columns = [
            ("Description", 4),
            ("Vendor ID", 0),
            ("Product ID", 1),
        ]
        for title, col_id in columns:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=col_id)
            tree_view.append_column(column)

    def populate_usb_lists(self):
        """Populate the USB device lists from libvirt."""
        self.attached_usb_store.clear()
        self.host_usb_store.clear()

        if not self.domain:
            self.notify("No VM domain available for USB actions.", Gtk.MessageType.WARNING)
            return

        # Populate attached USB devices
        try:
            domain_xml = self.domain.XMLDesc(0)
            root = ET.fromstring(domain_xml)
            attached_devices = vm_queries.get_attached_usb_devices(root)
            for dev in attached_devices:
                self.attached_usb_store.append([
                    dev.get("vendor_id", "N/A"),
                    dev.get("product_id", "N/A"),
                    "",  # Vendor name not in attached devices
                    "",  # Product name not in attached devices
                    f"{dev.get('vendor_id', '')}:{dev.get('product_id', '')}",
                ])
        except libvirt.libvirtError as e:
            self.notify(f"Failed to get attached USB devices: {e}", Gtk.MessageType.ERROR)
        except Exception as e:
            self.notify(f"Error getting attached USB devices: {e}", Gtk.MessageType.ERROR)

        # Populate host USB devices
        try:
            host_devices = libvirt_utils.get_host_usb_devices(self.conn)
            for dev in host_devices:
                self.host_usb_store.append([
                    dev.get("vendor_id", "N/A"),
                    dev.get("product_id", "N/A"),
                    dev.get("vendor_name", ""),
                    dev.get("product_name", ""),
                    dev.get("description", "N/A"),
                ])
        except libvirt.libvirtError as e:
            self.notify(f"Failed to get host USB devices: {e}", Gtk.MessageType.ERROR)
        except Exception as e:
            self.notify(f"Error getting host USB devices: {e}", Gtk.MessageType.ERROR)

        # Update button sensitivities
        self.on_attached_usb_selection_changed(self.attached_usb_tree_view.get_selection())
        self.on_host_usb_selection_changed(self.host_usb_tree_view.get_selection())

    def on_refresh_usb_lists_clicked(self, button):
        """Refresh the USB device lists."""
        self.populate_usb_lists()
        self.notify("USB device lists refreshed.", Gtk.MessageType.INFO)

    def on_attached_usb_selection_changed(self, selection):
        """Handle attached USB device selection change."""
        model, treeiter = selection.get_selected()
        self.detach_usb_button.set_sensitive(
            treeiter is not None and self.domain and self.domain.isActive()
        )
        # Deselect host device to avoid conflict
        if treeiter:
            self.host_usb_tree_view.get_selection().unselect_all()

    def on_host_usb_selection_changed(self, selection):
        """Handle host USB device selection change."""
        model, treeiter = selection.get_selected()
        self.attach_usb_button.set_sensitive(
            treeiter is not None and self.domain and self.domain.isActive()
        )
        # Deselect attached device to avoid conflict
        if treeiter:
            self.attached_usb_tree_view.get_selection().unselect_all()

    def on_attach_usb_clicked(self, button):
        """Attach the selected host USB device to the VM."""
        self.log("on_attach_usb_clicked called.")
        selection = self.host_usb_tree_view.get_selection()
        model, treeiter = selection.get_selected()

        if not treeiter:
            self.notify("No host USB device selected for attachment.", Gtk.MessageType.WARNING)
            self.log("No host USB device selected.")
            return

        vendor_id = model[treeiter][0]
        product_id = model[treeiter][1]
        description = model[treeiter][4]
        self.log(f"Selected host USB: {description} ({vendor_id}:{product_id})")

        self._show_wait_dialog(f"Attaching USB device '{description}'...")
        self.log("Wait dialog shown.")

        def _attach_usb_thread():
            self.log(f"Attempting to attach USB in thread: {description}")
            try:
                vm_actions.attach_usb_device(self.domain, vendor_id, product_id)
                self.log(f"USB device {description} attached successfully by vm_actions.")
                GLib.idle_add(
                    self.notify,
                    f"USB device '{description}' attached successfully.",
                    Gtk.MessageType.INFO,
                )
            except libvirt.libvirtError as e:
                self.log(f"libvirtError attaching USB: {e}")
                print(f"ERROR (libvirt): Failed to attach USB device: {e}", file=sys.stderr)
                GLib.idle_add(
                    self.notify,
                    f"Failed to attach USB device: {e}",
                    Gtk.MessageType.ERROR,
                )
            except Exception as e:
                self.log(f"Generic error attaching USB: {e}")
                print(f"ERROR (generic): Unexpected error during USB attach: {e}", file=sys.stderr)
                GLib.idle_add(
                    self.notify,
                    f"An unexpected error occurred: {e}",
                    Gtk.MessageType.ERROR,
                )
            finally:
                self.log("Attaching USB thread finished. Hiding wait dialog and refreshing.")
                GLib.idle_add(self._hide_wait_dialog)
                GLib.idle_add(self.populate_usb_lists)

        threading.Thread(target=_attach_usb_thread, daemon=True).start()

    def on_detach_usb_clicked(self, button):
        """Detach the selected USB device from the VM."""
        self.log("on_detach_usb_clicked called.")
        selection = self.attached_usb_tree_view.get_selection()
        model, treeiter = selection.get_selected()

        if not treeiter:
            self.notify("No attached USB device selected for detachment.", Gtk.MessageType.WARNING)
            self.log("No attached USB device selected.")
            return

        vendor_id = model[treeiter][0]
        product_id = model[treeiter][1]
        description = model[treeiter][4]
        self.log(f"Selected attached USB: {description} ({vendor_id}:{product_id})")

        self._show_wait_dialog(f"Detaching USB device '{description}'...")
        self.log("Wait dialog shown.")

        def _detach_usb_thread():
            self.log(f"Attempting to detach USB in thread: {description}")
            try:
                vm_actions.detach_usb_device(self.domain, vendor_id, product_id)
                self.log(f"USB device {description} detached successfully by vm_actions.")
                GLib.idle_add(
                    self.notify,
                    f"USB device '{description}' detached successfully.",
                    Gtk.MessageType.INFO,
                )
            except libvirt.libvirtError as e:
                self.log(f"libvirtError detaching USB: {e}")
                print(f"ERROR (libvirt): Failed to detach USB device: {e}", file=sys.stderr)
                GLib.idle_add(
                    self.notify,
                    f"Failed to detach USB device: {e}",
                    Gtk.MessageType.ERROR,
                )
            except Exception as e:
                self.log(f"Generic error detaching USB: {e}")
                print(f"ERROR (generic): Unexpected error during USB detach: {e}", file=sys.stderr)
                GLib.idle_add(
                    self.notify,
                    f"An unexpected error occurred: {e}",
                    Gtk.MessageType.ERROR,
                )
            finally:
                self.log("Detaching USB thread finished. Hiding wait dialog and refreshing.")
                GLib.idle_add(self._hide_wait_dialog)
                GLib.idle_add(self.populate_usb_lists)

        threading.Thread(target=_detach_usb_thread, daemon=True).start()

    def _show_wait_dialog(self, message: str):
        """Show a modal wait dialog with spinner."""
        self.wait_dialog = Gtk.Dialog(
            title="Please Wait",
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        self.wait_dialog.set_default_size(250, 100)
        self.wait_dialog.set_resizable(False)
        self.wait_dialog.set_decorated(False)

        content_area = self.wait_dialog.get_content_area()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        content_area.add(vbox)

        spinner = Gtk.Spinner()
        spinner.props.active = True
        spinner.set_size_request(30, 30)
        vbox.pack_start(spinner, False, False, 0)

        label = Gtk.Label(label=message)
        vbox.pack_start(label, False, False, 0)

        vbox.show_all()
        self.wait_dialog.show()
        self.wait_dialog.present()

        # Ensure UI updates
        while Gtk.events_pending():
            Gtk.main_iteration()

    def _hide_wait_dialog(self):
        """Hide the wait dialog."""
        if self.wait_dialog:
            self.wait_dialog.destroy()
            self.wait_dialog = None
            # Ensure UI updates
            while Gtk.events_pending():
                Gtk.main_iteration()


__all__ = ['USBTab']
