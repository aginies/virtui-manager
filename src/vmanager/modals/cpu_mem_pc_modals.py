"""
CPU MEM Machine type modals
"""
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Input, Label, ListView, Select

from ..constants import ErrorMessages, StaticText, ButtonLabels
from .base_modals import BaseModal, ValueListItem
from .utils_modals import InfoModal


class EditCpuModal(BaseModal[str | None]):
    """Modal screen for editing VCPU count."""

    def __init__(self, current_cpu: str = "") -> None:
        super().__init__()
        self.current_cpu = current_cpu

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-cpu-dialog", classes="edit-cpu-dialog"):
            yield Label(StaticText.ENTER_NEW_VCPU_COUNT)
            yield Input(placeholder="e.g., 2", id="cpu-input", type="integer", value=self.current_cpu)
            with Horizontal():
                yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            cpu_input = self.query_one("#cpu-input", Input)
            self.dismiss(cpu_input.value)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

class EditMemoryModal(BaseModal[str | None]):
    """Modal screen for editing memory size."""

    def __init__(self, current_memory: str = "") -> None:
        super().__init__()
        self.current_memory = current_memory

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-memory-dialog", classes="edit-memory-dialog"):
            yield Label(StaticText.ENTER_NEW_MEMORY_SIZE)
            yield Input(placeholder="e.g., 2048", id="memory-input", type="integer", value=self.current_memory)
            with Horizontal():
                yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            memory_input = self.query_one("#memory-input", Input)
            self.dismiss(memory_input.value)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

class SelectMachineTypeModal(BaseModal[str | None]):
    """Modal screen for selecting machine type."""

    def __init__(self, machine_types: list[str], current_machine_type: str = "") -> None:
        super().__init__()
        self.machine_types = machine_types
        self.current_machine_type = current_machine_type

    def compose(self) -> ComposeResult:
        with Vertical(id="select-machine-type-dialog", classes="select-machine-type-dialog"):
            yield Label(StaticText.SELECT_MACHINE_TYPE)
            with ScrollableContainer():
                yield ListView(
                    *[ValueListItem(Label(mt), value=mt) for mt in self.machine_types],
                    id="machine-type-list",
                    classes="machine-type-list"
                )
            with Horizontal():
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        list_view = self.query_one(ListView)
        try:
            #self.query_one(DirectoryTree).focus()
            current_index = self.machine_types.index(self.current_machine_type)
            list_view.index = current_index
        except (ValueError, IndexError):
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.item.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)

class EditCpuTuneModal(BaseModal[list[dict] | None]):
    """Modal screen for editing CPU Tune (vcpupin)."""

    def __init__(self, current_vcpupin: list[dict] = None, max_vcpus: int = 0) -> None:
        super().__init__()
        self.current_vcpupin = current_vcpupin or []
        self.max_vcpus = max_vcpus

    def compose(self) -> ComposeResult:
        # Convert list of dicts to string format: "0:0-1;1:2-3"
        current_val = "; ".join([f"{p['vcpu']}:{p['cpuset']}" for p in self.current_vcpupin])

        with Vertical(id="edit-cpu-tune-dialog", classes="edit-cpu-dialog"):
            yield Label(StaticText.ENTER_CPU_PINNING.format(max_vcpus=self.max_vcpus - 1))
            yield Label(StaticText.CPU_PINNING_FORMAT, classes="help-text")
            yield Input(placeholder="e.g., 0:0-1; 1:2-3", id="cputune-input", value=current_val)
            with Horizontal():
                yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")
                yield Button(ButtonLabels.HELP, variant="default", id="help-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            inp = self.query_one("#cputune-input", Input).value
            try:
                vcpupin_list = []
                if inp.strip():
                    parts = inp.split(';')
                    for part in parts:
                        if ':' in part:
                            vcpu, cpuset = part.split(':')
                            vcpu = vcpu.strip()
                            cpuset = cpuset.strip()

                            # Validate vcpu is integer and within range
                            vcpu_int = int(vcpu)
                            if self.max_vcpus > 0 and vcpu_int >= self.max_vcpus:
                                raise ValueError(f"vCPU {vcpu_int} exceeds max vCPUs ({self.max_vcpus})")

                            # Validate cpuset syntax (basic check)
                            if not all(c.isdigit() or c in ',-' for c in cpuset):
                                raise ValueError(f"Invalid cpuset syntax: {cpuset}")

                            vcpupin_list.append({'vcpu': vcpu, 'cpuset': cpuset})
                self.dismiss(vcpupin_list)
            except ValueError as e:
                self.app.show_error_message(ErrorMessages.VALIDATION_ERROR_TEMPLATE.format(error=e))
            except Exception as e:
                self.app.show_error_message(ErrorMessages.INVALID_FORMAT_TEMPLATE.format(error=e))
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "help-btn":
            help_text = """
# CPU Tune Help

## CPU Pinning (vcpupin)
Specify which physical CPUs (host CPUs) each virtual CPU (guest CPU) can run on.

## Format
`vcpu:cpuset; vcpu:cpuset; ...`

* **vcpu**: The ID of the virtual CPU.
* **cpuset**: A list or range of physical CPU IDs.

## Examples
* `0:0`: Pin vCPU 0 to physical CPU 0.
* `0:0-3`: Pin vCPU 0 to physical CPUs 0, 1, 2, and 3.
* `0:0,2`: Pin vCPU 0 to physical CPUs 0 and 2.
* `0:0-1; 1:2-3`: Pin vCPU 0 to physical CPUs 0-1, and vCPU 1 to physical CPUs 2-3.
            """
            self.app.push_screen(InfoModal("CPU Tuning Help", help_text))

class EditNumaTuneModal(BaseModal[dict | None]):
    """Modal screen for editing NUMA Tune."""

    def __init__(self, current_mode: str = "strict", current_nodeset: str = "") -> None:
        super().__init__()
        self.current_mode = current_mode if current_mode else "None"
        self.current_nodeset = current_nodeset

    def compose(self) -> ComposeResult:
        modes = [("strict", "strict"), ("preferred", "preferred"), ("interleave", "interleave"), ("None", "None")]

        with Vertical(id="edit-numatune-dialog", classes="edit-cpu-dialog"):
            yield Label(StaticText.NUMA_MEMORY_MODE)
            yield Select(modes, value=self.current_mode, id="numa-mode-select", allow_blank=False)
            yield Label(StaticText.NODESET)
            yield Input(placeholder="e.g., 0-1", id="numa-nodeset-input", value=self.current_nodeset)
            with Horizontal():
                yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")
                yield Button(ButtonLabels.HELP, variant="default", id="help-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            mode = self.query_one("#numa-mode-select", Select).value
            nodeset = self.query_one("#numa-nodeset-input", Input).value

            if mode == "None":
                self.dismiss({'mode': None, 'nodeset': None})
                return

            # Validate nodeset syntax
            if nodeset:
                if not all(c.isdigit() or c in ',-' for c in nodeset):
                     self.app.show_error_message(ErrorMessages.INVALID_NODESET_SYNTAX_TEMPLATE.format(nodeset=nodeset))
                     return

            self.dismiss({'mode': mode, 'nodeset': nodeset})
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "help-btn":
            help_text = """
# NUMA Tune Help

## Memory Modes
* **strict**: Memory allocation fails if it cannot be satisfied on the specified nodeset.
* **preferred**: Allocation is preferred on the specified nodeset but can fall back to others.
* **interleave**: Memory is allocated round-robin across the specified nodeset.

## Nodeset
Specify the NUMA nodes to use.
* Example: `0` (Node 0 only)
* Example: `0-1` (Nodes 0 and 1)
* Example: `0,2-3` (Node 0 and nodes 2 through 3)
            """
            self.app.push_screen(InfoModal("NUMA Tuning Help", help_text))
