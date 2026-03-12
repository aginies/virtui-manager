"""
Snapshot Tab

Provides VM snapshot management functionality (create, delete, restore).
"""

import threading
from typing import Optional, Callable

import gi
import libvirt
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from ... import vm_actions, vm_queries


class SnapshotTab:
    """
    Manages the snapshot tab UI and functionality.

    Provides snapshot management: create, delete, restore snapshots.
    """

    def __init__(self, domain, window=None,
                 log_callback: Optional[Callable[[str], None]] = None,
                 notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None,
                 reconnect_callback: Optional[Callable[[], None]] = None):
        """
        Initialize the snapshot tab.

        Args:
            domain: libvirt domain object
            window: Main window (for dialog parent)
            log_callback: Function to call for logging
            notification_callback: Function to call for notifications
            reconnect_callback: Function to call after snapshot restore
        """
        self.domain = domain
        self.window = window
        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None
        self.reconnect = reconnect_callback if reconnect_callback else lambda: None

        # UI elements
        self.snapshots_store: Optional[Gtk.ListStore] = None
        self.snapshots_tree_view: Optional[Gtk.TreeView] = None
        self.create_snapshot_button: Optional[Gtk.Button] = None
        self.delete_snapshot_button: Optional[Gtk.Button] = None
        self.restore_snapshot_button: Optional[Gtk.Button] = None

        # Wait dialog
        self.wait_dialog: Optional[Gtk.Dialog] = None

    def build_tab(self) -> Gtk.Box:
        """
        Build the snapshot tab UI.

        Returns:
            The tab container widget
        """
        tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tab.set_border_width(10)

        # Snapshot list store: name, description, creation_time, state, snapshot_object
        self.snapshots_store = Gtk.ListStore(str, str, str, str, object)
        self.snapshots_tree_view = Gtk.TreeView(model=self.snapshots_store)

        self._add_tree_columns()

        scroll_snapshots = Gtk.ScrolledWindow()
        scroll_snapshots.set_vexpand(True)
        scroll_snapshots.add(self.snapshots_tree_view)
        tab.pack_start(scroll_snapshots, True, True, 0)

        # Action buttons
        action_buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab.pack_start(action_buttons_box, False, False, 0)

        self.create_snapshot_button = Gtk.Button(label="Create Snapshot")
        self.create_snapshot_button.connect("clicked", self.on_create_snapshot_clicked)
        action_buttons_box.pack_start(self.create_snapshot_button, True, True, 0)

        self.delete_snapshot_button = Gtk.Button(label="Delete Snapshot")
        self.delete_snapshot_button.connect("clicked", self.on_delete_snapshot_clicked)
        self.delete_snapshot_button.set_sensitive(False)
        action_buttons_box.pack_start(self.delete_snapshot_button, True, True, 0)

        self.restore_snapshot_button = Gtk.Button(label="Restore Snapshot")
        self.restore_snapshot_button.connect("clicked", self.on_restore_snapshot_clicked)
        self.restore_snapshot_button.set_sensitive(False)
        action_buttons_box.pack_start(self.restore_snapshot_button, True, True, 0)

        refresh_button = Gtk.Button(label="Refresh Snapshots")
        refresh_button.connect("clicked", self.on_refresh_snapshots_clicked)
        action_buttons_box.pack_start(refresh_button, True, True, 0)

        # Connect selection change handler
        selection = self.snapshots_tree_view.get_selection()
        selection.connect("changed", self.on_selection_changed)

        return tab

    def _add_tree_columns(self):
        """Add columns to the snapshot tree view."""
        columns = [("Name", 0), ("Description", 1), ("Creation Time", 2), ("State", 3)]
        for title, col_id in columns:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=col_id)
            self.snapshots_tree_view.append_column(column)

    def populate_snapshots(self):
        """Populate the snapshots list from libvirt."""
        self.snapshots_store.clear()
        if self.domain:
            snapshots = vm_queries.get_vm_snapshots(self.domain)
            # Sort by creation time (newest first)
            snapshots.sort(key=lambda x: x.get("creation_time", ""), reverse=True)
            for snap in snapshots:
                self.snapshots_store.append([
                    snap.get("name", "N/A"),
                    snap.get("description", ""),
                    snap.get("creation_time", "N/A"),
                    snap.get("state", "N/A"),
                    snap.get("snapshot_object"),
                ])
        else:
            self.notify("No VM domain available to list snapshots.", Gtk.MessageType.WARNING)

    def on_selection_changed(self, selection):
        """Handle snapshot selection change."""
        model, treeiter = selection.get_selected()
        has_selection = treeiter is not None
        self.delete_snapshot_button.set_sensitive(has_selection)
        self.update_restore_button_sensitivity()

    def update_restore_button_sensitivity(self):
        """Update restore button based on selection and VM state."""
        selection = self.snapshots_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        is_vm_active = self.domain.isActive() if self.domain else True

        # Restore only when snapshot selected AND VM is stopped
        self.restore_snapshot_button.set_sensitive(treeiter is not None and not is_vm_active)

    def on_refresh_snapshots_clicked(self, button):
        """Refresh the snapshots list."""
        self.populate_snapshots()
        self.notify("Snapshots list refreshed.", Gtk.MessageType.INFO)

    def on_create_snapshot_clicked(self, button):
        """Show dialog and create a new snapshot."""
        dialog = Gtk.Dialog(
            title="Create Snapshot",
            parent=self.window,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )

        content_area = dialog.get_content_area()
        grid = Gtk.Grid(row_spacing=5, column_spacing=5, margin=10)
        content_area.add(grid)

        grid.attach(Gtk.Label(label="Snapshot Name:"), 0, 0, 1, 1)
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Required")
        grid.attach(name_entry, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Description:"), 0, 1, 1, 1)
        description_entry = Gtk.Entry()
        description_entry.set_placeholder_text("Optional")
        grid.attach(description_entry, 1, 1, 1, 1)

        quiesce_check = Gtk.CheckButton(label="Quiesce VM (Requires QEMU Guest Agent)")
        grid.attach(quiesce_check, 0, 2, 2, 1)

        grid.show_all()

        response = dialog.run()
        snapshot_name = name_entry.get_text().strip()
        snapshot_description = description_entry.get_text().strip()
        quiesce = quiesce_check.get_active()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            if not snapshot_name:
                self.notify("Snapshot name is required.", Gtk.MessageType.ERROR)
                return

            self._show_wait_dialog(f"Creating snapshot '{snapshot_name}'...")

            def _create_snapshot_thread():
                try:
                    vm_actions.create_vm_snapshot(
                        self.domain, snapshot_name, snapshot_description, quiesce
                    )
                    GLib.idle_add(
                        self.notify,
                        f"Snapshot '{snapshot_name}' created successfully.",
                        Gtk.MessageType.INFO,
                    )
                except libvirt.libvirtError as e:
                    GLib.idle_add(
                        self.notify,
                        f"Failed to create snapshot: {e}",
                        Gtk.MessageType.ERROR,
                    )
                except Exception as e:
                    GLib.idle_add(
                        self.notify,
                        f"An unexpected error occurred: {e}",
                        Gtk.MessageType.ERROR,
                    )
                finally:
                    GLib.idle_add(self._hide_wait_dialog)
                    GLib.idle_add(self.populate_snapshots)

            threading.Thread(target=_create_snapshot_thread, daemon=True).start()

    def on_delete_snapshot_clicked(self, button):
        """Delete the selected snapshot after confirmation."""
        selection = self.snapshots_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if not treeiter:
            self.notify("No snapshot selected for deletion.", Gtk.MessageType.WARNING)
            return

        snapshot_name = model[treeiter][0]

        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Confirm Snapshot Deletion",
        )
        dialog.format_secondary_text(
            f"Are you sure you want to delete snapshot '{snapshot_name}'?"
        )
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            self._show_wait_dialog(f"Deleting snapshot '{snapshot_name}'...")

            def _delete_snapshot_thread():
                try:
                    vm_actions.delete_vm_snapshot(self.domain, snapshot_name)
                    GLib.idle_add(
                        self.notify,
                        f"Snapshot '{snapshot_name}' deleted successfully.",
                        Gtk.MessageType.INFO,
                    )
                except libvirt.libvirtError as e:
                    GLib.idle_add(
                        self.notify,
                        f"Failed to delete snapshot: {e}",
                        Gtk.MessageType.ERROR,
                    )
                except Exception as e:
                    GLib.idle_add(
                        self.notify,
                        f"An unexpected error occurred: {e}",
                        Gtk.MessageType.ERROR,
                    )
                finally:
                    GLib.idle_add(self._hide_wait_dialog)
                    GLib.idle_add(self.populate_snapshots)

            threading.Thread(target=_delete_snapshot_thread, daemon=True).start()

    def on_restore_snapshot_clicked(self, button):
        """Restore VM to the selected snapshot after confirmation."""
        selection = self.snapshots_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if not treeiter:
            self.notify("No snapshot selected for restore.", Gtk.MessageType.WARNING)
            return

        snapshot_name = model[treeiter][0]

        if self.domain and self.domain.isActive():
            self.notify(
                "Cannot restore snapshot while VM is running. Please stop the VM first.",
                Gtk.MessageType.ERROR,
            )
            return

        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Confirm Snapshot Restore",
        )
        dialog.format_secondary_text(
            f"Are you sure you want to restore to snapshot '{snapshot_name}'? "
            "Any unsaved work in the current VM state will be lost."
        )
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            self._show_wait_dialog(f"Restoring VM to snapshot '{snapshot_name}'...")

            def _restore_snapshot_thread():
                try:
                    vm_actions.restore_vm_snapshot(self.domain, snapshot_name)
                    GLib.idle_add(
                        self.notify,
                        f"VM restored to snapshot '{snapshot_name}' successfully.",
                        Gtk.MessageType.INFO,
                    )
                    GLib.idle_add(self.reconnect)  # Reconnect display after restore
                except libvirt.libvirtError as e:
                    GLib.idle_add(
                        self.notify,
                        f"Failed to restore snapshot: {e}",
                        Gtk.MessageType.ERROR,
                    )
                except Exception as e:
                    GLib.idle_add(
                        self.notify,
                        f"An unexpected error occurred: {e}",
                        Gtk.MessageType.ERROR,
                    )
                finally:
                    GLib.idle_add(self._hide_wait_dialog)
                    GLib.idle_add(self.populate_snapshots)

            threading.Thread(target=_restore_snapshot_thread, daemon=True).start()

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


__all__ = ['SnapshotTab']
