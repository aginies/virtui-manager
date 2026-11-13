"""
Guest agent modal: status, guest info, guest operations, command execution
and SSH keys.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import libvirt
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ScreenResultType
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Static

from ..constants import ButtonLabels, DialogMessages, ErrorMessages, StaticText, SuccessMessages
from ..guest_agent import (
    GuestAgentError,
    agent_status,
    ga_fsfreeze_freeze,
    ga_fsfreeze_status,
    ga_fsfreeze_thaw,
    ga_fsinfo,
    ga_fstrim,
    ga_info,
    ga_set_time,
    ga_set_user_password,
    ga_shutdown,
    ga_supported,
    ga_suspend,
    is_available,
)
from ..utils import humanize_bytes
from .base_modals import BaseModal
from .guest_download_modal import GuestDownloadModal
from .guest_exec_modal import GuestExecModal
from .guest_ssh_keys_modal import GuestSSHKeysModal
from .guest_upload_modal import GuestUploadModal
from .utils_modals import ConfirmationDialog, _confirm_message, show_guest_agent_error


class GuestAgentModal(BaseModal[None]):
    """Modal showing QEMU guest agent status, guest info and guest actions."""

    def __init__(self, domain: libvirt.virDomain, vm_name: str) -> None:
        super().__init__()
        self.domain = domain
        self.vm_name = vm_name
        self.title = StaticText.GUEST_AGENT_TITLE_TEMPLATE.format(vm_name=vm_name)
        self._previous_app_title: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="guest-agent-dialog"):
            with Horizontal():
                yield Label(
                    StaticText.GUEST_AGENT_STATUS_LABEL + ":", classes="guest-info-label"
                )
                yield Label(
                    StaticText.GUEST_AGENT_STATUS_CHECKING,
                    id="guest-agent-status",
                    classes="guest-info-value",
                )
            with Horizontal():
                yield Label(StaticText.GUEST_HOSTNAME_LABEL + ":", classes="guest-info-label")
                yield Label("N/A", id="guest-hostname", classes="guest-info-value")
            with Horizontal():
                yield Label(StaticText.GUEST_OS_LABEL + ":", classes="guest-info-label")
                yield Label("N/A", id="guest-os", classes="guest-info-value")
            with Horizontal():
                yield Label(StaticText.GUEST_KERNEL_LABEL + ":", classes="guest-info-label")
                yield Label("N/A", id="guest-kernel", classes="guest-info-value")
            with Horizontal():
                yield Label(StaticText.GUEST_UPTIME_LABEL + ":", classes="guest-info-label")
                yield Label("N/A", id="guest-uptime", classes="guest-info-value")
            with Horizontal():
                yield Label(StaticText.GUEST_LOAD_LABEL + ":", classes="guest-info-label")
                yield Label("N/A", id="guest-load", classes="guest-info-value")
            yield Label(StaticText.GUEST_AGENT_NOT_CONFIGURED_HINT, id="guest-agent-hint")
            yield Static(classes="button-separator")
            yield Label(StaticText.GUEST_FILESYSTEMS_TITLE)
            fs_table = DataTable(id="guest-fs-table")
            fs_table.cursor_type = "row"
            fs_table.add_column(StaticText.GUEST_MOUNTPOINT_COLUMN)
            fs_table.add_column(StaticText.GUEST_FSTYPE_COLUMN)
            fs_table.add_column(StaticText.GUEST_TOTAL_COLUMN)
            fs_table.add_column(StaticText.GUEST_USED_COLUMN)
            fs_table.add_column(StaticText.GUEST_FREE_COLUMN)
            fs_table.add_column(StaticText.GUEST_USAGE_COLUMN)
            yield fs_table
            yield Static(classes="button-separator")
            yield Label(StaticText.GUEST_OPERATIONS_TITLE)
            with Horizontal(classes="guest-op-row"):
                yield Label(
                    StaticText.GUEST_FS_FREEZE_LABEL.format(state="N/A"), id="guest-fs-freeze"
                )
                yield Button(ButtonLabels.FREEZE_FS, id="guest-freeze", classes="detail-disks")
                yield Button(ButtonLabels.THAW_FS, id="guest-thaw", classes="detail-disks")
            with Horizontal(classes="guest-op-row"):
                yield Button(ButtonLabels.TRIM, id="guest-trim", classes="detail-disks")
                yield Button(ButtonLabels.SYNC_TIME, id="guest-sync-time", classes="detail-disks")
                yield Button(
                    ButtonLabels.SET_PASSWORD, id="guest-set-password", classes="detail-disks"
                )
            with Horizontal(classes="guest-op-row"):
                yield Button(
                    ButtonLabels.GUEST_SHUTDOWN, id="guest-shutdown", classes="detail-disks"
                )
                yield Button(
                    ButtonLabels.SUSPEND_RAM, id="guest-suspend-ram", classes="detail-disks"
                )
                yield Button(
                    ButtonLabels.SUSPEND_DISK, id="guest-suspend-disk", classes="detail-disks"
                )
            yield Static(classes="button-separator")
            with Horizontal(classes="guest-op-row"):
                yield Button(ButtonLabels.REFRESH, id="guest-refresh", classes="detail-disks")
                yield Button(ButtonLabels.RUN_COMMAND, id="guest-exec", classes="detail-disks")
                yield Button(ButtonLabels.SSH_KEYS, id="guest-ssh-keys", classes="detail-disks")
                yield Button(ButtonLabels.UPLOAD_FILE, id="guest-upload", classes="detail-disks")
                yield Button(
                    ButtonLabels.DOWNLOAD_FILE, id="guest-download", classes="detail-disks"
                )
            yield Static(classes="button-separator")
            yield Button(ButtonLabels.CLOSE, id="guest-agent-close", classes="detail-disks")

    def on_mount(self) -> None:
        self._previous_app_title = self.app.title
        self.app.title = self.title
        self._load_guest_info()

    def dismiss(self, result: ScreenResultType | None = None):
        """Close the modal and restore the previous window title."""
        if self._previous_app_title is not None:
            self.app.title = self._previous_app_title
        return super().dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guest-agent-close":
            self.dismiss()
        elif event.button.id == "guest-refresh":
            self._load_guest_info()
        elif event.button.id == "guest-exec":
            self.app.push_screen(GuestExecModal(self.domain, self.vm_name))
        elif event.button.id == "guest-ssh-keys":
            self.app.push_screen(GuestSSHKeysModal(self.domain, self.vm_name))
        elif event.button.id == "guest-upload":
            self.app.push_screen(GuestUploadModal(self.domain, self.vm_name))
        elif event.button.id == "guest-download":
            self.app.push_screen(GuestDownloadModal(self.domain, self.vm_name))
        elif event.button.id == "guest-trim":
            self._confirm_trim()
        elif event.button.id == "guest-sync-time":
            self._confirm_sync_time()
        elif event.button.id == "guest-shutdown":
            self._confirm_shutdown()
        elif event.button.id == "guest-suspend-ram":
            self._confirm_suspend("ram")
        elif event.button.id == "guest-suspend-disk":
            self._confirm_suspend("disk")
        elif event.button.id == "guest-set-password":
            self._open_set_password()
        elif event.button.id == "guest-freeze":
            self._run_freeze()
        elif event.button.id == "guest-thaw":
            self._run_thaw()

    def _load_guest_info(self) -> None:
        """Query the guest agent in a worker and populate the modal."""
        if not is_available():
            self.app.show_error_message(ErrorMessages.GUEST_AGENT_NOT_AVAILABLE)
            return

        try:
            status_label = self.query_one("#guest-agent-status", Label)
            status_label.update(StaticText.GUEST_AGENT_STATUS_CHECKING)
        except Exception:
            return

        def worker() -> None:
            data: dict[str, Any] = {}
            try:
                status = agent_status(self.domain)
                data["status"] = status
                if status == "running":
                    try:
                        data["supported"] = ga_supported(self.domain)
                    except GuestAgentError as e:
                        if e.reason != "unsupported":
                            raise
                        # 'guest-info' only exists on QEMU >= 9.2 agents; older
                        # agents still work, we just cannot report a version.
                        data["supported"] = {}
                    data["info"] = ga_info(self.domain)
                    data["fsinfo"] = ga_fsinfo(self.domain)
                    try:
                        data["freeze"] = ga_fsfreeze_status(self.domain)
                    except GuestAgentError:
                        data["freeze"] = "none"
            except Exception as e:
                logging.error("Failed to query guest agent: %s", e)
                data["status"] = "error"
                data["error"] = str(e)

            self.app.call_from_thread(self._update_guest_info, data)

        self.app.worker_manager.run(worker, name=f"guest_info_{self.vm_name}")

    def _update_guest_info(self, data: dict[str, Any]) -> None:
        """Update the modal widgets with the data fetched by the worker."""

        def set_label(widget_id: str, value: str) -> None:
            try:
                self.query_one(f"#{widget_id}", Label).update(value)
            except Exception:
                pass

        def clear_tables() -> None:
            try:
                self.query_one("#guest-fs-table", DataTable).clear()
            except Exception:
                pass

        try:
            self._populate_guest_info(data, set_label, clear_tables)
        except Exception:
            # The modal may have been closed while the worker was running.
            logging.debug("Guest agent modal widgets not available for update")

    def _populate_guest_info(
        self, data: dict[str, Any], set_label, clear_tables
    ) -> None:
        """Fill the modal widgets (called from the main thread)."""
        status = data.get("status")
        hint_display = False
        if status == "running":
            version = data.get("supported", {}).get("version", "unknown")
            if version == "unknown":
                set_label("guest-agent-status", StaticText.GUEST_AGENT_STATUS_RUNNING_UNKNOWN)
            else:
                set_label(
                    "guest-agent-status",
                    StaticText.GUEST_AGENT_STATUS_RUNNING.format(version=version),
                )
            info = data.get("info", {})
            osinfo = info.get("os", {})
            set_label("guest-hostname", info.get("hostname") or "N/A")
            set_label("guest-os", osinfo.get("pretty_name") or osinfo.get("name") or "N/A")
            set_label("guest-kernel", osinfo.get("kernel_release") or "N/A")
            set_label("guest-uptime", info.get("uptime_format") or "N/A")
            load = info.get("load", [])
            if len(load) >= 3:
                set_label(
                    "guest-load",
                    StaticText.GUEST_LOAD_FORMAT.format(load1=load[0], load5=load[1], load15=load[2]),
                )
            else:
                set_label("guest-load", "N/A")
            set_label(
                "guest-fs-freeze",
                StaticText.GUEST_FS_FREEZE_LABEL.format(state=data.get("freeze", "N/A")),
            )

            fs_table = self.query_one("#guest-fs-table", DataTable)
            fs_table.clear()
            for fs in data.get("fsinfo", []):
                total = fs.get("total_bytes") or 0
                used = fs.get("used_bytes") or 0
                free = max(total - used, 0)
                usage = f"{used * 100 // total:.0f}%" if total else "N/A"
                fs_table.add_row(
                    fs.get("mountpoint", ""),
                    fs.get("type", ""),
                    humanize_bytes(total),
                    humanize_bytes(used),
                    humanize_bytes(free),
                    usage,
                )
        elif status == "down":
            set_label("guest-agent-status", StaticText.GUEST_AGENT_STATUS_DOWN)
            clear_tables()
        elif status == "not_configured":
            set_label("guest-agent-status", StaticText.GUEST_AGENT_STATUS_NOT_CONFIGURED)
            hint_display = True
            clear_tables()
        elif status == "not_running":
            set_label("guest-agent-status", StaticText.GUEST_AGENT_STATUS_NOT_RUNNING)
            clear_tables()
        else:  # "error" or unexpected
            set_label("guest-agent-status", StaticText.GUEST_AGENT_STATUS_DOWN)
            clear_tables()
            self.app.show_error_message(
                ErrorMessages.GUEST_COMMAND_FAILED_TEMPLATE.format(
                    command="guest-info", error=data.get("error", "")
                )
            )

        try:
            self.query_one("#guest-agent-hint").display = hint_display
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Guest operations (fstrim, sync time, password, fsfreeze)
    # ------------------------------------------------------------------

    def _run_operation(
        self,
        button_id: str,
        command: str,
        func: Callable[[], Any],
        on_success: Callable[[Any], None],
    ) -> None:
        """Run a QGA operation in a worker, disabling the button meanwhile."""
        try:
            button = self.query_one(f"#{button_id}", Button)
            button.disabled = True
        except Exception:
            return

        def worker() -> None:
            try:
                result = func()
            except Exception as exc:
                # Capture before the except block deletes `exc`: the closure runs later.
                logging.error("Guest operation failed in %s: %s", self.vm_name, exc)
                error = exc

                def on_error() -> None:
                    button.disabled = False
                    if isinstance(error, GuestAgentError):
                        show_guest_agent_error(self.app, error)
                    else:
                        self.app.show_error_message(
                            ErrorMessages.GUEST_COMMAND_FAILED_TEMPLATE.format(
                                command=command, error=str(error)
                            )
                        )

                self.app.call_from_thread(on_error)
                return

            def on_done() -> None:
                button.disabled = False
                on_success(result)

            self.app.call_from_thread(on_done)

        self.app.worker_manager.run(worker, name=f"guest_op_{self.vm_name}_{button_id}")

    def _confirm_trim(self) -> None:
        prompt = _confirm_message(
            DialogMessages.CONFIRM_GUEST_TRIM_TEMPLATE.format(vm_name=self.vm_name)
        )
        self.app.push_screen(ConfirmationDialog(prompt), self._on_trim_confirmed)

    def _on_trim_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed:
            return

        def on_success(result: Any) -> None:
            self.app.show_success_message(
                SuccessMessages.GUEST_TRIM_DONE_TEMPLATE.format(bytes=humanize_bytes(result))
            )

        self._run_operation(
            "guest-trim", "guest-fstrim", lambda: ga_fstrim(self.domain), on_success
        )

    def _confirm_sync_time(self) -> None:
        prompt = _confirm_message(
            DialogMessages.CONFIRM_GUEST_SYNC_TIME_TEMPLATE.format(vm_name=self.vm_name)
        )
        self.app.push_screen(ConfirmationDialog(prompt), self._on_sync_time_confirmed)

    def _on_sync_time_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed:
            return

        def on_success(_result: Any) -> None:
            self.app.show_success_message(SuccessMessages.GUEST_TIME_SYNCED)

        self._run_operation(
            "guest-sync-time", "guest-set-time", lambda: ga_set_time(self.domain), on_success
        )

    def _confirm_shutdown(self) -> None:
        prompt = _confirm_message(
            DialogMessages.CONFIRM_GUEST_SHUTDOWN_TEMPLATE.format(vm_name=self.vm_name)
        )
        self.app.push_screen(ConfirmationDialog(prompt), self._on_shutdown_confirmed)

    def _on_shutdown_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed:
            return

        def on_success(_result: Any) -> None:
            self.app.show_success_message(SuccessMessages.GUEST_SHUTDOWN_REQUESTED)

        self._run_operation(
            "guest-shutdown", "guest-shutdown", lambda: ga_shutdown(self.domain), on_success
        )

    def _confirm_suspend(self, mode: str) -> None:
        button_id = f"guest-suspend-{mode}"
        prompt = _confirm_message(
            DialogMessages.CONFIRM_GUEST_SUSPEND_TEMPLATE.format(
                vm_name=self.vm_name, mode=mode
            )
        )

        def on_confirmed(confirmed: bool | None) -> None:
            if not confirmed:
                return

            def on_success(_result: Any) -> None:
                self.app.show_success_message(
                    SuccessMessages.GUEST_SUSPENDED_TEMPLATE.format(mode=mode)
                )

            self._run_operation(
                button_id,
                f"guest-suspend-{mode}",
                lambda: ga_suspend(self.domain, mode),
                on_success,
            )

        self.app.push_screen(ConfirmationDialog(prompt), on_confirmed)

    def _open_set_password(self) -> None:
        def on_result(result: tuple[str, str, bool] | None) -> None:
            if not result:
                return
            user, password, crypted = result

            def on_success(_res: Any) -> None:
                self.app.show_success_message(
                    SuccessMessages.GUEST_PASSWORD_SET_TEMPLATE.format(user=user)
                )

            self._run_operation(
                "guest-set-password",
                "guest-set-user-password",
                lambda: ga_set_user_password(self.domain, user, password, crypted=crypted),
                on_success,
            )

        self.app.push_screen(GuestSetPasswordModal(self.domain, self.vm_name), on_result)

    def _run_freeze(self) -> None:
        def on_success(result: Any) -> None:
            self._set_freeze_label("frozen")
            self.app.show_success_message(
                SuccessMessages.GUEST_FS_FROZEN_TEMPLATE.format(count=result)
            )

        self._run_operation(
            "guest-freeze", "guest-fsfreeze-freeze", lambda: ga_fsfreeze_freeze(self.domain),
            on_success,
        )

    def _run_thaw(self) -> None:
        def on_success(result: Any) -> None:
            self._set_freeze_label("thawed")
            self.app.show_success_message(
                SuccessMessages.GUEST_FS_THAWED_TEMPLATE.format(count=result)
            )

        self._run_operation(
            "guest-thaw", "guest-fsfreeze-thaw", lambda: ga_fsfreeze_thaw(self.domain),
            on_success,
        )

    def _set_freeze_label(self, state: str) -> None:
        try:
            self.query_one("#guest-fs-freeze", Label).update(
                StaticText.GUEST_FS_FREEZE_LABEL.format(state=state)
            )
        except Exception:
            pass


class GuestSetPasswordModal(BaseModal[tuple[str, str, bool] | None]):
    """Modal to set a user password inside the guest (QGA guest-set-user-password)."""

    def __init__(self, domain: libvirt.virDomain, vm_name: str) -> None:
        super().__init__()
        self.domain = domain
        self.vm_name = vm_name

    def compose(self) -> ComposeResult:
        with Vertical(id="guest-set-password-dialog"):
            yield Label(
                StaticText.GUEST_SET_PASSWORD_TITLE_TEMPLATE.format(vm_name=self.vm_name)
            )
            yield Label(StaticText.GUEST_SET_PASSWORD_USER_LABEL)
            yield Input(id="guest-password-user")
            yield Label(StaticText.GUEST_SET_PASSWORD_PASSWORD_LABEL)
            yield Input(password=True, id="guest-password-value")
            yield Checkbox(StaticText.GUEST_SET_PASSWORD_CRYPTED, id="guest-password-crypted")
            with Horizontal():
                yield Button(
                    ButtonLabels.SET_PASSWORD, variant="primary", id="guest-password-set"
                )
                yield Button(ButtonLabels.CANCEL, variant="error", id="guest-password-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guest-password-cancel":
            self.dismiss(None)
        elif event.button.id == "guest-password-set":
            user = self.query_one("#guest-password-user", Input).value.strip()
            password = self.query_one("#guest-password-value", Input).value
            if not user:
                self.app.show_error_message(ErrorMessages.GUEST_SET_PASSWORD_USER_REQUIRED)
                return
            if not password:
                self.app.show_error_message(ErrorMessages.GUEST_SET_PASSWORD_REQUIRED)
                return
            crypted = self.query_one("#guest-password-crypted", Checkbox).value
            self.dismiss((user, password, crypted))
