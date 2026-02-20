"""
VMcard Interface
"""

import datetime
import logging
import os
import subprocess
import threading
import time
import traceback
from functools import partial
from urllib.parse import urlparse

import libvirt
from rich.markdown import Markdown as RichMarkdown
from textual import on
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Click
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, Collapsible, Sparkline, Static, TabbedContent, TabPane

from .constants import (
    ButtonLabels,
    DialogMessages,
    ErrorMessages,
    ProgressMessages,
    SparklineLabels,
    StaticText,
    StatusText,
    SuccessMessages,
    TabTitles,
    VmAction,
    VMCardConstants,
    WarningMessages,
)
from .events import (
    VMActionButtonPressed,
    VmActionRequest,
    VmCardUpdateRequest,
    VMNameClicked,
    VMSelectionChanged,
)
from .modals.disk_pool_modals import SelectDiskModal
from .modals.howto_overlay_modal import HowToOverlayModal
from .modals.input_modals import InputModal, _sanitize_input
from .modals.migration_modals import MigrationModal
from .modals.utils_modals import ConfirmationDialog, LoadingModal, ProgressModal
from .modals.vmcard_dialog import (
    AdvancedCloneDialog,
    DeleteVMConfirmationDialog,
    RenameVMDialog,
    SelectSnapshotDialog,
    SnapshotNameDialog,
    WebConsoleConfigDialog,
)
from .modals.vmdetails_modals import VMDetailModal
from .modals.xml_modals import XMLDisplayModal
from .utils import (
    is_inside_tmux,
    extract_server_name_from_uri,
    generate_tooltip_markdown,
    remote_viewer_cmd,
    is_remote_connection,
)
from .vm_actions import (
    clone_vm,
    commit_disk_changes,
    create_external_overlay,
    create_vm_snapshot,
    delete_vm,
    delete_vm_snapshot,
    discard_overlay,
    hibernate_vm,
    rename_vm,
    restore_vm_snapshot,
)
from .vm_queries import (
    _get_domain_root,
    _parse_domain_xml,
    get_boot_info,
    get_overlay_disks,
    get_vm_cpu_details,
    get_vm_disks,
    get_vm_graphics_info,
    get_vm_network_ip,
    get_vm_snapshots,
    has_overlays,
)


class VMCardActions(Static):
    def compose(self):
        with TabbedContent(id="button-container"):
            with TabPane(TabTitles.MANAGE, id="manage-tab"):
                with Horizontal():
                    with Vertical():
                        yield Button(ButtonLabels.START, id="start", variant="success")
                        yield Button(ButtonLabels.SHUTDOWN, id="shutdown", variant="primary")
                        yield Button(ButtonLabels.FORCE_OFF, id="stop", variant="error")
                        yield Button(ButtonLabels.PAUSE, id="pause", variant="primary")
                        yield Button(ButtonLabels.RESUME, id="resume", variant="success")
                    with Vertical():
                        yield Button(ButtonLabels.WEB_CONSOLE, id="web_console", variant="default")
                        yield Button(ButtonLabels.CONNECT, id="connect", variant="default")
                        if is_inside_tmux():
                            yield Button(
                                ButtonLabels.TEXT_CONSOLE, id="tmux_console", variant="default"
                            )
            with TabPane(TabTitles.STATE_MANAGEMENT, id="snapshot-tab"):
                with Horizontal():
                    with Vertical():
                        yield Button(ButtonLabels.SNAPSHOT, id="snapshot_take", variant="primary")
                        yield Button(
                            ButtonLabels.RESTORE_SNAPSHOT, id="snapshot_restore", variant="primary"
                        )
                        yield Button(
                            ButtonLabels.DELETE_SNAPSHOT, id="snapshot_delete", variant="error"
                        )
                    with Vertical():
                        yield Button(ButtonLabels.HIBERNATE_VM, id="hibernate", variant="primary")
                        yield Button(
                            ButtonLabels.CREATE_OVERLAY, id="create_overlay", variant="primary"
                        )
                        yield Button(ButtonLabels.COMMIT_DISK, id="commit_disk", variant="error")
                        yield Button(
                            ButtonLabels.DISCARD_OVERLAY, id="discard_overlay", variant="error"
                        )
                        yield Button(
                            ButtonLabels.SNAP_OVERLAY_HELP,
                            id="snap_overlay_help",
                            variant="default",
                        )
            with TabPane(TabTitles.OTHER, id="special-tab"):
                with Horizontal():
                    with Vertical():
                        yield Button(
                            ButtonLabels.DELETE,
                            id="delete",
                            variant="error",
                            classes="delete-button",
                        )
                        yield Static(classes="button-separator")
                        yield Button(ButtonLabels.CLONE, id="clone", classes="clone-button")
                        yield Button(
                            ButtonLabels.MIGRATION,
                            id="migration",
                            variant="primary",
                            classes="migration-button",
                        )
                    with Vertical():
                        yield Button(
                            ButtonLabels.CONFIGURE, id="configure-button", variant="primary"
                        )
                        yield Button(ButtonLabels.VIEW_XML, id="xml")
                        yield Static(classes="button-separator")
                        yield Button(
                            ButtonLabels.RENAME,
                            id="rename-button",
                            variant="primary",
                            classes="rename-button",
                        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(VMActionButtonPressed(event.button.id))


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
    server_border_color = reactive(VMCardConstants.DEFAULT_BORDER_COLOR)
    is_selected = reactive(False)
    stats_view_mode = reactive("resources")  # "resources" or "io"
    internal_id = reactive("")
    compact_view = reactive(False)

    @property
    def raw_uuid(self) -> str:
        """Returns the raw UUID part of the internal_id."""
        return self.internal_id.split("@")[0]

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
        self._timer_lock = threading.Lock()
        self._last_click_time = 0

    def _get_uri(self) -> str:
        """Helper to get the URI for the current connection."""
        if not self.conn:
            return ""
        # Use cached URI lookup to avoid libvirt call
        uri = self.app.vm_service.get_uri_for_connection(self.conn)
        return uri or self.conn.getURI()

    def _get_vm_identity_info(self) -> tuple[str, str]:
        """Helper to get (raw_uuid, vm_name) using vm_service."""
        if not self.vm:
            return "", ""
        return self.app.vm_service.get_vm_identity(self.vm, self.conn)

    def _get_vm_display_name(self) -> str:
        """Returns the formatted VM name including server name if available."""
        if hasattr(self, "conn") and self.conn:
            uri = self._get_uri()
            server_display = extract_server_name_from_uri(uri)
            return f"{self.name} ({server_display})"
        return self.name

    def _get_snapshot_tab_title(self, num_snapshots: int = -1) -> str:
        """Get snapshot tab title. Pass num_snapshots to avoid blocking libvirt call."""
        if num_snapshots == -1:
            # If no count provided, don't fetch it here to avoid blocking.
            # For now, return default if we can't get it cheaply.
            return TabTitles.SNAP_OVER_UPDATE

        if self.vm:
            try:
                if num_snapshots <= 0:
                    return TabTitles.STATE_MANAGEMENT
                else:
                    return f"{TabTitles.STATE_MANAGEMENT}({num_snapshots})"
            except libvirt.libvirtError:
                pass  # Domain might be transient or invalid
        return TabTitles.STATE_MANAGEMENT

    def update_snapshot_tab_title(self, num_snapshots: int = -1) -> None:
        """Updates the snapshot tab title."""
        try:
            tabbed_content = self.query_one("#button-container", TabbedContent)
            tabbed_content.get_tab("snapshot-tab").update(
                self._get_snapshot_tab_title(num_snapshots)
            )
        except NoMatches:
            # Actions not mounted
            pass
        except Exception as e:
            logging.warning(f"Could not update snapshot tab title: {e}")

    def _update_webc_status(self) -> None:
        """Updates the web console status indicator and button."""
        webc_is_running = False
        if hasattr(self.app, "webconsole_manager") and self.vm:
            try:
                uuid = self.internal_id
                if uuid:  # ensure uuid is not empty
                    webc_is_running = self.app.webconsole_manager.is_running(uuid)
            except Exception as e:
                logging.warning(f"Error getting webconsole status for {self.internal_id}: {e}")

        # Update status indicator text
        new_indicator = " (WebC On)" if webc_is_running else ""
        if self.webc_status_indicator != new_indicator:
            self.webc_status_indicator = new_indicator

        # Update button label and style
        try:
            web_console_button = self.query_one("#web_console", Button)
            if webc_is_running:
                web_console_button.label = "Show Console"
                web_console_button.variant = "success"
            else:
                web_console_button.label = ButtonLabels.WEB_CONSOLE
                web_console_button.variant = "default"
        except NoMatches:
            pass

    def watch_webc_status_indicator(self, old_value: str, new_value: str) -> None:
        """Called when webc_status_indicator changes."""
        # Don't update if card is being removed or not mounted
        if not self.is_mounted:
            return
        if not self.ui:
            return
        status_widget = self.ui.get("status")
        if status_widget:
            status_text = f"{self.status}{new_value}"
            status_widget.update(status_text)

    def compose(self):
        self.ui["btn_quick_start"] = Button("▶", id="start", variant="success", classes="btn-small")
        self.ui["btn_quick_start"].tooltip = StaticText.START_VMS
        self.ui["btn_quick_view"] = Button("👁", id="connect", classes="btn-small")
        self.ui["btn_quick_view"].tooltip = StaticText.REMOTE_VIEWER
        self.ui["btn_quick_resume"] = Button(
            "⏯", id="resume", variant="success", classes="btn-small"
        )
        self.ui["btn_quick_resume"].tooltip = ButtonLabels.RESUME
        self.ui["btn_quick_stop"] = Button(
            "■", id="shutdown", variant="primary", classes="btn-small"
        )
        self.ui["btn_quick_stop"].tooltip = ButtonLabels.SHUTDOWN

        self.ui["checkbox"] = Checkbox(
            "",
            id="vm-select-checkbox",
            classes="vm-select-checkbox",
            value=self.is_selected,
            tooltip=StaticText.SELECT_VM,
        )
        self.ui["vmname"] = Static(self._get_vm_display_name(), id="vmname", classes="vmname")
        self.ui["status"] = Static(f"{self.status}{self.webc_status_indicator}", id="status")

        # Create all sparkline components
        self.ui["cpu_label"] = Static("", classes="sparkline-label")
        self.ui["cpu_sparkline"] = Sparkline([], id="cpu-sparkline")
        self.ui["cpu_sparkline_container"] = Horizontal(
            self.ui["cpu_label"],
            self.ui["cpu_sparkline"],
            id="cpu_sparkline_container",
            classes="sparkline-container resources-sparkline",
        )

        self.ui["mem_label"] = Static("", classes="sparkline-label")
        self.ui["mem_sparkline"] = Sparkline([], id="mem-sparkline")
        self.ui["mem_sparkline_container"] = Horizontal(
            self.ui["mem_label"],
            self.ui["mem_sparkline"],
            id="mem_sparkline_container",
            classes="sparkline-container resources-sparkline",
        )

        self.ui["disk_label"] = Static("", classes="sparkline-label")
        self.ui["disk_sparkline"] = Sparkline([], id="disk-sparkline")
        self.ui["disk_sparkline_container"] = Horizontal(
            self.ui["disk_label"],
            self.ui["disk_sparkline"],
            id="disk_sparkline_container",
            classes="sparkline-container io-sparkline",
        )

        self.ui["net_label"] = Static("", classes="sparkline-label")
        self.ui["net_sparkline"] = Sparkline([], id="net-sparkline")
        self.ui["net_sparkline_container"] = Horizontal(
            self.ui["net_label"],
            self.ui["net_sparkline"],
            id="net_sparkline_container",
            classes="sparkline-container io-sparkline",
        )

        # A single container for all sparklines that will handle clicks
        self.ui["sparklines_container"] = Vertical(
            self.ui["cpu_sparkline_container"],
            self.ui["mem_sparkline_container"],
            self.ui["disk_sparkline_container"],
            self.ui["net_sparkline_container"],
            id="sparklines-container-group",
        )

        self.ui["collapsible"] = Collapsible(title=StaticText.ACTIONS, id="actions-collapsible")

        with Vertical(id="info-container"):
            with Horizontal(id="vm-header-row"):
                yield self.ui["checkbox"]
                with Vertical():
                    yield self.ui["vmname"]
                    yield self.ui["status"]
                with Horizontal(classes="quick-actions"):
                    with Vertical():
                        yield self.ui["btn_quick_resume"]
                        yield self.ui["btn_quick_stop"]
                        yield self.ui["btn_quick_start"]
                        yield self.ui["btn_quick_view"]

            yield self.ui["sparklines_container"]
            yield self.ui["collapsible"]

    @on(Collapsible.Expanded, "#actions-collapsible")
    async def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
        if not self.query(VMCardActions):
            actions_view = VMCardActions()
            await self.ui["collapsible"].mount(actions_view)
            self.update_button_layout()

    @on(Collapsible.Collapsed, "#actions-collapsible")
    async def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:
        self._cleanup_actions()

    def _cleanup_actions(self):
        for child in self.query(VMCardActions):
            child.remove()

    def _is_remote_server(self) -> bool:
        """Checks if the VM is on a remote server."""
        if not self.conn:
            return False
        try:
            uri = self._get_uri()
            parsed = urlparse(uri)
            return (
                parsed.hostname not in (None, "localhost", "127.0.0.1")
                and parsed.scheme == "qemu+ssh"
            )
        except Exception:
            return False

    def _perform_tooltip_update(self) -> None:
        """Updates the tooltip for the VM name using Markdown."""
        # Don't update if card is being removed or not mounted
        if not self.is_mounted:
            return
        if not self.display or not self.ui or "vmname" not in self.ui:
            return

        uuid = self.internal_id
        if not uuid:
            return

        has_cached_xml = False
        with self.app.vm_service._cache_lock:
            vm_cache = self.app.vm_service._vm_data_cache.get(uuid, {})
            has_cached_xml = vm_cache.get("xml") is not None

        if self.compact_view and not has_cached_xml:
            self.ui["vmname"].tooltip = self._get_vm_display_name()
            return

        if self._is_remote_server() and not has_cached_xml:
            self.ui["vmname"].tooltip = None
            return
        # Use cached identity to avoid libvirt call
        try:
            if self.vm:
                raw_uuid, _ = self._get_vm_identity_info()
                uuid_display = raw_uuid.split("@")[0]  # Extract just the UUID part
            else:
                uuid_display = "Unknown"
        except Exception:
            uuid_display = "Unknown"

        hypervisor = "Unknown"
        if self.conn:
            uri = self._get_uri()
            hypervisor = extract_server_name_from_uri(uri)

        mem_display = f"{self.memory} MiB"
        if self.memory >= 1024:
            mem_display += f" ({self.memory / 1024:.2f} GiB)"

        ip_display = "N/A"
        if self.status == StatusText.RUNNING and self.ip_addresses:
            ips = []
            for iface in self.ip_addresses:
                # Guard against None or non-dict entries in ip_addresses list
                if iface and isinstance(iface, dict):
                    ips.extend(iface.get("ipv4", []))
            if ips:
                ip_display = ", ".join(ips)

        cpu_model_display = f" {self.cpu_model}" if self.cpu_model else ""

        tooltip_md = generate_tooltip_markdown(
            uuid=uuid_display,
            hypervisor=hypervisor,
            status=self.status,
            ip=ip_display,
            boot=self.boot_device or "N/A",
            cpu=self.cpu,
            cpu_model=cpu_model_display or "",
            memory=self.memory,
        )

        self.ui["vmname"].tooltip = RichMarkdown(tooltip_md)

    def on_mount(self) -> None:
        # Background is now set in CSS
        if self.is_selected:
            self.styles.border = (
                VMCardConstants.SELECTED_BORDER_TYPE,
                VMCardConstants.SELECTED_BORDER_COLOR,
            )
        else:
            self.styles.border = (VMCardConstants.DEFAULT_BORDER_TYPE, self.server_border_color)

        self.update_button_layout()
        self._update_status_styling()
        self._update_webc_status()
        self.watch_stats_view_mode(self.stats_view_mode, self.stats_view_mode)  # Initial setup
        self._perform_tooltip_update()

        uuid = self.internal_id
        if uuid and uuid in self.app.sparkline_data:
            self.update_sparkline_data()

        self.update_stats()
        self._apply_compact_view_styles(self.compact_view)

    def watch_stats_view_mode(self, old_mode: str, new_mode: str) -> None:
        """Update sparklines when view mode changes."""
        if not self.display or not self.ui or self.compact_view:
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
        if uuid and hasattr(self.app, "sparkline_data") and uuid in self.app.sparkline_data:
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

                with self.app.vm_service._sparkline_lock:
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
                with self.app.vm_service._sparkline_lock:
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

    def _apply_compact_view_styles(self, value: bool) -> None:
        """Apply styles for compact view."""
        if not self.ui:
            return

        sparklines = self.ui.get("sparklines_container")
        collapsible = self.ui.get("collapsible")
        vmname = self.ui.get("vmname")
        vmstatus = self.ui.get("status")
        checkbox = self.ui.get("checkbox")

        if value:
            if sparklines and sparklines.is_mounted:
                sparklines.display = False
            if collapsible and collapsible.is_mounted:
                collapsible.collapsed = True
                collapsible.remove()
            # if checkbox and checkbox.is_mounted:
            #    checkbox.display = False
        else:
            try:
                info_container = self.query_one("#info-container")
                if collapsible:
                    # Check if collapsible is already a child of info_container to avoid double mounting
                    if collapsible not in info_container.children:
                        info_container.mount(collapsible)
                if sparklines:
                    sparklines.display = True
                # if checkbox:
                #    checkbox.display = True

            except NoMatches:
                # This can happen if the card is not fully initialized or structures changed
                logging.warning(
                    f"Could not find #info-container on VMCard {self.name} when switching to detailed view."
                )
            except Exception as e:
                # Catch-all for potential mounting errors (e.g. already mounted elsewhere?)
                logging.warning(f"Error restoring collapsible in detailed view: {e}")

            # Ensure sparklines visibility is correct
            self.watch_stats_view_mode(self.stats_view_mode, self.stats_view_mode)

        # Change height based on compact_view
        if value:  # Compact view
            self.styles.height = 4
            self.styles.width = 20
            if vmname:
                vmname.styles.content_align = ("left", "middle")
            if vmstatus:
                vmstatus.styles.content_align = ("left", "middle")
            if checkbox:
                checkbox.styles.width = "2"
        else:  # Detailed view
            self.styles.height = 14
            self.styles.width = 41
            if vmname:
                vmname.styles.content_align = ("center", "middle")
            if vmstatus:
                vmstatus.styles.content_align = ("center", "middle")
            if checkbox:
                checkbox.styles.width = "5"

    def watch_compact_view(self, value: bool) -> None:
        """Called when compact_view changes."""
        self._apply_compact_view_styles(value)
        self._perform_tooltip_update()

    def watch_status(self, old_value: str, new_value: str) -> None:
        """Called when status changes."""
        # Don't update if card is being removed or not mounted
        if not self.is_mounted:
            return
        if not self.ui:
            return
        self._update_status_styling()
        self.watch_stats_view_mode(
            self.stats_view_mode, self.stats_view_mode
        )  # Re-evaluate sparkline visibility
        self.update_button_layout()
        self._perform_tooltip_update()

        status_widget = self.ui.get("status")
        if status_widget:
            status_widget.update(f"{new_value}{self.webc_status_indicator}")

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
        if self.is_selected:
            self.styles.border = (
                VMCardConstants.SELECTED_BORDER_TYPE,
                VMCardConstants.SELECTED_BORDER_COLOR,
            )
        else:
            self.styles.border = (VMCardConstants.DEFAULT_BORDER_TYPE, new_color)

    def on_click(self, event: Click) -> None:
        """Handle click events on the card."""
        if event.button == 3:
            self.is_selected = not self.is_selected
            self.post_message(
                VMSelectionChanged(vm_uuid=self.raw_uuid, is_selected=self.is_selected)
            )
            event.stop()

    def on_unmount(self) -> None:
        """Stop the timer and cancel any running stat workers when the widget is removed."""
        with self._timer_lock:
            if self.timer:
                self.timer.stop()
                self.timer = None
        # Cancel Workers
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
        with self._timer_lock:
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
        if not self.is_mounted:
            return
        if hasattr(self, "ui") and "checkbox" in self.ui:
            checkbox = self.ui.get("checkbox")
            try:
                checkbox.value = new_value
            except Exception:
                pass

        if new_value:
            self.styles.border = (
                VMCardConstants.SELECTED_BORDER_TYPE,
                VMCardConstants.SELECTED_BORDER_COLOR,
            )
        else:
            self.styles.border = (VMCardConstants.DEFAULT_BORDER_TYPE, self.server_border_color)

    def update_stats(self) -> None:
        """Schedules a worker to update statistics for the VM."""
        if not self.display:
            return
        if not self.vm:
            return

        # Capture uuid early to use consistently throughout
        uuid = self.internal_id
        if not uuid:
            return

        # Use lock to ensure atomic timer management and prevent race conditions
        # where multiple concurrent calls could create duplicate timers
        with self._timer_lock:
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
                interval = self.app.config.get("STATS_INTERVAL", 5)
                self.timer = self.set_timer(interval, self.update_stats)

            # Capture necessary state for the worker while still under lock
            # to ensure consistent snapshot of VM state
            worker_context = {
                "uuid": uuid,
                "vm": self.vm,
                "conn": self.conn,
                "current_status": self.status,
                "boot_device": self.boot_device,
                "cpu_model": self.cpu_model,
                "graphics_type": self.graphics_type,
                "boot_device_checked": getattr(self, "_boot_device_checked", False),
                "is_remote": self._is_remote_server(),
            }

        # Run worker outside the lock to avoid holding it during potentially
        # blocking operations. The worker_manager handles its own concurrency.
        self.app.worker_manager.run(
            partial(self._stats_data_fetch_worker, worker_context), name=f"update_stats_{uuid}"
        )

    def _stats_data_fetch_worker(self, ctx: dict) -> None:
        """Worker function to fetch stats and details. Executed in a thread."""
        uuid = ctx["uuid"]
        vm = ctx["vm"]

        try:
            # logging.debug(f"Starting update_stats worker for {self.name} (ID: {uuid})")
            stats = self.app.vm_service.get_vm_runtime_stats(vm)
            # logging.debug(f"Stats received for {self.name}: {stats}")

            # Prepare result dictionary
            result = {
                "uuid": uuid,
                "stats": stats,
                "ips": [],
                "boot_device": ctx["boot_device"],
                "cpu_model": ctx["cpu_model"],
                "graphics_type": ctx["graphics_type"],
                "boot_device_checked": ctx["boot_device_checked"],
                "error": None,
            }

            # Update info from cache if XML has been fetched (e.g. via Configure)
            vm_cache = self.app.vm_service._vm_data_cache.get(uuid, {})
            xml_content = vm_cache.get("xml")

            # Parse XML for static details if not yet checked
            if not result["boot_device_checked"]:
                root = None
                if xml_content:
                    root = _parse_domain_xml(xml_content)
                elif not ctx["is_remote"]:
                    # Fallback for local VMs: fetch XML once
                    try:
                        _, root = _get_domain_root(vm)
                    except Exception:
                        pass

                if root is not None:
                    boot_info = get_boot_info(ctx["conn"], root)
                    if boot_info["order"]:
                        result["boot_device"] = boot_info["order"][0]
                    result["cpu_model"] = get_vm_cpu_details(root) or ""
                    graphics_info = get_vm_graphics_info(root)
                    result["graphics_type"] = graphics_info.get("type")
                    result["boot_device_checked"] = True

            # If stats are missing (VM likely undefined or issue), return early
            if not stats:
                self.app.call_from_thread(self._apply_stats_update, result)
                return

            # Fetch IPs if running
            if stats.get("status") == StatusText.RUNNING:
                result["ips"] = get_vm_network_ip(vm)

            self.app.call_from_thread(self._apply_stats_update, result)

        except libvirt.libvirtError as e:
            error_code = e.get_error_code()
            result = {"uuid": uuid, "error": e, "error_code": error_code}
            self.app.call_from_thread(self._handle_stats_error, result)
        except Exception as e:
            if e.__class__.__name__ == "NoActiveAppError":
                return
            logging.error(f"Unexpected error in update_stats worker for {uuid}: {e}", exc_info=True)

    def _handle_stats_error(self, result: dict) -> None:
        """Handles errors returned from the stats worker."""
        error = result["error"]
        error_code = result.get("error_code")

        if error_code == libvirt.VIR_ERR_NO_DOMAIN:
            with self._timer_lock:
                if self.timer:
                    self.timer.stop()
            self.app.refresh_vm_list()
        elif error_code in [
            libvirt.VIR_ERR_SYSTEM_ERROR,
            libvirt.VIR_ERR_RPC,
            libvirt.VIR_ERR_NO_CONNECT,
            libvirt.VIR_ERR_INVALID_CONN,
        ]:
            logging.warning(f"Connection error for {self.name}: {error}. Triggering refresh.")
            self.app.refresh_vm_list(force=True)
        else:
            logging.warning(f"Libvirt error during stat update for {self.name}: {error}")

    def _apply_stats_update(self, result: dict) -> None:
        """Updates UI with fetched stats. Executed on main thread."""
        if not self.is_mounted:
            return

        stats = result["stats"]

        # Handle case where stats are missing (e.g. VM stopped/undefined during fetch)
        if not stats:
            if self.status != StatusText.STOPPED:
                self.status = StatusText.STOPPED
                self.ip_addresses = []
                self.boot_device = result["boot_device"]
                self.cpu_model = result["cpu_model"]
                self.graphics_type = result["graphics_type"]
            return

        # Update cached flag
        if result["boot_device_checked"]:
            self._boot_device_checked = True

        # Update Reactive Properties
        if self.status != stats["status"]:
            self.status = stats["status"]

        self.ip_addresses = result["ips"]
        self.boot_device = result["boot_device"]
        self.cpu_model = result["cpu_model"]
        self.graphics_type = result["graphics_type"]

        self.latest_disk_read = stats.get("disk_read_kbps", 0)
        self.latest_disk_write = stats.get("disk_write_kbps", 0)
        self.latest_net_rx = stats.get("net_rx_kbps", 0)
        self.latest_net_tx = stats.get("net_tx_kbps", 0)

        # Update web console status
        self._update_webc_status()

        # Update Sparkline Data Global Storage
        uuid = result["uuid"]
        if hasattr(self.app, "sparkline_data") and uuid in self.app.sparkline_data:
            storage = self.app.sparkline_data[uuid]

            def update_history(key, value):
                history = storage.get(key, [])
                history.append(value)
                if len(history) > 20:
                    history.pop(0)
                storage[key] = history

            with self.app.vm_service._sparkline_lock:
                # We check stats_view_mode from self since we are on main thread
                if self.stats_view_mode == "resources":
                    update_history("cpu", stats["cpu_percent"])
                    update_history("mem", stats["mem_percent"])
                else:  # io
                    update_history("disk", self.latest_disk_read + self.latest_disk_write)
                    update_history("net", self.latest_net_rx + self.latest_net_tx)

            self.update_sparkline_data()
        else:
            if not hasattr(self.app, "sparkline_data"):
                logging.error("app does not have sparkline_data attribute")
            # If uuid not in sparkline_data, it might be initializing, we skip.

    @on(
        Click,
        ".sparkline-container, #cpu-sparkline, #mem-sparkline, #disk-sparkline, #net-sparkline",
    )
    def toggle_stats_view(self) -> None:
        """Toggle between resource and I/O stat views."""
        if self.status in (StatusText.RUNNING, StatusText.PAUSED):
            self.stats_view_mode = "io" if self.stats_view_mode == "resources" else "resources"

    def update_button_layout(self):
        """Update the button layout based on current VM status."""
        self._update_fast_buttons()
        self._update_webc_status()

        if self.query("#rename-button"):
            # Check if collapsible is expanded before fetching heavy data
            collapsible = self.ui.get("collapsible")
            if collapsible and not collapsible.collapsed:
                self.app.set_timer(0.1, self._refresh_snapshot_tab_async)
                self.app.worker_manager.run(
                    self._fetch_actions_state_worker,
                    name=f"actions_state_{self.internal_id}",
                    exclusive=True,
                )

    def _update_fast_buttons(self):
        """Updates buttons that rely on cached/fast state."""
        is_loading = self.status == StatusText.LOADING
        is_stopped = self.status == StatusText.STOPPED
        is_running = self.status == StatusText.RUNNING
        is_paused = self.status == StatusText.PAUSED
        is_pmsuspended = self.status == StatusText.PMSUSPENDED
        is_blocked = self.status == StatusText.BLOCKED

        if not self.ui.get("btn_quick_start"):
            return

        self.ui["btn_quick_start"].display = is_stopped
        self.ui["btn_quick_stop"].display = is_running or is_blocked
        self.ui["btn_quick_view"].display = is_running or is_paused or is_blocked
        self.ui["btn_quick_resume"].display = is_paused or is_pmsuspended

        if not self.query("#rename-button"):
            return

        def update(selector, visible):
            for w in self.query(selector):
                w.display = visible

        update("#start", is_stopped)
        update("#shutdown", is_running or is_blocked)
        update("#hibernate", is_running or is_blocked)
        update("#stop", is_running or is_paused or is_pmsuspended or is_blocked)
        update("#delete", is_running or is_paused or is_stopped or is_pmsuspended or is_blocked)
        update("#clone", is_stopped)
        update("#migration", not is_loading)
        update("#rename-button", is_stopped)
        update("#pause", is_running)
        update("#resume", is_paused or is_pmsuspended)
        update("#connect", self.app.r_viewer_available)
        update("#web_console", (is_running or is_paused or is_blocked))
        update("#configure-button", not is_loading)
        update("#snap_overlay_help", not is_loading)
        update("#snapshot_take", not is_loading)
        update("#snapshot_restore", not is_running and not is_loading and not is_blocked)

        # XML Button Logic
        for xml_button in self.query("#xml"):
            if is_stopped:
                xml_button.label = ButtonLabels.EDIT_XML
                self.stats_view_mode = "resources"
            else:
                xml_button.label = ButtonLabels.VIEW_XML
            xml_button.display = not is_loading

    def _fetch_actions_state_worker(self):
        """Worker to fetch heavy state for actions."""
        try:
            if self.vm is None:
                return

            snapshot_summary = {"count": 0, "latest": None}
            has_overlay = False
            domain_missing = False
            state_tuple = self.app.vm_service._get_domain_state(self.vm)
            if not state_tuple:
                return

            last_fetch_key = f"snapshot_fetch_{self.internal_id}"

            # Use vm_service._cache_lock for thread-safe access to _last_snapshot_fetch
            # This prevents race conditions when multiple workers access the cache concurrently
            with self.app.vm_service._cache_lock:
                if hasattr(self.app, "_last_snapshot_fetch"):
                    cached_data = self.app._last_snapshot_fetch.get(last_fetch_key)
                    # dict format for caching data
                    if isinstance(cached_data, dict):
                        last_fetch = cached_data.get("time", 0)
                        if time.time() - last_fetch < 2:
                            # Use cached data and update UI immediately
                            snapshot_summary = cached_data.get(
                                "snapshot_summary", {"count": 0, "latest": None}
                            )
                            has_overlay = cached_data.get("has_overlay", False)
                            try:
                                self.app.call_from_thread(
                                    self._update_slow_buttons, snapshot_summary, has_overlay
                                )
                            except RuntimeError:
                                self._update_slow_buttons(snapshot_summary, has_overlay)
                            return
                    elif isinstance(cached_data, (int, float)):
                        if time.time() - cached_data < 2:
                            return

            # Fetch snapshot data outside the lock to avoid holding it during
            # potentially slow libvirt operations
            try:
                if self.vm and not domain_missing:
                    # Optimization: only fetch full details if there are snapshots
                    if self.vm.snapshotNum(0) > 0:
                        snapshots = get_vm_snapshots(self.vm)
                        if snapshots:
                            snapshot_summary["count"] = len(snapshots)
                            latest = snapshots[0]
                            snapshot_summary["latest"] = {
                                "name": latest["name"],
                                "time": latest["creation_time"],
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

            # Record fetch time and data with thread-safe access
            with self.app.vm_service._cache_lock:
                if not hasattr(self.app, "_last_snapshot_fetch"):
                    self.app._last_snapshot_fetch = {}

                self.app._last_snapshot_fetch[last_fetch_key] = {
                    "time": time.time(),
                    "snapshot_summary": snapshot_summary,
                    "has_overlay": has_overlay,
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
        if not self.query("#rename-button"):
            return

        snapshot_count = snapshot_summary.get("count", 0)

        # Update Tooltip on TabPane
        try:
            tabbed_content = self.query_one("#button-container", TabbedContent)
            pane = tabbed_content.get_tab("snapshot-tab")
            if snapshot_count > 0:
                latest = snapshot_summary.get("latest")
                info = (
                    f"{StaticText.LATEST_SNAPSHOT} {latest['name']} ({latest['time']})"
                    if latest
                    else "Unknown"
                )
                pane.tooltip = TabTitles.TOTAL_TAB.format(info=info, snapshot_count=snapshot_count)
            else:
                pane.tooltip = StaticText.NO_SNAPSHOTS_CREATED
        except Exception:
            pass

        is_running = self.status == StatusText.RUNNING
        is_stopped = self.status == StatusText.STOPPED
        is_loading = self.status == StatusText.LOADING
        is_pmsuspended = self.status == StatusText.PMSUSPENDED
        is_blocked = self.status == StatusText.BLOCKED

        has_snapshots = snapshot_count > 0

        def update(selector, visible):
            for w in self.query(selector):
                w.display = visible

        update(
            "#snapshot_restore",
            has_snapshots and not is_running and not is_loading and not is_blocked,
        )
        update("#snapshot_delete", has_snapshots)

        update("#commit_disk", (is_running or is_blocked) and has_overlay)
        update("#discard_overlay", is_stopped and has_overlay)
        update("#create_overlay", is_stopped and not has_overlay)

        self.update_snapshot_tab_title(snapshot_count)

    def _update_status_styling(self):
        status_widget = self.ui.get("status")
        if status_widget:
            status_widget.remove_class(
                "stopped", "running", "paused", "loading", "pmsuspended", "blocked"
            )
            if self.status == StatusText.LOADING:
                status_widget.add_class("loading")
            elif self.status == StatusText.RUNNING:
                status_widget.add_class("running")
            elif self.status == StatusText.STOPPED:
                status_widget.add_class("stopped")
            elif self.status == StatusText.PAUSED:
                status_widget.add_class("paused")
            elif self.status == StatusText.PMSUSPENDED:
                status_widget.add_class("pmsuspended")
            elif self.status == StatusText.BLOCKED:
                status_widget.add_class("blocked")

    @on(VMActionButtonPressed)
    def on_vm_action_button_pressed(self, event: VMActionButtonPressed) -> None:
        """Handle actions from the VMCardActions component."""
        self._dispatch_action(event.action_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses for quick actions directly on the card."""
        # Only handle quick buttons here.
        # Buttons in VMCardActions are handled via VMActionButtonPressed.
        if event.button.id in ("start", "connect", "resume", "shutdown"):
            self._dispatch_action(event.button.id)

    def _dispatch_action(self, action_id: str) -> None:
        """Dispatch action based on ID."""
        if action_id == "start":
            self.post_message(VmActionRequest(self.internal_id, VmAction.START))
            return

        button_handlers = {
            "shutdown": self._handle_shutdown_button,
            "hibernate": self._handle_hibernate_button,
            "stop": self._handle_stop_button,
            "pause": self._handle_pause_button,
            "resume": self._handle_resume_button,
            "xml": self._handle_xml_button,
            "connect": self._handle_connect_button,
            "web_console": self._handle_web_console_button,
            "tmux_console": self._handle_tmux_console_button,
            "snapshot_take": self._handle_snapshot_take_button,
            "snapshot_restore": self._handle_snapshot_restore_button,
            "snapshot_delete": self._handle_snapshot_delete_button,
            "delete": self._handle_delete_button,
            "clone": self._handle_clone_button,
            "migration": self._handle_migration_button,
            "rename-button": self._handle_rename_button,
            "configure-button": self._handle_configure_button,
            "create_overlay": self._handle_create_overlay,
            "commit_disk": self._handle_commit_disk,
            "discard_overlay": self._handle_discard_overlay,
            "snap_overlay_help": self._handle_overlay_help,
        }
        handler = button_handlers.get(action_id)
        if handler:
            handler()

    def _handle_overlay_help(self) -> None:
        """Handles the overlay help button press."""
        self.app.push_screen(HowToOverlayModal())

    def _handle_create_overlay(self) -> None:
        """Handles the create overlay button press."""
        try:
            # Use cached XML if available to avoid XMLDesc() call
            vm_cache = self.app.vm_service._vm_data_cache.get(self.internal_id, {})
            xml_content = vm_cache.get("xml")

            disks = get_vm_disks(self.vm)
            # Filter for actual disks (exclude cdroms, etc)
            valid_disks = [d["path"] for d in disks if d.get("device_type") == "disk"]

            if not valid_disks:
                self.app.show_error_message(ErrorMessages.NO_SUITABLE_DISKS_FOR_OVERLAY)
                return

            target_disk = valid_disks[0]

            _, vm_name = self._get_vm_identity_info()
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
                    self.app.show_success_message(
                        SuccessMessages.INPUT_SANITIZED.format(
                            original_input=overlay_name_raw, sanitized_input=overlay_name
                        )
                    )

                if not overlay_name:
                    self.app.show_error_message(ErrorMessages.OVERLAY_NAME_EMPTY_AFTER_SANITIZATION)
                    return

                self.app.vm_service.suppress_vm_events(self.internal_id)
                try:
                    create_external_overlay(self.vm, target_disk, overlay_name)
                    self.app.show_success_message(
                        SuccessMessages.OVERLAY_CREATED.format(overlay_name=overlay_name)
                    )
                    self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                    self._boot_device_checked = False
                    self.post_message(VmCardUpdateRequest(self.internal_id))
                    self.update_button_layout()
                except Exception as e:
                    self.app.show_error_message(
                        ErrorMessages.ERROR_CREATING_OVERLAY_TEMPLATE.format(error=e)
                    )
                finally:
                    self.app.vm_service.unsuppress_vm_events(self.internal_id)

            self.app.push_screen(
                InputModal(
                    "Enter name for new overlay volume:", default_name, restrict=r"[a-zA-Z0-9_-]*"
                ),
                on_name_input,
            )

        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.ERROR_PREPARING_OVERLAY_CREATION_TEMPLATE.format(error=e)
            )

    def _handle_discard_overlay(self) -> None:
        """Handles the discard overlay button press."""
        try:
            overlay_disks = get_overlay_disks(self.vm)

            if not overlay_disks:
                self.app.show_error_message(ErrorMessages.NO_OVERLAY_DISKS_FOUND)
                return

            def proceed_with_discard(target_disk: str | None):
                if not target_disk:
                    return

                def on_confirm(confirmed: bool):
                    if confirmed:
                        self.app.vm_service.suppress_vm_events(self.internal_id)
                        try:
                            discard_overlay(self.vm, target_disk)
                            self.app.show_success_message(
                                SuccessMessages.OVERLAY_DISCARDED.format(target_disk=target_disk)
                            )
                            self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                            self._boot_device_checked = False
                            self.post_message(VmCardUpdateRequest(self.internal_id))
                            self.update_button_layout()
                        except Exception as e:
                            self.app.show_error_message(
                                ErrorMessages.ERROR_DISCARDING_OVERLAY_TEMPLATE.format(error=e)
                            )
                        finally:
                            self.app.vm_service.unsuppress_vm_events(self.internal_id)

                self.app.push_screen(
                    ConfirmationDialog(
                        DialogMessages.CONFIRM_DISCARD_CHANGES.format(target_disk=target_disk)
                    ),
                    on_confirm,
                )

            if len(overlay_disks) == 1:
                proceed_with_discard(overlay_disks[0])
            else:
                self.app.push_screen(
                    SelectDiskModal(overlay_disks, StaticText.SELECT_OVERLAY_DISCARD),
                    proceed_with_discard,
                )

        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.ERROR_PREPARING_DISCARD_OVERLAY_TEMPLATE.format(error=e)
            )

    def _handle_commit_disk(self) -> None:
        """Handles the commit disk changes button press."""
        # This works on running VM.
        try:
            disks = get_vm_disks(self.vm)
            # Filter for actual disks (exclude cdroms, etc)
            target_disk = None
            for d in disks:
                if d.get("path"):
                    target_disk = d["path"]
                    break

            if not target_disk:
                self.app.show_error_message(ErrorMessages.NO_DISKS_FOUND_TO_COMMIT)
                return

            def on_confirm(confirmed: bool):
                if confirmed:
                    progress_modal = ProgressModal(
                        title=ProgressMessages.COMMITTING_CHANGES_FOR.format(name=self.name)
                    )
                    self.app.push_screen(progress_modal)

                    def do_commit():
                        self.app.vm_service.suppress_vm_events(self.internal_id)
                        try:
                            commit_disk_changes(self.vm, target_disk)
                            self.app.call_from_thread(
                                self.app.show_success_message, SuccessMessages.DISK_COMMITTED
                            )
                            self.app.call_from_thread(self.app.refresh_vm_list)
                            self.app.call_from_thread(self.update_button_layout)
                        except Exception as e:
                            self.app.call_from_thread(
                                self.app.show_error_message,
                                ErrorMessages.ERROR_COMMITTING_DISK_TEMPLATE.format(error=e),
                            )
                        finally:
                            self.app.vm_service.unsuppress_vm_events(self.internal_id)
                            self.app.call_from_thread(progress_modal.dismiss)

                    self.app.worker_manager.run(do_commit, name=f"commit_{self.name}")

            self.app.push_screen(
                ConfirmationDialog(
                    DialogMessages.CONFIRM_MERGE_CHANGES.format(target_disk=target_disk)
                ),
                on_confirm,
            )

        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.ERROR_PREPARING_COMMIT_TEMPLATE.format(error=e)
            )

    def _handle_shutdown_button(self) -> None:
        """Handles the shutdown button press."""
        logging.info(f"Attempting to gracefully shutdown VM: {self.name}")
        if self.status in (StatusText.RUNNING, StatusText.PAUSED):
            self.post_message(VmActionRequest(self.internal_id, VmAction.STOP))

    def _handle_hibernate_button(self) -> None:
        """Handles the save button press."""
        logging.info(f"Attempting to save (hibernate) VM: {self.name}")

        def do_save():
            self.stop_background_activities()
            self.app.vm_service.suppress_vm_events(self.internal_id)
            try:
                hibernate_vm(self.vm)
                self.app.call_from_thread(
                    self.app.show_success_message,
                    SuccessMessages.VM_SAVED_TEMPLATE.format(vm_name=self.name),
                )
                self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                self.app.call_from_thread(setattr, self, "status", StatusText.STOPPED)
                self.app.call_from_thread(self.update_button_layout)
            except Exception as e:
                self.app.call_from_thread(
                    self.app.show_error_message,
                    ErrorMessages.ERROR_ON_VM_DURING_ACTION.format(
                        vm_name=self.name, action="save", error=e
                    ),
                )
            finally:
                self.app.vm_service.unsuppress_vm_events(self.internal_id)

        if self.status in (StatusText.RUNNING, StatusText.PAUSED):
            self.app.worker_manager.run(do_save, name=f"save_{self.internal_id}")

    def stop_background_activities(self):
        """Stop background activities before action"""
        with self._timer_lock:
            if self.timer:
                self.timer.stop()
                self.timer = None
        self.app.worker_manager.cancel(f"update_stats_{self.internal_id}")
        self.app.worker_manager.cancel(f"actions_state_{self.internal_id}")
        self.app.worker_manager.cancel(f"refresh_snapshot_tab_{self.internal_id}")

    def _handle_stop_button(self) -> None:
        """Handles the stop button press."""
        logging.info(f"Attempting to stop VM: {self.name}")

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            # if self.vm.isActive():
            # maybe better to use cache status
            if self.status in (StatusText.RUNNING, StatusText.PAUSED):
                self.stop_background_activities()
                self.post_message(VmActionRequest(self.internal_id, VmAction.FORCE_OFF))

        message = f"{ErrorMessages.HARD_STOP_WARNING}\nAre you sure you want to stop '{self.name}'?"
        self.app.push_screen(ConfirmationDialog(message), on_confirm)

    def _handle_pause_button(self) -> None:
        """Handles the pause button press."""
        logging.info(f"Attempting to pause VM: {self.name}")
        # Use status instead of blocking isActive() call
        if self.status in (StatusText.RUNNING, StatusText.PAUSED):
            self.stop_background_activities()
            self.post_message(VmActionRequest(self.internal_id, VmAction.PAUSE))
        else:
            self.app.show_warning_message(
                WarningMessages.LIBVIRT_XML_NO_EFFECTIVE_CHANGE.format(vm_name=self.name)
            )

    def _handle_resume_button(self) -> None:
        """Handles the resume button press."""
        logging.info(f"Attempting to resume VM: {self.name}")
        self.stop_background_activities()
        self.status = StatusText.LOADING
        self.post_message(VmActionRequest(self.internal_id, VmAction.RESUME))
        self.app.set_timer(1.0, self.update_stats)

    def _handle_xml_button(self) -> None:
        """Handles the xml button press."""
        try:
            vm_cache = self.app.vm_service._vm_data_cache.get(self.internal_id, {})
            cached_xml = vm_cache.get("xml")

            xml_flags = 0
            try:
                original_xml = self.vm.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
                xml_flags = libvirt.VIR_DOMAIN_XML_SECURE
            except libvirt.libvirtError:
                original_xml = self.vm.XMLDesc(0)
                xml_flags = 0
            is_stopped = self.status == StatusText.STOPPED

            def handle_xml_modal_result(modified_xml: str | None):
                if modified_xml and is_stopped:
                    if original_xml.strip() != modified_xml.strip():
                        try:
                            conn = self.vm.connect()
                            new_domain = conn.defineXML(modified_xml)

                            # Verify if changes were effectively applied
                            new_xml = new_domain.XMLDesc(xml_flags)

                            if original_xml == new_xml:
                                self.app.show_warning_message(
                                    WarningMessages.LIBVIRT_XML_NO_EFFECTIVE_CHANGE.format(
                                        vm_name=self.name
                                    )
                                )
                                logging.warning(
                                    f"XML update for {self.name} resulted in no effective changes."
                                )
                            else:
                                self.app.show_success_message(
                                    SuccessMessages.VM_CONFIG_UPDATED.format(vm_name=self.name)
                                )
                                logging.info(f"Successfully updated XML for VM: {self.name}")

                            self.app.vm_service.invalidate_vm_state_cache(self.internal_id)
                            self._boot_device_checked = False
                            self.app.refresh_vm_list()
                        except libvirt.libvirtError as e:
                            self.app.show_error_message(
                                ErrorMessages.INVALID_XML_TEMPLATE.format(
                                    vm_name=self.name, error=e
                                )
                            )
                            logging.error(e)
                    else:
                        self.app.show_success_message(SuccessMessages.NO_XML_CHANGES)

            self.app.push_screen(
                XMLDisplayModal(original_xml, read_only=not is_stopped), handle_xml_modal_result
            )
        except libvirt.libvirtError as e:
            self.app.show_error_message(
                ErrorMessages.ERROR_GETTING_XML_TEMPLATE.format(vm_name=self.name, error=e)
            )
        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE.format(error=e)
            )
            logging.error(f"Unexpected error handling XML button: {traceback.format_exc()}")

    def _handle_connect_button(self) -> None:
        """Handles the connect button press by running the remove virt viewer in a worker."""
        logging.info(f"Attempting to connect to VM: {self.name}")
        if not hasattr(self, "conn") or not self.conn:
            self.app.show_error_message(ErrorMessages.CONNECTION_INFO_NOT_AVAILABLE)
            return

        def do_connect() -> None:
            try:
                uri = self._get_uri()
                _, domain_name = self._get_vm_identity_info()
                command = remote_viewer_cmd(uri, domain_name, self.app.r_viewer)

                # env = os.environ.copy()
                # env['GDK_BACKEND'] = 'x11'
                try:
                    proc = subprocess.Popen(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid,
                        # env=env
                    )
                    logging.info(
                        f"{self.app.r_viewer} started with PID {proc.pid} for {domain_name}"
                    )
                    self.app.show_quick_message(
                        f"Remote viewer {self.app.r_viewer} started for {domain_name}"
                    )
                except Exception as e:
                    logging.error(f"Failed to spawn {self.app.r_viewer} for {domain_name}: {e}")
                    self.app.call_from_thread(
                        self.app.show_error_message,
                        ErrorMessages.REMOTE_VIEWER_FAILED_TO_START_TEMPLATE.format(
                            viewer=self.app.r_viewer, domain_name=domain_name, error=e
                        ),
                    )
                    return

            except FileNotFoundError:
                self.app.call_from_thread(
                    self.app.show_error_message, ErrorMessages.R_VIEWER_NOT_FOUND
                )
            except libvirt.libvirtError as e:
                self.app.call_from_thread(
                    self.app.show_error_message,
                    ErrorMessages.ERROR_GETTING_VM_DETAILS_TEMPLATE.format(
                        vm_name=self.name, error=e
                    ),
                )
            except Exception as e:
                logging.error(f"An unexpected error occurred during connect: {e}", exc_info=True)
                self.app.call_from_thread(
                    self.app.show_error_message, ErrorMessages.UNEXPECTED_ERROR_CONNECTING
                )

        self.app.worker_manager.run(do_connect, name=f"r_viewer_{self.name}")

    def _handle_web_console_button(self) -> None:
        """Handles the web console button press by opening a config dialog."""
        worker = partial(self.app.webconsole_manager.start_console, self.vm, self.conn)

        try:
            uuid = self.internal_id
            if self.app.webconsole_manager.is_running(uuid):
                self.app.worker_manager.run(worker, name=f"show_console_{self.vm.name()}")
                return
        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.ERROR_CHECKING_WEB_CONSOLE_STATUS_TEMPLATE.format(
                    vm_name=self.name, error=e
                )
            )
            return

        uri = self._get_uri()
        is_remote = is_remote_connection(uri)

        if is_remote:

            def handle_dialog_result(should_start: bool) -> None:
                if should_start:
                    self.app.worker_manager.run(worker, name=f"start_console_{self.vm.name()}")

            self.app.push_screen(WebConsoleConfigDialog(is_remote=is_remote), handle_dialog_result)
        else:
            self.app.worker_manager.run(worker, name=f"start_console_{self.vm.name()}")

    def _handle_tmux_console_button(self) -> None:
        """Handles the text console button press by opening a new tmux window."""
        logging.info(f"Attempting to open text console for VM: {self.name}")

        # Check if running in tmux
        if not os.environ.get("TMUX"):
            self.app.show_error_message("This feature requires running inside tmux.")
            return

        try:
            # Use cached values to avoid libvirt calls where possible
            uri = self._get_uri()

            # Get proper domain name
            _, domain_name = self._get_vm_identity_info()

            # Construct command
            # tmux new-window -n "Console: <vm_name>" "virsh -c <uri> console <vm_name>; read"
            help_msg = (
                "echo '---------------------------------------------------------'; "
                "echo 'Tmux Navigation Help:'; "
                "echo ' Ctrl+B N or P - Move to the next or previous window.'; "
                "echo ' Ctrl+B W      - Open a panel to navigate across windows in multiple sessions.'; "
                "echo ' Ctrl+]        - Close the current view.'; "
                "echo ' Ctrl+B ?      - View all keybindings. Press Q to exit.';"
                "echo '---------------------------------------------------------'; "
                "echo 'Starting console...'; sleep 1;"
            )
            cmd = [
                "tmux",
                "new-window",
                "-n",
                f"{domain_name}",
                f"{help_msg} virsh -c {uri} console {domain_name}; echo '\nConsole session ended. Press Enter to close window.'; read",
            ]

            logging.info(f"Launching tmux console: {' '.join(cmd)}")
            subprocess.Popen(cmd)
            self.app.show_quick_message(f"Opened console for {domain_name}")

        except Exception as e:
            logging.error(f"Failed to open tmux console: {e}")
            self.app.show_error_message(f"Failed to open console: {e}")

    def _handle_snapshot_take_button(self) -> None:
        """Handles the snapshot take button press."""
        logging.info(f"Attempting to take snapshot for VM: {self.name}")
        vm = self.vm
        internal_id = self.internal_id
        vm_name = self.name

        def handle_snapshot_result(result: dict | None) -> None:
            # Always refresh tab title when modal closes, even if cancelled
            self._refresh_snapshot_tab_async()
            if result:
                name = result["name"]
                description = result["description"]
                quiesce = result.get("quiesce", False)

                self.stop_background_activities()
                self.app.worker_manager.cancel(f"refresh_snapshot_tab_{internal_id}")

                loading_modal = LoadingModal(message=f"Taking snapshot for {vm_name}...")
                self.app.push_screen(loading_modal)

                def do_snapshot():
                    self.app.vm_service.suppress_vm_events(internal_id)
                    error = None
                    try:
                        create_vm_snapshot(vm, name, description, quiesce=quiesce)
                    except Exception as e:
                        error = e
                    finally:
                        self.app.vm_service.unsuppress_vm_events(internal_id)

                    # Invalidate caches in worker thread to avoid blocking main thread
                    if not error:
                        try:
                            self.app.vm_service.invalidate_vm_state_cache(internal_id)
                            self.app.vm_service.invalidate_domain_cache()
                        except Exception:
                            pass

                    def finalize_snapshot():
                        loading_modal.dismiss()
                        if error:
                            self.app.show_error_message(
                                ErrorMessages.SNAPSHOT_ERROR_TEMPLATE.format(
                                    vm_name=vm_name, error=error
                                )
                            )
                        else:
                            self.app.show_success_message(
                                SuccessMessages.SNAPSHOT_CREATED.format(snapshot_name=name)
                            )
                            # Defer refresh and restart stats to avoid racing
                            self.app.set_timer(0.5, self._refresh_snapshot_tab_async)
                        # Restart stats timer after a delay
                        self.app.set_timer(1.0, self.update_stats)

                    self.app.call_from_thread(finalize_snapshot)

                self.app.worker_manager.run(do_snapshot, name=f"snapshot_take_{internal_id}")

        self.app.push_screen(SnapshotNameDialog(vm), handle_snapshot_result)

    def _handle_snapshot_restore_button(self) -> None:
        """Handles the snapshot restore button press."""
        logging.info(f"Attempting to restore snapshot for VM: {self.name}")

        loading_modal = LoadingModal(message="Fetching snapshots...")
        self.app.push_screen(loading_modal)

        def fetch_and_show_worker():
            try:
                vm = self.vm
                internal_id = self.internal_id
                vm_name = self.name

                snapshots_info = get_vm_snapshots(vm)
                self.app.call_from_thread(loading_modal.dismiss)

                if not snapshots_info:
                    self.app.call_from_thread(
                        self.app.show_error_message, ErrorMessages.NO_SNAPSHOTS_TO_RESTORE
                    )
                    return

                def restore_snapshot_callback(snapshot_name: str | None) -> None:
                    self._refresh_snapshot_tab_async()
                    if snapshot_name:
                        # Stop all background activity for this card before starting the restore
                        if self.timer:
                            self.timer.stop()
                            self.timer = None
                        self.app.worker_manager.cancel(f"update_stats_{internal_id}")
                        self.app.worker_manager.cancel(f"actions_state_{internal_id}")
                        self.app.worker_manager.cancel(f"refresh_snapshot_tab_{internal_id}")

                        restore_loading_modal = LoadingModal(
                            message=f"Restoring snapshot {snapshot_name}..."
                        )
                        self.app.push_screen(restore_loading_modal)

                        def do_restore():
                            self.app.vm_service.suppress_vm_events(internal_id)
                            error = None
                            try:
                                restore_vm_snapshot(vm, snapshot_name)
                            except Exception as e:
                                error = e
                            finally:
                                self.app.vm_service.unsuppress_vm_events(internal_id)

                            # Invalidate caches in worker thread to avoid blocking main thread
                            if not error:
                                try:
                                    self.app.vm_service.invalidate_vm_state_cache(internal_id)
                                    self.app.vm_service.invalidate_domain_cache()
                                except Exception as e:
                                    logging.warning(
                                        f"[do_restore] Cache invalidation FAILED for {vm_name}: {e}"
                                    )
                                    pass

                            def finalize_ui():
                                restore_loading_modal.dismiss()
                                if error:
                                    self.app.show_error_message(
                                        ErrorMessages.ERROR_ON_VM_DURING_ACTION.format(
                                            vm_name=vm_name, action="snapshot restore", error=error
                                        )
                                    )
                                else:
                                    self._boot_device_checked = False
                                    self.app.show_success_message(
                                        SuccessMessages.SNAPSHOT_RESTORED.format(
                                            snapshot_name=snapshot_name
                                        )
                                    )
                                    logging.info(
                                        f"Successfully restored snapshot [b]{snapshot_name}[/b] for VM: {vm_name}"
                                    )
                                    self.app.refresh_vm_list(force=True)

                            self.app.call_from_thread(finalize_ui)

                        self.app.worker_manager.run(
                            do_restore, name=f"snapshot_restore_{internal_id}"
                        )

                self.app.call_from_thread(
                    self.app.push_screen,
                    SelectSnapshotDialog(snapshots_info, "Select snapshot to restore"),
                    restore_snapshot_callback,
                )

            except Exception as e:
                self.app.call_from_thread(loading_modal.dismiss)
                self.app.call_from_thread(
                    self.app.show_error_message,
                    ErrorMessages.ERROR_FETCHING_SNAPSHOTS_TEMPLATE.format(error=e),
                )

        self.app.worker_manager.run(
            fetch_and_show_worker, name=f"snapshot_restore_fetch_{self.internal_id}"
        )

    def _handle_snapshot_delete_button(self) -> None:
        """Handles the snapshot delete button press."""
        logging.info(f"Attempting to delete snapshot for VM: {self.name}")

        loading_modal = LoadingModal(message="Fetching snapshots...")
        self.app.push_screen(loading_modal)

        def fetch_and_show_worker():
            try:
                vm = self.vm
                internal_id = self.internal_id
                vm_name = self.name

                snapshots_info = get_vm_snapshots(vm)
                self.app.call_from_thread(loading_modal.dismiss)

                if not snapshots_info:
                    self.app.call_from_thread(
                        self.app.show_error_message, ErrorMessages.NO_SNAPSHOTS_TO_DELETE
                    )
                    return

                def delete_snapshot_callback(snapshot_name: str | None) -> None:
                    self._refresh_snapshot_tab_async()
                    if snapshot_name:

                        def on_confirm(confirmed: bool) -> None:
                            if confirmed:
                                self.stop_background_activities()
                                loading_modal = LoadingModal(
                                    message=f"Deleting snapshot {snapshot_name}..."
                                )
                                self.app.push_screen(loading_modal)

                                def do_delete():
                                    self.app.vm_service.suppress_vm_events(internal_id)
                                    error = None
                                    try:
                                        delete_vm_snapshot(vm, snapshot_name)
                                    except Exception as e:
                                        error = e
                                    finally:
                                        self.app.vm_service.unsuppress_vm_events(internal_id)

                                    # Invalidate caches
                                    if not error:
                                        try:
                                            self.app.vm_service.invalidate_vm_cache(internal_id)
                                            self.app.vm_service.invalidate_domain_cache()
                                        except Exception:
                                            pass

                                    def finalize_ui():
                                        loading_modal.dismiss()
                                        if error:
                                            self.app.show_error_message(
                                                ErrorMessages.ERROR_ON_VM_DURING_ACTION.format(
                                                    vm_name=vm_name,
                                                    action="snapshot delete",
                                                    error=error,
                                                )
                                            )
                                        else:
                                            self.app.show_success_message(
                                                SuccessMessages.SNAPSHOT_DELETED.format(
                                                    snapshot_name=snapshot_name
                                                )
                                            )
                                            logging.info(
                                                f"Successfully deleted snapshot '{snapshot_name}' for VM: {vm_name}"
                                            )
                                            self.app.set_timer(
                                                0.1, self._refresh_snapshot_tab_async
                                            )
                                        # Restart stats timer
                                        self.update_stats()

                                    self.app.call_from_thread(finalize_ui)

                                self.app.worker_manager.run(
                                    do_delete, name=f"snapshot_delete_{internal_id}"
                                )

                        self.app.push_screen(
                            ConfirmationDialog(
                                DialogMessages.DELETE_SNAPSHOT_CONFIRMATION.format(
                                    name=snapshot_name
                                )
                            ),
                            on_confirm,
                        )

                self.app.call_from_thread(
                    self.app.push_screen,
                    SelectSnapshotDialog(snapshots_info, "Select snapshot to delete"),
                    delete_snapshot_callback,
                )

            except Exception as e:
                self.app.call_from_thread(loading_modal.dismiss)
                self.app.call_from_thread(
                    self.app.show_error_message,
                    ErrorMessages.ERROR_FETCHING_SNAPSHOTS_TEMPLATE.format(error=e),
                )

        self.app.worker_manager.run(
            fetch_and_show_worker, name=f"snapshot_delete_fetch_{self.internal_id}"
        )

    def _refresh_snapshot_tab_async(self) -> None:
        """Refreshes the snapshot tab title asynchronously in a worker."""

        def fetch_and_update():
            try:
                if not self.vm:
                    return

                # Fetch current snapshot count and summary
                snapshot_summary = {"count": 0, "latest": None}
                try:
                    # Optimization: only fetch full details if there are snapshots
                    if self.vm.snapshotNum(0) > 0:
                        snapshots = get_vm_snapshots(self.vm)
                        if snapshots:
                            snapshot_summary["count"] = len(snapshots)
                            latest = snapshots[0]
                            snapshot_summary["latest"] = {
                                "name": latest["name"],
                                "time": latest["creation_time"],
                            }
                except libvirt.libvirtError as e:
                    if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                        logging.info(f"Domain no longer exists for {self.name}")
                        return
                    else:
                        logging.warning(f"Could not get snapshot details for {self.name}: {e}")
                        return

                snapshot_count = snapshot_summary.get("count", 0)

                # Update UI on main thread
                def update_ui():
                    if self.is_mounted:
                        self.update_snapshot_tab_title(snapshot_count)

                        # Update Tooltip on TabPane
                        try:
                            tabbed_content = self.query_one("#button-container", TabbedContent)
                            pane = tabbed_content.get_tab("snapshot-tab")
                            if snapshot_count > 0:
                                latest = snapshot_summary.get("latest")
                                info = (
                                    f"{StaticText.LATEST_SNAPSHOT} {latest['name']} ({latest['time']})"
                                    if latest
                                    else "Unknown"
                                )
                                pane.tooltip = TabTitles.TOTAL_TAB.format(
                                    info=info, snapshot_count=snapshot_count
                                )
                            else:
                                pane.tooltip = StaticText.NO_SNAPSHOTS_CREATED
                        except Exception:
                            pass

                        # Also update button visibility
                        if self.query("#rename-button"):
                            has_snapshots = snapshot_count > 0
                            is_running = self.status == StatusText.RUNNING
                            is_loading = self.status == StatusText.LOADING

                            def update_btn(selector, visible):
                                for w in self.query(selector):
                                    w.display = visible

                            update_btn(
                                "#snapshot_restore",
                                has_snapshots and not is_running and not is_loading,
                            )
                            update_btn("#snapshot_delete", has_snapshots)

                self.app.call_from_thread(update_ui)

            except Exception as e:
                logging.error(f"Error refreshing snapshot tab for {self.name}: {e}")

        # Run in worker to avoid blocking UI
        self.app.worker_manager.run(
            fetch_and_update, name=f"refresh_snapshot_tab_{self.internal_id}", exclusive=True
        )

    def _handle_delete_button(self) -> None:
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

            vm_name = self.name
            internal_id = self.internal_id

            progress_modal = ProgressModal(title=f"Deleting {vm_name}...")
            self.app.push_screen(progress_modal)

            def log_callback(message: str):
                self.app.call_from_thread(progress_modal.add_log, message)

            def do_delete():
                # Stop stats worker to avoid conflicts
                def stop_stats():
                    if self.timer:
                        self.timer.stop()
                        self.timer = None

                self.app.call_from_thread(stop_stats)

                self.app.worker_manager.cancel(f"update_stats_{internal_id}")
                self.app.worker_manager.cancel(f"actions_state_{internal_id}")

                self.app.vm_service.suppress_vm_events(internal_id)
                try:
                    # delete_vm handles opening its own connection
                    delete_vm(
                        self.vm,
                        delete_storage=delete_storage,
                        delete_nvram=True,
                        log_callback=log_callback,
                    )

                    self.app.call_from_thread(
                        self.app.show_success_message,
                        SuccessMessages.VM_DELETED.format(vm_name=vm_name),
                    )

                    # Invalidate cache
                    self.app.vm_service.invalidate_vm_cache(internal_id)
                    # If it was selected, unselect it
                    if internal_id in self.app.selected_vm_uuids:
                        self.app.selected_vm_uuids.discard(internal_id)

                    self.app.vm_service.unsuppress_vm_events(internal_id)
                    self.app.call_from_thread(self.app.refresh_vm_list, force=True)

                except Exception as e:
                    self.app.vm_service.unsuppress_vm_events(internal_id)
                    self.app.call_from_thread(
                        self.app.show_error_message,
                        ErrorMessages.ERROR_DELETING_VM_TEMPLATE.format(vm_name=vm_name, error=e),
                    )
                finally:
                    self.app.call_from_thread(progress_modal.dismiss)

            self.app.worker_manager.run(do_delete, name=f"delete_{internal_id}")

        self.app.push_screen(DeleteVMConfirmationDialog(self.name), on_confirm)

    def _handle_clone_button(self) -> None:
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

                # Suppress events for the source VM
                app.vm_service.suppress_vm_events(self.internal_id)

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
                        log_callback(
                            f"ERROR: {ErrorMessages.ERROR_GETTING_EXISTING_VM_NAMES_TEMPLATE.format(error=e)}"
                        )
                        app.call_from_thread(
                            app.show_error_message,
                            ErrorMessages.ERROR_GETTING_EXISTING_VM_NAMES_TEMPLATE.format(error=e),
                        )
                        app.call_from_thread(progress_modal.dismiss)
                        return

                    proposed_names = []
                    for i in range(1, count + 1):
                        new_name = f"{base_name}{suffix}{i}" if count > 1 else base_name
                        proposed_names.append(new_name)
                    log_callback(f"INFO: Proposed Name(s): {proposed_names}")

                    conflicting_names = [
                        name for name in proposed_names if name in existing_vm_names
                    ]
                    if conflicting_names:
                        msg = f"The following VM names already exist: {', '.join(conflicting_names)}. Aborting cloning."
                        log_callback(f"ERROR: {msg}")
                        app.call_from_thread(app.show_error_message, msg)
                        app.call_from_thread(progress_modal.dismiss)
                        return
                    else:
                        log_callback("INFO: No Conflicting Name")
                        storage_msg = (
                            "with storage cloning"
                            if clone_storage
                            else "without storage cloning (linked clone)"
                        )
                        log_callback(f"INFO: No Conflicting Name - proceeding {storage_msg}")

                    success_clones, failed_clones = [], []
                    app.call_from_thread(
                        lambda: progress_modal.query_one("#progress-bar").update(total=count)
                    )

                    for i in range(1, count + 1):
                        new_name = f"{base_name}{suffix}{i}" if count > 1 else base_name
                        try:
                            log_callback(f"Cloning '{self.name}' to '{new_name}'...")
                            clone_vm(
                                self.vm,
                                new_name,
                                clone_storage=clone_storage,
                                log_callback=log_callback,
                            )
                            success_clones.append(new_name)
                            log_callback(f"Successfully cloned VM '{self.name}' to '{new_name}'")
                        except Exception as e:
                            failed_clones.append(new_name)
                            log_callback(f"ERROR: Error cloning VM {self.name} to {new_name}: {e}")
                        finally:
                            app.call_from_thread(
                                lambda: progress_modal.query_one("#progress-bar").advance(1)
                            )

                    if success_clones:
                        msg = SuccessMessages.VM_CLONED.format(vm_names=", ".join(success_clones))
                        app.call_from_thread(app.show_success_message, msg)
                        log_callback(msg)
                    if failed_clones:
                        msg = ErrorMessages.VM_CLONE_FAILED_TEMPLATE.format(
                            vm_names=", ".join(failed_clones)
                        )
                        app.call_from_thread(app.show_error_message, msg)
                        log_callback(f"ERROR: {msg}")

                    if success_clones:
                        # app.call_from_thread(app.vm_service.invalidate_domain_cache)
                        app.call_from_thread(app.refresh_vm_list)
                    app.call_from_thread(progress_modal.dismiss)

                finally:
                    # Unsuppress events
                    app.vm_service.unsuppress_vm_events(self.internal_id)
                    # Restart stats worker
                    app.call_from_thread(self.update_stats)

            app.worker_manager.run(do_clone, name=f"clone_{self.name}")

        # def on_confirm(confirmed: bool) -> None:
        #    if confirmed:
        #        self.app.push_screen(AdvancedCloneDialog(), handle_clone_results)

        # self.app.push_screen(ConfirmationDialog(DialogMessages.EXPERIMENTAL), on_confirm)
        self.app.push_screen(AdvancedCloneDialog(), handle_clone_results)

    def _handle_rename_button(self) -> None:
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
                self.app.show_success_message(
                    SuccessMessages.INPUT_SANITIZED.format(
                        original_input=new_name_raw, sanitized_input=new_name
                    )
                )

            if not new_name:
                self.app.show_error_message(ErrorMessages.VM_NAME_EMPTY_AFTER_SANITIZATION)
                return
            if new_name == self.name:
                self.app.show_success_message(SuccessMessages.VM_RENAME_NO_CHANGE)
                return

            def do_rename():
                internal_id = self.internal_id
                self.app.vm_service.suppress_vm_events(internal_id)
                try:
                    rename_vm(self.vm, new_name)
                    self.app.show_success_message(
                        SuccessMessages.VM_RENAMED.format(old_name=self.name, new_name=new_name)
                    )
                    self.app.vm_service.invalidate_domain_cache()
                    self._boot_device_checked = False
                    self.app.refresh_vm_list()
                    logging.info(f"Successfully renamed VM '{self.name}' to '{new_name}'")
                except Exception as e:
                    self.app.show_error_message(
                        ErrorMessages.ERROR_RENAMING_VM_TEMPLATE.format(vm_name=self.name, error=e)
                    )
                finally:
                    self.app.vm_service.unsuppress_vm_events(internal_id)

            def on_confirm_rename(confirmed: bool, delete_snapshots=False) -> None:
                if confirmed:
                    do_rename()
                    if delete_snapshots:
                        self.app.set_timer(0.1, self._refresh_snapshot_tab_async)
                else:
                    self.app.show_success_message(SuccessMessages.VM_RENAME_CANCELLED)

            msg = f"Are you sure you want to rename VM {self.name} to {new_name}?\n\nWarning: This operation involves undefining and redefining the VM."
            self.app.push_screen(ConfirmationDialog(msg), on_confirm_rename)

        self.app.push_screen(RenameVMDialog(current_name=self.name), handle_rename)

    def _handle_configure_button(self) -> None:
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
                        active_uris, uuid, domain=vm_obj, conn=conn_obj, cached_ips=cached_ips
                    )

                    def show_details():
                        loading_modal.dismiss()
                        if not result:
                            self.app.show_error_message(
                                ErrorMessages.VM_NOT_FOUND_ON_ACTIVE_SERVER_TEMPLATE.format(
                                    vm_name=vm_name, uuid=uuid
                                )
                            )
                            return

                        vm_info, domain, conn_for_domain = result

                        def on_detail_modal_dismissed(_=None):
                            self.post_message(VmCardUpdateRequest(self.internal_id))
                            self._perform_tooltip_update()

                        self.app.push_screen(
                            VMDetailModal(
                                vm_name,
                                vm_info,
                                domain,
                                conn_for_domain,
                                self.app.vm_service.invalidate_vm_state_cache,
                            ),
                            on_detail_modal_dismissed,
                        )

                    self.app.call_from_thread(show_details)

                except Exception as e:

                    def show_error(error_instance):
                        loading_modal.dismiss()
                        self.app.show_error_message(
                            ErrorMessages.ERROR_GETTING_VM_DETAILS_TEMPLATE.format(
                                vm_name=vm_name, error=error_instance
                            )
                        )

                    self.app.call_from_thread(show_error, e)

            self.app.worker_manager.run(get_details_worker, name=f"get_details_{uuid}")

        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.ERROR_GETTING_ID_TEMPLATE.format(vm_name=self.name, error=e)
            )

    def _handle_migration_button(self) -> None:
        """Handles the migration button press."""
        if len(self.app.active_uris) < 2:
            self.app.show_error_message(ErrorMessages.SELECT_AT_LEAST_TWO_SERVERS_FOR_MIGRATION)
            return

        selected_vm_uuids = list(self.app.selected_vm_uuids)
        selected_vms = []
        if selected_vm_uuids:
            found_domains_dict = self.app.vm_service.find_domains_by_uuids(
                self.app.active_uris, selected_vm_uuids
            )
            for uuid in selected_vm_uuids:
                domain = found_domains_dict.get(uuid)
                if domain:
                    selected_vms.append(domain)
                else:
                    self.app.show_error_message(
                        ErrorMessages.SELECTED_VM_NOT_FOUND_ON_ACTIVE_SERVER_TEMPLATE.format(
                            uuid=uuid
                        )
                    )
        if not selected_vms:
            selected_vms = [self.vm]

        logging.info(f"Migration initiated for VMs: {[vm.name() for vm in selected_vms]}")

        self.app.initiate_migration(selected_vms)

    @on(Checkbox.Changed, "#vm-select-checkbox")
    def on_vm_select_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handles when the VM selection checkbox is changed."""
        self.is_selected = event.value
        self.post_message(VMSelectionChanged(vm_uuid=self.raw_uuid, is_selected=event.value))

    @on(Click, "#vmname")
    def on_click_vmname(self) -> None:
        """Handle clicks on the VM name part of the VM card."""
        click_time = time.time()
        if click_time - getattr(self, "_last_click_time", 0) < 0.5:
            # Double click detected
            if not self.compact_view:
                self._fetch_xml_and_update_tooltip()
            self._last_click_time = 0
        else:
            self._last_click_time = click_time
            self.post_message(VMNameClicked(vm_name=self.name, vm_uuid=self.raw_uuid))

    def _fetch_xml_and_update_tooltip(self):
        """Fetches the XML configuration and updates the tooltip."""
        if not self.vm:
            return

        def fetch_worker():
            try:
                # Use vm_service to get XML (handles caching)
                self.app.vm_service._get_domain_xml(self.vm, internal_id=self.internal_id)

                # Update tooltip on main thread
                self.app.call_from_thread(self._perform_tooltip_update)
                self.app.call_from_thread(
                    self.app.show_quick_message, f"Info refreshed for {self.name}"
                )

            except Exception as e:
                logging.error(f"Error fetching XML for tooltip: {e}")

        self.app.worker_manager.run(fetch_worker, name=f"xml_tooltip_{self.internal_id}")
