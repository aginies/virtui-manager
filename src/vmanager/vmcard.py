"""
VMcard Interface
"""
import subprocess
import os
import logging
import traceback
import datetime
import time
from functools import partial
from urllib.parse import urlparse
import libvirt
from rich.markdown import Markdown as RichMarkdown

from textual.widgets import (
        Static, Button, TabbedContent,
        TabPane, Sparkline, Checkbox, Collapsible
        )
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual import on
from textual.events import Click
from textual.css.query import NoMatches

from events import VMNameClicked, VMSelectionChanged, VmActionRequest, VmCardUpdateRequest
from vm_actions import (
        clone_vm, rename_vm, create_vm_snapshot,
        restore_vm_snapshot, delete_vm_snapshot,
        create_external_overlay, commit_disk_changes,
        discard_overlay
        )

from vm_queries import (
        get_vm_snapshots, has_overlays, get_overlay_disks,
        get_vm_network_ip, get_boot_info, _get_domain_root,
        get_vm_disks, get_vm_cpu_details, get_vm_graphics_info, _parse_domain_xml,
        )
from modals.xml_modals import XMLDisplayModal
from modals.utils_modals import ConfirmationDialog, ProgressModal, LoadingModal
from modals.vmdetails_modals import VMDetailModal
from modals.migration_modals import MigrationModal
from modals.disk_pool_modals import SelectDiskModal
from modals.howto_overlay_modal import HowToOverlayModal
from modals.input_modals import InputModal, _sanitize_input
from vmcard_dialog import (
        DeleteVMConfirmationDialog, WebConsoleConfigDialog,
        AdvancedCloneDialog, RenameVMDialog, SelectSnapshotDialog, SnapshotNameDialog
        )
from utils import (
        extract_server_name_from_uri,
        generate_tooltip_markdown,
)
from constants import (
    ButtonLabels, ButtonIds, TabTitles, StatusText,
    SparklineLabels, ErrorMessages, DialogMessages, VmAction
)

class VMCardActions(Static):
    def __init__(self, card) -> None:
        self.card = card
        super().__init__()

    def compose(self):
        self.card.ui[ButtonIds.START] = Button(ButtonLabels.START, id=ButtonIds.START, variant="success")
        self.card.ui[ButtonIds.SHUTDOWN] = Button(ButtonLabels.SHUTDOWN, id=ButtonIds.SHUTDOWN, variant="primary")
        self.card.ui[ButtonIds.STOP] = Button(ButtonLabels.FORCE_OFF, id=ButtonIds.STOP, variant="error")
        self.card.ui[ButtonIds.PAUSE] = Button(ButtonLabels.PAUSE, id=ButtonIds.PAUSE, variant="primary")
        self.card.ui[ButtonIds.RESUME] = Button(ButtonLabels.RESUME, id=ButtonIds.RESUME, variant="success")
        self.card.ui[ButtonIds.CONFIGURE_BUTTON] = Button(ButtonLabels.CONFIGURE, id=ButtonIds.CONFIGURE_BUTTON, variant="primary")
        self.card.ui[ButtonIds.WEB_CONSOLE] = Button(ButtonLabels.WEB_CONSOLE, id=ButtonIds.WEB_CONSOLE, variant="default")
        self.card.ui[ButtonIds.CONNECT] = Button(ButtonLabels.CONNECT, id=ButtonIds.CONNECT, variant="default")
        
        self.card.ui[ButtonIds.SNAPSHOT_TAKE] = Button(ButtonLabels.SNAPSHOT, id=ButtonIds.SNAPSHOT_TAKE, variant="primary")
        self.card.ui[ButtonIds.SNAPSHOT_RESTORE] = Button(ButtonLabels.RESTORE_SNAPSHOT, id=ButtonIds.SNAPSHOT_RESTORE, variant="primary")
        self.card.ui[ButtonIds.SNAPSHOT_DELETE] = Button(ButtonLabels.DELETE_SNAPSHOT, id=ButtonIds.SNAPSHOT_DELETE, variant="error")

        self.card.ui[ButtonIds.DELETE] = Button(ButtonLabels.DELETE, id=ButtonIds.DELETE, variant="success", classes="delete-button")
        self.card.ui[ButtonIds.CLONE] = Button(ButtonLabels.CLONE, id=ButtonIds.CLONE, classes="clone-button")
        self.card.ui[ButtonIds.MIGRATION] = Button(ButtonLabels.MIGRATION, id=ButtonIds.MIGRATION, variant="primary", classes="migration-button")
        self.card.ui[ButtonIds.XML] = Button(ButtonLabels.VIEW_XML, id=ButtonIds.XML)
        self.card.ui[ButtonIds.RENAME_BUTTON] = Button(ButtonLabels.RENAME, id=ButtonIds.RENAME_BUTTON, variant="primary", classes="rename-button")

        self.card.ui[ButtonIds.CREATE_OVERLAY] = Button(ButtonLabels.CREATE_OVERLAY, id=ButtonIds.CREATE_OVERLAY, variant="primary")
        self.card.ui[ButtonIds.COMMIT_DISK] = Button(ButtonLabels.COMMIT_DISK, id=ButtonIds.COMMIT_DISK, variant="error")
        self.card.ui[ButtonIds.DISCARD_OVERLAY] = Button(ButtonLabels.DISCARD_OVERLAY, id=ButtonIds.DISCARD_OVERLAY, variant="error")
        self.card.ui[ButtonIds.SNAP_OVERLAY_HELP] = Button(ButtonLabels.SNAP_OVERLAY_HELP, id=ButtonIds.SNAP_OVERLAY_HELP, variant="default")

        self.card.ui["tabbed_content"] = TabbedContent(id="button-container")

        with self.card.ui["tabbed_content"]:
            with TabPane(TabTitles.MANAGE, id="manage-tab"):
                with Horizontal():
                    with Vertical():
                        yield self.card.ui[ButtonIds.START]
                        yield self.card.ui[ButtonIds.SHUTDOWN]
                        yield self.card.ui[ButtonIds.STOP]
                        yield self.card.ui[ButtonIds.PAUSE]
                        yield self.card.ui[ButtonIds.RESUME]
                    with Vertical():
                        yield self.card.ui[ButtonIds.CONFIGURE_BUTTON]
                        yield self.card.ui[ButtonIds.WEB_CONSOLE]
                        yield self.card.ui[ButtonIds.CONNECT]
            with TabPane(self.card._get_snapshot_tab_title(), id="snapshot-tab"):
                with Horizontal():
                    with Vertical():
                        yield self.card.ui[ButtonIds.SNAPSHOT_TAKE]
                        yield self.card.ui[ButtonIds.SNAPSHOT_RESTORE]
                        yield self.card.ui[ButtonIds.SNAPSHOT_DELETE]
                    with Vertical():
                        yield self.card.ui[ButtonIds.CREATE_OVERLAY]
                        yield self.card.ui[ButtonIds.COMMIT_DISK]
                        yield self.card.ui[ButtonIds.DISCARD_OVERLAY]
                        yield self.card.ui[ButtonIds.SNAP_OVERLAY_HELP]
            with TabPane(TabTitles.OTHER, id="special-tab"):
                with Horizontal():
                    with Vertical():
                        yield self.card.ui[ButtonIds.DELETE]
                        yield Static(classes="button-separator")
                        yield self.card.ui[ButtonIds.CLONE]
                        yield self.card.ui[ButtonIds.MIGRATION]
                    with Vertical():
                        yield self.card.ui[ButtonIds.XML]
                        yield Static(classes="button-separator")
                        yield self.card.ui[ButtonIds.RENAME_BUTTON]


class VMCard(Static):
    """
    Main VM card
    """
    name = reactive("")
    status = reactive("")
    cpu = reactive(0)
    memory = reactive(0)
    vm = reactive(None)
    conn = reactive(None)
    ip_addresses = reactive([])
    boot_device = reactive("")
    cpu_model = reactive("")

    webc_status_indicator = reactive("")
    graphics_type = reactive(None)
    server_border_color = reactive("green")
    is_selected = reactive(False)
    stats_view_mode = reactive("resources") # "resources" or "io"
    internal_id = reactive("")
    compact_view = reactive(False)

    @property
    def raw_uuid(self) -> str:
        """Returns the raw UUID part of the internal_id."""
        return self.internal_id.split('@')[0]

    # To store the latest raw stat values for display
    latest_disk_read = reactive(0.0)
    latest_disk_write = reactive(0.0)
    latest_net_rx = reactive(0.0)
    latest_net_tx = reactive(0.0)

    def __init__(self, is_selected: bool = False) -> None:
        self.ui = {}
        super().__init__()
        self.is_selected = is_selected
        self.timer = None
        self._boot_device_checked = False

    def _get_vm_display_name(self) -> str:
        """Returns the formatted VM name including server name if available."""
        if hasattr(self, 'conn') and self.conn:
            #server_display = extract_server_name_from_uri(self.conn.getURI())
            # Use cached URI lookup to avoid libvirt call
            uri = self.app.vm_service.get_uri_for_connection(self.conn)
            if not uri:
                uri = self.conn.getURI()
            server_display = extract_server_name_from_uri(uri)
            return f"{self.name} ({server_display})"
        return self.name

    def _get_snapshot_tab_title(self, num_snapshots: int = -1) -> str:
        """Get snapshot tab title. Pass num_snapshots to avoid blocking libvirt call."""
        if num_snapshots == -1:
             # If no count provided, don't fetch it here to avoid blocking.
             # For now, return default if we can't get it cheaply.
             return TabTitles.SNAP_OVER_UPDATE # TabTitles.SNAPSHOT + "/" + TabTitles.OVERLAY

        if self.vm:
            try:
                if num_snapshots == 0:
                    return TabTitles.SNAPSHOT + "/" + TabTitles.OVERLAY
                elif num_snapshots == 1:
                    return TabTitles.SNAPSHOT + "(" + str(num_snapshots) + ")" + "/" + TabTitles.OVERLAY
                elif num_snapshots >= 2:
                    return TabTitles.SNAPSHOTS + "(" + str(num_snapshots) + ")" "/" + TabTitles.OVERLAY
            except libvirt.libvirtError:
                pass # Domain might be transient or invalid
        return TabTitles.SNAPSHOT + "/" + TabTitles.OVERLAY

    def update_snapshot_tab_title(self, num_snapshots: int = -1) -> None:
        """Updates the snapshot tab title."""
        try:
            if not self.ui:
                return

            tabbed_content = self.ui.get("tabbed_content")
            if tabbed_content:
                tabbed_content.get_tab("snapshot-tab").update(self._get_snapshot_tab_title(num_snapshots))
        except NoMatches:
            logging.warning("Could not find snapshot tab to update title.")

    def _update_webc_status(self) -> None:
        """Updates the web console status indicator and button."""
        webc_is_running = False
        if hasattr(self.app, 'webconsole_manager') and self.vm:
            try:
                uuid = self.internal_id
                if uuid: # ensure uuid is not empty
                    webc_is_running = self.app.webconsole_manager.is_running(uuid)
            except Exception as e:
                logging.warning(f"Error getting webconsole status for {self.internal_id}: {e}")

        # Update status indicator text
        new_indicator = " (WebC On)" if webc_is_running else ""
        if self.webc_status_indicator != new_indicator:
            self.webc_status_indicator = new_indicator

        # Update button label and style
        web_console_button = self.ui.get(ButtonIds.WEB_CONSOLE)
        if web_console_button:
            if webc_is_running:
                web_console_button.label = "Show Console"
                web_console_button.variant = "success"
            else:
                web_console_button.label = ButtonLabels.WEB_CONSOLE
                web_console_button.variant = "default"

    def watch_webc_status_indicator(self, old_value: str, new_value: str) -> None:
        """Called when webc_status_indicator changes."""
        if not self.ui:
            return
        status_widget = self.ui.get("status")
        if status_widget:
            status_text = f"Status: {self.status}{new_value}"
            status_widget.update(status_text)

    def compose(self):
        self.ui["checkbox"] = Checkbox("", id="vm-select-checkbox", classes="vm-select-checkbox", value=self.is_selected)
        self.ui["vmname"] = Static(self._get_vm_display_name(), id="vmname", classes="vmname")
        self.ui["status"] = Static(f"Status: {self.status}{self.webc_status_indicator}", id="status", classes=self.status.lower())

        # Create all sparkline components
        self.ui["cpu_label"] = Static("", classes="sparkline-label")
        self.ui["cpu_sparkline"] = Sparkline([], id="cpu-sparkline")
        self.ui["cpu_sparkline_container"] = Horizontal(self.ui["cpu_label"], self.ui["cpu_sparkline"], id="cpu_sparkline_container", classes="sparkline-container resources-sparkline")

        self.ui["mem_label"] = Static("", classes="sparkline-label")
        self.ui["mem_sparkline"] = Sparkline([], id="mem-sparkline")
        self.ui["mem_sparkline_container"] = Horizontal(self.ui["mem_label"], self.ui["mem_sparkline"], id="mem_sparkline_container", classes="sparkline-container resources-sparkline")

        self.ui["disk_label"] = Static("", classes="sparkline-label")
        self.ui["disk_sparkline"] = Sparkline([], id="disk-sparkline")
        self.ui["disk_sparkline_container"] = Horizontal(self.ui["disk_label"], self.ui["disk_sparkline"], id="disk_sparkline_container", classes="sparkline-container io-sparkline")

        self.ui["net_label"] = Static("", classes="sparkline-label")
        self.ui["net_sparkline"] = Sparkline([], id="net-sparkline")
        self.ui["net_sparkline_container"] = Horizontal(self.ui["net_label"], self.ui["net_sparkline"], id="net_sparkline_container", classes="sparkline-container io-sparkline")

        # A single container for all sparklines that will handle clicks
        self.ui["sparklines_container"] = Vertical(
            self.ui["cpu_sparkline_container"],
            self.ui["mem_sparkline_container"],
            self.ui["disk_sparkline_container"],
            self.ui["net_sparkline_container"],
            id="sparklines-container-group"
        )

        self.ui["collapsible"] = Collapsible(title="Actions", id="actions-collapsible")

        with Vertical(id="info-container"):
            with Horizontal(id="vm-header-row"):
                yield self.ui["checkbox"]
                with Vertical():
                    yield self.ui["vmname"]
                    yield self.ui["status"]

            yield self.ui["sparklines_container"]
            yield self.ui["collapsible"]

    @on(Collapsible.Expanded, "#actions-collapsible")
    async def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
        if not self.ui.get("tabbed_content"):
            actions_view = VMCardActions(self)
            await self.ui["collapsible"].mount(actions_view)
            self.update_button_layout()

    @on(Collapsible.Collapsed, "#actions-collapsible")
    async def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:
        self._cleanup_actions()

    def _cleanup_actions(self):
        try:
            for child in self.query(VMCardActions):
                child.remove()
        except NoMatches:
            pass

        # Clean up dynamic UI references to avoid memory leaks and stale state
        keys_to_keep = {
            "checkbox", "vmname", "status", "collapsible",
            "sparklines_container",
            # Resource Sparklines
            "cpu_label", "cpu_sparkline", "cpu_sparkline_container",
            "mem_label", "mem_sparkline", "mem_sparkline_container",
            # IO Sparklines
            "disk_label", "disk_sparkline", "disk_sparkline_container",
            "net_label", "net_sparkline", "net_sparkline_container",
        }
        for key in list(self.ui.keys()):
            if key not in keys_to_keep:
                self.ui.pop(key, None)

    def _is_remote_server(self) -> bool:
        """Checks if the VM is on a remote server."""
        if not self.conn:
            return False
        try:
            #uri = self.conn.getURI()
            # Use cached URI to avoid libvirt call
            uri = self.app.vm_service.get_uri_for_connection(self.conn)
            if not uri:
                uri = self.conn.getURI()
            parsed = urlparse(uri)
            return parsed.hostname not in (None, "localhost", "127.0.0.1") and parsed.scheme == "qemu+ssh"
        except Exception:
            return False

    def _perform_tooltip_update(self) -> None:
        """Updates the tooltip for the VM name using Markdown."""
        if not self.display:
            return
        if not self.ui or "vmname" not in self.ui:
            return
        
        uuid = self.internal_id
        if not uuid:
            return

        has_cached_xml = False
        with self.app.vm_service._cache_lock:
            vm_cache = self.app.vm_service._vm_data_cache.get(uuid, {})
            has_cached_xml = vm_cache.get('xml') is not None

        if self._is_remote_server() and not has_cached_xml:
            self.ui["vmname"].tooltip = None
            return
        # Use cached identity to avoid libvirt call
        try:
            #uuid_display = self.vm.UUIDString() if self.vm else "Unknown"
            if self.vm:
                raw_uuid, _ = self.app.vm_service.get_vm_identity(self.vm, self.conn)
                uuid_display = raw_uuid.split('@')[0]  # Extract just the UUID part
            else:
                uuid_display = "Unknown"
        except Exception:
            uuid_display = "Unknown"

        hypervisor = "Unknown"
        if self.conn:
            #hypervisor = extract_server_name_from_uri(self.conn.getURI())
            uri = self.app.vm_service.get_uri_for_connection(self.conn) or self.conn.getURI()
            hypervisor = extract_server_name_from_uri(uri)

        mem_display = f"{self.memory} MiB"
        if self.memory >= 1024:
            mem_display += f" ({self.memory / 1024:.2f} GiB)"

        ip_display = "N/A"
        if self.status == StatusText.RUNNING and self.ip_addresses:
            ips = []
            for iface in self.ip_addresses:
                ips.extend(iface.get('ipv4', []))
            if ips:
                ip_display = ", ".join(ips)

        cpu_model_display = f" ({self.cpu_model})" if self.cpu_model else ""

        tooltip_md = generate_tooltip_markdown(
            uuid=uuid_display,
            hypervisor=hypervisor,
            status=self.status,
            ip=ip_display,
            boot=self.boot_device or "N/A",
            cpu=self.cpu,
            cpu_model=self.cpu_model or "",
            memory=self.memory
        )

        self.ui["vmname"].tooltip = RichMarkdown(tooltip_md)

    def on_mount(self) -> None:
        self.styles.background = "#323232"
        if self.is_selected:
            self.styles.border = ("panel", "white")
        else:
            self.styles.border = ("solid", self.server_border_color)

        self.update_button_layout()
        self._update_status_styling()
        self._update_webc_status()
        self.watch_stats_view_mode(self.stats_view_mode, self.stats_view_mode) # Initial setup
        self._perform_tooltip_update()

        uuid = self.internal_id
        if uuid and uuid in self.app.sparkline_data:
            self.update_sparkline_data()

        self.update_stats()
        self.watch_compact_view(self.compact_view)

    def watch_stats_view_mode(self, old_mode: str, new_mode: str) -> None:
        """Update sparklines when view mode changes."""
        if not self.display or not self.ui:
            return

        is_active = self.status in (StatusText.RUNNING, StatusText.PAUSED)
        is_resources_mode = new_mode == "resources"

        sparklines_container = self.ui.get("sparklines_container")
        if sparklines_container:
            sparklines_container.display = is_active

        # Toggle individual rows
        for widget in self.query(".resources-sparkline"):
            widget.display = is_resources_mode
        for widget in self.query(".io-sparkline"):
            widget.display = not is_resources_mode

        # If the VM is active, update the data for the newly visible sparklines
        if is_active:
            self.update_sparkline_data()

    def update_sparkline_data(self) -> None:
        """Updates the labels and data of the sparklines based on the current view mode."""
        if not self.is_mounted or not self.display or self.compact_view:
            return

        uuid = self.internal_id
        storage = {}
        if uuid and hasattr(self.app, 'sparkline_data') and uuid in self.app.sparkline_data:
            storage = self.app.sparkline_data[uuid]

        if self.stats_view_mode == "resources":
            cpu_label = self.ui.get("cpu_label")
            mem_label = self.ui.get("mem_label")
            cpu_sparkline = self.ui.get("cpu_sparkline")
            mem_sparkline = self.ui.get("mem_sparkline")

            if all([cpu_label, mem_label, cpu_sparkline, mem_sparkline]):
                if self.cpu > 0:
                    cpu_text = SparklineLabels.VCPU.format(cpu=self.cpu)
                else:
                    cpu_text = SparklineLabels.VCPU.split("}", 1)[1].strip()

                if self.memory > 0:
                    mem_gb = round(self.memory / 1024, 1)
                    mem_text = SparklineLabels.MEMORY_GB.format(mem=mem_gb)
                else:
                    mem_text = SparklineLabels.MEMORY_GB.split("}", 1)[1].strip()

                cpu_label.update(cpu_text)
                mem_label.update(mem_text)

                cpu_sparkline.data = list(storage.get("cpu", []))
                mem_sparkline.data = list(storage.get("mem", []))
        else:  # io mode
            disk_label = self.ui.get("disk_label")
            net_label = self.ui.get("net_label")
            disk_sparkline = self.ui.get("disk_sparkline")
            net_sparkline = self.ui.get("net_sparkline")

            if all([disk_label, net_label, disk_sparkline, net_sparkline]):
                disk_read_mb = self.latest_disk_read / 1024
                disk_write_mb = self.latest_disk_write / 1024
                net_rx_mb = self.latest_net_rx / 1024
                net_tx_mb = self.latest_net_tx / 1024

                disk_text = SparklineLabels.DISK_RW.format(read=disk_read_mb, write=disk_write_mb)
                net_text = SparklineLabels.NET_RX_TX.format(rx=net_rx_mb, tx=net_tx_mb)

                disk_label.update(disk_text)
                net_label.update(net_text)

                disk_sparkline.data = list(storage.get("disk", []))
                net_sparkline.data = list(storage.get("net", []))

    def watch_name(self, value: str) -> None:
        """Called when name changes."""
        if self.ui:
            vmname_widget = self.ui.get("vmname")
            if vmname_widget:
                vmname_widget.update(self._get_vm_display_name())

    def watch_cpu(self, value: int) -> None:
        """Called when cpu count changes."""
        self._perform_tooltip_update()

    def watch_memory(self, value: int) -> None:
        """Called when memory changes."""
        self._perform_tooltip_update()

    def watch_ip_addresses(self, value: list) -> None:
        """Called when IP addresses change."""
        self._perform_tooltip_update()

    def watch_boot_device(self, value: str) -> None:
        """Called when boot device changes."""
        self._perform_tooltip_update()

    def watch_cpu_model(self, value: str) -> None:
        """Called when cpu_model changes."""
        self._perform_tooltip_update()

    def watch_graphics_type(self, old_value: str, new_value: str) -> None:
        """Called when graphics_type changes."""
        self._perform_tooltip_update()

    def watch_internal_id(self, old_value: str, new_value: str) -> None:
        """Called when internal_id changes (card reuse)."""
        if old_value and old_value != new_value:
            # Cancel old stats worker if running
            self.app.worker_manager.cancel(f"update_stats_{old_value}")
            self.app.worker_manager.cancel(f"actions_state_{old_value}")
            self.app.worker_manager.cancel(f"refresh_snapshot_tab_{old_value}")

        if old_value != new_value:
            # Reset heavy state UI elements
            self.update_snapshot_tab_title(-1)
            self.update_button_layout()
            self.update_stats()
            self._perform_tooltip_update()

    def watch_compact_view(self, value: bool) -> None:
        """Called when compact_view changes."""
        if not self.ui or not self.is_mounted:
            return

        #sparklines = self.ui.get("sparklines_container")
        collapsible = self.ui.get("collapsible")

        if value: # if compact view, add hidden class
            #if sparklines and sparklines.is_mounted:
            #    logging.info("DEBUG remove spark")
            #    sparklines.remove()
            if collapsible and collapsible.is_mounted:
                collapsible.collapsed = True
                collapsible.remove()
        else:
            info_container = self.query_one("#info-container")
            #if sparklines:
            #    info_container.mount(sparklines)
            if collapsible:
                info_container.mount(collapsible)

            # Ensure sparklines visibility is correct
            self.watch_stats_view_mode(self.stats_view_mode, self.stats_view_mode)

        # Change height based on compact_view
        if value: # Compact view
            self.styles.height = 4
        else: # Detailed view
            self.styles.height = 14

    def watch_status(self, old_value: str, new_value: str) -> None:
        """Called when status changes."""
        if not self.ui:
            return
        self._update_status_styling()
        self.watch_stats_view_mode(self.stats_view_mode, self.stats_view_mode) # Re-evaluate sparkline visibility
        self.update_button_layout()
        self._perform_tooltip_update()

        status_widget = self.ui.get("status")
        if status_widget:
            status_widget.update(f"Status: {new_value}{self.webc_status_indicator}")

        if new_value == StatusText.RUNNING:
            # Only invalidate cache if transitioning from STOPPED (clean boot)
            # This prevents cache thrashing if status flickers (e.g. Unknown -> Running)
            if old_value == StatusText.STOPPED:
                try:
                    self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                except Exception:
                    pass
            self.update_stats()

    def watch_server_border_color(self, old_color: str, new_color: str) -> None:
        """Called when server_border_color changes."""
        self.styles.border = ("solid", new_color)

    def on_unmount(self) -> None:
        """Stop the timer and cancel any running stat workers when the widget is removed."""
        if self.timer:
            self.timer.stop()
        if self.vm:
            try:
                uuid = self.internal_id
                self.app.worker_manager.cancel(f"update_stats_{uuid}")
            except Exception:
                pass

        # Reset collapsible state for next mount
        collapsible = self.ui.get("collapsible")
        if collapsible and not collapsible.collapsed:
            collapsible.collapsed = True

        self._cleanup_actions()

    def reset_for_reuse(self) -> None:
        """Reset card state for reuse by another VM."""
        # Stop any running timers
        if self.timer:
            self.timer.stop()
            self.timer = None

        # Cancel any running workers
        if self.internal_id:
            try:
                self.app.worker_manager.cancel(f"update_stats_{self.internal_id}")
                self.app.worker_manager.cancel(f"actions_state_{self.internal_id}")
            except Exception:
                pass

        # Reset flags
        self._boot_device_checked = False

    def watch_is_selected(self, old_value: bool, new_value: bool) -> None:
        """Called when is_selected changes to update the checkbox."""
        if not self.ui:
            return
        checkbox = self.ui.get("checkbox")
        if checkbox:
            checkbox.value = new_value

        if new_value:
            self.styles.border = ("panel", "white")
        else:
            self.styles.border = ("solid", self.server_border_color)

    def update_stats(self) -> None:
        """Schedules a worker to update statistics for the VM."""
        if not self.display:
            return
        if not self.vm:
            return

        # Cancel previous timer if it exists to prevent accumulation
        if self.timer:
            self.timer.stop()
            self.timer = None

        is_stopped = self.status == StatusText.STOPPED

        # If the VM is stopped, we don't schedule a recurring timer.
        # The worker will run once to check if the state has changed.
        # If it has (e.g., started externally), the watch_status handler
        # will call update_stats again, and a timer will be scheduled.
        if not is_stopped:
            interval = self.app.config.get('STATS_INTERVAL', 5)
            self.timer = self.set_timer(interval, self.update_stats)

        # If the VM is stopped, we still run the worker once to catch external state changes.
        uuid = self.internal_id
        if not uuid:
            return

        # Capture reactive values on main thread to avoid unsafe access in worker
        current_status = self.status
        current_boot_device = self.boot_device
        current_cpu_model = self.cpu_model
        current_graphics_type = self.graphics_type
        is_remote = self._is_remote_server()
        app_ref = self.app
        vm_service = app_ref.vm_service

        def update_worker():
            try:
                logging.debug(f"Starting update_stats worker for {self.name} (ID: {uuid})")
                stats = self.app.vm_service.get_vm_runtime_stats(self.vm)
                logging.debug(f"Stats received for {self.name}: {stats}")
                # Update info from cache if XML has been fetched (e.g. via Configure)
                vm_cache = vm_service._vm_data_cache.get(uuid, {})
                xml_content = vm_cache.get('xml')
                boot_dev = current_boot_device
                cpu_model = current_cpu_model
                graphics_type = current_graphics_type


                if not getattr(self, "_boot_device_checked", False):
                    if xml_content:
                        root = _parse_domain_xml(xml_content)
                        if root is not None:
                            # Use cached XML when available
                            boot_info = get_boot_info(self.conn, root)
                            if boot_info['order']:
                                boot_dev = boot_info['order'][0]
                            cpu_model = get_vm_cpu_details(root) or ""
                            graphics_info = get_vm_graphics_info(root)
                            graphics_type = graphics_info.get("type")
                            self._boot_device_checked = True
                    elif not is_remote:
                        # Fallback for local VMs: fetch XML once to populate cache
                        # This is acceptable as it's done only once and cached
                        try:
                            # This calls XMLDesc() only once, then caches it
                            _, root = _get_domain_root(self.vm)
                            if root is not None:
                                boot_info = get_boot_info(self.conn, root)
                                if boot_info['order']:
                                    boot_dev = boot_info['order'][0]
                                cpu_model = get_vm_cpu_details(root) or ""
                                graphics_info = get_vm_graphics_info(root)
                                graphics_type = graphics_info.get("type")

                                # XML is now cached by _get_domain_root, so we're done
                                # No need for additional cache population
                                self._boot_device_checked = True
                        except Exception:
                            pass

                if not stats:
                    if current_status != StatusText.STOPPED:
                        self.app.call_from_thread(setattr, self, 'status', StatusText.STOPPED)
                        self.app.call_from_thread(setattr, self, 'ip_addresses', [])
                        self.app.call_from_thread(setattr, self, 'boot_device', boot_dev)
                        self.app.call_from_thread(setattr, self, 'cpu_model', cpu_model)
                        self.app.call_from_thread(setattr, self, 'graphics_type', graphics_type)
                    return

                # Fetch IPs if running (check stats status, not UI status which might be stale)
                ips = []
                if stats.get("status") == StatusText.RUNNING:
                    ips = get_vm_network_ip(self.vm)

                def apply_stats_to_ui():
                    if not self.is_mounted:
                        return
                    if self.status != stats["status"]:
                        self.status = stats["status"]

                    self.ip_addresses = ips
                    self.boot_device = boot_dev
                    self.cpu_model = cpu_model
                    self.graphics_type = graphics_type

                    self.latest_disk_read = stats.get('disk_read_kbps', 0)
                    self.latest_disk_write = stats.get('disk_write_kbps', 0)
                    self.latest_net_rx = stats.get('net_rx_kbps', 0)
                    self.latest_net_tx = stats.get('net_tx_kbps', 0)

                    # Update web console status here instead of every cycle
                    self._update_webc_status()

                    if hasattr(self.app, "sparkline_data"):
                        if uuid in self.app.sparkline_data:
                            storage = self.app.sparkline_data[uuid]

                            def update_history(key, value):
                                history = storage.get(key, [])
                                history.append(value)
                                if len(history) > 20:
                                    history.pop(0)
                                storage[key] = history

                            if self.stats_view_mode == "resources":
                                update_history("cpu", stats["cpu_percent"])
                                update_history("mem", stats["mem_percent"])
                            else: # io
                                update_history("disk", self.latest_disk_read + self.latest_disk_write)
                                update_history("net", self.latest_net_rx + self.latest_net_tx)

                            self.update_sparkline_data()
                        else:
                            logging.warning(f"UUID {uuid} not found in sparkline_data")
                    else:
                        logging.error("app does not have sparkline_data attribute")

                try:
                    self.app.call_from_thread(apply_stats_to_ui)
                except RuntimeError:
                    apply_stats_to_ui()

            except libvirt.libvirtError as e:
                error_code = e.get_error_code()
                if error_code == libvirt.VIR_ERR_NO_DOMAIN:
                    if self.timer:
                        try:
                            self.app.call_from_thread(self.timer.stop)
                        except RuntimeError:
                            self.timer.stop()
                    try:
                        self.app.call_from_thread(self.app.refresh_vm_list)
                    except RuntimeError:
                        self.app.refresh_vm_list()
                elif error_code in [libvirt.VIR_ERR_SYSTEM_ERROR, libvirt.VIR_ERR_RPC, libvirt.VIR_ERR_NO_CONNECT, libvirt.VIR_ERR_INVALID_CONN]:
                    logging.warning(f"Connection error for {self.name}: {e}. Triggering refresh.")
                    try:
                        self.app.call_from_thread(self.app.refresh_vm_list, force=True)
                    except RuntimeError:
                        self.app.refresh_vm_list(force=True)
                else:
                    logging.warning(f"Libvirt error during stat update for {self.name}: {e}")
            except Exception as e:
                if e.__class__.__name__ == "NoActiveAppError":
                    return
                logging.error(f"Unexpected error in update_stats worker for {self.name}: {e}", exc_info=True)

        self.app.worker_manager.run(update_worker, name=f"update_stats_{uuid}")

    @on(Click, ".sparkline-container, #cpu-sparkline, #mem-sparkline, #disk-sparkline, #net-sparkline")
    def toggle_stats_view(self) -> None:
        """Toggle between resource and I/O stat views."""
        if self.status in (StatusText.RUNNING, StatusText.PAUSED):
            self.stats_view_mode = "io" if self.stats_view_mode == "resources" else "resources"

    def update_button_layout(self):
        """Update the button layout based on current VM status."""
        self._update_fast_buttons()
        self._update_webc_status()

        # Trigger background fetch for heavy data (snapshots, overlays) only if actions are visible
        if self.ui.get(ButtonIds.RENAME_BUTTON):
            # Check if collapsible is expanded before fetching heavy data
            collapsible = self.ui.get("collapsible")
            if collapsible and not collapsible.collapsed:
                self.app.set_timer(0.1, self._refresh_snapshot_tab_async)
                self.app.worker_manager.run(
                    self._fetch_actions_state_worker,
                    name=f"actions_state_{self.internal_id}",
                    exclusive=True
                )

    def _update_fast_buttons(self):
        """Updates buttons that rely on cached/fast state."""
        is_loading = self.status == StatusText.LOADING
        is_stopped = self.status == StatusText.STOPPED
        is_running = self.status == StatusText.RUNNING
        is_paused = self.status == StatusText.PAUSED

        if not self.ui.get(ButtonIds.RENAME_BUTTON):
            return

        self.ui[ButtonIds.START].display = is_stopped
        self.ui[ButtonIds.SHUTDOWN].display = is_running
        self.ui[ButtonIds.STOP].display = is_running or is_paused
        self.ui[ButtonIds.DELETE].display = is_running or is_paused or is_stopped
        self.ui[ButtonIds.CLONE].display = is_stopped
        self.ui[ButtonIds.MIGRATION].display = not is_loading
        self.ui[ButtonIds.RENAME_BUTTON].display = is_stopped
        self.ui[ButtonIds.PAUSE].display = is_running
        self.ui[ButtonIds.RESUME].display = is_paused
        self.ui[ButtonIds.CONNECT].display = self.app.r_viewer_available
        self.ui[ButtonIds.WEB_CONSOLE].display = (is_running or is_paused)
        self.ui[ButtonIds.CONFIGURE_BUTTON].display = not is_loading
        self.ui[ButtonIds.SNAP_OVERLAY_HELP].display = not is_loading
        self.ui[ButtonIds.SNAPSHOT_TAKE].display = not is_loading
        self.ui[ButtonIds.SNAPSHOT_RESTORE].display = not is_running and not is_loading

        xml_button = self.ui[ButtonIds.XML]
        if is_stopped:
            xml_button.label = "Edit XML"
            self.stats_view_mode = "resources"
        else:
            xml_button.label = "View XML"
        xml_button.display = not is_loading

    def _fetch_actions_state_worker(self):
        """Worker to fetch heavy state for actions."""
        try:
            snapshot_summary = {'count': 0, 'latest': None}
            has_overlay = False
            domain_missing = False
            state_tuple = self.app.vm_service._get_domain_state(self.vm)
            if not state_tuple:
                return

            last_fetch_key = f"snapshot_fetch_{self.internal_id}"
            if hasattr(self.app, '_last_snapshot_fetch'):
                cached_data = self.app._last_snapshot_fetch.get(last_fetch_key)
                # dict format for caching data
                if isinstance(cached_data, dict):
                    last_fetch = cached_data.get('time', 0)
                    if time.time() - last_fetch < 2:
                        # Use cached data and update UI immediately
                        snapshot_summary = cached_data.get('snapshot_summary', {'count': 0, 'latest': None})
                        has_overlay = cached_data.get('has_overlay', False)
                        try:
                            self.app.call_from_thread(self._update_slow_buttons, snapshot_summary, has_overlay)
                        except RuntimeError:
                            self._update_slow_buttons(snapshot_summary, has_overlay)
                        return
                elif isinstance(cached_data, (int, float)):
                    if time.time() - cached_data < 2:
                        return

            #try:
            #    if self.vm:
            #        snapshot_count = self.vm.snapshotNum(0)
            #except libvirt.libvirtError as e:
            #    if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
            #        domain_missing = True
            #    else:
            #        logging.warning(f"Could not get snapshot count for {self.name}: {e}")

            try:
                if self.vm and not domain_missing:
                    # Optimization: only fetch full details if there are snapshots
                    if self.vm.snapshotNum(0) > 0:
                        snapshots = get_vm_snapshots(self.vm)
                        if snapshots:
                            snapshot_summary['count'] = len(snapshots)
                            latest = snapshots[0]
                            snapshot_summary['latest'] = {
                                'name': latest['name'],
                                'time': latest['creation_time']
                            }
            except Exception:
                pass

            try:
                if self.vm and not domain_missing:
                    has_overlay = has_overlays(self.vm)
            except Exception:
                pass

            if domain_missing:
                try:
                    self.app.call_from_thread(self.app.refresh_vm_list)
                except RuntimeError:
                    self.app.refresh_vm_list()
                return

            # Record fetch time and data
            if not hasattr(self.app, '_last_snapshot_fetch'):
                self.app._last_snapshot_fetch = {}

            self.app._last_snapshot_fetch[last_fetch_key] = {
                'time': time.time(),
                'snapshot_summary': snapshot_summary,
                'has_overlay': has_overlay
            }

            def update_ui():
                self._update_slow_buttons(snapshot_summary, has_overlay)
            
            try:
                self.app.call_from_thread(update_ui)
            except RuntimeError:
                update_ui()

        except Exception as e:
            logging.error(f"Error in actions state worker for {self.name}: {e}")

    def _update_slow_buttons(self, snapshot_summary: dict, has_overlay: bool):
        """Updates buttons that rely on heavy state."""
        if not self.ui.get(ButtonIds.RENAME_BUTTON):
            return

        snapshot_count = snapshot_summary.get('count', 0)

        # Update Tooltip on TabPane
        tabbed_content = self.ui.get("tabbed_content")
        if tabbed_content:
            try:
                pane = tabbed_content.get_tab("snapshot-tab")
                if snapshot_count > 0:
                    latest = snapshot_summary.get('latest')
                    info = f"Latest: {latest['name']} ({latest['time']})" if latest else "Unknown"
                    pane.tooltip = f"{info}\nTotal: {snapshot_count}"
                else:
                    pane.tooltip = "No Snapshots created"
            except Exception:
                pass

        is_running = self.status == StatusText.RUNNING
        is_stopped = self.status == StatusText.STOPPED
        is_loading = self.status == StatusText.LOADING

        has_snapshots = snapshot_count > 0

        self.ui[ButtonIds.SNAPSHOT_RESTORE].display = has_snapshots and not is_running and not is_loading
        self.ui[ButtonIds.SNAPSHOT_DELETE].display = has_snapshots

        self.ui[ButtonIds.COMMIT_DISK].display = is_running and has_overlay
        self.ui[ButtonIds.DISCARD_OVERLAY].display = is_stopped and has_overlay
        self.ui[ButtonIds.CREATE_OVERLAY].display = is_stopped and not has_overlay

        self.update_snapshot_tab_title(snapshot_count)

    def _update_status_styling(self):
        status_widget = self.ui.get("status")
        if status_widget:
            status_widget.remove_class("stopped", "running", "paused", "loading")
            if self.status == StatusText.LOADING:
                status_widget.add_class("loading")
            else:
                status_widget.add_class(self.status.lower())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == ButtonIds.START:
            self.post_message(VmActionRequest(self.internal_id, VmAction.START))
            return

        button_handlers = {
            ButtonIds.SHUTDOWN: self._handle_shutdown_button,
            ButtonIds.STOP: self._handle_stop_button,
            ButtonIds.PAUSE: self._handle_pause_button,
            ButtonIds.RESUME: self._handle_resume_button,
            ButtonIds.XML: self._handle_xml_button,
            ButtonIds.CONNECT: self._handle_connect_button,
            ButtonIds.WEB_CONSOLE: self._handle_web_console_button,
            ButtonIds.SNAPSHOT_TAKE: self._handle_snapshot_take_button,
            ButtonIds.SNAPSHOT_RESTORE: self._handle_snapshot_restore_button,
            ButtonIds.SNAPSHOT_DELETE: self._handle_snapshot_delete_button,
            ButtonIds.DELETE: self._handle_delete_button,
            ButtonIds.CLONE: self._handle_clone_button,
            ButtonIds.MIGRATION: self._handle_migration_button,
            ButtonIds.RENAME_BUTTON: self._handle_rename_button,
            ButtonIds.CONFIGURE_BUTTON: self._handle_configure_button,
            ButtonIds.CREATE_OVERLAY: self._handle_create_overlay,
            ButtonIds.COMMIT_DISK: self._handle_commit_disk,
            ButtonIds.DISCARD_OVERLAY: self._handle_discard_overlay,
            ButtonIds.SNAP_OVERLAY_HELP: self._handle_overlay_help,
        }
        handler = button_handlers.get(event.button.id)
        if handler:
            handler(event)

    def _handle_overlay_help(self, event: Button.Pressed) -> None:
        """Handles the overlay help button press."""
        self.app.push_screen(HowToOverlayModal())

    def _handle_create_overlay(self, event: Button.Pressed) -> None:
        """Handles the create overlay button press."""
        try:
            # Use cached XML if available to avoid XMLDesc() call
            vm_cache = self.app.vm_service._vm_data_cache.get(self.internal_id, {})
            xml_content = vm_cache.get('xml')

            disks = get_vm_disks(self.vm)
            # Filter for actual disks (exclude cdroms, etc)
            valid_disks = [d['path'] for d in disks if d.get('device_type') == 'disk']

            if not valid_disks:
                self.app.show_error_message("No suitable disks found for overlay.")
                return

            target_disk = valid_disks[0]

            _, vm_name = self.app.vm_service.get_vm_identity(self.vm, self.conn)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            default_name = f"{vm_name}_overlay_{timestamp}.qcow2"

            def on_name_input(overlay_name_raw: str | None):
                if not overlay_name_raw:
                    return

                try:
                    overlay_name, was_modified = _sanitize_input(overlay_name_raw)
                except ValueError as e:
                    self.app.show_error_message(str(e))
                    return

                if was_modified:
                    self.app.show_success_message(f"Input sanitized: '{overlay_name_raw}' changed to '{overlay_name}'")

                if not overlay_name:
                    self.app.show_error_message("Overlay volume name cannot be empty after sanitization.")
                    return

                try:
                    create_external_overlay(self.vm, target_disk, overlay_name)
                    self.app.show_success_message(f"Overlay [b]{overlay_name}[/b] created and attached.")
                    self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                    self._boot_device_checked = False
                    self.post_message(VmCardUpdateRequest(self.internal_id))
                    self.update_button_layout()
                except Exception as e:
                    self.app.show_error_message(f"Error creating overlay: {e}")

            self.app.push_screen(InputModal("Enter name for new overlay volume:", default_name, restrict=r"[a-zA-Z0-9_-]*"), on_name_input)

        except Exception as e:
            self.app.show_error_message(f"Error preparing overlay creation: {e}")

    def _handle_discard_overlay(self, event: Button.Pressed) -> None:
        """Handles the discard overlay button press."""
        try:
            overlay_disks = get_overlay_disks(self.vm)

            if not overlay_disks:
                self.app.show_error_message("No overlay disks found.")
                return

            def proceed_with_discard(target_disk: str | None):
                if not target_disk:
                    return

                def on_confirm(confirmed: bool):
                    if confirmed:
                        try:
                            discard_overlay(self.vm, target_disk)
                            self.app.show_success_message(f"Overlay for [b]{target_disk}[/b] discarded and reverted to base image.")
                            self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                            self._boot_device_checked = False
                            self.post_message(VmCardUpdateRequest(self.internal_id))
                            self.update_button_layout()
                        except Exception as e:
                            self.app.show_error_message(f"Error discarding overlay: {e}")

                self.app.push_screen(
                    ConfirmationDialog(f"Are you sure you want to discard changes in '{target_disk}' and revert to its backing file? This action cannot be undone."),
                    on_confirm
                )

            if len(overlay_disks) == 1:
                proceed_with_discard(overlay_disks[0])
            else:
                self.app.push_screen(
                    SelectDiskModal(overlay_disks, "Select overlay disk to discard:"),
                    proceed_with_discard
                )

        except Exception as e:
            self.app.show_error_message(f"Error preparing discard overlay: {e}")


    def _handle_commit_disk(self, event: Button.Pressed) -> None:
        """Handles the commit disk changes button press."""
        # This works on running VM.
        try:
            disks = get_vm_disks(self.vm)
            # Filter for actual disks (exclude cdroms, etc)
            target_disk = None
            for d in disks:
                if d.get('path'):
                    target_disk = d['path']
                    break

            if not target_disk:
                self.app.show_error_message("No disks found to commit.")
                return

            def on_confirm(confirmed: bool):
                if confirmed:
                    progress_modal = ProgressModal(title=f"Committing changes for {self.name}...")
                    self.app.push_screen(progress_modal)

                    def do_commit():
                        try:
                            commit_disk_changes(self.vm, target_disk)
                            self.app.call_from_thread(self.app.show_success_message, "Disk changes committed successfully.")
                            self.app.call_from_thread(self.app.refresh_vm_list)
                            self.app.call_from_thread(self.update_button_layout)
                        except Exception as e:
                            self.app.call_from_thread(self.app.show_error_message, f"Error committing disk: {e}")
                        finally:
                            self.app.call_from_thread(progress_modal.dismiss)

                    self.app.worker_manager.run(do_commit, name=f"commit_{self.name}")

            self.app.push_screen(
                ConfirmationDialog(f"Are you sure you want to merge changes from '{target_disk}' into its backing file?"),
                on_confirm
            )

        except Exception as e:
            self.app.show_error_message(f"Error preparing commit: {e}")

    def _handle_shutdown_button(self, event: Button.Pressed) -> None:
        """Handles the shutdown button press."""
        logging.info(f"Attempting to gracefully shutdown VM: {self.name}")
        if self.status in (StatusText.RUNNING, StatusText.PAUSED):
            self.post_message(VmActionRequest(self.internal_id, VmAction.STOP))

    def _handle_stop_button(self, event: Button.Pressed) -> None:
        """Handles the stop button press."""
        logging.info(f"Attempting to stop VM: {self.name}")

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            #if self.vm.isActive():
            # maybe better to use cache status
            if self.status in (StatusText.RUNNING, StatusText.PAUSED):
                self.post_message(VmActionRequest(self.internal_id, VmAction.FORCE_OFF))

        message = f"{ErrorMessages.HARD_STOP_WARNING}\nAre you sure you want to stop '{self.name}'?"
        self.app.push_screen(ConfirmationDialog(message), on_confirm)

    def _handle_pause_button(self, event: Button.Pressed) -> None:
        """Handles the pause button press."""
        logging.info(f"Attempting to pause VM: {self.name}")
        if self.vm.isActive():
            self.post_message(VmActionRequest(self.internal_id, VmAction.PAUSE))

    def _handle_resume_button(self, event: Button.Pressed) -> None:
        """Handles the resume button press."""
        logging.info(f"Attempting to resume VM: {self.name}")
        self.post_message(VmActionRequest(self.internal_id, VmAction.RESUME))

    def _handle_xml_button(self, event: Button.Pressed) -> None:
        """Handles the xml button press."""
        try:
            vm_cache = self.app.vm_service._vm_data_cache.get(self.internal_id, {})
            cached_xml = vm_cache.get('xml')

            try:
                original_xml = self.vm.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
            except libvirt.libvirtError:
                original_xml = self.vm.XMLDesc(0)
            is_stopped = self.status == StatusText.STOPPED

            def handle_xml_modal_result(modified_xml: str | None):
                if modified_xml and is_stopped:
                    if original_xml.strip() != modified_xml.strip():
                        try:
                            conn = self.vm.connect()
                            conn.defineXML(modified_xml)
                            self.app.show_success_message(f"VM [b]{self.name}[/b] configuration updated successfully.")
                            logging.info(f"Successfully updated XML for VM: {self.name}")
                            self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                            self._boot_device_checked = False
                            self.app.refresh_vm_list()
                        except libvirt.libvirtError as e:
                            error_msg = f"Invalid XML for '{self.name}': {e}. Your changes have been discarded."
                            self.app.show_error_message(error_msg)
                            logging.error(error_msg)
                    else:
                        self.app.show_success_message("No changes made to the XML configuration.")

            self.app.push_screen(
                XMLDisplayModal(original_xml, read_only=not is_stopped),
                handle_xml_modal_result
            )
        except libvirt.libvirtError as e:
            self.app.show_error_message(f"Error getting XML for VM [b]{self.name}[/b]: {e}")
        except Exception as e:
            self.app.show_error_message(f"An unexpected error occurred: {e}")
            logging.error(f"Unexpected error handling XML button: {traceback.format_exc()}")

    def _handle_connect_button(self, event: Button.Pressed) -> None:
        """Handles the connect button press by running the remove virt viewer in a worker."""
        logging.info(f"Attempting to connect to VM: {self.name}")
        if not hasattr(self, 'conn') or not self.conn:
            self.app.show_error_message("Connection info not available for this VM.")
            return

        def do_connect() -> None:
            try:
                # Use cached values to avoid libvirt calls
                uri = self.app.vm_service.get_uri_for_connection(self.conn)
                if not uri:
                    uri = self.conn.getURI()

                _, domain_name = self.app.vm_service.get_vm_identity(self.vm, self.conn)
                command = [ self.app.r_viewer, "--connect", uri, "--domain-name", domain_name]
                logging.info(f"Spawning detached {self.app.r_viewer}: {' '.join(command)}")

                #env = os.environ.copy()
                #env['GDK_BACKEND'] = 'x11'
                try:
                    proc = subprocess.Popen(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid,
                        #env=env
                    )
                    logging.info(f"{self.app.r_viewer} started with PID {proc.pid} for {domain_name}")
                    self.app.show_quick_message(f"Remote viewer {self.app.r_viewer} started for {domain_name}")
                except Exception as e:
                    logging.error(f"Failed to spawn {self.app.r_viewer} for {domain_name}: {e}")
                    self.app.call_from_thread(
                        self.app.show_error_message,
                        f"{self.app.r_viewer} failed to start for {domain_name}: {e}"
                    )
                    return

            except FileNotFoundError:
                self.app.call_from_thread(
                    self.app.show_error_message,
                    ErrorMessages.R_VIEWER_NOT_FOUND
                )
            except libvirt.libvirtError as e:
                self.app.call_from_thread(
                    self.app.show_error_message,
                    f"Error getting VM details for [b]{self.name}[/b]: {e}"
                )
            except Exception as e:
                logging.error(f"An unexpected error occurred during connect: {e}", exc_info=True)
                self.app.call_from_thread(
                    self.app.show_error_message,
                    "An unexpected error occurred while trying to connect."
                )

        self.app.worker_manager.run(do_connect, name=f"r_viewer_{self.name}")

    def _handle_web_console_button(self, event: Button.Pressed) -> None:
        """Handles the web console button press by opening a config dialog."""
        worker = partial(self.app.webconsole_manager.start_console, self.vm, self.conn)

        try:
            uuid = self.internal_id
            if self.app.webconsole_manager.is_running(uuid):
                self.app.worker_manager.run(
                   worker, name=f"show_console_{self.vm.name()}"
                )
                return
        except Exception as e:
            self.app.show_error_message(f"Error checking web console status for [b]{self.name}[/b]: {e}")
            return

        #is_remote = self.app.webconsole_manager.is_remote_connection(self.conn.getURI())
        # Use cached URI to avoid libvirt call
        uri = self.app.vm_service.get_uri_for_connection(self.conn)
        if not uri:
            uri = self.conn.getURI()
        is_remote = self.app.webconsole_manager.is_remote_connection(uri)


        if is_remote:
            def handle_dialog_result(should_start: bool) -> None:
                if should_start:
                    self.app.worker_manager.run(
                        worker, name=f"start_console_{self.vm.name()}"
                    )

            self.app.push_screen(
                WebConsoleConfigDialog(is_remote=is_remote),
                handle_dialog_result
            )
        else:
            self.app.worker_manager.run(worker, name=f"start_console_{self.vm.name()}")

    def _handle_snapshot_take_button(self, event: Button.Pressed) -> None:
        """Handles the snapshot take button press."""
        logging.info(f"Attempting to take snapshot for VM: {self.name}")

        def handle_snapshot_result(result: dict | None) -> None:
            # Always refresh tab title when modal closes, even if cancelled
            self._refresh_snapshot_tab_async()
            if result:
                name = result["name"]
                description = result["description"]
                quiesce = result.get("quiesce", False)
                try:
                    create_vm_snapshot(self.vm, name, description, quiesce=quiesce)
                    self.app.vm_service.invalidate_vm_cache(self.internal_id)
                    self.app.vm_service.invalidate_domain_cache() # Force refresh of domain objects
                    self.app.set_timer(0.1, self._refresh_snapshot_tab_async)
                    self.app.show_success_message(f"Snapshot [b]{name}[/b] created successfully.")
                except Exception as e:
                    self.app.show_error_message(f"Snapshot error for [b]{self.name}[/b]: {e}")

        self.app.push_screen(SnapshotNameDialog(self.vm), handle_snapshot_result)

    def _handle_snapshot_restore_button(self, event: Button.Pressed) -> None:
        """Handles the snapshot restore button press."""
        logging.info(f"Attempting to restore snapshot for VM: {self.name}")
        snapshots_info = get_vm_snapshots(self.vm)
        if not snapshots_info:
            self.app.show_error_message("No snapshots to restore.")
            return

        def restore_snapshot(snapshot_name: str | None) -> None:
            # Always refresh tab title when modal closes
            self._refresh_snapshot_tab_async()
            if snapshot_name:
                try:
                    restore_vm_snapshot(self.vm, snapshot_name)
                    self.app.vm_service.invalidate_vm_cache(self.internal_id)
                    self.app.vm_service.invalidate_domain_cache() # Force refresh of domain objects
                    self._boot_device_checked = False
                    #self.app.set_timer(0.5, self._refresh_snapshot_tab_async)
                    self.app.show_success_message(f"Restored to snapshot [b]{snapshot_name}[/b] successfully.")
                    logging.info(f"Successfully restored snapshot [b]{snapshot_name}[/b] for VM: {self.name}")
                except Exception as e:
                    self.app.show_error_message(f"Error on VM [b]{self.name}[/b] during 'snapshot restore': {e}")

        self.app.push_screen(SelectSnapshotDialog(snapshots_info, "Select snapshot to restore"), restore_snapshot)

    def _handle_snapshot_delete_button(self, event: Button.Pressed) -> None:
        """Handles the snapshot delete button press."""
        logging.info(f"Attempting to delete snapshot for VM: {self.name}")
        snapshots_info = get_vm_snapshots(self.vm)
        if not snapshots_info:
            self.app.show_error_message("No snapshots to delete.")
            return

        def delete_snapshot(snapshot_name: str | None) -> None:
            # Refresh again after confirmation dialog closes
            self._refresh_snapshot_tab_async()
            if snapshot_name:
                def on_confirm(confirmed: bool) -> None:
                    if confirmed:
                        try:
                            delete_vm_snapshot(self.vm, snapshot_name)
                            self.app.show_success_message(f"Snapshot [b]{snapshot_name}[/b] deleted successfully.")
                            self.app.vm_service.invalidate_vm_cache(self.internal_id)
                            self.app.vm_service.invalidate_domain_cache() # Force refresh of domain objects
                            self.app.set_timer(0.1, self._refresh_snapshot_tab_async)
                            logging.info(f"Successfully deleted snapshot '{snapshot_name}' for VM: {self.name}")
                        except Exception as e:
                            self.app.show_error_message(f"Error on VM [b]{self.name}[/b] during 'snapshot delete': {e}")

                self.app.push_screen(
                    ConfirmationDialog(DialogMessages.DELETE_SNAPSHOT_CONFIRMATION.format(name=snapshot_name)), on_confirm
                )

        self.app.push_screen(SelectSnapshotDialog(snapshots_info, "Select snapshot to delete"), delete_snapshot)

    def _refresh_snapshot_tab_async(self) -> None:
        """Refreshes the snapshot tab title asynchronously in a worker."""
        def fetch_and_update():
            try:
                if not self.vm:
                    return

                # Fetch current snapshot count and summary
                snapshot_summary = {'count': 0, 'latest': None}
                try:
                    # Optimization: only fetch full details if there are snapshots
                    if self.vm.snapshotNum(0) > 0:
                        snapshots = get_vm_snapshots(self.vm)
                        if snapshots:
                            snapshot_summary['count'] = len(snapshots)
                            latest = snapshots[0]
                            snapshot_summary['latest'] = {
                                'name': latest['name'],
                                'time': latest['creation_time']
                            }
                except libvirt.libvirtError as e:
                    if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                        logging.info(f"Domain no longer exists for {self.name}")
                        return
                    else:
                        logging.warning(f"Could not get snapshot details for {self.name}: {e}")
                        return

                snapshot_count = snapshot_summary.get('count', 0)

                # Update UI on main thread
                def update_ui():
                    if self.is_mounted:
                        self.update_snapshot_tab_title(snapshot_count)

                        # Update Tooltip on TabPane
                        tabbed_content = self.ui.get("tabbed_content")
                        if tabbed_content:
                            try:
                                pane = tabbed_content.get_tab("snapshot-tab")
                                if snapshot_count > 0:
                                    latest = snapshot_summary.get('latest')
                                    info = f"Latest: {latest['name']} ({latest['time']})" if latest else "Unknown"
                                    pane.tooltip = f"{info}\nTotal: {snapshot_count}"
                                else:
                                    pane.tooltip = "No Snapshots created"
                            except Exception:
                                pass

                        # Also update button visibility
                        if self.ui.get(ButtonIds.RENAME_BUTTON):
                            has_snapshots = snapshot_count > 0
                            is_running = self.status == StatusText.RUNNING
                            is_loading = self.status == StatusText.LOADING
                            self.ui[ButtonIds.SNAPSHOT_RESTORE].display = has_snapshots and not is_running and not is_loading
                            self.ui[ButtonIds.SNAPSHOT_DELETE].display = has_snapshots

                self.app.call_from_thread(update_ui)

            except Exception as e:
                logging.error(f"Error refreshing snapshot tab for {self.name}: {e}")

        # Run in worker to avoid blocking UI
        self.app.worker_manager.run(
            fetch_and_update,
            name=f"refresh_snapshot_tab_{self.internal_id}",
            exclusive=True
        )


    def _handle_delete_button(self, event: Button.Pressed) -> None:
        """Handles the delete button press."""
        logging.info(f"Attempting to delete VM: {self.name}")

        # Collapse the actions collapsible if it's open
        collapsible = self.ui.get("collapsible")
        if collapsible and not collapsible.collapsed:
            collapsible.collapsed = True

        def on_confirm(result: tuple[bool, bool]) -> None:
            confirmed, delete_storage = result
            if not confirmed:
                return
            self.post_message(VmActionRequest(self.internal_id, VmAction.DELETE, delete_storage=delete_storage))

        self.app.push_screen(
            DeleteVMConfirmationDialog(self.name), on_confirm
        )

    def _handle_clone_button(self, event: Button.Pressed) -> None:
        """Handles the clone button press."""
        app = self.app

        def handle_clone_results(result: dict | None) -> None:
            if not result:
                return

            base_name = result["base_name"]
            count = result["count"]
            suffix = result["suffix"]
            clone_storage = result.get("clone_storage", True)

            progress_modal = ProgressModal(title=f"Cloning {self.name}...")
            app.push_screen(progress_modal)

            def log_callback(message: str):
                app.call_from_thread(progress_modal.add_log, message)

            def do_clone():
                # Stop stats worker to avoid conflicts during heavy I/O
                def stop_stats_workers():
                    if self.timer:
                        self.timer.stop()
                        self.timer = None
                    self.app.worker_manager.cancel(f"update_stats_{self.internal_id}")
                    self.app.worker_manager.cancel(f"actions_state_{self.internal_id}")
                
                app.call_from_thread(stop_stats_workers)

                try:
                    existing_vm_names = set()
                    try:
                        # Use self.conn (which is the connection for this VM)
                        # We use listAllDomains() to get all VMs, to check for name collisions.
                        # This covers active and inactive VMs.
                        all_domains = self.conn.listAllDomains(0)
                        for domain in all_domains:
                            _, name = self.app.vm_service.get_vm_identity(domain, self.conn)
                            existing_vm_names.add(name)

                    except libvirt.libvirtError as e:
                        log_callback(f"ERROR: Error getting existing VM names: {e}")
                        app.call_from_thread(app.show_error_message, f"Error getting existing VM names: {e}")
                        app.call_from_thread(progress_modal.dismiss)
                        return

                    proposed_names = []
                    for i in range(1, count + 1):
                        new_name = f"{base_name}{suffix}{i}" if count > 1 else base_name
                        proposed_names.append(new_name)
                    log_callback(f"INFO: Proposed Name(s): {proposed_names}")

                    conflicting_names = [name for name in proposed_names if name in existing_vm_names]
                    if conflicting_names:
                        msg = f"The following VM names already exist: {', '.join(conflicting_names)}. Aborting cloning."
                        log_callback(f"ERROR: {msg}")
                        app.call_from_thread(app.show_error_message, msg)
                        app.call_from_thread(progress_modal.dismiss)
                        return
                    else:
                        log_callback("INFO: No Conflicting Name")
                        storage_msg = "with storage cloning" if clone_storage else "without storage cloning (linked clone)"
                        log_callback(f"INFO: No Conflicting Name - proceeding {storage_msg}")

                    success_clones, failed_clones = [], []
                    app.call_from_thread(lambda: progress_modal.query_one("#progress-bar").update(total=count))

                    for i in range(1, count + 1):
                        new_name = f"{base_name}{suffix}{i}" if count > 1 else base_name
                        try:
                            log_callback(f"Cloning '{self.name}' to '{new_name}'...")
                            clone_vm(self.vm, new_name, clone_storage=clone_storage, log_callback=log_callback)
                            success_clones.append(new_name)
                            log_callback(f"Successfully cloned VM '{self.name}' to '{new_name}'")
                        except Exception as e:
                            failed_clones.append(new_name)
                            log_callback(f"ERROR: Error cloning VM {self.name} to {new_name}: {e}")
                        finally:
                            app.call_from_thread(lambda: progress_modal.query_one("#progress-bar").advance(1))

                    if success_clones:
                        msg = f"Successfully cloned to: {', '.join(success_clones)}"
                        app.call_from_thread(app.show_success_message, msg)
                        log_callback(msg)
                    if failed_clones:
                        msg = f"Failed to clone to: {', '.join(failed_clones)}"
                        app.call_from_thread(app.show_error_message, msg)
                        log_callback(f"ERROR: {msg}")

                    if success_clones:
                        #app.call_from_thread(app.vm_service.invalidate_domain_cache)
                        app.call_from_thread(app.refresh_vm_list)
                    app.call_from_thread(progress_modal.dismiss)
                
                finally:
                    # Restart stats worker
                    app.call_from_thread(self.update_stats)

            app.worker_manager.run(do_clone, name=f"clone_{self.name}")

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.app.push_screen(AdvancedCloneDialog(), handle_clone_results)

        self.app.push_screen(ConfirmationDialog(DialogMessages.EXPERIMENTAL), on_confirm)


    def _handle_rename_button(self, event: Button.Pressed) -> None:
        """Handles the rename button press."""
        logging.info(f"Attempting to rename VM: {self.name}")

        def handle_rename(new_name_raw: str | None) -> None:
            if not new_name_raw:
                return

            try:
                new_name, was_modified = _sanitize_input(new_name_raw)
            except ValueError as e:
                self.app.show_error_message(str(e))
                return

            if was_modified:
                self.app.show_success_message(f"Input sanitized: [b]{new_name_raw}[/b] changed to [b]{new_name}[/b]")

            if not new_name:
                self.app.show_error_message("VM name cannot be empty after sanitization.")
                return
            if new_name == self.name:
                self.app.show_success_message("New VM name is the same as the old name. No rename performed.")
                return

            def do_rename(delete_snapshots=False):
                try:
                    rename_vm(self.vm, new_name, delete_snapshots=delete_snapshots)
                    msg = f"VM '{self.name}' renamed to '{new_name}' successfully."
                    if delete_snapshots:
                        msg = f"Snapshots deleted and VM '{self.name}' renamed to '{new_name}' successfully."
                    self.app.show_success_message(msg)
                    self.app.vm_service.invalidate_domain_cache()
                    self._boot_device_checked = False
                    self.app.refresh_vm_list()
                    logging.info(f"Successfully renamed VM '{self.name}' to '{new_name}'")
                except Exception as e:
                    self.app.show_error_message(f"Error renaming VM [b]{self.name}[/b]: {e}")

            num_snapshots = self.vm.snapshotNum(0)
            if num_snapshots > 0:
                def on_confirm_delete(confirmed: bool) -> None:
                    self._refresh_snapshot_tab_async()
                    if confirmed:
                        do_rename(delete_snapshots=True)
                        self.app.set_timer(0.1, self._refresh_snapshot_tab_async)
                    else:
                        self.app.show_success_message("VM rename cancelled.")

                self.app.push_screen(
                    ConfirmationDialog(DialogMessages.DELETE_SNAPSHOTS_AND_RENAME.format(count=num_snapshots)),
                    on_confirm_delete
                )
            else:
                do_rename()

        self.app.push_screen(RenameVMDialog(current_name=self.name), handle_rename)

    def _handle_configure_button(self, event: Button.Pressed) -> None:
        """Handles the configure button press."""
        try:
            self._boot_device_checked = False

            uuid = self.internal_id
            vm_name = self.name
            active_uris = list(self.app.active_uris)

            # Capture variables for thread safety
            vm_obj = self.vm
            conn_obj = self.conn
            cached_ips = list(self.ip_addresses) if self.ip_addresses else None

            loading_modal = LoadingModal()
            self.app.push_screen(loading_modal)

            def get_details_worker():
                try:
                    result = self.app.vm_service.get_vm_details(
                        active_uris, 
                        uuid, 
                        domain=vm_obj, 
                        conn=conn_obj, 
                        cached_ips=cached_ips
                    )

                    def show_details():
                        loading_modal.dismiss()
                        if not result:
                            self.app.show_error_message(f"VM [b]{vm_name}[/b] with internal ID [b]{uuid}[/b] not found on any active server.")
                            return

                        vm_info, domain, conn_for_domain = result

                        def on_detail_modal_dismissed(res):
                            self.post_message(VmCardUpdateRequest(self.internal_id))
                            self._perform_tooltip_update()

                        self.app.push_screen(
                            VMDetailModal(vm_name, vm_info, domain, conn_for_domain, self.app.vm_service.invalidate_vm_state_cache),
                            on_detail_modal_dismissed
                        )

                    self.app.call_from_thread(show_details)

                except Exception as e:
                    def show_error():
                        loading_modal.dismiss()
                        self.app.show_error_message(f"Error getting details for [b]{vm_name}[/b]: {e}")
                    self.app.call_from_thread(show_error)

            self.app.worker_manager.run(get_details_worker, name=f"get_details_{uuid}")

        except Exception as e:
            self.app.show_error_message(f"Error getting ID for [b]{self.name}[/b]: {e}")

    def _handle_migration_button(self, event: Button.Pressed) -> None:
        """Handles the migration button press."""
        if len(self.app.active_uris) < 2:
            self.app.show_error_message("Please select at least two servers in 'Select Servers' to enable migration.")
            return

        selected_vm_uuids = self.app.selected_vm_uuids
        selected_vms = []
        if selected_vm_uuids:
            for uuid in selected_vm_uuids:
                #Use cached domain lookup instead of iterating all URIs
                with self.app.vm_service._cache_lock:
                    domain = self.app.vm_service._domain_cache.get(uuid)
                    
                if domain:
                    try:
                        # Verify domain is still valid
                        domain.info()
                        selected_vms.append(domain)
                        found_domain = True
                    except libvirt.libvirtError:
                        found_domain = False
                else:
                    found_domain = False
                if not found_domain:
                    self.app.show_error_message(f"Selected VM with ID [b]{uuid}[/b] not found on any active server.")

        if not selected_vms:
            selected_vms = [self.vm]

        logging.info(f"Migration initiated for VMs: {[vm.name() for vm in selected_vms]}")

        #source_conns = {vm.connect().getURI() for vm in selected_vms}
        source_conns = set()
        for vm in selected_vms:
            conn = vm.connect()
            uri = self.app.vm_service.get_uri_for_connection(conn)
            if not uri:
                uri = conn.getURI()
            source_conns.add(uri)

        if len(source_conns) > 1:
            self.app.show_error_message("Cannot migrate VMs from different source hosts at the same time.")
            return

        #active_vms = [vm for vm in selected_vms if vm.isActive()]
        # Check status from cache instead of isActive()
        # We can infer from the card's status which VMs are active
        active_vms = []
        for vm in selected_vms:
            try:
                state_tuple = self.app.vm_service._get_domain_state(vm)
                if state_tuple:
                    state, _ = state_tuple
                    if state in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                        active_vms.append(vm)
            except:
                # Fallback to isActive() if cache lookup fails
                if vm.isActive():
                    active_vms.append(vm)

        is_live = len(active_vms) > 0
        if is_live and len(active_vms) < len(selected_vms):
            self.app.show_error_message("Cannot migrate running/paused and stopped VMs at the same time.")
            return

        active_uris = self.app.vm_service.get_all_uris()
        all_connections = {uri: self.app.vm_service.get_connection(uri) for uri in active_uris if self.app.vm_service.get_connection(uri)}

        #source_uri = selected_vms[0].connect().getURI()
        # Use cached URI
        source_conn = selected_vms[0].connect()
        source_uri = self.app.vm_service.get_uri_for_connection(source_conn)
        if not source_uri:
            source_uri = source_conn.getURI()

        if source_uri == "qemu:///system":
            self.app.show_error_message(
                ErrorMessages.MIGRATION_LOCALHOST_NOT_SUPPORTED
            )
            return

        dest_uris = [uri for uri in active_uris if uri != source_uri]
        if not dest_uris:
            self.app.show_error_message(ErrorMessages.NO_DESTINATION_SERVERS)
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.app.push_screen(MigrationModal(vms=selected_vms, is_live=is_live, connections=all_connections))

        self.app.push_screen(ConfirmationDialog(DialogMessages.EXPERIMENTAL), on_confirm)

    @on(Checkbox.Changed, "#vm-select-checkbox")
    def on_vm_select_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handles when the VM selection checkbox is changed."""
        self.is_selected = event.value
        self.post_message(VMSelectionChanged(vm_uuid=self.raw_uuid, is_selected=event.value))

    @on(Click, "#vmname")
    def on_click_vmname(self) -> None:
        """Handle clicks on the VM name part of the VM card."""
        self.post_message(VMNameClicked(vm_name=self.name, vm_uuid=self.raw_uuid))

