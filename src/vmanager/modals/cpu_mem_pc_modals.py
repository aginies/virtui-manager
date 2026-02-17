"""
CPU MEM Machine type modals
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Input, Label, ListView, Select

from ..constants import ButtonLabels, ErrorMessages, StaticText
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
            yield Input(
                placeholder=StaticText.VCPU_COUNT_EXAMPLE,
                id="cpu-input",
                type="integer",
                value=self.current_cpu,
            )
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
            yield Input(
                placeholder=StaticText.MEMORY_SIZE_EXAMPLE,
                id="memory-input",
                type="integer",
                value=self.current_memory,
            )
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
                    classes="machine-type-list",
                )
            with Horizontal():
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        list_view = self.query_one(ListView)
        try:
            # self.query_one(DirectoryTree).focus()
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
            yield Input(
                placeholder=StaticText.CPU_PINNING_EXAMPLE, id="cputune-input", value=current_val
            )
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
                    parts = inp.split(";")
                    for part in parts:
                        if ":" in part:
                            vcpu, cpuset = part.split(":")
                            vcpu = vcpu.strip()
                            cpuset = cpuset.strip()

                            # Validate vcpu is integer and within range
                            vcpu_int = int(vcpu)
                            if self.max_vcpus > 0 and vcpu_int >= self.max_vcpus:
                                raise ValueError(
                                    ErrorMessages.VCPU_EXCEEDS_MAX_TEMPLATE.format(
                                        vcpu_int=vcpu_int, max_vcpus=self.max_vcpus
                                    )
                                )

                            # Validate cpuset syntax (basic check)
                            if not all(c.isdigit() or c in ",-" for c in cpuset):
                                raise ValueError(
                                    ErrorMessages.INVALID_CPUSET_SYNTAX_TEMPLATE.format(
                                        cpuset=cpuset
                                    )
                                )

                            vcpupin_list.append({"vcpu": vcpu, "cpuset": cpuset})
                self.dismiss(vcpupin_list)
            except ValueError as e:
                self.app.show_error_message(ErrorMessages.VALIDATION_ERROR_TEMPLATE.format(error=e))
            except Exception as e:
                self.app.show_error_message(ErrorMessages.INVALID_FORMAT_TEMPLATE.format(error=e))
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "help-btn":
            self.app.push_screen(
                InfoModal(StaticText.CPU_TUNE_HELP_TITLE, StaticText.CPU_TUNE_HELP_TEXT)
            )


class EditNumaTuneModal(BaseModal[dict | None]):
    """Modal screen for editing NUMA Tune."""

    def __init__(self, current_mode: str = "strict", current_nodeset: str = "") -> None:
        super().__init__()
        self.current_mode = current_mode if current_mode else "None"
        self.current_nodeset = current_nodeset

    def compose(self) -> ComposeResult:
        modes = [
            ("strict", "strict"),
            ("preferred", "preferred"),
            ("interleave", "interleave"),
            ("None", "None"),
        ]

        with Vertical(id="edit-numatune-dialog", classes="edit-cpu-dialog"):
            yield Label(StaticText.NUMA_MEMORY_MODE)
            yield Select(modes, value=self.current_mode, id="numa-mode-select", allow_blank=False)
            yield Label(StaticText.NODESET)
            yield Input(
                placeholder=StaticText.NODESET_EXAMPLE,
                id="numa-nodeset-input",
                value=self.current_nodeset,
            )
            with Horizontal():
                yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")
                yield Button(ButtonLabels.HELP, variant="default", id="help-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            mode = self.query_one("#numa-mode-select", Select).value
            nodeset = self.query_one("#numa-nodeset-input", Input).value

            if mode == "None":
                self.dismiss({"mode": None, "nodeset": None})
                return

            # Validate nodeset syntax
            if nodeset:
                if not all(c.isdigit() or c in ",-" for c in nodeset):
                    self.app.show_error_message(
                        ErrorMessages.INVALID_NODESET_SYNTAX_TEMPLATE.format(nodeset=nodeset)
                    )
                    return

            self.dismiss({"mode": mode, "nodeset": nodeset})
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "help-btn":
            self.app.push_screen(
                InfoModal(StaticText.NUMA_TUNE_HELP_TITLE, StaticText.NUMA_TUNE_HELP_TEXT)
            )
