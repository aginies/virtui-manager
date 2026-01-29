"""
Modal for user configuration
"""
import shutil
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual import on
from textual.widgets import Label, Button, Input, Checkbox, Static, Select

from ..config import save_config, get_user_config_path
from ..constants import AppInfo, WarningMessages, SuccessMessages, ErrorMessages, StaticText, ButtonLabels
from .base_modals import BaseModal
from ..utils import check_r_viewer

class ConfigModal(BaseModal[None]):
    """Modal screen for configuring the application."""

    def __init__(self, config: dict) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="config-dialog"):
            yield Label(f"{AppInfo.namecase} Configuration", id="config-title")
            yield Static(StaticText.EDITING_CONFIG_PATH.format(get_user_config_path=get_user_config_path()), id="config-title-file") #classes="config-path-label")
            with ScrollableContainer():
                # Performance settings
                yield Label(StaticText.PERFORMANCE, classes="config-section-label")
                with Horizontal():
                    yield Label(StaticText.STATS_INTERVAL)
                    yield Input(
                        value=str(self.config.get("STATS_INTERVAL", 5)),
                        id="stats-interval-input",
                        type="integer",
                        tooltip="Interval for updating VM Statusn, Statistics (CPU, Memory, I/O) in seconds."
                    )

                # Logging settings
                yield Label(StaticText.LOG_FILE_PATH)
                yield Input(
                    value=self.config.get("LOG_FILE_PATH", ""),
                    id="log-file-path-input",
                    tooltip="Full path to the application log file"
                )

                yield Label(StaticText.LOGGING_LEVEL)
                yield Select(
                    [
                        ("DEBUG", "DEBUG"),
                        ("INFO", "INFO"),
                        ("WARNING", "WARNING"),
                        ("ERROR", "ERROR"),
                        ("CRITICAL", "CRITICAL"),
                    ],
                    value=self.config.get("LOG_LEVEL", "INFO"),
                    id="log-level-select",
                    prompt="Select a logging level"
                    )
                    
                # Remote Viewer Settings
                yield Label(StaticText.REMOTE_VIEWER)
                   
                viewers = []
                if shutil.which("virtui-remote-viewer"):
                    viewers.append(("virtui-remote-viewer", "virtui-remote-viewer"))
                if shutil.which("virt-viewer"):
                    viewers.append(("virt-viewer", "virt-viewer"))

                current_viewer = self.config.get("REMOTE_VIEWER")
                if current_viewer not in [v[1] for v in viewers]:
                    current_viewer = Select.BLANK

                if not viewers:
                     yield Label(StaticText.NO_REMOTE_VIEWERS_FOUND)
                else:
                    auto_detected = check_r_viewer()
                    yield Label(f"Select Default Remote Viewer (Auto-detect: {auto_detected}):")
                    yield Select(
                        viewers,
                        value=current_viewer,
                        id="remote-viewer-select",
                        allow_blank=True,
                        prompt="Select a viewer"
                        )

                # Web console settings
                yield Label(StaticText.WEB_CONSOLE_NOVNC, classes="config-section-label")
                yield Checkbox(
                    StaticText.ENABLE_REMOTE_WEBCONSOLE,
                    self.config.get("REMOTE_WEBCONSOLE", False),
                    id="remote-webconsole-checkbox",
                    tooltip="Enable secure SSH and noVNC remote viewing for headless server environments"
                )
                yield Label(StaticText.WEBSOCKIFY_PATH)
                yield Input(
                    value=self.config.get("websockify_path", "/usr/bin/websockify"),
                    id="websockify-path-input",
                    tooltip="Path to the websockify binary"
                )
                yield Label(StaticText.NOVNC_PATH)
                yield Input(
                    value=self.config.get("novnc_path", "/usr/share/novnc/"),
                    id="novnc-path-input",
                    tooltip="Path to noVNC files"
                )
                with Horizontal(classes="port-range-container"):
                    yield Label(StaticText.WEBSOCKIFY_PORT_RANGE, classes="port-range-label")
                    yield Input(
                        value=str(self.config.get("WC_PORT_RANGE_START", 40000)),
                        id="wc-port-start-input",
                        type="integer",
                        classes="port-range-input",
                        tooltip="Start port for websockify"
                    )
                    yield Input(
                        value=str(self.config.get("WC_PORT_RANGE_END", 40050)),
                        id="wc-port-end-input",
                        type="integer",
                        classes="port-range-input",
                        tooltip="End port for websockify"
                    )
                with Vertical():
                    with Horizontal():
                        yield Label(StaticText.VNC_QUALITY)
                        yield Input(
                            value=str(self.config.get("VNC_QUALITY", 0)),
                            id="vnc-quality-input",
                            type="integer",
                            tooltip="VNC quality setting (0-9)"
                        )
                        yield Label(StaticText.VNC_COMPRESSION)
                        yield Input(
                            value=str(self.config.get("VNC_COMPRESSION", 9)),
                            id="vnc-compression-input",
                            type="integer",
                            tooltip="VNC compression level (0-9)"
                        )

                with Horizontal():
                    yield Button(ButtonLabels.SAVE, variant="primary", id="save-config-btn")
                    yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-config-btn":
            try:
                self.config["REMOTE_WEBCONSOLE"] = self.query_one("#remote-webconsole-checkbox", Checkbox).value
                self.config["websockify_path"] = self.query_one("#websockify-path-input", Input).value
                self.config["novnc_path"] = self.query_one("#novnc-path-input", Input).value
                self.config["WC_PORT_RANGE_START"] = int(self.query_one("#wc-port-start-input", Input).value)
                self.config["WC_PORT_RANGE_END"] = int(self.query_one("#wc-port-end-input", Input).value)
                self.config["VNC_QUALITY"] = int(self.query_one("#vnc-quality-input", Input).value)
                self.config["VNC_COMPRESSION"] = int(self.query_one("#vnc-compression-input", Input).value)
                self.config["STATS_INTERVAL"] = int(self.query_one("#stats-interval-input", Input).value)
                self.config["LOG_FILE_PATH"] = self.query_one("#log-file-path-input", Input).value
                self.config["LOG_LEVEL"] = self.query_one("#log-level-select", Select).value

                try:
                    viewer_select = self.query_one("#remote-viewer-select", Select)
                    if viewer_select.value != Select.BLANK:
                        self.config["REMOTE_VIEWER"] = viewer_select.value
                    else:
                        self.config["REMOTE_VIEWER"] = None
                        self.app.show_warning_message(WarningMessages.NO_REMOTE_VIEWER_SELECTED)
                except Exception:
                    pass

                save_config(self.config)
                self.app.show_success_message(SuccessMessages.CONFIGURATION_SAVED)
                self.dismiss(self.config)
            except Exception as e:
                self.app.show_error_message(ErrorMessages.ERROR_SAVING_CONFIGURATION_TEMPLATE.format(e=e))
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
