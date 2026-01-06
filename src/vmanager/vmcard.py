"""
VMcard Interface
"""
import subprocess
import logging
import traceback
import datetime
from functools import partial
from urllib.parse import urlparse
import libvirt
from rich.markdown import Markdown as RichMarkdown

from textual.widgets import (
        Static, Button, TabbedContent,
        TabPane, Sparkline, Checkbox
        )
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual import on
from textual.events import Click
from textual.css.query import NoMatches

from events import VMNameClicked, VMSelectionChanged, VmActionRequest
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
        get_status
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
from utils import extract_server_name_from_uri
from constants import (
    ButtonLabels, ButtonIds, TabTitles, StatusText,
    SparklineLabels, ErrorMessages, DialogMessages, VmAction
)

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
        self._tooltip_update_timer = None

    def _get_vm_display_name(self) -> str:
        """Returns the formatted VM name including server name if available."""
        if hasattr(self, 'conn') and self.conn:
            server_display = extract_server_name_from_uri(self.conn.getURI())
            return f"{self.name} ({server_display})"
        return self.name

    def _get_snapshot_tab_title(self) -> str:
        if self.vm:
            try:
                num_snapshots = len(self.vm.listAllSnapshots(0))
                if num_snapshots == 0:
                    return TabTitles.SNAPSHOT + "/" + TabTitles.OVERLAY
                elif num_snapshots > 0 and num_snapshots < 2:
                    return TabTitles.SNAPSHOT + "(" + str(num_snapshots) + ")" + "/" + TabTitles.OVERLAY
                elif num_snapshots > 1:
                    return TabTitles.SNAPSHOTS + "(" + str(num_snapshots) + ")" "/" + TabTitles.OVERLAY
            except libvirt.libvirtError:
                pass # Domain might be transient or invalid

    def update_snapshot_tab_title(self) -> None:
        """Updates the snapshot tab title."""
        try:
            if not self.ui:
                return

            tabbed_content = self.ui.get("tabbed_content")
            if tabbed_content:
                tabbed_content.get_tab("snapshot-tab").update(self._get_snapshot_tab_title())
        except NoMatches:
            logging.warning("Could not find snapshot tab to update title.")

    def _update_webc_status(self) -> None:
        """Updates the web console status indicator."""
        if hasattr(self.app, 'webconsole_manager') and self.vm:
            try:
                uuid = self.vm.UUIDString()
                if self.app.webconsole_manager.is_running(uuid):
                    if self.webc_status_indicator != " (WebC On)":
                        self.webc_status_indicator = " (WebC On)"
                    return
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    pass # The VM is gone, let other parts handle the refresh.
                else:
                    logging.warning(f"Error getting UUID for webconsole status: {e}")

        if self.webc_status_indicator != "":
            self.webc_status_indicator = ""

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
        
        self.ui["top_label"] = Static("", id="top-sparkline-label", classes="sparkline-label")
        self.ui["top_sparkline"] = Sparkline([], id="top-sparkline")
        self.ui["bottom_label"] = Static("", id="bottom-sparkline-label", classes="sparkline-label")
        self.ui["bottom_sparkline"] = Sparkline([], id="bottom-sparkline")

        self.ui["cpu_container"] = Horizontal(self.ui["top_label"], self.ui["top_sparkline"], id="cpu-sparkline-container", classes="sparkline-container")
        self.ui["mem_container"] = Horizontal(self.ui["bottom_label"], self.ui["bottom_sparkline"], id="mem-sparkline-container", classes="sparkline-container")

        self.ui[ButtonIds.START] = Button(ButtonLabels.START, id=ButtonIds.START, variant="success")
        self.ui[ButtonIds.SHUTDOWN] = Button(ButtonLabels.SHUTDOWN, id=ButtonIds.SHUTDOWN, variant="primary")
        self.ui[ButtonIds.STOP] = Button(ButtonLabels.FORCE_OFF, id=ButtonIds.STOP, variant="error")
        self.ui[ButtonIds.PAUSE] = Button(ButtonLabels.PAUSE, id=ButtonIds.PAUSE, variant="primary")
        self.ui[ButtonIds.RESUME] = Button(ButtonLabels.RESUME, id=ButtonIds.RESUME, variant="success")
        self.ui[ButtonIds.CONFIGURE_BUTTON] = Button(ButtonLabels.CONFIGURE, id=ButtonIds.CONFIGURE_BUTTON, variant="primary")
        self.ui[ButtonIds.WEB_CONSOLE] = Button(ButtonLabels.WEB_CONSOLE, id=ButtonIds.WEB_CONSOLE, variant="default")
        self.ui[ButtonIds.CONNECT] = Button(ButtonLabels.CONNECT, id=ButtonIds.CONNECT, variant="default")
        
        self.ui[ButtonIds.SNAPSHOT_TAKE] = Button(ButtonLabels.SNAPSHOT, id=ButtonIds.SNAPSHOT_TAKE, variant="primary")
        self.ui[ButtonIds.SNAPSHOT_RESTORE] = Button(ButtonLabels.RESTORE_SNAPSHOT, id=ButtonIds.SNAPSHOT_RESTORE, variant="primary")
        self.ui[ButtonIds.SNAPSHOT_DELETE] = Button(ButtonLabels.DELETE_SNAPSHOT, id=ButtonIds.SNAPSHOT_DELETE, variant="error")

        self.ui[ButtonIds.DELETE] = Button(ButtonLabels.DELETE, id=ButtonIds.DELETE, variant="success", classes="delete-button")
        self.ui[ButtonIds.CLONE] = Button(ButtonLabels.CLONE, id=ButtonIds.CLONE, classes="clone-button")
        self.ui[ButtonIds.MIGRATION] = Button(ButtonLabels.MIGRATION, id=ButtonIds.MIGRATION, variant="primary", classes="migration-button")
        self.ui[ButtonIds.XML] = Button(ButtonLabels.VIEW_XML, id=ButtonIds.XML)
        self.ui[ButtonIds.RENAME_BUTTON] = Button(ButtonLabels.RENAME, id=ButtonIds.RENAME_BUTTON, variant="primary", classes="rename-button")

        self.ui[ButtonIds.CREATE_OVERLAY] = Button(ButtonLabels.CREATE_OVERLAY, id=ButtonIds.CREATE_OVERLAY, variant="primary")
        self.ui[ButtonIds.COMMIT_DISK] = Button(ButtonLabels.COMMIT_DISK, id=ButtonIds.COMMIT_DISK, variant="error")
        self.ui[ButtonIds.DISCARD_OVERLAY] = Button(ButtonLabels.DISCARD_OVERLAY, id=ButtonIds.DISCARD_OVERLAY, variant="error")
        self.ui[ButtonIds.SNAP_OVERLAY_HELP] = Button(ButtonLabels.SNAP_OVERLAY_HELP, id=ButtonIds.SNAP_OVERLAY_HELP, variant="default")

        self.ui["tabbed_content"] = TabbedContent(id="button-container")

        with Vertical(id="info-container"):
            with Horizontal(id="vm-header-row"):
                yield self.ui["checkbox"]
                with Vertical():
                    yield self.ui["vmname"]
                    yield self.ui["status"]
            
            yield self.ui["cpu_container"]
            yield self.ui["mem_container"]

            with self.ui["tabbed_content"]:
                with TabPane(TabTitles.MANAGE, id="manage-tab"):
                    with Horizontal():
                        with Vertical():
                            yield self.ui[ButtonIds.START]
                            yield self.ui[ButtonIds.SHUTDOWN]
                            yield self.ui[ButtonIds.STOP]
                            yield self.ui[ButtonIds.PAUSE]
                            yield self.ui[ButtonIds.RESUME]
                        with Vertical():
                            yield self.ui[ButtonIds.CONFIGURE_BUTTON]
                            yield self.ui[ButtonIds.WEB_CONSOLE]
                            yield self.ui[ButtonIds.CONNECT]
                with TabPane(self._get_snapshot_tab_title(), id="snapshot-tab"):
                    with Horizontal():
                        with Vertical():
                            yield self.ui[ButtonIds.SNAPSHOT_TAKE]
                            yield self.ui[ButtonIds.SNAPSHOT_RESTORE]
                            yield self.ui[ButtonIds.SNAPSHOT_DELETE]
                        with Vertical():
                            yield self.ui[ButtonIds.CREATE_OVERLAY]
                            yield self.ui[ButtonIds.COMMIT_DISK]
                            yield self.ui[ButtonIds.DISCARD_OVERLAY]
                            yield self.ui[ButtonIds.SNAP_OVERLAY_HELP]
                with TabPane(TabTitles.OTHER, id="special-tab"):
                    with Horizontal():
                        with Vertical():
                            yield self.ui[ButtonIds.DELETE]
                            yield Static(classes="button-separator")
                            yield self.ui[ButtonIds.CLONE]
                            yield self.ui[ButtonIds.MIGRATION]
                        with Vertical():
                            yield self.ui[ButtonIds.XML]
                            yield Static(classes="button-separator")
                            yield self.ui[ButtonIds.RENAME_BUTTON]

    def _update_tooltip(self) -> None:
        """Schedules a tooltip update to debounce frequent calls."""
        if self._tooltip_update_timer:
            self._tooltip_update_timer.stop()
        self._tooltip_update_timer = self.set_timer(0.2, self._perform_tooltip_update)

    def _is_remote_server(self) -> bool:
        """Checks if the VM is on a remote server."""
        if not self.conn:
            return False
        try:
            uri = self.conn.getURI()
            parsed = urlparse(uri)
            return parsed.hostname not in (None, "localhost", "127.0.0.1") and parsed.scheme == "qemu+ssh"
        except Exception:
            return False

    def _perform_tooltip_update(self) -> None:
        """Updates the tooltip for the VM name using Markdown."""
        if not self.ui or "vmname" not in self.ui:
            return
        
        if self._is_remote_server():
            self.ui["vmname"].tooltip = None
            return

        try:
            uuid = self.vm.UUIDString() if self.vm else "Unknown"
        except libvirt.libvirtError:
            uuid = "Unknown"

        hypervisor = "Unknown"
        if self.conn:
            hypervisor = extract_server_name_from_uri(self.conn.getURI())

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

        tooltip_md = (
            f"`{uuid}`  \n"
            f"**Hypervisor:** {hypervisor}  \n"
            f"**Status:** {self.status}  \n"
            f"**IP:** {ip_display}  \n"
            f"**Boot:** {self.boot_device or 'N/A'}  \n"
            f"**VCPUs:** {self.cpu}{cpu_model_display}  \n"
            f"**Memory:** {mem_display}"
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
        self.update_sparkline_display()
        self._update_tooltip()

        if self.vm:
            try:
                uuid = self.vm.UUIDString()
                if uuid in self.app.sparkline_data:
                    self.update_sparkline_display()
            except (libvirt.libvirtError, NoMatches):
                pass

        self.update_stats()
        # Timer is now managed within update_stats for dynamic intervals
        # self.timer = self.set_interval(stats_interval, self.update_stats)

    def watch_stats_view_mode(self, old_mode: str, new_mode: str) -> None:
        """Update sparklines when view mode changes."""
        if not self.ui:
            return
        self.update_sparkline_display()

    def update_sparkline_display(self) -> None:
        """Updates the labels and data of the sparklines based on the current view mode."""
        top_label = self.ui.get("top_label")
        bottom_label = self.ui.get("bottom_label")
        top_sparkline = self.ui.get("top_sparkline")
        bottom_sparkline = self.ui.get("bottom_sparkline")

        if not all([top_label, bottom_label, top_sparkline, bottom_sparkline]):
            return

        uuid = self.vm.UUIDString() if self.vm else None

        # Determine data source
        storage = {}
        if uuid and hasattr(self.app, 'sparkline_data') and uuid in self.app.sparkline_data:
            storage = self.app.sparkline_data[uuid]

        if self.stats_view_mode == "resources":
            mem_gb = round(self.memory / 1024, 1)
            top_text = SparklineLabels.VCPU.format(cpu=self.cpu)
            bottom_text = SparklineLabels.MEMORY_GB.format(mem=mem_gb)
            top_data = list(storage.get("cpu", []))
            bottom_data = list(storage.get("mem", []))
        else: # io mode
            disk_read_mb = self.latest_disk_read / 1024
            disk_write_mb = self.latest_disk_write / 1024
            net_rx_mb = self.latest_net_rx / 1024
            net_tx_mb = self.latest_net_tx / 1024

            top_text = SparklineLabels.DISK_RW.format(read=disk_read_mb, write=disk_write_mb)
            bottom_text = SparklineLabels.NET_RX_TX.format(rx=net_rx_mb, tx=net_tx_mb)
            top_data = list(storage.get("disk", []))
            bottom_data = list(storage.get("net", []))

        # Update UI
        top_label.update(top_text)
        bottom_label.update(bottom_text)

        # Only update data if we have storage (avoids clearing if not needed, though empty list is fine)
        # Actually existing logic updated data even if empty, which clears the sparkline.
        top_sparkline.data = top_data
        bottom_sparkline.data = bottom_data

    def watch_name(self, value: str) -> None:
        """Called when name changes."""
        if self.ui:
            vmname_widget = self.ui.get("vmname")
            if vmname_widget:
                vmname_widget.update(self._get_vm_display_name())

    def watch_cpu(self, value: int) -> None:
        """Called when cpu count changes."""
        self._update_tooltip()

    def watch_memory(self, value: int) -> None:
        """Called when memory changes."""
        self._update_tooltip()

    def watch_ip_addresses(self, value: list) -> None:
        """Called when IP addresses change."""
        self._update_tooltip()

    def watch_boot_device(self, value: str) -> None:
        """Called when boot device changes."""
        self._update_tooltip()

    def watch_cpu_model(self, value: str) -> None:
        """Called when cpu_model changes."""
        self._update_tooltip()

    def watch_graphics_type(self, old_value: str, new_value: str) -> None:
        """Called when graphics_type changes."""
        self.update_button_layout()

    def watch_status(self, old_value: str, new_value: str) -> None:
        """Called when status changes."""
        if not self.ui:
            return
        self._update_status_styling()
        self.update_button_layout()
        self._update_tooltip()

        status_widget = self.ui.get("status")
        if status_widget:
            status_widget.update(f"Status: {new_value}{self.webc_status_indicator}")

    def watch_server_border_color(self, old_color: str, new_color: str) -> None:
        """Called when server_border_color changes."""
        self.styles.border = ("solid", new_color)

    def on_unmount(self) -> None:
        """Stop the timer and cancel any running stat workers when the widget is removed."""
        if self.timer:
            self.timer.stop()
        if self._tooltip_update_timer:
            self._tooltip_update_timer.stop()
        if self.vm:
            try:
                uuid = self.vm.UUIDString()
                self.app.worker_manager.cancel(f"update_stats_{uuid}")
            except libvirt.libvirtError:
                pass

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
        if not self.vm:
            return

        # Cancel previous timer if it exists to prevent accumulation
        if self.timer:
            self.timer.stop()

        # Schedule next update
        interval = self.app.config.get('STATS_INTERVAL', 5)
        self.timer = self.set_timer(interval, self.update_stats)

        try:
            uuid = self.vm.UUIDString()
        except libvirt.libvirtError:
            if self.timer:
                self.timer.stop()
            return

        # Capture reactive values on main thread to avoid unsafe access in worker
        current_status = self.status
        current_boot_device = self.boot_device
        current_cpu_model = self.cpu_model
        current_graphics_type = self.graphics_type
        is_remote = self._is_remote_server()

        def update_worker():
            try:
                if is_remote:
                    # Minimal fetch for remote servers: ONLY status
                    try:
                        state, _ = self.vm.state()
                        status = get_status(self.vm, state=state)
                        stats = {
                            "status": status,
                            "cpu_percent": 0.0,
                            "mem_percent": 0.0,
                            "disk_read_kbps": 0.0,
                            "disk_write_kbps": 0.0,
                            "net_rx_kbps": 0.0,
                            "net_tx_kbps": 0.0
                        }
                    except libvirt.libvirtError:
                        stats = None
                else:
                    stats = self.app.vm_service.get_vm_runtime_stats(self.vm)

                # Update info from cache if XML has been fetched (e.g. via Configure)
                vm_cache = self.app.vm_service._vm_data_cache.get(uuid, {})
                xml_content = vm_cache.get('xml')
                boot_dev = current_boot_device
                cpu_model = current_cpu_model
                graphics_type = current_graphics_type


                if not getattr(self, "_boot_device_checked", False):
                    if xml_content:
                        root = _parse_domain_xml(xml_content)
                        if root is not None:
                            # Always update from cache if available
                            boot_info = get_boot_info(self.conn, root)
                            if boot_info['order']:
                                boot_dev = boot_info['order'][0]
                            cpu_model = get_vm_cpu_details(root) or ""
                            graphics_info = get_vm_graphics_info(root)
                            graphics_type = graphics_info.get("type")
                            self._boot_device_checked = True
                    elif not is_remote:
                        # Fallback: Fetch XML once if not in cache to get static info like Boot/CPU model
                        # Skipped for remote servers
                        try:
                            _, root = _get_domain_root(self.vm)
                            if root is not None:
                                boot_info = get_boot_info(self.conn, root)
                                if boot_info['order']:
                                    boot_dev = boot_info['order'][0]
                                cpu_model = get_vm_cpu_details(root) or ""
                                graphics_info = get_vm_graphics_info(root)
                                graphics_type = graphics_info.get("type")

                                # Opportunistically populate cache to avoid future fetches
                                # This is safe because we are in a worker thread
                                self.app.vm_service._get_domain_info_and_xml(self.vm)
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

                    if hasattr(self.app, "sparkline_data") and uuid in self.app.sparkline_data:
                        storage = self.app.sparkline_data[uuid]

                        def update_history(key, value):
                            history = storage.get(key, [])
                            history.append(value)
                            if len(history) > 20:
                                history.pop(0)
                            storage[key] = history

                        update_history("cpu", stats["cpu_percent"])
                        update_history("mem", stats["mem_percent"])
                        update_history("disk", self.latest_disk_read + self.latest_disk_write)
                        update_history("net", self.latest_net_rx + self.latest_net_tx)

                        self.update_sparkline_display()

                self.app.call_from_thread(apply_stats_to_ui)

            except libvirt.libvirtError as e:
                error_code = e.get_error_code()
                if error_code == libvirt.VIR_ERR_NO_DOMAIN:
                    if self.timer:
                        self.app.call_from_thread(self.timer.stop)
                    self.app.call_from_thread(self.app.refresh_vm_list)
                elif error_code in [libvirt.VIR_ERR_SYSTEM_ERROR, libvirt.VIR_ERR_RPC, libvirt.VIR_ERR_NO_CONNECT, libvirt.VIR_ERR_INVALID_CONN]:
                    logging.warning(f"Connection error for {self.name}: {e}. Triggering refresh.")
                    self.app.call_from_thread(self.app.refresh_vm_list, force=True)
                else:
                    logging.warning(f"Libvirt error during stat update for {self.name}: {e}")
            except Exception as e:
                logging.error(f"Unexpected error in update_stats worker for {self.name}: {e}", exc_info=True)

        self.app.worker_manager.run(update_worker, name=f"update_stats_{uuid}")

    @on(Click, "#top-sparkline, #bottom-sparkline")
    def toggle_stats_view(self) -> None:
        """Toggle between resource and I/O stat views."""
        if self.status == StatusText.RUNNING:
             self.stats_view_mode = "io" if self.stats_view_mode == "resources" else "resources"


    def update_button_layout(self):
        """Update the button layout based on current VM status."""
        rename_button = self.ui.get(ButtonIds.RENAME_BUTTON)
        if not rename_button: return # Assume if one is missing, others might be too or we are not cached yet.

        is_loading = self.status == StatusText.LOADING
        is_stopped = self.status == StatusText.STOPPED
        is_running = self.status == StatusText.RUNNING
        is_paused = self.status == StatusText.PAUSED
        has_snapshots = False
        try:
            if self.vm and not is_loading:
                has_snapshots = len(self.vm.listAllSnapshots(0)) > 0
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                self.app.refresh_vm_list()
                return
            logging.warning(f"Could not get snapshot count for {self.name}: {e}")

        self.ui[ButtonIds.START].display = is_stopped
        self.ui[ButtonIds.SHUTDOWN].display = is_running
        self.ui[ButtonIds.STOP].display = is_running or is_paused
        self.ui[ButtonIds.DELETE].display = is_running or is_paused or is_stopped
        self.ui[ButtonIds.CLONE].display = is_stopped
        self.ui[ButtonIds.MIGRATION].display = not is_loading
        self.ui[ButtonIds.RENAME_BUTTON].display = is_stopped
        self.ui[ButtonIds.PAUSE].display = is_running
        self.ui[ButtonIds.RESUME].display = is_paused
        self.ui[ButtonIds.CONNECT].display = (is_running or is_paused) and self.app.virt_viewer_available
        logging.info(f"graphics_type: {self.graphics_type}")
        # TOFIX
        #self.ui[ButtonIds.WEB_CONSOLE].display = (is_running or is_paused) and self.graphics_type == "vnc" and self.app.websockify_available and self.app.novnc_available
        self.ui[ButtonIds.WEB_CONSOLE].display = (is_running or is_paused)# and self.app.websockify_available and self.app.novnc_available
        self.ui[ButtonIds.SNAPSHOT_RESTORE].display = has_snapshots
        self.ui[ButtonIds.SNAPSHOT_DELETE].display = has_snapshots
        self.ui[ButtonIds.CONFIGURE_BUTTON].display = not is_loading

        has_overlay_disk = False
        try:
            if self.vm and not is_loading:
                uuid = self.vm.UUIDString()
                vm_cache = self.app.vm_service._vm_data_cache.get(uuid, {})
                if vm_cache.get('xml'):
                    has_overlay_disk = has_overlays(self.vm)
        except:
            pass

        self.ui[ButtonIds.COMMIT_DISK].display = is_running and has_overlay_disk
        self.ui[ButtonIds.DISCARD_OVERLAY].display = is_stopped and has_overlay_disk
        self.ui[ButtonIds.CREATE_OVERLAY].display = is_stopped and not has_overlay_disk
        self.ui[ButtonIds.SNAP_OVERLAY_HELP].display = not is_loading
        self.ui[ButtonIds.SNAPSHOT_TAKE].display = not is_loading

        self.ui["cpu_container"].display = not is_stopped and not is_loading
        self.ui["mem_container"].display = not is_stopped and not is_loading

        xml_button = self.ui[ButtonIds.XML]
        if is_stopped:
            xml_button.label = "Edit XML"
            self.stats_view_mode = "resources" # Reset to default when stopped
        else:
            xml_button.label = "View XML"
        xml_button.display = not is_loading

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
            self.post_message(VmActionRequest(self.vm.UUIDString(), VmAction.START))
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
            disks = get_vm_disks(self.vm)
            # Filter for actual disks (exclude cdroms, etc)
            valid_disks = [d['path'] for d in disks if d.get('device_type') == 'disk']

            if not valid_disks:
                self.app.show_error_message("No suitable disks found for overlay.")
                return

            target_disk = valid_disks[0]

            vm_name = self.vm.name()
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
                    self.app.show_success_message(f"Overlay '{overlay_name}' created and attached.")
                    self.app.vm_service.invalidate_vm_cache(self.vm.UUIDString())
                    self._boot_device_checked = False
                    self.app.refresh_vm_list() # To update details
                    self.update_button_layout()
                except Exception as e:
                    self.app.show_error_message(f"Error creating overlay: {e}")

            self.app.push_screen(InputModal("Enter name for new overlay volume:", default_name), on_name_input)

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
                            self.app.show_success_message(f"Overlay for '{target_disk}' discarded and reverted to base image.")
                            self.app.vm_service.invalidate_vm_cache(self.vm.UUIDString())
                            self._boot_device_checked = False
                            self.app.refresh_vm_list()
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
        if self.vm.isActive():
            self.post_message(VmActionRequest(self.vm.UUIDString(), VmAction.STOP))

    def _handle_stop_button(self, event: Button.Pressed) -> None:
        """Handles the stop button press."""
        logging.info(f"Attempting to stop VM: {self.name}")

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            if self.vm.isActive():
                self.post_message(VmActionRequest(self.vm.UUIDString(), VmAction.FORCE_OFF))

        message = f"{ErrorMessages.HARD_STOP_WARNING}\nAre you sure you want to stop '{self.name}'?"
        self.app.push_screen(ConfirmationDialog(message), on_confirm)

    def _handle_pause_button(self, event: Button.Pressed) -> None:
        """Handles the pause button press."""
        logging.info(f"Attempting to pause VM: {self.name}")
        if self.vm.isActive():
            self.post_message(VmActionRequest(self.vm.UUIDString(), VmAction.PAUSE))

    def _handle_resume_button(self, event: Button.Pressed) -> None:
        """Handles the resume button press."""
        logging.info(f"Attempting to resume VM: {self.name}")
        self.post_message(VmActionRequest(self.vm.UUIDString(), VmAction.RESUME))

    def _handle_xml_button(self, event: Button.Pressed) -> None:
        """Handles the xml button press."""
        try:
            original_xml = self.vm.XMLDesc(0)
            is_stopped = self.status == StatusText.STOPPED

            def handle_xml_modal_result(modified_xml: str | None):
                if modified_xml and is_stopped:
                    if original_xml.strip() != modified_xml.strip():
                        try:
                            conn = self.vm.connect()
                            conn.defineXML(modified_xml)
                            self.app.show_success_message(f"VM '{self.name}' configuration updated successfully.")
                            logging.info(f"Successfully updated XML for VM: {self.name}")
                            self.app.vm_service.invalidate_vm_cache(self.vm.UUIDString())
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
            self.app.show_error_message(f"Error getting XML for VM {self.name}: {e}")
        except Exception as e:
            self.app.show_error_message(f"An unexpected error occurred: {e}")
            logging.error(f"Unexpected error handling XML button: {traceback.format_exc()}")

    def _handle_connect_button(self, event: Button.Pressed) -> None:
        """Handles the connect button press by running virt-viewer in a worker."""
        logging.info(f"Attempting to connect to VM: {self.name}")
        if not hasattr(self, 'conn') or not self.conn:
            self.app.show_error_message("Connection info not available for this VM.")
            return

        def do_connect() -> None:
            try:
                uri = self.conn.getURI()
                domain_name = self.vm.name()

                command = ["virt-viewer", "--connect", uri, domain_name]
                logging.info(f"Executing command: {' '.join(command)}")

                result = subprocess.run(command, capture_output=True, text=True, check=False)

                if result.returncode != 0:
                    error_message = result.stderr.strip()
                    logging.error(f"virt-viewer failed for {domain_name}: {error_message}")
                    if "cannot open display" in error_message:
                        self.app.call_from_thread(
                            self.app.show_error_message, 
                            ErrorMessages.CANNOT_OPEN_DISPLAY
                        )
                    else:
                        self.app.call_from_thread(
                            self.app.show_error_message,
                            f"virt-viewer failed: {error_message}"
                        )
                else:
                    logging.info(f"virt-viewer for {domain_name} closed.")

            except FileNotFoundError:
                self.app.call_from_thread(
                    self.app.show_error_message,
                    ErrorMessages.VIRT_VIEWER_NOT_FOUND
                )
            except libvirt.libvirtError as e:
                self.app.call_from_thread(
                    self.app.show_error_message,
                    f"Error getting VM details for {self.name}: {e}"
                )
            except Exception as e:
                logging.error(f"An unexpected error occurred during connect: {e}", exc_info=True)
                self.app.call_from_thread(
                    self.app.show_error_message,
                    "An unexpected error occurred while trying to connect."
                )

        self.app.worker_manager.run(do_connect, name=f"virt_viewer_{self.name}")

    def _handle_web_console_button(self, event: Button.Pressed) -> None:
        """Handles the web console button press by opening a config dialog."""
        worker = partial(self.app.webconsole_manager.start_console, self.vm, self.conn)

        try:
            uuid = self.vm.UUIDString()
            if self.app.webconsole_manager.is_running(uuid):
                self.app.worker_manager.run(
                    worker, name=f"show_console_{self.vm.name()}"
                )
                return
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                self.app.refresh_vm_list()
                return
            self.app.show_error_message(f"Error checking web console status for {self.name}: {e}")
            return

        is_remote = self.app.webconsole_manager.is_remote_connection(self.conn.getURI())

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
            if result:
                name = result["name"]
                description = result["description"]
                quiesce = result.get("quiesce", False)
                try:
                    create_vm_snapshot(self.vm, name, description, quiesce=quiesce)
                    self.app.vm_service.invalidate_vm_cache(self.vm.UUIDString())
                    self.update_button_layout()
                    self.app.show_success_message(f"Snapshot '{name}' created successfully.")
                except Exception as e:
                    self.app.show_error_message(f"Snapshot error for {self.name}: {e}")

            self.update_snapshot_tab_title()
        self.app.push_screen(SnapshotNameDialog(self.vm), handle_snapshot_result)

    def _handle_snapshot_restore_button(self, event: Button.Pressed) -> None:
        """Handles the snapshot restore button press."""
        logging.info(f"Attempting to restore snapshot for VM: {self.name}")
        snapshots_info = get_vm_snapshots(self.vm)
        if not snapshots_info:
            self.app.show_error_message("No snapshots to restore.")
            return

        def restore_snapshot(snapshot_name: str | None) -> None:
            if snapshot_name:
                try:
                    restore_vm_snapshot(self.vm, snapshot_name)
                    self.app.vm_service.invalidate_vm_cache(self.vm.UUIDString())
                    self._boot_device_checked = False
                    self.app.show_success_message(f"Restored to snapshot '{snapshot_name}' successfully.")
                    logging.info(f"Successfully restored snapshot '{snapshot_name}' for VM: {self.name}")
                except Exception as e:
                    self.app.show_error_message(f"Error on VM {self.name} during 'snapshot restore': {e}")

        self.app.push_screen(SelectSnapshotDialog(snapshots_info, "Select snapshot to restore"), restore_snapshot)

    def _handle_snapshot_delete_button(self, event: Button.Pressed) -> None:
        """Handles the snapshot delete button press."""
        logging.info(f"Attempting to delete snapshot for VM: {self.name}")
        snapshots_info = get_vm_snapshots(self.vm)
        if not snapshots_info:
            self.app.show_error_message("No snapshots to delete.")
            return

        def delete_snapshot(snapshot_name: str | None) -> None:
            if snapshot_name:
                def on_confirm(confirmed: bool) -> None:
                    if confirmed:
                        try:
                            delete_vm_snapshot(self.vm, snapshot_name)
                            self.app.show_success_message(f"Snapshot '{snapshot_name}' deleted successfully.")
                            self.app.vm_service.invalidate_vm_cache(self.vm.UUIDString())
                            self.update_button_layout()
                            logging.info(f"Successfully deleted snapshot '{snapshot_name}' for VM: {self.name}")
                        except Exception as e:
                            self.app.show_error_message(f"Error on VM {self.name} during 'snapshot delete': {e}")

                    self.update_snapshot_tab_title()
                self.app.push_screen(
                    ConfirmationDialog(DialogMessages.DELETE_SNAPSHOT_CONFIRMATION.format(name=snapshot_name)), on_confirm
                )

        self.app.push_screen(SelectSnapshotDialog(snapshots_info, "Select snapshot to delete"), delete_snapshot)

    def _handle_delete_button(self, event: Button.Pressed) -> None:
        """Handles the delete button press."""
        logging.info(f"Attempting to delete VM: {self.name}")

        def on_confirm(result: tuple[bool, bool]) -> None:
            confirmed, delete_storage = result
            if not confirmed:
                return
            self.post_message(VmActionRequest(self.vm.UUIDString(), VmAction.DELETE, delete_storage=delete_storage))

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

            progress_modal = ProgressModal(title=f"Cloning {self.name}...")
            app.push_screen(progress_modal)

            def log_callback(message: str):
                app.call_from_thread(progress_modal.add_log, message)

            def do_clone() -> None:
                log_callback(f"Attempting to clone VM: {self.name}")
                existing_vm_names = set()
                try:
                    all_domains = self.conn.listAllDomains(0)
                    for domain in all_domains:
                        existing_vm_names.add(domain.name())
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

                success_clones, failed_clones = [], []
                app.call_from_thread(lambda: progress_modal.query_one("#progress-bar").update(total=count))

                for i in range(1, count + 1):
                    new_name = f"{base_name}{suffix}{i}" if count > 1 else base_name
                    try:
                        log_callback(f"Cloning '{self.name}' to '{new_name}'...")
                        clone_vm(self.vm, new_name, log_callback=log_callback)
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
                    app.call_from_thread(app.vm_service.invalidate_domain_cache)
                    app.call_from_thread(app.refresh_vm_list)
                app.call_from_thread(progress_modal.dismiss)

            app.worker_manager.run(do_clone, name=f"clone_{self.name}")

        app.push_screen(AdvancedCloneDialog(), handle_clone_results)

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
                self.app.show_success_message(f"Input sanitized: '{new_name_raw}' changed to '{new_name}'")
            
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
                    self.app.show_error_message(f"Error renaming VM {self.name}: {e}")

            num_snapshots = self.vm.snapshotNum(0)
            if num_snapshots > 0:
                def on_confirm_delete(confirmed: bool) -> None:
                    if confirmed:
                        do_rename(delete_snapshots=True)
                    else:
                        self.app.show_success_message("VM rename cancelled.")

                    self.update_snapshot_tab_title()
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
            
            uuid = self.vm.UUIDString()
            vm_name = self.name
            active_uris = list(self.app.active_uris)

            loading_modal = LoadingModal()
            self.app.push_screen(loading_modal)

            def get_details_worker():
                try:
                    result = self.app.vm_service.get_vm_details(active_uris, uuid)
                    
                    def show_details():
                        loading_modal.dismiss()
                        if not result:
                            self.app.show_error_message(f"VM {vm_name} with UUID {uuid} not found on any active server.")
                            return
                        
                        vm_info, domain, conn_for_domain = result
                        
                        def on_detail_modal_dismissed(res):
                            self.app.refresh_vm_list(force=True)

                        self.app.push_screen(
                            VMDetailModal(vm_name, vm_info, domain, conn_for_domain, self.app.vm_service.invalidate_vm_cache),
                            on_detail_modal_dismissed
                        )
                    
                    self.app.call_from_thread(show_details)

                except Exception as e:
                    def show_error():
                        loading_modal.dismiss()
                        self.app.show_error_message(f"Error getting details for {vm_name}: {e}")
                    self.app.call_from_thread(show_error)

            self.app.worker_manager.run(get_details_worker, name=f"get_details_{uuid}")

        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                self.app.refresh_vm_list()
                return
            self.app.show_error_message(f"Error getting UUID for {self.name}: {e}")

    def _handle_migration_button(self, event: Button.Pressed) -> None:
        """Handles the migration button press."""
        if len(self.app.active_uris) < 2:
            self.app.show_error_message("Please select at least two servers in 'Select Servers' to enable migration.")
            return

        selected_vm_uuids = self.app.selected_vm_uuids
        selected_vms = []
        if selected_vm_uuids:
            for uuid in selected_vm_uuids:
                found_domain = None
                for uri in self.app.active_uris:
                    conn = self.app.vm_service.connect(uri)
                    if conn:
                        try:
                            domain = conn.lookupByUUIDString(uuid)
                            selected_vms.append(domain)
                            found_domain = True
                            break
                        except libvirt.libvirtError:
                            continue
                if not found_domain:
                    self.app.show_error_message(f"Selected VM with UUID {uuid} not found on any active server.")

        if not selected_vms:
            selected_vms = [self.vm]

        logging.info(f"Migration initiated for VMs: {[vm.name() for vm in selected_vms]}")

        source_conns = {vm.connect().getURI() for vm in selected_vms}
        if len(source_conns) > 1:
            self.app.show_error_message("Cannot migrate VMs from different source hosts at the same time.")
            return

        active_vms = [vm for vm in selected_vms if vm.isActive()]
        is_live = len(active_vms) > 0
        if is_live and len(active_vms) < len(selected_vms):
            self.app.show_error_message("Cannot migrate running/paused and stopped VMs at the same time.")
            return

        active_uris = self.app.vm_service.get_all_uris()
        all_connections = {uri: self.app.vm_service.get_connection(uri) for uri in active_uris if self.app.vm_service.get_connection(uri)}

        source_uri = selected_vms[0].connect().getURI()
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
        self.post_message(VMSelectionChanged(vm_uuid=self.vm.UUIDString(), is_selected=event.value))

    @on(Click, "#vmname")
    def on_click_vmname(self) -> None:
        """Handle clicks on the VM name part of the VM card."""
        self.post_message(VMNameClicked(vm_name=self.name, vm_uuid=self.vm.UUIDString()))

    @on(Click, "#cpu-mem-info")
    def on_click_cpu_mem_info(self) -> None:
        """Handle clicks on the CPU/Memory info part of the VM card."""
        self.post_message(VMNameClicked(vm_name=self.name, vm_uuid=self.vm.UUIDString()))
