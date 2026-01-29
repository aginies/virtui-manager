"""
Basic modal to show stat of cache
"""
from textual.app import ComposeResult
from textual.widgets import Static, Button, DataTable
from textual.containers import Vertical, Horizontal
from .base_modals import BaseModal
from ..constants import StaticText, SuccessMessages, ButtonLabels

class CacheStatsModal(BaseModal[None]):
    """Modal displaying cache statistics in a table."""

    def __init__(self, cache_monitor):
        super().__init__()
        self.cache_monitor = cache_monitor

    def compose(self) -> ComposeResult:
        with Vertical(id="cache-stats-dialog"):
            yield Static(StaticText.CACHE_PERFORMANCE_STATISTICS, classes="dialog-title")
            yield DataTable(id="stats-table")
        with Vertical():
            with Horizontal():
                yield Button(ButtonLabels.REFRESH, id="refresh-btn", variant="primary")
                yield Button(ButtonLabels.CLEAR_CACHES, id="clear-btn", variant="error")
                yield Button(ButtonLabels.CLOSE, id="close-btn", variant="default")

    def on_mount(self) -> None:
        """Setup the table."""
        self._update_table()

    def _update_table(self) -> None:
        """Update the statistics table."""
        table = self.query_one("#stats-table", DataTable)
        table.clear(columns=True)

        # Add columns
        table.add_column("Function", key="function")
        table.add_column("Hit Rate", key="hit_rate")
        table.add_column("Hits", key="hits")
        table.add_column("Misses", key="misses")
        table.add_column("Size", key="size")
        table.add_column("Efficiency", key="efficiency")

        # Add rows
        stats = self.cache_monitor.get_all_stats()
        for name, data in stats.items():
            table.add_row(
                name,
                f"{data['hit_rate']:.1f}%",
                str(data['hits']),
                str(data['misses']),
                f"{data['current_size']}/{data['max_size']}",
                data['efficiency']
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-btn":
            self._update_table()
        elif event.button.id == "clear-btn":
            self.cache_monitor.clear_all_caches()
            self._update_table()
            self.app.show_success_message(SuccessMessages.ALL_CACHES_CLEARED)
        elif event.button.id == "close-btn":
            self.dismiss()
