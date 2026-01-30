"""
Main interface
"""
import os
import sys
import re
from threading import RLock
from concurrent.futures import ThreadPoolExecutor
import logging
import argparse
from collections import deque
from typing import Any, Callable
import libvirt

from textual.app import App, ComposeResult, on
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.binding import Binding
from textual.widgets import (
    Button, Footer, Header, Label, Link, Static, Collapsible,
)
from textual.worker import Worker, WorkerState

from .config import load_config, save_config, get_log_path
from .constants import (
        WarningMessages,
        SuccessMessages,
        VmAction, VmStatus, ButtonLabels, BindingDescriptions,
        ErrorMessages, AppInfo, StatusText, ServerPallette, QuickMessages, ProgressMessages,
        )
from .events import VmActionRequest, VMSelectionChanged, VmCardUpdateRequest #,VMNameClicked
from .libvirt_error_handler import register_error_handler
from .modals.bulk_modals import BulkActionModal
from .modals.config_modal import ConfigModal
from .modals.log_modal import LogModal
from .modals.server_modals import ServerManagementModal
from .modals.server_prefs_modals import ServerPrefModal
from .modals.select_server_modals import SelectOneServerModal, SelectServerModal
from .modals.selection_modals import PatternSelectModal
from .modals.capabilities_modal import CapabilitiesTreeModal
from .modals.cache_stats_modal import CacheStatsModal
from .modals.utils_modals import (
    show_error_message,
    show_success_message,
    show_warning_message,
    show_quick_message,
    show_in_progress_message,
    LoadingModal,
    ConfirmationDialog,
)
from .modals.vmanager_modals import (
    FilterModal,
)
from .modals.virsh_modals import VirshShellScreen
from .modals.provisioning_modals import InstallVMModal
from .modals.host_dashboard_modal import HostDashboardModal
from .utils import (
    check_novnc_path,
    check_r_viewer,
    check_websockify,
    generate_webconsole_keys_if_needed,
    get_server_color_cached,
    setup_cache_monitoring,
    setup_logging
)
from .vm_queries import (
    get_status,
)
from .libvirt_utils import (
        get_internal_id, get_host_resources,
        get_total_vm_allocation, get_active_vm_allocation
        )
from .vm_service import VMService
from .vmcard import VMCard
from .vmcard_pool import VMCardPool
from .webconsole_manager import WebConsoleManager

setup_logging()

class WorkerManager:
    """A class to manage and track Textual workers."""

    def __init__(self, app: App):
        self.app = app
        self.workers: dict[str, Worker] = {}
        self._lock = RLock()

    def run(
        self,
        callable: Callable[..., Any],
        *,
        name: str,
        group: str | None = None,
        exclusive: bool = True,
        thread: bool = True,
        description: str | None = None,
        exit_on_error: bool = True,
    ) -> Worker | None:
        """
        Runs and tracks a worker, preventing overlaps for workers with the same name.
        """
        if exclusive and self.is_running(name):
            #logging.warning(f"Worker '{name}' is already running. Skipping new run.")
            return None
        with self._lock:
            self._cleanup_finished_workers()
            if exclusive and name in self.workers:
                logging.debug(f"Worker '{name}' is already running. Skipping new run.")
                return None

            worker = self.app.run_worker(
                callable,
                name=name,
                thread=thread,
                group=group,
                exclusive=exclusive,
                description=description,
                exit_on_error=exit_on_error,
            )
            self.workers[name] = worker

        return worker

    def _cleanup_finished_workers(self) -> None:
        """Removes finished workers from the tracking dictionary."""
        finished_worker_names = [
            name for name, worker in self.workers.items()
            if worker.state in (WorkerState.SUCCESS, WorkerState.CANCELLED, WorkerState.ERROR)
        ]
        for name in finished_worker_names:
            del self.workers[name]

    def is_running(self, name: str) -> bool:
        """Check if a worker with the given name is currently running."""
        with self._lock:
            self._cleanup_finished_workers()
            return name in self.workers

    def cancel(self, name: str) -> Worker | None:
        """
        Cancel a running worker by name.

        Returns the cancelled Worker if found, None otherwise.
        """
        with self._lock:
            worker = self.workers.pop(name, None)

        if worker:
            worker.cancel()
            return worker
        return None

    def cancel_all(self) -> None:
        """Cancel all running workers."""
        with self._lock:
            workers = list(self.workers.values())
            self.workers.clear()

        for worker in workers:
            worker.cancel()

class VMManagerTUI(App):
    """A Textual application to manage VMs."""

    BINDINGS = [
        Binding(key="v", action="view_log", description=BindingDescriptions.LOG),
        Binding(key="f", action="filter_view", description=BindingDescriptions.FILTER, show=False),
        Binding(key="k", action="compact_view", description=BindingDescriptions.COMPACT_VIEW, show=True),
        #Binding(key="p", action="server_preferences", description=BindingDescriptions.SERVER_PREFS),
        Binding(key="c", action="config", description=BindingDescriptions.CONFIG, show=True),
        Binding(key="b", action="bulk_cmd", description=BindingDescriptions.BULK_CMD, show=False),
        Binding(key="s", action="select_server", description=BindingDescriptions.SELECT_SERVERS, show=False),
        Binding(key="l", action="manage_server", description=BindingDescriptions.MANAGE_SERVERS, show=False),
        Binding(key="p", action="pattern_select", description=BindingDescriptions.PATTERN_SELECT, show=False),
        Binding(key="ctrl+a", action="toggle_select_all", description=BindingDescriptions.SELECT_ALL),
        Binding(key="ctrl+u", action="unselect_all", description=BindingDescriptions.UNSELECT_ALL),
        Binding(key="left", action="previous_page", description=BindingDescriptions.PREVIOUS_PAGE, show=False),
        Binding(key="right", action="next_page", description=BindingDescriptions.NEXT_PAGE, show=False),
        Binding(key="up", action="filter_running", description=BindingDescriptions.RUNNING_VMS, show=False),
        Binding(key="down", action="filter_all", description=BindingDescriptions.ALL_VMS, show=False),
        Binding(key="ctrl+v", action="virsh_shell", description=BindingDescriptions.VIRSH_SHELL, show=False ),
        Binding(key="h", action="host_capabilities", description=BindingDescriptions.HOST_CAPABILITIES, show=False),
        Binding(key="H", action="host_dashboard", description=BindingDescriptions.HOST_DASHBOARD, show=False),
        Binding(key="i", action="install_vm", description=BindingDescriptions.INSTALL_VM, show=True),
        Binding(key="ctrl+l", action="toggle_stats_logging", description=BindingDescriptions.TOGGLE_STATS, show=False),
        Binding(key="ctrl+s", action="show_cache_stats", description=BindingDescriptions.CACHE_STATS, show=False),
        Binding(key="q", action="quit", description=BindingDescriptions.QUIT),
    ]

    config = load_config()
    servers = config.get('servers', [])
    r_viewer_available = reactive(True)
    websockify_available = reactive(True)
    novnc_available = reactive(True)
    initial_cache_loading = reactive(False)
    initial_cache_complete = reactive(False)

    @staticmethod
    def _get_initial_active_uris(servers_list):
        if not servers_list:
            return []

        autoconnect_uris = []
        for server in servers_list:
            if server.get('autoconnect', False):
                autoconnect_uris.append(server['uri'])
                logging.info(f"Autoconnect enabled for server: {server.get('name', server['uri'])}")

        if autoconnect_uris:
            logging.info(f"Autoconnecting to {len(autoconnect_uris)} server(s)")
            return autoconnect_uris

        logging.info("No autoconnect servers configured")
        return []

    active_uris = reactive(_get_initial_active_uris(servers))
    current_page = reactive(0)
    # changing that will break CSS value!
    VMS_PER_PAGE = config.get('VMS_PER_PAGE', 6)
    WC_PORT_RANGE_START = config.get('WC_PORT_RANGE_START')
    WC_PORT_RANGE_END = config.get('WC_PORT_RANGE_END')
    sort_by = reactive(VmStatus.DEFAULT)
    search_text = reactive("")
    num_pages = reactive(1)
    selected_vm_uuids: reactive[set[str]] = reactive(set)
    bulk_operation_in_progress = reactive(False)
    compact_view = reactive(False)

    SERVER_COLOR_PALETTE = ServerPallette.COLOR
    CSS_PATH = ["vmanager.css", "vmcard.css", "dialog.css"]

    def __init__(self):
        super().__init__()
        self.vm_service = VMService()
        self.vm_service.set_data_update_callback(self.on_vm_data_update)
        self.vm_service.set_vm_update_callback(self.on_vm_update)
        self.vm_service.set_message_callback(self.on_service_message)
        self.worker_manager = WorkerManager(self)
        self.webconsole_manager = WebConsoleManager(self)
        self.server_color_map = {}
        self._color_index = 0
        self.ui = {}
        self.devel = "(Devel v" + AppInfo.version + ")"
        self.vm_card_pool = VMCardPool(self.VMS_PER_PAGE + 8)
        self._resize_timer = None
        self.filtered_server_uris = None
        self.last_total_calls = {}
        self.last_method_calls = {}
        self._stats_logging_active = False
        self._stats_interval_timer = None
        self.last_increase = {}  # Dict {uri: last_how_many_more}
        self.last_method_increase = {}  # Dict {(uri, method): last_increase}
        self.r_viewer = None

    def on_unmount(self) -> None:
        """Called when the application is unmounted."""
        if hasattr(self, 'vm_service'):
            self.vm_service.disconnect_all()
        if hasattr(self, 'worker_manager'):
            # worker_manager doesn't seem to have a stop method based on quick look but it uses workers
            pass

    def on_service_message(self, level: str, message: str):
        """Callback from VMService to display messages."""
        # Detect connection loss message and immediately remove VMs
        # Format: "Connection to {uri} lost: {reason}. Attempting to reconnect..."
        if "Connection to" in message and "lost:" in message and "Attempting to reconnect" in message:
            match = re.search(r"Connection to (.+) lost:", message)
            if match:
                uri = match.group(1)
                try:
                    self.call_from_thread(self._remove_vms_for_uri, uri)
                except RuntimeError:
                    self._remove_vms_for_uri(uri)

        target = self.show_success_message
        if level == "error":
            target = self.show_error_message
        elif level == "warning":
            target = self.show_warning_message
        elif level == "progress":
            target = self.show_in_progress_message
        
        try:
            self.call_from_thread(target, message)
        except RuntimeError:
            target(message)

    def on_vm_data_update(self):
        """Callback from VMService when data is updated."""
        # Avoid refreshing during bulk operations to prevent double refreshes and UI freezes
        # The bulk operation will trigger a forced refresh upon completion.
        if self.bulk_operation_in_progress:
            return

        try:
            self.call_from_thread(self.refresh_vm_list)
        except RuntimeError:
            self.refresh_vm_list()

        try:
            self.call_from_thread(self.worker_manager._cleanup_finished_workers)
        except RuntimeError:
            self.worker_manager._cleanup_finished_workers()

    def on_vm_update(self, internal_id: str):
        """Callback from VMService for specific VM updates."""
        try:
            self.call_from_thread(self.post_message, VmCardUpdateRequest(internal_id))
        except RuntimeError:
            self.post_message(VmCardUpdateRequest(internal_id))

    def watch_bulk_operation_in_progress(self, in_progress: bool) -> None:
        """
        Called when bulk_operation_in_progress changes.
        Disables or enables the collapsible 'Actions' button on all VM cards.
        """
        if in_progress:
            self._previous_compact_view = self.compact_view
            self.compact_view = True
        else:
            if hasattr(self, '_previous_compact_view'):
                self.compact_view = self._previous_compact_view

        for card in self.vm_card_pool.active_cards.values():
            if card.is_mounted:
                try:
                    collapsible = card.query_one("#actions-collapsible", Collapsible)
                    # dont disable for now
                    #collapsible.disabled = in_progress
                    if in_progress:
                        collapsible.collapsed = True
                except Exception:
                    # This can happen if the card is being removed from the DOM
                    pass

    def get_server_color(self, uri: str) -> str:
        """Assigns and returns a consistent color for a given server URI."""
        return get_server_color_cached(uri, tuple(self.SERVER_COLOR_PALETTE))

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        self.ui["vms_container"] = Vertical(id="vms-container")
        self.ui["error_footer"] = Static(id="error-footer", classes="error-message")
        self.ui["page_info"] = Label("", id="page-info", classes="")
        self.ui["prev_button"] = Button(
                ButtonLabels.PREVIOUS_PAGE, id="prev-button", variant="primary", classes="ctrlpage"
            )
        self.ui["next_button"] = Button(
                ButtonLabels.NEXT_PAGE, id="next-button", variant="primary", classes="ctrlpage"
            )
        self.ui["pagination_controls"] = Horizontal(
            self.ui["prev_button"],
            self.ui["page_info"],
            self.ui["next_button"],
            id="pagination-controls"
        )
        self.ui["pagination_controls"].styles.display = "none"
        self.ui["pagination_controls"].styles.align_horizontal = "center"
        self.ui["pagination_controls"].styles.height = "auto"
        self.ui["pagination_controls"].styles.padding_bottom = 0

        yield Header()
        with Horizontal(classes="top-controls"):
            yield Button(
                ButtonLabels.SELECT_SERVER, id="select_server_button", classes="Buttonpage"
            )
            yield Button(ButtonLabels.MANAGE_SERVERS, id="manage_servers_button", classes="Buttonpage")
            yield Button(
                ButtonLabels.SERVER_PREFERENCES, id="server_preferences_button", classes="Buttonpage"
            )
            yield Button(ButtonLabels.FILTER_VM, id="filter_button", classes="Buttonpage")
            #yield Button(ButtonLabels.VIEW_LOG, id="view-log-button", classes="Buttonpage")
            # yield Button("Virsh Shell", id="virsh_shell_button", classes="Buttonpage")
            yield Button(ButtonLabels.BULK_CMD, id="bulk_selected_vms", classes="Buttonpage")
            yield Button(ButtonLabels.PATTERN_SELECT, id="pattern_select_button", classes="Buttonpage")
            #yield Button(ButtonLabels.CONFIG, id="config-button", classes="Buttonpage")
            #yield Button(
            #    ButtonLabels.COMPACT_VIEW, id="compact-view-button", classes="Buttonpage"
            #)
            yield Link("About", url="https://aginies.github.io/virtui-manager/")

        yield self.ui["pagination_controls"]
        yield self.ui["vms_container"]
        yield self.ui["error_footer"]
        yield Footer()
        self.show_success_message(SuccessMessages.TERMINAL_COPY_HINT)

    def reload_servers(self, new_servers):
        self.servers = new_servers
        self.config["servers"] = new_servers
        save_config(self.config)

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        register_error_handler()
        self.title = f"{AppInfo.namecase} {self.devel}"

        self.r_viewer = check_r_viewer(self.config.get("REMOTE_VIEWER"))
        if self.r_viewer is None:
            self.show_error_message(
                ErrorMessages.R_VIEWER_NOT_FOUND
            )
            self.r_viewer_available = False
        else:
            self.show_quick_message(QuickMessages.REMOTE_VIEWER_SELECTED.format(viewer=self.r_viewer))

        if not check_websockify():
            self.show_error_message(
                ErrorMessages.WEBSOCKIFY_NOT_FOUND
            )
            self.websockify_available = False

        if not check_novnc_path():
            self.show_error_message(
                ErrorMessages.NOVNC_NOT_FOUND
            )
            self.novnc_available = False

        messages = generate_webconsole_keys_if_needed()
        for level, message in messages:
            if level == "info":
                self.show_success_message(message)
            else:
                self.show_error_message(message)

        self.sparkline_data = {}

        vms_container = self.ui.get("vms_container")
        if vms_container:
            vms_container.styles.grid_size_columns = 2

        if not self.servers:
            self.show_success_message(SuccessMessages.NO_SERVERS_CONFIGURED)
        else:
            # Launch initial connection and cache loading in background
            if self.active_uris:
                self.initial_cache_loading = True
                self.worker_manager.run(self._perform_initial_connection_and_cache, name="initial_connect")
            # Ensure layout and pool are initialized
            self._update_layout_for_size()

    def _perform_initial_connection_and_cache(self):
        """Connects to servers in background and then triggers cache loading."""
        if self.active_uris:
            for uri in self.active_uris:
                self.call_from_thread(self.show_in_progress_message, ProgressMessages.CONNECTING_TO_SERVER.format(uri=uri))
                success = self.connect_libvirt(uri)
                if success:
                    self.call_from_thread(self.show_success_message, SuccessMessages.CONNECTED_TO_SERVER.format(uri=uri))
                else:
                    error_msg = self.vm_service.connection_manager.get_connection_error(uri)
                    if error_msg:
                        self.call_from_thread(self.show_error_message, error_msg)

        # Proceed to cache worker
        self._initial_cache_worker()

    def _log_cache_statistics(self) -> None:
        """Log cache and libvirt call statistics periodically."""
        cache_monitor = setup_cache_monitoring()
        cache_monitor.log_stats()

        # Log libvirt call statistics
        call_stats = self.vm_service.connection_manager.get_stats()
        if call_stats:
            logging.debug("=== Libvirt Call Statistics ===")
            for uri, methods in sorted(call_stats.items()):
                server_name = uri
                for s in self.servers:
                    if s['uri'] == uri:
                        server_name = s['name']
                        break

                total_calls = sum(methods.values())

                previous_total = self.last_total_calls.get(uri, 0)
                previous_how_many_more = self.last_increase.get(uri, 0)
                total_increase = total_calls - previous_total
                increase_pct = 0.0
                how_many_more = total_calls - previous_total
                if total_increase > 0:
                    increase_pct = 100 - (previous_how_many_more*100 / total_increase)

                logging.debug(f"{server_name} ({uri}): {total_calls} calls | +{total_increase} ({increase_pct:.1f}%)")
                previous_how_many_more = how_many_more

                # Initialize previous method calls dict for this URI if needed
                if uri not in self.last_method_calls:
                    self.last_method_calls[uri] = {}

                # Sort methods by frequency
                sorted_methods = sorted(methods.items(), key=lambda x: x[1], reverse=True)
                for method, count in sorted_methods:
                    prev_method_count = self.last_method_calls[uri].get(method, 0)

                    self.last_method_calls[uri][method] = count
                    how_many_more_count = count - prev_method_count
                    logging.debug(f"  - {method}: {count} calls (+{how_many_more_count})")

                self.last_increase[uri] = how_many_more
                self.last_total_calls[uri] = total_calls

    def action_show_cache_stats(self) -> None:
        """Show cache statistics in a modal."""
        self.app.push_screen(CacheStatsModal(setup_cache_monitoring()))

    def action_toggle_stats_logging(self) -> None:
        """Toggle periodic statistics logging."""
        if self._stats_logging_active:
            if self._stats_interval_timer:
                self._stats_interval_timer.stop()
                self._stats_interval_timer = None
            self._stats_logging_active = False
            setup_cache_monitoring(enable=False)
            self.show_success_message(SuccessMessages.STATS_LOGGING_DISABLED)
        else:
            setup_cache_monitoring(enable=True)
            self._log_cache_statistics()
            self._stats_interval_timer = self.set_interval(10, self._log_cache_statistics)
            self._stats_logging_active = True
            self.show_success_message(SuccessMessages.STATS_LOGGING_ENABLED)

    def _initial_cache_worker(self):
        """Pre-loads VM cache before displaying the UI."""
        try:
            # Force cache update and fetch all VM data
            domains_to_display, total_vms, total_filtered_vms, server_names, all_active_uuids = self.vm_service.get_vms(
                self.active_uris,
                self.servers,
                self.sort_by,
                self.search_text,
                set(),
                force=True,
                page_start=0,
                page_end=self.VMS_PER_PAGE
            )

            # Check for connection errors and display them
            uris_to_check = list(self.active_uris)
            for uri in uris_to_check:
                if not self.vm_service.connection_manager.has_connection(uri):
                    error_msg = self.vm_service.connection_manager.get_connection_error(uri)
                    if error_msg:
                        # Find server name for better error message
                        server_name = uri
                        for s in self.servers:
                            if s['uri'] == uri:
                                server_name = s['name']
                                break
                        self.call_from_thread(self.show_error_message, ErrorMessages.SERVER_CONNECTION_ERROR.format(server_name=server_name, error_msg=error_msg))

                        if self.vm_service.connection_manager.is_max_retries_reached(uri):
                             self.call_from_thread(self.remove_active_uri, uri)

            # Pre-cache info and XML only for the first page of VMs
            # Full info will be loaded on-demand when cards are displayed
            vms_per_page = self.VMS_PER_PAGE
            vms_to_cache = domains_to_display[:vms_per_page]

            active_vms_on_page = []
            for domain, conn in vms_to_cache:
                try:
                    #state, _ = domain.state()
                    state_tuple = self.vm_service._get_domain_state(domain)
                    if not state_tuple:
                        state, _ = domain.state()
                    else:
                        state, _ = state_tuple
                    if state in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                        active_vms_on_page.append(domain.name())

                    self.vm_service._get_domain_info(domain)
                except libvirt.libvirtError:
                    pass

            if active_vms_on_page:
                vms_list_str = ", ".join(active_vms_on_page)
                self.call_from_thread(self.show_quick_message, QuickMessages.CACHING_VM_STATE.format(vms_list=vms_list_str))

            self.call_from_thread(self._on_initial_cache_complete)

        except Exception as e:
            self.call_from_thread(
                self.show_error_message, 
                ErrorMessages.ERROR_DURING_INITIAL_CACHE_LOADING.format(error=e)
            )

    def _on_initial_cache_complete(self):
        """Called when initial cache loading is complete."""
        self.initial_cache_loading = False
        self.initial_cache_complete = True
        if self.servers:
            self.show_quick_message(QuickMessages.VM_DATA_LOADED)
            self.refresh_vm_list()

    def _update_layout_for_size(self):
        """Update the layout based on the terminal size."""
        vms_container = self.ui.get("vms_container")
        if not vms_container:
            return

        width = self.size.width
        height = self.size.height
        cols = 2
        container_width = 86

        if width >= 212:
            cols = 5
            container_width = 213
        elif width >= 169:
            cols = 4
            container_width = 170
        elif width >= 128:
            cols = 3
            container_width = 129
        elif width >= 86:
            cols = 2
            container_width = 86
        else:  # width < 86
            cols = 2
            container_width = 84

        rows = 2  # Default to 2 rows
        if height > 42:
            rows = 3

        if self.compact_view:
            rows *= 3
            cols *= 2

        vms_container.styles.width = container_width
        vms_container.styles.grid_size_columns = cols

        old_vms_per_page = self.VMS_PER_PAGE
        
        self.VMS_PER_PAGE = cols * rows
        if self.compact_view:
            self.VMS_PER_PAGE = cols * rows + cols

        if width < 86:
            self.VMS_PER_PAGE = self.config.get("VMS_PER_PAGE", 4)

        if not self.compact_view and hasattr(self, '_saved_page_before_compact'):
            self.current_page = self._saved_page_before_compact
            del self._saved_page_before_compact

        self.vm_card_pool.pool_size = self.VMS_PER_PAGE + 8
        self.vm_card_pool.prefill_pool()

        if self.VMS_PER_PAGE > 9 and old_vms_per_page <= 9 and not self.compact_view:
            self.show_warning_message(WarningMessages.VMS_PER_PAGE_PERFORMANCE_WARNING.format(vms_per_page=self.VMS_PER_PAGE))

        self.refresh_vm_list(force=True)

    def on_resize(self, event):
        """Handle terminal resize events."""
        if not self.is_mounted:
            return
        if self._resize_timer:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(0.5, self._update_layout_for_size)

    def on_unload(self) -> None:
        """Called when the app is about to be unloaded."""
        # TOFIX
        #self.webconsole_manager.terminate_all()
        #if self._stats_logging_active:
        #    cache_monitor.log_stats()
        self.worker_manager.cancel_all()
        self.vm_service.disconnect_all()

    def _get_active_connections(self):
        """Generator that yields active libvirt connection objects."""
        for uri in self.active_uris:
            conn = self.vm_service.connect(uri)
            if conn:
                yield conn
            else:
                self.show_error_message(ErrorMessages.FAILED_TO_OPEN_CONNECTION.format(uri=uri))

    def connect_libvirt(self, uri: str) -> None:
        """Connects to libvirt."""
        conn = self.vm_service.connect(uri)
        if conn:
            return True
        else:
            error_msg = self.vm_service.connection_manager.get_connection_error(uri)
            if not error_msg:
                error_msg = f"Failed to connect to {uri}"
            logging.error(error_msg)
            return False

    def show_error_message(self, message: str):
        show_error_message(self, message)

    def show_success_message(self, message: str):
        show_success_message(self, message)

    def show_quick_message(self, message: str):
        show_quick_message(self, message)

    def show_in_progress_message(self, message: str):
        show_in_progress_message(self, message)

    def show_warning_message(self, message: str):
        show_warning_message(self, message)

    @on(Button.Pressed, f"#compact-view-button")
    def action_compact_view(self) -> None:
        """Toggle compact view."""
        if self.bulk_operation_in_progress:
            self.show_warning_message(WarningMessages.COMPACT_VIEW_LOCKED)
            return

        if not self.compact_view:
            self._saved_page_before_compact = self.current_page

        self.compact_view = not self.compact_view

    def watch_compact_view(self, value: bool) -> None:
        """Update layout for compact view."""
        for card in self.query(VMCard):
            card.compact_view = value
        vms_container = self.ui.get("vms_container")
        if vms_container:
            if value:
                vms_container.styles.grid_rows = "4"
            else:
                vms_container.styles.grid_rows = "14"
        self._update_layout_for_size()

    @on(Button.Pressed, "#select_server_button")
    def action_select_server(self) -> None:
        """Select servers to connect to."""
        servers_with_colors = []
        for s in self.servers:
            s_copy = s.copy()
            s_copy['color'] = self.get_server_color(s['uri'])
            servers_with_colors.append(s_copy)

        self.push_screen(SelectServerModal(servers_with_colors, self.active_uris, self.vm_service), self.handle_select_server_result)

    def handle_select_server_result(self, selected_uris: list[str] | None) -> None:
        """Handle the result from the SelectServer screen."""
        if selected_uris is None: # User cancelled
            return

        logging.info(f"Servers selected: {selected_uris}")

        # Disconnect from servers that are no longer selected
        uris_to_disconnect = [uri for uri in self.active_uris if uri not in selected_uris]

        # Connect to newly selected servers
        uris_to_connect = [uri for uri in selected_uris if uri not in self.active_uris]
        # Show connecting message for each new server
        for uri in uris_to_connect:
            self.show_in_progress_message(ProgressMessages.CONNECTING_TO_SERVER.format(uri=uri))
        for uri in uris_to_disconnect:
            # Cleanup UI caches for VMs on this server
            uuids_to_release = [
                uuid for uuid, card in self.vm_card_pool.active_cards.items()
                if card.conn and (self.vm_service.get_uri_for_connection(card.conn) == uri)
            ]
            for uuid in uuids_to_release:
                self.vm_card_pool.release_card(uuid)
                if uuid in self.sparkline_data:
                    del self.sparkline_data[uuid]

            self.vm_service.disconnect(uri)

        # Reset failure counts for selected URIs to allow immediate reconnection attempts
        for uri in selected_uris:
            self.vm_service.reset_connection_failures(uri)

        self.active_uris = selected_uris
        self.filtered_server_uris = None
        self.current_page = 0


        # For newly connected servers, attempt connection and show results
        connection_results = []
        for uri in uris_to_connect:
            success = self.connect_libvirt(uri)
            connection_results.append((uri, success))

        # Show connection results after a brief delay to ensure proper message ordering
        def show_connection_results():
            import time
            time.sleep(0.5)  # Brief delay to ensure "Connecting..." message is shown first
            for uri, success in connection_results:
                server_name = uri
                for s in self.servers:
                    if s['uri'] == uri:
                        server_name = s['name']
                        break
                if success:
                    self.call_from_thread(self.show_success_message, SuccessMessages.SERVER_CONNECTED.format(name=server_name))
                else:
                    error_msg = self.vm_service.connection_manager.get_connection_error(uri)
                    if error_msg:
                        self.call_from_thread(self.show_error_message, ErrorMessages.SERVER_FAILED_TO_CONNECT.format(server_name=server_name, error_msg=error_msg))

        if uris_to_connect:
            self.worker_manager.run(show_connection_results, name="show_connection_results")

        self.refresh_vm_list()

    def remove_active_uri(self, uri: str) -> None:
        """Removes a URI from the active list and configuration, effectively disconnecting it."""
        if uri in self.active_uris:
            logging.info(f"Removing {uri} from active URIs due to connection failure.")

            # Remove from servers list
            self.servers = [s for s in self.servers if s['uri'] != uri]

            # Save config to prevent autoconnect on next startup
            self.config['servers'] = self.servers
            save_config(self.config)

            # Create a new list excluding the failed URI
            new_active_uris = [u for u in self.active_uris if u != uri]

            # Use handle_select_server_result to perform cleanup properly
            self.handle_select_server_result(new_active_uris)

    def _collapse_all_action_collapsibles(self) -> None:
        """Collapses all 'Actions' collapsibles across all visible VM cards."""
        for card_id, card in self.vm_card_pool.active_cards.items():
            collapsible = card.ui.get("collapsible")
            if collapsible and not collapsible.collapsed:
                collapsible.collapsed = True
                logging.debug(f"Collapsed actions for VM: {card.name} (ID: {card.internal_id})")

    def _remove_vms_for_uri(self, uri: str) -> None:
        """Removes all VM cards associated with the given URI."""
        logging.info(f"Removing VMs for {uri} from view.")

        # Identify UUIDs to remove
        uuids_to_release = [
            uuid for uuid, card in self.vm_card_pool.active_cards.items()
            if card.conn and (self.vm_service.get_uri_for_connection(card.conn) == uri)
        ]

        for uuid in uuids_to_release:
            self.vm_card_pool.release_card(uuid)
            if uuid in self.sparkline_data:
                del self.sparkline_data[uuid]

    @on(Button.Pressed, "#filter_button")
    def action_filter_view(self) -> None:
        """Filter the VM list."""
        available_servers = []
        for uri in self.active_uris:
            name = uri
            for s in self.servers:
                if s['uri'] == uri:
                    name = s['name']
                    break
            available_servers.append({'name': name, 'uri': uri, 'color': self.get_server_color(uri)})

        selected_servers = self.filtered_server_uris if self.filtered_server_uris is not None else list(self.active_uris)

        self.push_screen(FilterModal(current_search=self.search_text, current_status=self.sort_by, available_servers=available_servers, selected_servers=selected_servers))

    def action_filter_running(self) -> None:
        """Filter to show only running VMs (shortcut: Up)."""
        if self.sort_by != VmStatus.RUNNING:
            self.sort_by = VmStatus.RUNNING
            self.current_page = 0
            self.show_quick_message(QuickMessages.FILTER_RUNNING_VMS)
            self.refresh_vm_list()

    def action_filter_all(self) -> None:
        """Reset filter to show all VMs (shortcut: Down)."""
        if self.sort_by != VmStatus.DEFAULT:
            self.sort_by = VmStatus.DEFAULT
            self.current_page = 0
            self.show_quick_message(QuickMessages.FILTER_ALL_VMS)
            self.refresh_vm_list()

    @on(FilterModal.FilterChanged)
    def on_filter_changed(self, message: FilterModal.FilterChanged) -> None:
        """Handle the FilterChanged message from the filter modal."""
        new_status = message.status
        new_search = message.search
        new_selected_servers = message.selected_servers

        logging.info(f"Filter changed to status={new_status}, search='{new_search}', servers={new_selected_servers}")

        status_changed = self.sort_by != new_status
        search_changed = self.search_text != new_search

        current_filtered = self.filtered_server_uris if self.filtered_server_uris is not None else list(self.active_uris)
        servers_changed = set(current_filtered) != set(new_selected_servers)

        if status_changed or search_changed or servers_changed:
            self.sort_by = new_status
            self.search_text = new_search
            self.filtered_server_uris = new_selected_servers
            self.current_page = 0
            self.show_in_progress_message(ProgressMessages.LOADING_VM_DATA_FROM_REMOTE_SERVERS)
            self.refresh_vm_list()

    def action_config(self) -> None:
        """Open the configuration modal."""
        self.old_log_level = self.config.get("LOG_LEVEL", "INFO")
        self.push_screen(ConfigModal(self.config), self.handle_config_result)

    def handle_config_result(self, result: dict | None) -> None:
        """Handle the result from the ConfigModal."""
        if result:
            old_stats_interval = self.config.get("STATS_INTERVAL")

            self.config = result
            new_log_level_str = self.config.get("LOG_LEVEL", "INFO")
            if self.old_log_level != new_log_level_str:
                new_log_level = getattr(logging, new_log_level_str.upper(), logging.INFO)
                logging.getLogger().setLevel(new_log_level)
                for handler in logging.getLogger().handlers:
                    handler.setLevel(new_log_level)
                self.show_success_message(SuccessMessages.LOG_LEVEL_CHANGED.format(level=new_log_level_str))

            # Update remote viewer if changed
            self.r_viewer = check_r_viewer(self.config.get("REMOTE_VIEWER"))
            if self.r_viewer is None:
                self.show_error_message(ErrorMessages.R_VIEWER_NOT_FOUND)
                self.r_viewer_available = False
            else:
                self.r_viewer_available = True

            if (self.config.get("STATS_INTERVAL") != old_stats_interval):
                self.show_in_progress_message(ProgressMessages.CONFIG_UPDATED_REFRESHING_VM_LIST)
                self.refresh_vm_list(force=False, optimize_for_current_page=True)
            else:
                self.show_success_message(SuccessMessages.CONFIG_UPDATED)

    @on(Button.Pressed, "#config_button")
    def on_config_button_pressed(self, event: Button.Pressed) -> None:
        """Callback for the config button."""
        self.action_config()

    def on_server_management(self, result: list | str | None) -> None:
        """Callback for ServerManagementModal."""
        if result is None:
            return
        if isinstance(result, list):
            self.reload_servers(result)
            return

        server_uri = result
        if server_uri:
            self.change_connection(server_uri)

    @on(Button.Pressed, "#manage_servers_button")
    def action_manage_server(self) -> None:
        """Manage the list of servers."""
        self.push_screen(ServerManagementModal(self.servers), self.on_server_management)

    @on(Button.Pressed, "#view_log_button")
    def action_view_log(self) -> None:
        """View the application log file (last 1000 lines)."""
        log_path = get_log_path()
        try:
            with open(log_path, "r") as f:
                log_content = "".join(deque(f, 1000))
        except FileNotFoundError:
            log_content = f"Log file ({log_path}) not found."
        except Exception as e:
            log_content = f"Error reading log file: {e}"
        self.push_screen(LogModal(log_content))

    @on(Button.Pressed, "#server_preferences_button")
    def action_server_preferences(self) -> None:
        """Show server preferences modal, prompting for a server if needed."""
        def launch_server_prefs(uri: str):
            if WebConsoleManager.is_remote_connection(uri):
                loading = LoadingModal()
                self.push_screen(loading)

                def show_prefs():
                    try:
                        modal = ServerPrefModal(uri=uri)
                        self.call_from_thread(loading.dismiss)
                        self.call_from_thread(self.push_screen, modal)
                    except Exception as e:
                        self.call_from_thread(loading.dismiss)
                        self.call_from_thread(self.show_error_message, ErrorMessages.PREFERENCES_LAUNCH_ERROR.format(error=e))

                self.worker_manager.run(show_prefs, name="launch_server_prefs")
            else:
                self.push_screen(ServerPrefModal(uri=uri))

        #def on_confirm(confirmed: bool) -> None:
        #    if confirmed:
        #        self._select_server_and_run(launch_server_prefs, "Select a server for Preferences", "Open")

        #self.app.push_screen(ConfirmationDialog(DialogMessages.EXPERIMENTAL), on_confirm)
        self._select_server_and_run(launch_server_prefs, "Select a server for Preferences", "Open")

    def _select_server_and_run(self, callback: callable, modal_title: str, modal_button_label: str) -> None:
        """
        Helper to select a server and run a callback with the selected URI.
        Handles 0, 1, or multiple active servers.
        """
        if len(self.active_uris) == 0:
            self.show_error_message(ErrorMessages.NOT_CONNECTED_TO_ANY_SERVER)
            return

        if len(self.active_uris) == 1:
            callback(self.active_uris[0])
            return

        server_options = []
        for uri in self.active_uris:
            name = uri
            for server in self.servers:
                if server['uri'] == uri:
                    name = server['name']
                    break
            server_options.append({'name': name, 'uri': uri})

        def on_server_selected(uri: str | None):
            if uri:
                callback(uri)

        self.push_screen(SelectOneServerModal(server_options, title=modal_title, button_label=modal_button_label), on_server_selected)

    def action_virsh_shell(self) -> None:
        """Show the virsh shell modal."""
        def launch_virsh_shell(uri: str):
            self.push_screen(VirshShellScreen(uri=uri))

        self._select_server_and_run(launch_virsh_shell, "Select a server for Virsh Shell", "Launch")

    @on(Button.Pressed, "#virsh_shell_button")
    def on_virsh_shell_button_pressed(self, event: Button.Pressed) -> None:
        """Callback for the virsh shell button."""
        self.action_virsh_shell()

    def action_host_dashboard(self) -> None:
        """Show Host Resource Dashboard."""
        def launch_dashboard_modal(uri: str):
            conn = self.vm_service.connect(uri)
            if conn:
                # Find server name
                server_name = uri
                for s in self.servers:
                    if s['uri'] == uri:
                        server_name = s['name']
                        break
                self.push_screen(HostDashboardModal(conn, server_name))
            else:
                self.show_error_message(ErrorMessages.COULD_NOT_CONNECT_TO_SERVER.format(uri=uri))

        self._select_server_and_run(launch_dashboard_modal, "Select a server for Dashboard", "View Dashboard")

    def action_host_capabilities(self) -> None:
        """Show Host Capabilities."""
        def launch_caps_modal(uri: str):
            conn = self.vm_service.connect(uri)
            if conn:
                self.push_screen(CapabilitiesTreeModal(conn))
            else:
                self.show_error_message(ErrorMessages.COULD_NOT_CONNECT_TO_SERVER.format(uri=uri))

        self._select_server_and_run(launch_caps_modal, "Select a server for Capabilities", "View")

    def action_install_vm(self) -> None:
        """Launch the VM Installation Modal."""
        def launch_install_modal(uri: str):
            self.push_screen(InstallVMModal(self.vm_service, uri), self.handle_install_vm_result)

        self._select_server_and_run(launch_install_modal, "Select a server for VM Installation", "Install Here")

    def handle_install_vm_result(self, result: bool | None) -> None:
        """Handle result from installation modal."""
        if result:
            self.refresh_vm_list(force=True)

    @on(VmActionRequest)
    def on_vm_action_request(self, message: VmActionRequest) -> None:
        """Handles a request to perform an action on a VM."""

        def action_worker():
            domain = self.vm_service.find_domain_by_uuid(self.active_uris, message.internal_id)
            if not domain:
                self.call_from_thread(self.show_error_message, ErrorMessages.VM_NOT_FOUND_BY_ID.format(vm_id=message.internal_id))
                return

            #vm_name = domain.name()
            # Use cached identity to avoid extra libvirt call
            _, vm_name = self.vm_service.get_vm_identity(domain)
            logging.info(f"Action Request: {message.action} for VM: {vm_name} (ID: {message.internal_id})")

            self.vm_service.suppress_vm_events(message.internal_id)
            try:
                # Message are done by events
                if message.action == VmAction.START:
                    # Check resources
                    try:
                        conn = domain.connect()
                        host_res = get_host_resources(conn)
                        current_alloc = get_active_vm_allocation(conn)

                        # domain.info() -> [state, maxMem(KB), memory(KB), nrVirtCpu, cpuTime]
                        vm_info = domain.info()
                        vm_mem_mb = vm_info[1] // 1024
                        vm_vcpus = vm_info[3]

                        host_mem_mb = host_res.get('available_memory', 0)
                        host_cpus = host_res.get('total_cpus', 0)

                        active_mem_mb = current_alloc.get('active_allocated_memory', 0)
                        active_vcpus = current_alloc.get('active_allocated_vcpus', 0)

                        overcommit_mem = (active_mem_mb + vm_mem_mb) > host_mem_mb
                        overcommit_cpu = (active_vcpus + vm_vcpus) > host_cpus

                        if overcommit_mem or overcommit_cpu:
                            warnings = []
                            if overcommit_mem:
                                warnings.append(f"Memory: {active_mem_mb + vm_mem_mb} MB > {host_mem_mb} MB")
                            if overcommit_cpu:
                                warnings.append(f"vCPUs: {active_vcpus + vm_vcpus} > {host_cpus}")

                            warning_msg = (
                                f"Starting VM '{vm_name}' will exceed host capacity (Active Allocation):\n"
                                f"{chr(10).join(warnings)}"
                            )
                            self.show_warning_message(warning_msg)
                    except Exception as e:
                        logging.error(f"Error checking resources before start: {e}")

                    self.vm_service.start_vm(domain)
                elif message.action == VmAction.STOP:
                    self.vm_service.stop_vm(domain)
                elif message.action == VmAction.PAUSE:
                    self.vm_service.pause_vm(domain)
                elif message.action == VmAction.RESUME:
                    self.vm_service.resume_vm(domain)
                elif message.action == VmAction.FORCE_OFF:
                    self.vm_service.force_off_vm(domain)
                elif message.action == VmAction.DELETE:
                    self.bulk_operation_in_progress = True
                    self.vm_service.delete_vm(domain, delete_storage=message.delete_storage, delete_nvram=True)
                    #self.vm_service.invalidate_vm_cache(message.internal_id)
                    if message.internal_id in self.selected_vm_uuids:
                        self.selected_vm_uuids.discard(message.internal_id)
                    self.call_from_thread(self.refresh_vm_list, force=True)
            except Exception as e:
                self.call_from_thread(
                    self.show_error_message,
                    ErrorMessages.ERROR_ON_VM_DURING_ACTION.format(vm_name=vm_name, action=message.action, error=e),
                )
            finally:
                self.vm_service.unsuppress_vm_events(message.internal_id)
                # Always try to unset the flag from the main thread
                def unset_flag():
                    if self.bulk_operation_in_progress:
                        self.bulk_operation_in_progress = False
                try:
                    self.call_from_thread(unset_flag)
                except RuntimeError:
                    logging.warning("Could not unset bulk operation flag: event loop not running.")

        self.worker_manager.run(
            action_worker, name=f"action_{message.action}_{message.internal_id}"
        )

    def action_toggle_select_all(self) -> None:
        """Selects or deselects all VMs on the current page."""
        visible_cards = self.query(VMCard)
        if not visible_cards:
            return

        # If all visible cards are already selected, deselect them. Otherwise, select them.
        all_currently_selected = all(card.is_selected for card in visible_cards)

        target_selection_state = not all_currently_selected

        for card in visible_cards:
            card.is_selected = target_selection_state

    def action_unselect_all(self) -> None:
        """Unselects all VMs across all pages."""
        if not self.selected_vm_uuids:
            return

        self.selected_vm_uuids.clear()
        # Update UI for visible cards
        for card in self.query(VMCard):
            card.is_selected = False

        self.show_quick_message(QuickMessages.ALL_VMS_UNSELECTED)

    @on(VMSelectionChanged)
    def on_vm_selection_changed(self, message: VMSelectionChanged) -> None:
        """Handles when a VM's selection state changes."""
        if message.is_selected:
            self.selected_vm_uuids.add(message.internal_id)
        else:
            self.selected_vm_uuids.discard(message.internal_id)

    def handle_bulk_action_result(self, result: dict | None) -> None:
        """Handles the result from the BulkActionModal."""
        if result is None:
            self.selected_vm_uuids.clear()
            self.refresh_vm_list()
            return

        action_type = result.get('action')
        delete_storage_flag = result.get('delete_storage', False)

        if not action_type:
            self.show_error_message(ErrorMessages.NO_ACTION_TYPE_BULK_MODAL)
            return

        selected_uuids_copy = list(self.selected_vm_uuids)  # Take a copy for the worker

        # Handle 'Edit Configuration' separately as it's a UI interaction
        if action_type == 'edit_config':
            # Find domains for the selected UUIDs
            found_domains_map = self.vm_service.find_domains_by_uuids(self.active_uris, selected_uuids_copy)
            selected_domains = list(found_domains_map.values())

            if not selected_domains:
                self.show_error_message(ErrorMessages.VM_NOT_FOUND_FOR_EDITING)
                return

            # Check if all selected VMs are stopped
            active_vms = []
            for domain in selected_domains:
                if domain.isActive():
                    active_vms.append(domain.name())

            if active_vms:
                self.show_error_message(ErrorMessages.VMS_MUST_BE_STOPPED_FOR_BULK_EDITING.format(running_vms=', '.join(active_vms)))
                # Restore selection since we are aborting
                self.selected_vm_uuids = set(selected_uuids_copy)
                return

            def on_confirm(confirmed: bool) -> None:
                if not confirmed:
                    self.selected_vm_uuids = set(selected_uuids_copy) # Restore selection
                    return

                # Use the first VM as a reference for the UI (e.g. current settings)
                reference_domain = selected_domains[0]
                try:
                    reference_uuid = get_internal_id(reference_domain)
                    result = self.vm_service.get_vm_details(
                        self.active_uris,
                        reference_uuid,
                        domain=reference_domain
                    )

                    if result:
                        vm_info, domain, conn = result
                        from modals.vmdetails_modals import VMDetailModal # Import here to avoid circular dep if any

                        self.push_screen(
                            VMDetailModal(
                                vm_name=vm_info['name'],
                                vm_info=vm_info,
                                domain=domain,
                                conn=conn,
                                invalidate_cache_callback=self.vm_service.invalidate_vm_state_cache,
                                selected_domains=selected_domains
                            )
                        )
                        # Clear selection after launching modal
                        self.selected_vm_uuids.clear()
                    else:
                        self.show_error_message(ErrorMessages.COULD_NOT_LOAD_DETAILS_FOR_REFERENCE_VM)
                except Exception as e:
                    self.app.show_error_message(ErrorMessages.BULK_EDIT_PREP_ERROR.format(error=e))

            warning_message = "This will apply configuration changes to all selected VMs based on the settings you choose.\n\nSome changes modify the VM's XML directly. All change cannot be undone.\n\nAre you sure you want to proceed?"
            self.app.push_screen(ConfirmationDialog(warning_message), on_confirm)
            return

        self.selected_vm_uuids.clear()
        self.bulk_operation_in_progress = True

        self.worker_manager.run(
            lambda: self._perform_bulk_action_worker(
                action_type, selected_uuids_copy, delete_storage_flag
            ),
            name=f"bulk_action_{action_type}",
        )

    def _perform_bulk_action_worker(self, action_type: str, vm_uuids: list[str], delete_storage_flag: bool = False) -> None:
        """Worker function to orchestrate a bulk action using the VMService."""

        # Stop workers for all selected VMs to prevent conflicts
        for uuid in vm_uuids:
            vm_card = self.vm_card_pool.active_cards.get(uuid)
            if vm_card:
                def stop_card_workers(card=vm_card):
                    if card.timer:
                        card.timer.stop()
                        card.timer = None
                    self.worker_manager.cancel(f"update_stats_{card.internal_id}")
                    self.worker_manager.cancel(f"actions_state_{card.internal_id}")

                self.call_from_thread(stop_card_workers)

        # Define a dummy progress callback
        def dummy_progress_callback(event_type: str, *args, **kwargs):
            pass

        try:
            successful_vms, failed_vms = self.vm_service.perform_bulk_action(
                self.active_uris,
                vm_uuids,
                action_type,
                delete_storage_flag,
                dummy_progress_callback  # Pass the dummy callback
            )

            summary = f"Bulk action '{action_type}' complete. Successful: {len(successful_vms)}, Failed: {len(failed_vms)}"
            logging.info(summary) 

            if successful_vms:
                self.call_from_thread(self.show_success_message, SuccessMessages.BULK_ACTION_SUCCESS_TEMPLATE.format(action_type=action_type, count=len(successful_vms)))
            if failed_vms:
                self.call_from_thread(self.show_error_message, ErrorMessages.BULK_ACTION_FAILED_TEMPLATE.format(action_type=action_type, count=len(failed_vms)))

        except Exception as e:
            logging.error(f"An unexpected error occurred during bulk action service call: {e}", exc_info=True)
            self.call_from_thread(self.show_error_message, ErrorMessages.FATAL_ERROR_BULK_ACTION.format(error=e))

        finally:
            # Ensure these are called on the main thread
            # Always force refresh to ensure UI is in sync with backend (e.g. deletions, additions)
            # Unlock immediately so UI is not stuck if refresh fails
            def unlock_and_refresh():
                self.bulk_operation_in_progress = False
                self.refresh_vm_list(force=True)

            self.call_from_thread(unlock_and_refresh)


    def change_connection(self, uri: str) -> None:
        """Change the active connection to a single server and refresh."""
        logging.info(f"Changing connection to {uri}")
        if not uri or uri.strip() == "":
            return

        self.handle_select_server_result([uri])

    def refresh_vm_list(self, force: bool = False, optimize_for_current_page: bool = False, on_complete: Callable | None = None) -> None:
        """Refreshes the list of VMs by running the fetch-and-display logic in a worker."""
        # Don't display VMs until initial cache is complete
        if self.initial_cache_loading and not self.initial_cache_complete:
            return

        # Try to run the worker. If it's already running, this will do nothing.
        selected_uuids = set(self.selected_vm_uuids)
        current_page = self.current_page
        vms_per_page = self.VMS_PER_PAGE

        uris_to_query = self.filtered_server_uris if self.filtered_server_uris is not None else list(self.active_uris)

        if force:
            self.worker_manager.cancel("list_vms")

        self.worker_manager.run(
            lambda: self.list_vms_worker(
                selected_uuids,
                current_page,
                vms_per_page,
                uris_to_query,
                force=force,
                optimize_for_current_page=optimize_for_current_page,
                on_complete=on_complete,
            ),
            name="list_vms"
        )

    def list_vms_worker(
            self,
            selected_uuids: set[str],
            current_page: int,
            vms_per_page: int,
            uris_to_query: list[str],
            force: bool = False,
            optimize_for_current_page: bool = False,
            on_complete: Callable | None = None
            ):
        """Worker to fetch, filter, and display VMs using a diffing strategy."""
        try:
            start_index = current_page * vms_per_page
            end_index = start_index + vms_per_page
            page_start = start_index if optimize_for_current_page else None
            page_end = end_index if optimize_for_current_page else None

            domains_to_display, total_vms, total_filtered_vms, server_names, all_active_uuids = self.vm_service.get_vms(
                uris_to_query,
                self.servers,
                self.sort_by,
                self.search_text,
                selected_uuids,
                force=force,
                page_start=page_start,
                page_end=page_end
            )

            reset_page = False
            if current_page > 0 and current_page * vms_per_page >= total_filtered_vms:
                current_page = 0
                reset_page = True

            start_index = current_page * vms_per_page
            end_index = start_index + vms_per_page
            paginated_domains = domains_to_display[start_index:end_index]

            # Parallelize fetching of VM info
            def fetch_vm_data(item):
                domain, conn = item
                try:
                    uri = self.vm_service.get_uri_for_connection(conn) or conn.getURI()
                    uuid, vm_name = self.vm_service.get_vm_identity(domain, conn, known_uri=uri)

                    # Get info from cache or fetch if not present
                    info = self.vm_service._get_domain_info(domain)
                    cached_details = self.vm_service.get_cached_vm_details(uuid)

                    # Explicitly get state from cache/service
                    state_tuple = self.vm_service._get_domain_state(domain, internal_id=uuid)

                    effective_state = None
                    if state_tuple:
                        effective_state = state_tuple[0]
                    elif info:
                        effective_state = info[0]

                    if effective_state is not None:
                        status = get_status(domain, state=effective_state)
                    elif cached_details:
                        status = cached_details['status']
                    else:
                        status = StatusText.LOADING

                    if info:
                        cpu = info[3]
                        memory = info[1] // 1024
                    elif cached_details:
                        cpu = cached_details['cpu']
                        memory = cached_details['memory']
                    else:
                        cpu = 0
                        memory = 0

                    return {
                        'uuid': uuid,
                        'name': vm_name,
                        'status': status,
                        'cpu': cpu,
                        'memory': memory,
                        'is_selected': uuid in selected_uuids,
                        'domain': domain,
                        'conn': conn,
                        'uri': uri
                    }
                except libvirt.libvirtError as e:
                    if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                        logging.warning(f"Skipping display of non-existent VM during refresh.")
                        return None
                    else:
                        try:
                            name_for_error = vm_name if 'vm_name' in locals() else domain.name()
                        except:
                            name_for_error = "Unknown"
                        self.call_from_thread(self.show_error_message, ErrorMessages.VM_INFO_ERROR.format(vm_name=name_for_error, error=e))
                        return None

            vm_data_list = []
            page_uuids = set()

            with ThreadPoolExecutor(max_workers=20) as executor:
                results = list(executor.map(fetch_vm_data, paginated_domains))

            for result in results:
                if result:
                    vm_data_list.append(result)
                    page_uuids.add(result['uuid'])

            # Cleanup cache: remove cards for VMs that no longer exist at all
            all_uuids_from_libvirt = set(all_active_uuids)

            def update_ui_on_main_thread():
                if reset_page:
                    self.current_page = 0

                # Update visible UUIDs in service
                self.vm_service.update_visible_uuids(page_uuids)

                # Cleanup sparkline data for deleted VMs
                for uuid in list(self.sparkline_data.keys()):
                    if uuid not in all_uuids_from_libvirt:
                        del self.sparkline_data[uuid]

                vms_container = self.ui.get("vms_container")
                if not vms_container:
                    return

                current_widgets = list(vms_container.children)

                # Step 1: Remove excess widgets if page size shrunk
                widgets_to_remove = []
                while len(current_widgets) > len(vm_data_list):
                    widgets_to_remove.append(current_widgets.pop())

                if widgets_to_remove:
                    # Release cards first (updates pool state)
                    for widget in widgets_to_remove:
                        uuid = widget.internal_id
                        if uuid in self.vm_card_pool.active_cards:
                            self.vm_card_pool.release_card(uuid)

                    # Batch remove from UI if possible, or sequential
                    for widget in widgets_to_remove:
                        if widget.is_mounted:
                            widget.remove()

                # Step 2: Update existing widgets and add new ones
                cards_to_mount = []

                for i, data in enumerate(vm_data_list):
                    uuid = data['uuid']

                    if i < len(current_widgets):
                        # Reuse existing active widget
                        card = current_widgets[i]
                        old_uuid = card.internal_id

                        # Update Pool Tracking if identity changed
                        if old_uuid != uuid:
                            if old_uuid in self.vm_card_pool.active_cards:
                                # Remove old booking
                                del self.vm_card_pool.active_cards[old_uuid]
                            # Book new UUID
                            self.vm_card_pool.active_cards[uuid] = card
                    else:
                        # Get new widget from pool
                        card = self.vm_card_pool.get_or_create_card(uuid)
                        cards_to_mount.append(card)

                    # Apply Data to Card
                    if uuid not in self.sparkline_data:
                        self.sparkline_data[uuid] = {"cpu": [], "mem": [], "disk": [], "net": []}

                    card.vm = data['domain']
                    card.conn = data['conn']
                    card.name = data['name']
                    card.cpu = data['cpu']
                    card.memory = data['memory']
                    card.is_selected = data['is_selected']
                    card.server_border_color = self.get_server_color(data['uri'])
                    card.status = data['status']
                    card.internal_id = uuid
                    card.compact_view = self.compact_view

                # Mount any new cards
                if cards_to_mount:
                    vms_container.mount(*cards_to_mount)

                for card in vms_container.query(VMCard):
                    card.compact_view = self.compact_view

                # Check for connection errors to display via show_error_message
                errors = []
                uris_to_remove = []
                config_changed = False

                for uri in self.active_uris:
                    # Get server name for better error messages
                    server_name = uri
                    for s in self.servers:
                        if s['uri'] == uri:
                            server_name = s['name']
                            break

                    # Check for permanent failure
                    failed_attempts = self.vm_service.connection_manager.get_failed_attempts(uri)
                    if failed_attempts >= 2:
                        uris_to_remove.append(uri)
                        # Find server and disable autoconnect
                        for s in self.servers:
                            if s['uri'] == uri and s.get('autoconnect', False):
                                s['autoconnect'] = False
                                config_changed = True
                                logging.info(f"Disabled autoconnect for {server_name} due to connection failure.")

                    err = self.vm_service.connection_manager.get_connection_error(uri)
                    if err and uri not in uris_to_remove:
                        # Only show error if not already marked for removal
                        # This prevents duplicate error messages
                        errors.append(f"Server '{server_name}': {err}")

                # Display collected errors
                for error in errors:
                    self.show_error_message(error)

                # Handle removal of permanently failed servers
                if uris_to_remove:
                    removed_names = []
                    for uri in uris_to_remove:
                        for s in self.servers:
                            if s['uri'] == uri:
                                removed_names.append(s['name'])
                                break

                    self.active_uris = [u for u in self.active_uris if u not in uris_to_remove]
                    if self.filtered_server_uris:
                        self.filtered_server_uris = [u for u in self.filtered_server_uris if u not in uris_to_remove]

                    if removed_names:
                        self.show_error_message(ErrorMessages.SERVER_DISCONNECTED_AUTOCONNECT_DISABLED.format(names=', '.join(removed_names)))

                if config_changed:
                    self.config['servers'] = self.servers
                    save_config(self.config)

                # Main tittle with Servers name
                self.title = f"{AppInfo.namecase} {self.devel} - {'| '.join(sorted(server_names))}"
                self.update_pagination_controls(total_filtered_vms, total_vms_unfiltered=len(domains_to_display))

            self.call_from_thread(update_ui_on_main_thread)

        except Exception as e:
            self.call_from_thread(self.show_error_message, ErrorMessages.ERROR_FETCHING_VM_DATA.format(error=e))
        finally:
            if on_complete:
                self.call_from_thread(on_complete)


    def update_pagination_controls(self, total_filtered_vms: int, total_vms_unfiltered: int):
        pagination_controls = self.ui.get("pagination_controls")
        if not pagination_controls:
            return

        if total_vms_unfiltered <= self.VMS_PER_PAGE:
            pagination_controls.styles.display = "none"
            return
        else:
            pagination_controls.styles.display = "block"

        num_pages = (total_filtered_vms + self.VMS_PER_PAGE - 1) // self.VMS_PER_PAGE
        self.num_pages = num_pages

        page_info = self.ui.get("page_info")
        if page_info:
            page_info.update(f" [ {self.current_page + 1}/{num_pages} ]")

        prev_button = self.ui.get("prev_button")
        if prev_button:
            prev_button.disabled = self.current_page == 0

        next_button = self.ui.get("next_button")
        if next_button:
            next_button.disabled = self.current_page >= num_pages - 1

    @on(Button.Pressed, "#prev-button")
    def action_previous_page(self) -> None:
        """Go to the previous page."""
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_vm_list()

    @on(Button.Pressed, "#next-button")
    def action_next_page(self) -> None:
        """Go to the next page."""
        if self.current_page < self.num_pages - 1:
            self.current_page += 1
            self.refresh_vm_list()

    @on(Button.Pressed, "#pattern_select_button")
    def action_pattern_select(self) -> None:
        """Handles the 'Pattern Sel' button press."""
        if not self.active_uris:
            self.show_error_message(ErrorMessages.NO_ACTIVE_SERVERS)
            return

        # Gather all known VMs from cache
        available_vms = []
        with self.vm_service._cache_lock:
            for uuid, domain in self.vm_service._domain_cache.items():
                try:
                    conn = self.vm_service._uuid_to_conn_cache.get(uuid)
                    #uri = self.vm_service.get_uri_for_connection(conn) or conn.getURI()
                    # Use cached URI lookup to avoid libvirt call
                    uri = self.vm_service.get_uri_for_connection(conn)
                    if not uri:
                        uri = conn.getURI()
                    # Use cached identity to avoid libvirt call
                    _, name = self.vm_service.get_vm_identity(domain, conn, known_uri=uri)
                    available_vms.append({
                        'uuid': uuid,
                        'name': name,
                        'uri': uri
                    })
                except Exception:
                    continue

        if not available_vms:
            self.show_error_message(ErrorMessages.NO_VMS_IN_CACHE)
            return

        # Prepare server list for the modal, matching FilterModal logic
        available_servers = []
        for uri in self.active_uris:
            name = uri
            for s in self.servers:
                if s['uri'] == uri:
                    name = s['name']
                    break
            available_servers.append({
                'name': name, 
                'uri': uri, 
                'color': self.get_server_color(uri)
            })

        selected_servers = self.filtered_server_uris if self.filtered_server_uris is not None else list(self.active_uris)

        def handle_result(selected_uuids: set[str] | None):
            if selected_uuids:
                # Add found UUIDs to current selection
                self.selected_vm_uuids.update(selected_uuids)
                self.show_success_message(SuccessMessages.VMS_SELECTED_BY_PATTERN.format(count=len(selected_uuids)))
                self.refresh_vm_list()

        self.push_screen(PatternSelectModal(available_vms, available_servers, selected_servers), handle_result)

    @on(Button.Pressed, "#bulk_selected_vms")
    def action_bulk_cmd(self) -> None:
        #def on_bulk_selected_vms_button_pressed(self) -> None:
        """Handles the 'Bulk Selected' button press."""
        self._collapse_all_action_collapsibles()
        if not self.selected_vm_uuids:
            self.show_error_message(ErrorMessages.NO_VMS_SELECTED)
            return

        uuids_snapshot = list(self.selected_vm_uuids)

        def get_names_and_show_modal():
            """Worker to fetch VM names and display the bulk action modal."""
            uuids = uuids_snapshot

            # Use the service to find specific domains by their internal ID (UUID@URI)
            # This correctly handles cases where identical UUIDs exist on different servers
            found_domains_map = self.vm_service.find_domains_by_uuids(self.active_uris, uuids, check_validity=False)

            all_names = set()
            for domain in found_domains_map.values():
                try:
                    #all_names.add(domain.name())
                    _, name = self.vm_service.get_vm_identity(domain)
                    all_names.add(name)
                except libvirt.libvirtError:
                    pass

            vm_names_list = sorted(list(all_names))

            if vm_names_list:
                self.call_from_thread(
                    self.push_screen, BulkActionModal(vm_names_list), self.handle_bulk_action_result
                )
            else:
                self.call_from_thread(self.show_error_message, ErrorMessages.BULK_ACTION_VM_NAMES_RETRIEVAL_FAILED)

        self.worker_manager.run(
            get_names_and_show_modal,
            name="get_bulk_vm_names",
        )


    async def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    @on(VmCardUpdateRequest)
    def on_vm_card_update_request(self, message: VmCardUpdateRequest) -> None:
        """
        Optimized method to update a single VM card without full refresh.
        Called when a VM card needs fresh data.
        """
        vm_internal_id = message.internal_id
        logging.debug(f"Only refresh ID: {vm_internal_id}")
        def update_single_card():
            try:
                domain = self.vm_service.find_domain_by_uuid(self.active_uris, vm_internal_id)
                if not domain:
                    logging.warning(f"Domain not found for update: {vm_internal_id}")
                    return

                # Use cached methods to minimize libvirt calls
                # Pass internal_id to ensure we hit the exact cache entry updated by the event
                state_tuple = self.vm_service._get_domain_state(domain, internal_id=vm_internal_id)
                if not state_tuple:
                    logging.warning(f"Could not get state for {vm_internal_id}")
                    return

                state, _ = state_tuple
                logging.debug(f"Update card {vm_internal_id}: State={state}")

                # Only fetch full info if VM is running/paused
                if state in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                    info = self.vm_service._get_domain_info(domain, internal_id=vm_internal_id)
                    if info:
                        status = get_status(domain, state=state)
                        cpu = info[3]
                        memory = info[1] // 1024
                    else:
                        return
                else:
                    # For stopped VMs, use minimal data
                    status = get_status(domain, state=state)
                    # Try to get from cache
                    cached_details = self.vm_service.get_cached_vm_details(vm_internal_id)
                    if cached_details:
                        cpu = cached_details['cpu']
                        memory = cached_details['memory']
                    else:
                        cpu = 0
                        memory = 0
                
                logging.debug(f"Updating card {vm_internal_id} with status {status}")
                # Update card on main thread
                def update_ui():
                    card = self.vm_card_pool.active_cards.get(vm_internal_id)
                    if card and card.is_mounted:
                        card.status = status
                        card.cpu = cpu
                        card.memory = memory
                self.call_from_thread(update_ui)

            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    self.vm_service.invalidate_vm_cache(vm_internal_id)
                logging.debug(f"Error updating card for {vm_internal_id}: {e}")

        self.worker_manager.run(
            update_single_card, name=f"update_card_{vm_internal_id}"
        )


def main():
    """Entry point for vmanager TUI application."""
    parser = argparse.ArgumentParser(description="A Textual application to manage VMs.")
    parser.add_argument("--cmd", action="store_true", help="Run in command-line interpreter mode.")
    args = parser.parse_args()

    if args.cmd:
        from vmanager_cmd import VManagerCMD
        VManagerCMD().cmdloop()
    else:
        terminal_size = os.get_terminal_size()
        if terminal_size.lines < 34:
            print(f"Terminal height is too small ({terminal_size.lines} lines). Please resize to at least 34 lines.")
            sys.exit(1)
        if terminal_size.columns < 86:
            print(f"Terminal width is too small ({terminal_size.columns} columns). Please resize to at least 86 columns.")
            sys.exit(1)
        app = VMManagerTUI()
        app.run()

if __name__ == "__main__":
    main()
