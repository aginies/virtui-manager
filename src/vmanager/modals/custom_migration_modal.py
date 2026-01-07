
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Static, Select, Checkbox
from textual.containers import Vertical

class CustomMigrationModal(ModalScreen[dict | None]):
    """A modal to confirm custom migration actions."""

    def __init__(self, actions: list[dict], **kwargs):
        super().__init__(**kwargs)
        self.actions = actions
        self.selections = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="custom-migration-dialog"):
            yield Static("[bold]Custom Migration Plan[/bold]")

            for i, action in enumerate(self.actions):
                if action["type"] == "move_volume":
                    yield Static(f"Disk: [b]{action['volume_name']}[/b]")
                    yield Static(f"  Source Pool: {action['source_pool']}")
                    dest_pools = action.get("dest_pools", [])
                    if dest_pools:
                        yield Select(
                            [(pool, pool) for pool in dest_pools],
                            prompt="Select Destination Pool",
                            id=f"pool-select-{i}"
                        )
                    else:
                        yield Static("  No destination pools available.")
                elif action["type"] == "manual_copy":
                    yield Static(f"Disk: [b]{action['disk_path']}[/b]")
                    yield Static(f"  Action: {action['message']}")

            yield Checkbox("Undefine source VM", value=True, id="undefine-checkbox")

            with Vertical(classes="modal-buttons"):
                yield Button("Confirm", variant="primary", id="confirm")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            for i, action in enumerate(self.actions):
                if action["type"] == "move_volume":
                    select = self.query_one(f"#pool-select-{i}", Select)
                    self.selections[i] = select.value

            self.selections['undefine_source'] = self.query_one("#undefine-checkbox").value
            self.dismiss(self.selections)
        else:
            self.dismiss(None)
