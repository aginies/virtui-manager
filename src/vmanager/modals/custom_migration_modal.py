
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Static, Select, Checkbox
from textual.containers import Vertical
from ..constants import StaticText, ButtonLabels

class CustomMigrationModal(ModalScreen[dict | None]):
    """A modal to confirm custom migration actions."""

    def __init__(self, actions: list[dict], **kwargs):
        super().__init__(**kwargs)
        self.actions = actions
        self.selections = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="custom-migration-dialog"):
            yield Static(StaticText.CUSTOM_MIGRATION_PLAN)

            for i, action in enumerate(self.actions):
                if action["type"] == "move_volume":
                    yield Static(StaticText.DISK_VOLUME_NAME.format(volume_name=action['volume_name']))
                    yield Static(StaticText.SOURCE_POOL.format(source_pool=action['source_pool']))
                    dest_pools = action.get("dest_pools", [])
                    if dest_pools:
                        yield Select(
                            [(pool, pool) for pool in dest_pools],
                            prompt="Select Destination Pool",
                            id=f"pool-select-{i}"
                        )
                    else:
                        yield Static(StaticText.NO_DESTINATION_POOLS)
                elif action["type"] == "manual_copy":
                    yield Static(StaticText.DISK_PATH.format(disk_path=action['disk_path']))
                    yield Static(StaticText.ACTION_MESSAGE.format(message=action['message']))

            yield Checkbox(StaticText.UNDEFINE_SOURCE_VM, value=True, id="undefine-checkbox")

            with Vertical(classes="modal-buttons"):
                yield Button(ButtonLabels.CONFIRM, variant="primary", id="confirm")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel")

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
