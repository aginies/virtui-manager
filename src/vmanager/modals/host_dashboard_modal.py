"""
Modal for displaying Host Resource Dashboard.
"""
import libvirt
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Grid
from textual.screen import ModalScreen
from textual.widgets import Label, Button, ProgressBar, Static, Rule, TabbedContent, TabPane
from textual import on, work

from .base_modals import BaseModal
from ..constants import StaticText, ButtonLabels
from ..libvirt_utils import get_host_resources, get_total_vm_allocation
from ..constants import TabTitles

class HostDashboardModal(BaseModal[None]):
    """Modal to show host resource usage and VM allocations."""

    def __init__(self, conn: libvirt.virConnect, server_name: str = "Unknown") -> None:
        super().__init__()
        self.conn = conn
        self.server_name = server_name
        self.host_resources = get_host_resources(conn)
        self.vm_allocation = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="host-dashboard-dialog"):
            yield Label(f"{StaticText.HOST_RESOURCE_DASHBOARD} - {self.server_name}", id="dialog-title")

            with Vertical(classes="info-section-dashboard"):
                yield Label(StaticText.HOST_DETAILS, classes="section-title-dashboard")
                with Grid(classes="host-info-grid"):
                    yield Label(StaticText.MODEL)
                    yield Label(self.host_resources.get('model', 'N/A'))
                    yield Label("CPUs:")
                    yield Label(f"{self.host_resources.get('total_cpus', 0)} ({self.host_resources.get('mhz', 0)} MHz)")
                    yield Label(StaticText.TOPOLOGY)
                    yield Label(f"{self.host_resources.get('nodes', 1)} nodes, {self.host_resources.get('sockets', 1)} sockets, {self.host_resources.get('cores', 1)} cores, {self.host_resources.get('threads', 1)} threads")
            
            with Vertical(classes="info-section-dashboard"):
                yield Label(StaticText.MEMORY_USAGE, classes="section-title-dashboard")
                total_mem = self.host_resources.get('available_memory', 0)
                free_mem = self.host_resources.get('free_memory', 0)
                used_mem = total_mem - free_mem
                mem_pct = (used_mem / total_mem * 100) if total_mem > 0 else 0
                
                with Horizontal(classes="usage-row"):
                    yield Label(f"{used_mem/1024:.1f} GB / {total_mem/1024:.1f} GB ({mem_pct:.1f}%)")
                    yield ProgressBar(total=100, show_percentage=False, show_eta=False, id="mem-bar")
            
            with Vertical(classes="info-section-dashboard"):
                yield Label(StaticText.VM_ALLOCATION, classes="section-title-dashboard")
                yield Label(StaticText.WAITING_TO_START_COLLECTION, id="progress-label")
                
                with TabbedContent():
                    with TabPane(TabTitles.ACTIVE_ALLOCATION, id="tab-active"):
                        # Active CPU Allocation
                        yield Label(f"{StaticText.ALLOCATED_VCPUS} (Active):", id="cpu-alloc-label-active")
                        yield ProgressBar(total=100, show_percentage=False, show_eta=False, id="cpu-alloc-bar-active")
                        yield Rule()
                        # Active Memory Allocation
                        yield Label(f"{StaticText.ALLOCATED_MEMORY} (Active):", id="mem-alloc-label-active")
                        yield ProgressBar(total=100, show_percentage=False, show_eta=False, id="mem-alloc-bar-active")
                    with TabPane(TabTitles.TOTAL_ALLOCATION, id="tab-total"):
                        # Total CPU Allocation
                        yield Label(f"{StaticText.ALLOCATED_VCPUS} (Total):", id="cpu-alloc-label-total")
                        yield ProgressBar(total=100, show_percentage=False, show_eta=False, id="cpu-alloc-bar-total")
                        yield Rule()
                        # Total Memory Allocation
                        yield Label(f"{StaticText.ALLOCATED_MEMORY} (Total):", id="mem-alloc-label-total")
                        yield ProgressBar(total=100, show_percentage=False, show_eta=False, id="mem-alloc-bar-total")

            with Horizontal(classes="dialog-buttons"):
                yield Button(ButtonLabels.CLOSE, id="close-btn", variant="primary")

    def on_mount(self) -> None:
        # Update progress bars
        total_mem = self.host_resources.get('available_memory', 0)
        free_mem = self.host_resources.get('free_memory', 0)
        used_mem = total_mem - free_mem
        mem_pct = (used_mem / total_mem * 100) if total_mem > 0 else 0
        self.query_one("#mem-bar", ProgressBar).update(progress=mem_pct)
        
        self.compute_vm_allocation()

    @work(thread=True)
    def compute_vm_allocation(self) -> None:
        def progress(current, total):
            self.app.call_from_thread(self.update_progress, current, total)
        
        self.vm_allocation = get_total_vm_allocation(self.conn, progress_callback=progress)
        self.app.call_from_thread(self.update_dashboard)

    def update_progress(self, current: int, total: int) -> None:
        try:
            lbl = self.query_one("#progress-label", Label)
            lbl.update(StaticText.COLLECTING_VM_INFO.format(current=current, total=total))
        except:
            pass

    def update_dashboard(self) -> None:
        # Hide progress label
        try:
            self.query_one("#progress-label", Label).styles.display = "none"
        except:
            pass
        
        total_host_cpus = self.host_resources.get('total_cpus', 1)
        total_mem = self.host_resources.get('available_memory', 0)

        # Total Allocation
        alloc_cpus_total = self.vm_allocation.get('total_allocated_vcpus', 0)
        cpu_alloc_pct_total = (alloc_cpus_total / total_host_cpus * 100)
        
        self.query_one("#cpu-alloc-bar-total", ProgressBar).update(progress=cpu_alloc_pct_total)
        self.query_one("#cpu-alloc-label-total", Label).update(f"{StaticText.ALLOCATED_VCPUS} (Total): {alloc_cpus_total} / {total_host_cpus} ({cpu_alloc_pct_total:.1f}%)")
        
        alloc_mem_total = self.vm_allocation.get('total_allocated_memory', 0)
        mem_alloc_pct_total = (alloc_mem_total / total_mem * 100) if total_mem > 0 else 0
        
        self.query_one("#mem-alloc-bar-total", ProgressBar).update(progress=mem_alloc_pct_total)
        self.query_one("#mem-alloc-label-total", Label).update(f"{StaticText.ALLOCATED_MEMORY} (Total): {alloc_mem_total/1024:.1f} GB / {total_mem/1024:.1f} GB ({mem_alloc_pct_total:.1f}%)")

        # Active Allocation
        alloc_cpus_active = self.vm_allocation.get('active_allocated_vcpus', 0)
        cpu_alloc_pct_active = (alloc_cpus_active / total_host_cpus * 100)
        self.query_one("#cpu-alloc-bar-active", ProgressBar).update(progress=cpu_alloc_pct_active)
        self.query_one("#cpu-alloc-label-active", Label).update(f"{StaticText.ALLOCATED_VCPUS} (Active): {alloc_cpus_active} / {total_host_cpus} ({cpu_alloc_pct_active:.1f}%)")

        alloc_mem_active = self.vm_allocation.get('active_allocated_memory', 0)
        mem_alloc_pct_active = (alloc_mem_active / total_mem * 100) if total_mem > 0 else 0
        self.query_one("#mem-alloc-bar-active", ProgressBar).update(progress=mem_alloc_pct_active)
        self.query_one("#mem-alloc-label-active", Label).update(f"{StaticText.ALLOCATED_MEMORY} (Active): {alloc_mem_active/1024:.1f} GB / {total_mem/1024:.1f} GB ({mem_alloc_pct_active:.1f}%)")

    @on(Button.Pressed, "#close-btn")
    def on_close_btn_pressed(self) -> None:
        self.dismiss()
