"""
Guest file download modal (QGA guest-file-* commands).
"""
from __future__ import annotations

import logging
import os
import threading

import libvirt
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ScreenResultType
from textual.widgets import Button, Input, Label, Markdown, ProgressBar, Select

from ..constants import ButtonLabels, DialogMessages, ErrorMessages, StaticText, SuccessMessages
from ..guest_agent import DownloadCancelled, GuestAgentError, ga_download_file, is_available
from ..utils import humanize_bytes
from .base_modals import BaseModal
from .utils_modals import (
    ConfirmationDialog,
    DirectorySelectionModal,
    _confirm_message,
    show_guest_agent_error,
)


class GuestDownloadModal(BaseModal[None]):
    """Modal to download a file from the guest via the QEMU guest agent."""

    def __init__(self, domain: libvirt.virDomain, vm_name: str) -> None:
        super().__init__()
        self.domain = domain
        self.vm_name = vm_name
        self._cancel_event: threading.Event | None = None
        self._downloading = False
        # Last local path auto-filled from the guest path (None until first fill)
        self._auto_local: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="guest-download-dialog"):
            yield Label(StaticText.GUEST_DOWNLOAD_TITLE_TEMPLATE.format(vm_name=self.vm_name))
            yield Markdown(StaticText.GUEST_DOWNLOAD_WARNING)
            yield Label(StaticText.GUEST_DOWNLOAD_REMOTE_LABEL)
            yield Input(
                placeholder=StaticText.GUEST_DOWNLOAD_REMOTE_PLACEHOLDER,
                id="guest-download-remote",
            )
            yield Label(StaticText.GUEST_DOWNLOAD_LOCAL_LABEL)
            with Horizontal(id="guest-download-local-row"):
                yield Input(
                    placeholder=StaticText.GUEST_DOWNLOAD_LOCAL_PLACEHOLDER,
                    id="guest-download-local",
                )
                yield Button(ButtonLabels.BROWSE, id="guest-download-browse")
            yield Label(StaticText.GUEST_DOWNLOAD_LOCAL_HINT)
            yield Label(StaticText.GUEST_UPLOAD_MODE_LABEL)
            yield Select(
                [
                    (StaticText.GUEST_UPLOAD_MODE_OVERWRITE, "w"),
                    (StaticText.GUEST_UPLOAD_MODE_APPEND, "a"),
                ],
                value="w",
                id="guest-download-mode",
            )
            with Horizontal(id="guest-download-progress-row"):
                yield ProgressBar(
                    total=None, show_eta=False, id="guest-download-progress"
                )
                yield Label("", id="guest-download-bytes")
            with Horizontal(id="guest-download-buttons"):
                yield Button(
                    ButtonLabels.DOWNLOAD_FILE, variant="primary", id="guest-download-start"
                )
                yield Button(ButtonLabels.CANCEL, variant="error", id="guest-download-cancel")

    def on_mount(self) -> None:
        # Hide the progress row until a download starts.
        self.query_one("#guest-download-progress", ProgressBar).visible = False
        self.query_one("#guest-download-bytes", Label).visible = False

    @on(Input.Changed, "#guest-download-remote")
    def _on_remote_change(self, event: Input.Changed) -> None:
        local_input = self.query_one("#guest-download-local", Input)
        # Stop syncing once the user typed a custom destination
        if local_input.value.strip() and local_input.value != self._auto_local:
            return
        value = event.value
        basename = value.strip().rstrip("/").split("/")[-1] if value.strip() else ""
        if basename:
            self._auto_local = os.path.join(os.getcwd(), basename)
            local_input.value = self._auto_local

    def dismiss(self, result: ScreenResultType | None = None):
        """Close the modal, cancelling a running download."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        return super().dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guest-download-browse":
            self._browse_local_dir()
        elif event.button.id == "guest-download-start":
            self._start_download()
        elif event.button.id == "guest-download-cancel":
            if self._downloading and self._cancel_event is not None:
                # Stop the running download between chunks
                self._cancel_event.set()
            else:
                self.dismiss()

    def _browse_local_dir(self) -> None:
        """Open the directory picker to choose where to save the file."""
        start_dir = os.path.expanduser("~")
        if not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("/")

        def on_directory_selected(path: str | None) -> None:
            if not path or not self.is_mounted:
                return
            basename = self._remote_basename()
            local_input = self.query_one("#guest-download-local", Input)
            self._auto_local = os.path.join(path, basename)
            local_input.value = self._auto_local

        self.app.push_screen(DirectorySelectionModal(path=start_dir), on_directory_selected)

    def _remote_basename(self) -> str:
        remote = self.query_one("#guest-download-remote", Input).value.strip()
        return remote.rstrip("/").split("/")[-1] if remote else "file"

    def _start_download(self) -> None:
        if not is_available():
            self.app.show_error_message(ErrorMessages.GUEST_AGENT_NOT_AVAILABLE)
            return

        remote_path = self.query_one("#guest-download-remote", Input).value.strip()
        if not remote_path:
            self.app.show_error_message(ErrorMessages.GUEST_DOWNLOAD_REMOTE_REQUIRED)
            return

        local_path = self.query_one("#guest-download-local", Input).value.strip()
        if not local_path:
            self.app.show_error_message(ErrorMessages.GUEST_DOWNLOAD_LOCAL_REQUIRED)
            return
        local_path = os.path.expanduser(local_path)

        parent_dir = os.path.dirname(os.path.abspath(local_path))
        if not os.path.isdir(parent_dir):
            self.app.show_error_message(
                ErrorMessages.GUEST_DOWNLOAD_LOCAL_DIR_MISSING_TEMPLATE.format(dir=parent_dir)
            )
            return

        mode = self.query_one("#guest-download-mode", Select).value
        if mode not in ("w", "a"):
            mode = "w"

        # Never silently overwrite a local file.
        if mode == "w" and os.path.exists(local_path):
            prompt = _confirm_message(
                DialogMessages.CONFIRM_GUEST_DOWNLOAD_OVERWRITE_TEMPLATE.format(path=local_path)
            )

            def on_overwrite_confirmed(confirmed: bool | None) -> None:
                if confirmed:
                    self._begin_download(remote_path, local_path, mode)

            self.app.push_screen(ConfirmationDialog(prompt), on_overwrite_confirmed)
            return

        self._begin_download(remote_path, local_path, mode)

    def _begin_download(self, remote_path: str, local_path: str, mode: str) -> None:
        self._downloading = True
        self._cancel_event = threading.Event()
        self.query_one("#guest-download-start", Button).disabled = True
        self.query_one("#guest-download-browse", Button).disabled = True
        progress_bar = self.query_one("#guest-download-progress", ProgressBar)
        bytes_label = self.query_one("#guest-download-bytes", Label)
        progress_bar.visible = True
        bytes_label.visible = True

        def on_progress(bytes_read: int) -> None:
            self.app.call_from_thread(self._on_progress, bytes_read)

        def worker() -> None:
            try:
                written = ga_download_file(
                    self.domain,
                    remote_path,
                    local_path,
                    mode=mode,
                    progress_callback=on_progress,
                    cancel_event=self._cancel_event,
                )
            except Exception as exc:
                # Capture before the except block deletes `exc`: the closure runs later.
                if isinstance(exc, DownloadCancelled):
                    logging.info("Download from %s cancelled: %s", self.vm_name, exc)
                else:
                    logging.error("Download from %s failed: %s", self.vm_name, exc)
                error = exc

                def on_error() -> None:
                    self._finish_download_ui()
                    if not self.is_mounted:
                        return
                    if isinstance(error, DownloadCancelled):
                        self.app.show_warning_message(
                            StaticText.GUEST_DOWNLOAD_CANCELLED_TEMPLATE.format(
                                bytes=humanize_bytes(error.bytes_read)
                            )
                        )
                    elif isinstance(error, GuestAgentError):
                        show_guest_agent_error(self.app, error)
                    else:
                        self.app.show_error_message(
                            ErrorMessages.GUEST_DOWNLOAD_FAILED_TEMPLATE.format(
                                path=remote_path, error=str(error)
                            )
                        )

                self.app.call_from_thread(on_error)
                return

            def on_done() -> None:
                if not self.is_mounted:
                    return
                self.app.show_success_message(
                    SuccessMessages.GUEST_DOWNLOAD_DONE_TEMPLATE.format(
                        bytes=humanize_bytes(written), path=remote_path
                    )
                )
                self.dismiss()

            self.app.call_from_thread(on_done)

        self.app.worker_manager.run(worker, name=f"guest_download_{self.vm_name}")

    def _on_progress(self, bytes_read: int) -> None:
        """Update the bytes-received label (called on the UI thread)."""
        if not self.is_mounted:
            return
        try:
            self.query_one("#guest-download-bytes", Label).update(
                StaticText.GUEST_DOWNLOAD_BYTES_TEMPLATE.format(bytes=humanize_bytes(bytes_read))
            )
        except Exception:
            pass

    def _finish_download_ui(self) -> None:
        """Re-enable the form after a download ends with an error or cancel."""
        self._downloading = False
        self._cancel_event = None
        try:
            self.query_one("#guest-download-start", Button).disabled = False
            self.query_one("#guest-download-browse", Button).disabled = False
        except Exception:
            pass