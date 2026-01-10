"""
Main interface
"""
import os
import sys
import logging
import argparse
from typing import Any, Callable
import libvirt

from textual.app import App, ComposeResult, on
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button, Footer, Header, Label, Link, Static,
)
from textual.worker import Worker, WorkerState

from config import load_config, save_config, get_log_path
from constants import (
        VmAction, VmStatus, ButtonLabels, ButtonIds,
        ErrorMessages, AppInfo, StatusText, ServerPallette
        )
from events import VmActionRequest, VMSelectionChanged, VmCardUpdateRequest #,VMNameClicked
from libvirt_error_handler import register_error_handler
from modals.bulk_modals import BulkActionModal
from modals.config_modal import ConfigModal
from modals.log_modal import LogModal
from modals.server_modals import ServerManagementModal
from modals.server_prefs_modals import ServerPrefModal
from modals.select_server_modals import SelectOneServerModal, SelectServerModal
from modals.selection_modals import PatternSelectModal
from modals.cache_stats_modal import CacheStatsModal
from modals.utils_modals import (
    show_error_message,
    show_success_message,
    show_warning_message,
    show_quick_message,
    LoadingModal,
    ConfirmationDialog,
)
from modals.vmanager_modals import (
    FilterModal,
)
from modals.virsh_modals import VirshShellScreen
from utils import (
    check_novnc_path,
    check_virt_viewer,
    check_websockify,
    generate_webconsole_keys_if_needed,
    get_server_color_cached,
    format_server_names,
    setup_cache_monitoring,
)
from vm_queries import (
    get_status,
)
from vm_service import VMService
from vmcard import VMCard
from webconsole_manager import WebConsoleManager

# Configure logging
logging.basicConfig(
    filename=get_log_path(),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

class WorkerManager:
    """A class to manage and track Textual workers."""

    def __init__(self, app: App):
        self.app = app
        self.workers: dict[str, Worker] = {}

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
        self._cleanup_finished_workers()
        return name in self.workers and self.workers[name].state not in (
            WorkerState.SUCCESS,
            WorkerState.CANCELLED,
            WorkerState.ERROR,
        )

    def cancel(self, name: str) -> bool:
        """Cancel a running worker by name. Returns True if cancelled, False otherwise."""
        if name in self.workers:
            self.workers[name].cancel()
            return True
        return False

    def cancel_all(self) -> None:
        """Cancel all running workers."""
        for worker in list(self.workers.values()):
            worker.cancel()
        self.workers.clear()

class VMManagerTUI(App):
    """A Textual application to manage VMs."""

    BINDINGS = [
        ("ctrl+s", "show_cache_stats", ""),
        ("v", "view_log", "Log"),
        ("f", "filter_view", "Filter"),
        #("p", "server_preferences", "ServerPrefs"),
        ("c", "config", "Config"),
        ("s", "select_server", "SelServers"),
        ("m", "manage_server", "ServList"),
        ("p", "pattern_select", "PatternSel"),
        ("ctrl+a", "toggle_select_all", "Sel/Des All"),
        ("ctrl+u", "unselect_all", "Unselect All"),
        ("ctrl+v", "virsh_shell", "Virsh"),
        ("ctrl+l", "toggle_stats_logging", "Log Stats"),
        ("q", "quit", "Quit"),
    ]

    config = load_config()
    servers = config.get('servers', [])
    virt_viewer_available = reactive(True)
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
    VMS_PER_PAGE = config.get('VMS_PER_PAGE', 4)
    MAX_VM_CARDS = config.get('MAX_VM_CARDS', 250)
    WC_PORT_RANGE_START = config.get('WC_PORT_RANGE_START')
    WC_PORT_RANGE_END = config.get('WC_PORT_RANGE_END')
    sort_by = reactive(VmStatus.DEFAULT)
    search_text = reactive("")
    num_pages = reactive(1)
    selected_vm_uuids: reactive[set[str]] = reactive(set)
    bulk_operation_in_progress = reactive(False)

    SERVER_COLOR_PALETTE = ServerPallette.COLOR
    CSS_PATH = ["vmanager.css", "vmcard.css", "dialog.css"]

    def __init__(self):
        super().__init__()
        self.vm_service = VMService()
        self.vm_service.set_data_update_callback(self.on_vm_data_update)
        self.worker_manager = WorkerManager(self)
        self.webconsole_manager = WebConsoleManager(self)
        self.server_color_map = {}
        self._color_index = 0
        self.ui = {}
        self.devel = "(Devel v" + AppInfo.version + ")"
        self.vm_cards: dict[str, VMCard] = {}
        self._resize_timer = None
        self.filtered_server_uris = None
        self.last_total_calls = {}
        self.last_method_calls = {}
        self._stats_logging_active = False
        self._stats_interval_timer = None
        self.last_increase = {}  # Dict {uri: last_how_many_more}
        self.last_method_increase = {}  # Dict {(uri, method): last_increase}

    def on_vm_data_update(self):
        """Callback from VMService when data is updated."""
        self.call_from_thread(self.refresh_vm_list)
        self.call_from_thread(self.worker_manager._cleanup_finished_workers)

    def get_server_color(self, uri: str) -> str:
        """Assigns and returns a consistent color for a given server URI."""
        return get_server_color_cached(uri, tuple(self.SERVER_COLOR_PALETTE))

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        self.ui["vms_container"] = Vertical(id="vms-container")
        self.ui["error_footer"] = Static(id="error-footer", classes="error-message")
        self.ui["page_info"] = Label("", id="page-info", classes="")
        self.ui["prev_button"] = Button(
                ButtonLabels.PREVIOUS_PAGE, id=ButtonIds.PREV_BUTTON, variant="primary", classes="ctrlpage"
            )
        self.ui["next_button"] = Button(
                ButtonLabels.NEXT_PAGE, id=ButtonIds.NEXT_BUTTON, variant="primary", classes="ctrlpage"
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
                ButtonLabels.SELECT_SERVER, id=ButtonIds.SELECT_SERVER_BUTTON, classes="Buttonpage"
            )
            yield Button(ButtonLabels.MANAGE_SERVERS, id=ButtonIds.MANAGE_SERVERS_BUTTON, classes="Buttonpage")
            yield Button(
                ButtonLabels.SERVER_PREFERENCES, id=ButtonIds.SERVER_PREFERENCES_BUTTON, classes="Buttonpage"
            )
            yield Button(ButtonLabels.FILTER_VM, id=ButtonIds.FILTER_BUTTON, classes="Buttonpage")
            #yield Button(ButtonLabels.VIEW_LOG, id=ButtonIds.VIEW_LOG_BUTTON, classes="Buttonpage")
            # yield Button("Virsh Shell", id="virsh_shell_button", classes="Buttonpage")
            yield Button(ButtonLabels.BULK_CMD, id=ButtonIds.BULK_SELECTED_VMS, classes="Buttonpage")
            yield Button(ButtonLabels.PATTERN_SELECT, id=ButtonIds.PATTERN_SELECT_BUTTON, classes="Buttonpage")
            yield Button(ButtonLabels.CONFIG, id=ButtonIds.CONFIG_BUTTON, classes="Buttonpage")
            yield Link("About", url="https://aginies.github.io/virtui-manager/")

        yield self.ui["pagination_controls"]
        yield self.ui["vms_container"]
        yield self.ui["error_footer"]
        yield Footer()
        self.show_success_message(
            "In some Terminal use 'Shift' key while selecting text with the mouse to copy it."
        )

    def reload_servers(self, new_servers):
        self.servers = new_servers
        self.config["servers"] = new_servers
        save_config(self.config)

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        register_error_handler()
        self.title = f"{AppInfo.namecase} {self.devel}"

        if not check_virt_viewer():
            self.show_error_message(
                ErrorMessages.VIRT_VIEWER_NOT_FOUND
            )
            self.virt_viewer_available = False

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

        error_footer = self.ui.get("error_footer")
        if error_footer:
            error_footer.styles.height = 0
            error_footer.styles.overflow = "hidden"
            error_footer.styles.padding = 0

        vms_container = self.ui.get("vms_container")
        if vms_container:
            vms_container.styles.grid_size_columns = 2

        if not self.servers:
            self.show_success_message(
                "No servers configured. Please add one via 'Servers List'."
            )
        else:
            # Launch initial cache loading before displaying VMs
            if self.active_uris:
                for uri in self.active_uris:
                    self.connect_libvirt(uri)

                self.initial_cache_loading = True
                self.worker_manager.run(self._initial_cache_worker, name="initial_cache")

    def _log_cache_statistics(self) -> None:
        """Log cache and libvirt call statistics periodically."""
        cache_monitor = setup_cache_monitoring()
        cache_monitor.log_stats()

        # Log libvirt call statistics
        call_stats = self.vm_service.connection_manager.get_stats()
        if call_stats:
            logging.info("=== Libvirt Call Statistics ===")
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

                logging.info(f"{server_name} ({uri}): {total_calls} calls | +{total_increase} ({increase_pct:.1f}%)")
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
                    logging.info(f"  - {method}: {count} calls (+{how_many_more_count})")

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
            self.show_success_message("Statistics logging and monitoring disabled.")
        else:
            setup_cache_monitoring(enable=True)
            self._log_cache_statistics()
            self._stats_interval_timer = self.set_interval(10, self._log_cache_statistics)
            self._stats_logging_active = True
            self.show_success_message("Statistics logging and monitoring enabled (every 10s).")

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
                self.call_from_thread(self.show_quick_message, f"Caching VM state for: {vms_list_str}")

            self.call_from_thread(self._on_initial_cache_complete)

        except Exception as e:
            self.call_from_thread(
                self.show_error_message, 
                f"Error during initial cache loading: {e}"
            )

    def _on_initial_cache_complete(self):
        """Called when initial cache loading is complete."""
        self.initial_cache_loading = False
        self.initial_cache_complete = True
        self.show_quick_message("VM data loaded. Displaying VMs...")
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

        vms_container.styles.grid_size_columns = cols
        vms_container.styles.width = container_width

        old_vms_per_page = self.VMS_PER_PAGE
        self.VMS_PER_PAGE = cols * rows

        if width < 86:
            self.VMS_PER_PAGE = self.config.get("VMS_PER_PAGE", 4)

        if self.VMS_PER_PAGE > 6 and old_vms_per_page <= 6:
            self.show_warning_message(
                f"Displaying {self.VMS_PER_PAGE} VMs per page. CPU usage may increase; 6 is recommended for optimal performance."
            )

        self.refresh_vm_list()

    def on_resize(self, event):
        """Handle terminal resize events."""
        if not self.is_mounted:
            return
        if self._resize_timer:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(1, self._update_layout_for_size)

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
                self.show_error_message(f"Failed to open connection to {uri}")

    def connect_libvirt(self, uri: str) -> None:
        """Connects to libvirt."""
        conn = self.vm_service.connect(uri)
        if conn is None:
            self.show_error_message(f"Failed to connect to {uri}")

    def show_error_message(self, message: str):
        show_error_message(self, message)

    def show_success_message(self, message: str):
        show_success_message(self, message)

    def show_quick_message(self, message: str):
        show_quick_message(self, message)

    def show_warning_message(self, message: str):
        show_warning_message(self, message)

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
        for uri in uris_to_disconnect:
            # Cleanup UI caches for VMs on this server
            uuids_to_remove = [
                uuid for uuid, card in self.vm_cards.items()
                if card.conn and (self.vm_service.get_uri_for_connection(card.conn) == uri)
            ]
            for uuid in uuids_to_remove:
                if self.vm_cards[uuid].is_mounted:
                    self.vm_cards[uuid].remove()
                del self.vm_cards[uuid]
                if uuid in self.sparkline_data:
                    del self.sparkline_data[uuid]

            self.vm_service.disconnect(uri)

        self.active_uris = selected_uris
        self.filtered_server_uris = None
        self.current_page = 0

        self.refresh_vm_list()

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
            self.show_quick_message("Loading VM data from remote server(s)...")
            self.refresh_vm_list()

    def action_config(self) -> None:
        """Open the configuration modal."""
        self.push_screen(ConfigModal(self.config), self.handle_config_result)

    def handle_config_result(self, result: dict | None) -> None:
        """Handle the result from the ConfigModal."""
        if result:
            old_cache_ttl = self.config.get("CACHE_TTL")
            old_stats_interval = self.config.get("STATS_INTERVAL")

            self.config = result

            if (self.config.get("CACHE_TTL") != old_cache_ttl or
                self.config.get("STATS_INTERVAL") != old_stats_interval):
                self.show_success_message("Configuration updated. Refreshing VM list...")
                self.refresh_vm_list(force=False, optimize_for_current_page=True)
            else:
                self.show_success_message("Configuration updated.")

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
        """View the application log file."""
        log_path = get_log_path()
        try:
            with open(log_path, "r") as f:
                log_content = f.read()
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
                        self.call_from_thread(self.show_error_message, f"Error launching preferences: {e}")

                self.worker_manager.run(show_prefs, name="launch_server_prefs")
            else:
                self.push_screen(ServerPrefModal(uri=uri))

        self._select_server_and_run(launch_server_prefs, "Select a server for Preferences", "Open")

    def _select_server_and_run(self, callback: callable, modal_title: str, modal_button_label: str) -> None:
        """
        Helper to select a server and run a callback with the selected URI.
        Handles 0, 1, or multiple active servers.
        """
        if len(self.active_uris) == 0:
            self.show_error_message("Not connected to any server.")
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

    @on(VmActionRequest)
    def on_vm_action_request(self, message: VmActionRequest) -> None:
        """Handles a request to perform an action on a VM."""

        def action_worker():
            domain = self.vm_service.find_domain_by_uuid(self.active_uris, message.vm_uuid)
            if not domain:
                self.call_from_thread(self.show_error_message, f"Could not find VM with UUID {message.vm_uuid}")
                return

            #vm_name = domain.name()
            # Use cached identity to avoid extra libvirt call
            _, vm_name = self.vm_service.get_vm_identity(domain)
            try:
                if message.action == VmAction.START:
                    self.vm_service.start_vm(domain)
                    self.call_from_thread(self.show_success_message, f"VM '{vm_name}' started successfully.")
                elif message.action == VmAction.STOP:
                    self.vm_service.stop_vm(domain)
                    self.call_from_thread(self.show_success_message, f"Sent shutdown signal to VM '{vm_name}'.")
                elif message.action == VmAction.PAUSE:
                    self.vm_service.pause_vm(domain)
                    self.call_from_thread(self.show_success_message, f"VM '{vm_name}' paused successfully.")
                elif message.action == VmAction.FORCE_OFF:
                    self.vm_service.force_off_vm(domain)
                    self.call_from_thread(self.show_success_message, f"VM '{vm_name}' forcefully stopped.")
                elif message.action == VmAction.DELETE:
                    self.vm_service.delete_vm(domain, delete_storage=message.delete_storage)
                    self.vm_service.invalidate_domain_cache()
                    self.call_from_thread(self.show_success_message, f"VM '{vm_name}' deleted successfully.")
                elif message.action == VmAction.RESUME:
                    self.vm_service.resume_vm(domain)
                    self.call_from_thread(self.show_success_message, f"VM '{vm_name}' resumed successfully.")
                # Other actions (stop, pause, etc.) will be handled here in the future
                else:
                    self.call_from_thread(self.show_error_message, f"Unknown action '{message.action}' requested.")
                    return

                # If action was successful, refresh the list or update single card
                if message.action in [VmAction.START, VmAction.STOP, VmAction.PAUSE, VmAction.FORCE_OFF, VmAction.RESUME] and self.sort_by == VmStatus.DEFAULT:
                     self.call_from_thread(self.post_message, VmCardUpdateRequest(message.vm_uuid))
                else:
                     self.call_from_thread(self.refresh_vm_list)

            except Exception as e:
                self.call_from_thread(
                    self.show_error_message,
                    f"Error on VM '{vm_name}' during '{message.action}': {e}",
                )

        self.worker_manager.run(
            action_worker, name=f"action_{message.action}_{message.vm_uuid}"
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

        self.show_quick_message("All VMs unselected.")

    @on(VMSelectionChanged)
    def on_vm_selection_changed(self, message: VMSelectionChanged) -> None:
        """Handles when a VM's selection state changes."""
        if message.is_selected:
            self.selected_vm_uuids.add(message.vm_uuid)
        else:
            self.selected_vm_uuids.discard(message.vm_uuid)

    def handle_bulk_action_result(self, result: dict | None) -> None:
        """Handles the result from the BulkActionModal."""
        if result is None:
            return

        action_type = result.get('action')
        delete_storage_flag = result.get('delete_storage', False)

        if not action_type:
            self.show_error_message("No action type received from bulk action modal.")
            return

        selected_uuids_copy = list(self.selected_vm_uuids)  # Take a copy for the worker

        # Handle 'Edit Configuration' separately as it's a UI interaction
        if action_type == 'edit_config':
            # Find domains for the selected UUIDs
            found_domains_map = self.vm_service.find_domains_by_uuids(self.active_uris, selected_uuids_copy)
            selected_domains = list(found_domains_map.values())

            if not selected_domains:
                self.show_error_message("Could not find any of the selected VMs for editing.")
                return

            # Check if all selected VMs are stopped
            active_vms = []
            for domain in selected_domains:
                if domain.isActive():
                    active_vms.append(domain.name())

            if active_vms:
                self.show_error_message(f"All VMs must be stopped for bulk editing. Running VMs: {', '.join(active_vms)}")
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
                    reference_uuid = self.vm_service._get_internal_id(reference_domain)
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
                                invalidate_cache_callback=self.vm_service.invalidate_vm_cache,
                                selected_domains=selected_domains
                            )
                        )
                        # Clear selection after launching modal
                        self.selected_vm_uuids.clear()
                    else:
                        self.show_error_message("Could not load details for reference VM.")
                except Exception as e:
                    self.app.show_error_message(f"Error preparing bulk edit: {e}")

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
                self.call_from_thread(self.show_success_message, f"Bulk action '{action_type}' successful for {len(successful_vms)} VMs.")
            if failed_vms:
                self.call_from_thread(self.show_error_message, f"Bulk action '{action_type}' failed for {len(failed_vms)} VMs.")

        except Exception as e:
            logging.error(f"An unexpected error occurred during bulk action service call: {e}", exc_info=True)
            self.call_from_thread(self.show_error_message, f"A fatal error occurred during bulk action: {e}")

        finally:
            # Ensure these are called on the main thread
            force_refresh = action_type == VmAction.DELETE
            self.call_from_thread(self.refresh_vm_list, force=force_refresh)
            self.call_from_thread(setattr, self, 'bulk_operation_in_progress', False) # Reset flag


    def change_connection(self, uri: str) -> None:
        """Change the active connection to a single server and refresh."""
        logging.info(f"Changing connection to {uri}")
        if not uri or uri.strip() == "":
            return

        self.handle_select_server_result([uri])

    def refresh_vm_list(self, force: bool = False, optimize_for_current_page: bool = False) -> None:
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
                optimize_for_current_page=current_page
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
            optimize_for_current_page: bool = False
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
        except Exception as e:
            self.call_from_thread(self.show_error_message, f"Error fetching VM data: {e}")
            return

        reset_page = False
        if current_page > 0 and current_page * vms_per_page >= total_filtered_vms:
            current_page = 0
            reset_page = True

        start_index = current_page * vms_per_page
        end_index = start_index + vms_per_page
        paginated_domains = domains_to_display[start_index:end_index]

        # Collect data in worker thread
        vm_data_list = []
        page_uuids = set()

        for domain, conn in paginated_domains:
            try:
                uri = self.vm_service.get_uri_for_connection(conn) or conn.getURI()
                uuid, vm_name = self.vm_service.get_vm_identity(domain, conn, known_uri=uri)
                page_uuids.add(uuid)

                # Get info from cache or fetch if not present. This is safe as we are in a worker.
                info = self.vm_service._get_domain_info(domain)
                cached_details = self.vm_service.get_cached_vm_details(uuid)

                if info:
                    status = get_status(domain, state=info[0])
                    cpu = info[3]
                    memory = info[1] // 1024
                elif cached_details:
                    status = cached_details['status']
                    cpu = cached_details['cpu']
                    memory = cached_details['memory']
                else:
                    status = StatusText.LOADING
                    cpu = 0
                    memory = 0

                vm_data = {
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
                vm_data_list.append(vm_data)

            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    logging.warning(f"Skipping display of non-existent VM during refresh.")
                    continue
                else:
                    try:
                        name_for_error = vm_name if 'vm_name' in locals() else domain.name()
                    except:
                        name_for_error = "Unknown"
                    self.call_from_thread(self.show_error_message, f"Error getting info for VM '{name_for_error}': {e}")
                    continue

        # Cleanup cache: remove cards for VMs that no longer exist at all
        all_uuids_from_libvirt = set(all_active_uuids)

        def update_ui():
            if reset_page:
                self.current_page = 0

            # Update visible UUIDs in service
            self.vm_service.update_visible_uuids(page_uuids)

            # Perform cache cleanup on main thread to be safe with widget removal
            cached_uuids = set(self.vm_cards.keys())
            uuids_to_remove_from_cache = cached_uuids - all_uuids_from_libvirt

            for uuid in uuids_to_remove_from_cache:
                logging.info(f"Removing stale VM card from cache: {uuid}")
                if self.vm_cards[uuid].is_mounted:
                    self.vm_cards[uuid].remove()
                del self.vm_cards[uuid]
                if uuid in self.sparkline_data:
                    del self.sparkline_data[uuid]

            # Enforce MAX_VM_CARDS limit
            if len(self.vm_cards) > self.MAX_VM_CARDS:
                # Get UUIDs that are NOT on the current page and remove them until we are under the limit
                off_page_uuids = [uuid for uuid in self.vm_cards.keys() if uuid not in page_uuids]
                # Sort by something? For now just take the first ones
                while len(self.vm_cards) > self.MAX_VM_CARDS and off_page_uuids:
                    uuid_to_remove = off_page_uuids.pop(0)
                    logging.info(f"Removing card from cache (limit reached): {uuid_to_remove}")
                    if self.vm_cards[uuid_to_remove].is_mounted:
                        self.vm_cards[uuid_to_remove].remove()
                    del self.vm_cards[uuid_to_remove]
                    if uuid_to_remove in self.sparkline_data:
                        del self.sparkline_data[uuid_to_remove]

            vms_container = self.ui.get("vms_container")
            if not vms_container:
                return

            # Remove cards from container that are not in the new page layout
            for card in vms_container.query(VMCard):
                try:
                    if not card.vm or card.internal_id not in page_uuids:
                        card.remove()
                except (libvirt.libvirtError, AttributeError):
                    card.remove()

            cards_to_mount = []
            for data in vm_data_list:
                uuid = data['uuid']
                vm_card = self.vm_cards.get(uuid)

                if vm_card:
                    # Update existing card
                    vm_card.vm = data['domain']
                    vm_card.conn = data['conn']
                    vm_card.name = data['name']
                    vm_card.cpu = data['cpu']
                    vm_card.memory = data['memory']
                    vm_card.is_selected = data['is_selected']
                    vm_card.server_border_color = self.get_server_color(data['uri'])
                    vm_card.status = data['status']
                    vm_card.internal_id = uuid
                else:
                    # Create new card
                    if uuid not in self.sparkline_data:
                        self.sparkline_data[uuid] = {"cpu": [], "mem": [], "disk": [], "net": []}

                    vm_card = VMCard(is_selected=data['is_selected'])
                    vm_card.name = data['name']
                    vm_card.status = data['status']
                    vm_card.cpu = data['cpu']
                    vm_card.memory = data['memory']
                    vm_card.vm = data['domain']
                    vm_card.conn = data['conn']
                    vm_card.server_border_color = self.get_server_color(data['uri'])
                    vm_card.cpu_model = ""
                    vm_card.internal_id = uuid
                    self.vm_cards[uuid] = vm_card

                cards_to_mount.append(vm_card)

            # Mount the cards. This will add new ones and re-order existing ones.
            vms_container.mount(*cards_to_mount)

            self.sub_title = f"Servers: {format_server_names(tuple(server_names))}"
            self.update_pagination_controls(total_filtered_vms, total_vms_unfiltered=len(domains_to_display))

        self.call_from_thread(update_ui)


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
            self.show_error_message("No active servers.")
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
            self.show_error_message("No VMs found in cache. Try refreshing first.")
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
                self.show_success_message(f"Selected {len(selected_uuids)} VMs matching pattern.")
                self.refresh_vm_list()

        self.push_screen(PatternSelectModal(available_vms, available_servers, selected_servers), handle_result)

    @on(Button.Pressed, "#bulk_selected_vms")
    def on_bulk_selected_vms_button_pressed(self) -> None:
        """Handles the 'Bulk Selected' button press."""
        if not self.selected_vm_uuids:
            self.show_error_message("No VMs selected.")
            return

        uuids_snapshot = list(self.selected_vm_uuids)

        def get_names_and_show_modal():
            """Worker to fetch VM names and display the bulk action modal."""
            uuids = uuids_snapshot

            # Use the service to find specific domains by their internal ID (UUID@URI)
            # This correctly handles cases where identical UUIDs exist on different servers
            found_domains_map = self.vm_service.find_domains_by_uuids(self.active_uris, uuids)

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
                self.call_from_thread(
                    self.show_error_message, "Could not retrieve names for selected VMs."
                )

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
        vm_uuid = message.vm_uuid
        logging.info(f"Only refresh: {vm_uuid}")
        def update_single_card():
            try:
                domain = self.vm_service.find_domain_by_uuid(self.active_uris, vm_uuid)
                if not domain:
                    return

                # Use cached methods to minimize libvirt calls
                state_tuple = self.vm_service._get_domain_state(domain)
                if not state_tuple:
                    return

                state, _ = state_tuple

                # Only fetch full info if VM is running/paused
                if state in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                    info = self.vm_service._get_domain_info(domain)
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
                    cached_details = self.vm_service.get_cached_vm_details(vm_uuid)
                    if cached_details:
                        cpu = cached_details['cpu']
                        memory = cached_details['memory']
                    else:
                        cpu = 0
                        memory = 0
               # Update card on main thread
                def update_ui():
                    card = self.vm_cards.get(vm_uuid)
                    if card and card.is_mounted:
                        card.status = status
                        card.cpu = cpu
                        card.memory = memory

                self.call_from_thread(update_ui)

            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    self.vm_service.invalidate_vm_cache(vm_uuid)
                logging.debug(f"Error updating card for {vm_uuid}: {e}")

        self.worker_manager.run(
            update_single_card, name=f"update_card_{vm_uuid}"
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
