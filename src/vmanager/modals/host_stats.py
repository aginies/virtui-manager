"""
Host Stats modals
"""
import logging
import threading
from textual.app import ComposeResult
from textual.widgets import Static, Label
from textual.reactive import reactive
from textual.events import Click, Message
from textual.worker import get_current_worker

from ..libvirt_utils import get_host_resources, get_active_vm_allocation
from ..utils import extract_server_name_from_uri

class SingleHostStat(Static):
    """
    Displays stats for a single host.
    """
    server_name = reactive("")

    class ServerLabelClicked(Message):
        """Posted when the server label is clicked."""
        def __init__(self, server_uri: str, server_name: str) -> None:
            super().__init__()
            self.server_uri = server_uri
            self.server_name = server_name

    DEFAULT_CSS = """
    SingleHostStat {
        layout: horizontal;
        height: auto;
        padding: 0 1;
        background: $boost;
        align-vertical: middle;
    }
    .stat-label {
        color: $text;
    }
    """

    def __init__(self, uri: str, name: str, vm_service, server_color: str = "white"):
        super().__init__()
        self.uri = uri
        self.server_name = name
        self.vm_service = vm_service
        self.server_color = server_color
        self.server_label = Label("", id=f"single_host_stat_label_{self.server_name.replace(' ', '_').replace('.', '_')}")
        self.cpu_label = Label("", classes="stat-label")
        self.mem_label = Label("", classes="stat-label")
        self.host_res = None

    def compose(self) -> ComposeResult:
        self.server_label.styles.color = self.server_color
        self.server_label.styles.text_style = "bold"
        self.server_label.update(f"{self.server_name} ")
        yield self.server_label
        yield self.cpu_label
        yield Label(" ")
        yield self.mem_label

    def on_click(self, event: Click) -> None:
        """Called when the user clicks on a widget."""
        if event.control.id == self.server_label.id:
            self.post_message(self.ServerLabelClicked(self.uri, self.server_name))

    def update_stats(self):
        """Fetches and updates stats for this host."""
        def _fetch_and_update():
            try:
                # Check cancellation before potentially expensive op
                try:
                    if get_current_worker().is_cancelled:
                        return
                except Exception:
                    pass

                conn = self.vm_service.connect(self.uri)
                if not conn:
                    if threading.current_thread() is threading.main_thread():
                        self.cpu_label.update("Offline")
                        self.mem_label.update("Offline")
                    else:
                        self.app.call_from_thread(self.cpu_label.update, "Offline")
                        self.app.call_from_thread(self.mem_label.update, "Offline")
                    return

                if self.host_res is None:
                    self.host_res = get_host_resources(conn)

                current_alloc = get_active_vm_allocation(conn)
                
                # Check cancellation again after expensive op
                try:
                    if get_current_worker().is_cancelled:
                        return
                except Exception:
                    pass

                total_cpus = self.host_res.get('total_cpus', 1)
                total_mem = self.host_res.get('available_memory', 1) # MB

                used_cpus = current_alloc.get('active_allocated_vcpus', 0)
                used_mem = current_alloc.get('active_allocated_memory', 0) # MB

                cpu_pct = (used_cpus / total_cpus) * 100
                mem_pct = (used_mem / total_mem) * 100
                # Format memory string (GB if > 1024 MB)
                def fmt_mem(mb):
                    if mb >= 1024:
                        return f"{mb/1024:.1f}G"
                    return f"{mb}M"

                # UI Updates need to be on main thread
                def _update_ui():
                    def get_status_bck(pct):
                        if pct >= 90:
                            return ("red")
                        if pct >= 75:
                            return ("orange")
                        if pct >= 55:
                            return ("yellow")
                        return ("green")

                    self.cpu_label.update(f"{used_cpus}/{total_cpus}CPU")
                    self.cpu_label.styles.background = get_status_bck(cpu_pct)

                    self.mem_label.update(f"{fmt_mem(used_mem)}/{fmt_mem(total_mem)}")
                    self.mem_label.styles.background = get_status_bck(mem_pct)

                if threading.current_thread() is threading.main_thread():
                    _update_ui()
                else:
                    self.app.call_from_thread(_update_ui)

            except Exception as e:
                logging.error(f"Error updating host stats for {self.name}: {e}")
                if threading.current_thread() is threading.main_thread():
                    self.cpu_label.update("Err")
                    self.mem_label.update("Err")
                else:
                    self.app.call_from_thread(self.cpu_label.update, "Err")
                    self.app.call_from_thread(self.mem_label.update, "Err")

        _fetch_and_update()

class HostStats(Static):
    """
    Container for multiple SingleHostStat widgets.
    """
    DEFAULT_CSS = """
    HostStats {
        layout: grid;
        grid-size: 3;
        overflow-y: auto;
        margin-bottom: 0;
        margin-top: 0;
    }
    """

    def __init__(self, vm_service, get_server_color_callback):
        super().__init__()
        self.vm_service = vm_service
        self.get_server_color = get_server_color_callback
        self.active_hosts = {}

    def update_hosts(self, active_uris, servers):
        """
        Reconciles the list of active hosts.
        """
        current_uris = set(active_uris)
        existing_uris = set(self.active_hosts.keys())

        # Remove stale
        for uri in existing_uris - current_uris:
            widget = self.active_hosts.pop(uri)
            widget.remove()

        # Add new hosts
        for uri in current_uris - existing_uris:
            name = self._get_server_name(uri, servers)
            color = self.get_server_color(uri)
            widget = SingleHostStat(uri, name, self.vm_service, color)
            self.active_hosts[uri] = widget
            self.mount(widget)
            self.app.set_timer(0.5, widget.update_stats)

        if current_uris:
            self.styles.display = "block"
        else:
            self.styles.display = "none"

    def _get_server_name(self, uri: str, servers) -> str:
        """Helper to get server name from URI."""
        if servers:
            for s in servers:
                if s['uri'] == uri:
                    return s.get('name', extract_server_name_from_uri(uri))
        return extract_server_name_from_uri(uri)

    def refresh_stats(self):
        """Triggers update on all children."""
        for widget in self.active_hosts.values():
            widget.update_stats()
