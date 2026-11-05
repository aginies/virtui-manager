"""
Guest file upload modal (QGA guest-file-* commands).
"""
from __future__ import annotations

import logging
import os
import threading

import libvirt
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ScreenResultType
from textual.widgets import Button, Input, Label, Markdown, ProgressBar, Select

from ..constants import ButtonLabels, ErrorMessages, StaticText, SuccessMessages
from ..guest_agent import GuestAgentError, UploadCancelled, ga_upload_file, is_available
from ..utils import humanize_bytes
from .base_modals import BaseModal
from .utils_modals import FileSelectionModal, show_guest_agent_error


class GuestUploadModal(BaseModal[None]):
    """Modal to upload a local file to the guest via the QEMU guest agent."""

    def __init__(self, domain: libvirt.virDomain, vm_name: str) -> None:
        super().__init__()
        self.domain = domain
        self.vm_name = vm_name
        self._local_path: str | None = None
        self._cancel_event: threading.Event | None = None
        self._uploading = False

    def compose(self) -> ComposeResult:
        with Vertical(id="guest-upload-dialog"):
            yield Label(StaticText.GUEST_UPLOAD_TITLE_TEMPLATE.format(vm_name=self.vm_name))
            yield Markdown(StaticText.GUEST_UPLOAD_WARNING)
            yield Label(StaticText.GUEST_UPLOAD_LOCAL_LABEL)
            with Horizontal(id="guest-upload-local-row"):
                yield Input(
                    placeholder=StaticText.GUEST_UPLOAD_LOCAL_PLACEHOLDER,
                    id="guest-upload-local",
                    disabled=True,
                )
                yield Button(ButtonLabels.BROWSE, id="guest-upload-browse")
            yield Label(StaticText.GUEST_UPLOAD_DEST_LABEL)
            yield Input(
                placeholder=StaticText.GUEST_UPLOAD_DEST_PLACEHOLDER, id="guest-upload-dest"
            )
            yield Label(StaticText.GUEST_UPLOAD_DEST_HINT)
            yield Label(StaticText.GUEST_UPLOAD_MODE_LABEL)
            yield Select(
                [
                    (StaticText.GUEST_UPLOAD_MODE_OVERWRITE, "w"),
                    (StaticText.GUEST_UPLOAD_MODE_APPEND, "a"),
                ],
                value="w",
                id="guest-upload-mode",
            )
            yield ProgressBar(total=100, show_eta=False, id="guest-upload-progress")
            with Horizontal(id="guest-upload-buttons"):
                yield Button(ButtonLabels.UPLOAD_FILE, variant="primary", id="guest-upload-start")
                yield Button(ButtonLabels.CANCEL, variant="error", id="guest-upload-cancel")

    def on_mount(self) -> None:
        # ProgressBar has no 'visible' constructor argument; hide it until
        # an upload starts.
        self.query_one("#guest-upload-progress", ProgressBar).visible = False

    def dismiss(self, result: ScreenResultType | None = None):
        """Close the modal, cancelling a running upload."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        return super().dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guest-upload-browse":
            self._browse_local_file()
        elif event.button.id == "guest-upload-start":
            self._start_upload()
        elif event.button.id == "guest-upload-cancel":
            if self._uploading and self._cancel_event is not None:
                # Stop the running upload between chunks
                self._cancel_event.set()
            else:
                self.dismiss()

    def _browse_local_file(self) -> None:
        """Open the file browser to pick the local file to upload."""
        start_dir = os.path.expanduser("~")
        if not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("/")

        def on_file_selected(path: str | None) -> None:
            if not path or not self.is_mounted:
                return
            self._local_path = path
            self.query_one("#guest-upload-local", Input).value = path
            dest = self.query_one("#guest-upload-dest", Input)
            if not dest.value.strip():
                dest.value = f"/tmp/{os.path.basename(path)}"

        self.app.push_screen(FileSelectionModal(path=start_dir), on_file_selected)

    def _start_upload(self) -> None:
        if not is_available():
            self.app.show_error_message(ErrorMessages.GUEST_AGENT_NOT_AVAILABLE)
            return

        local_path = self._local_path
        if not local_path:
            self.app.show_error_message(ErrorMessages.GUEST_UPLOAD_LOCAL_REQUIRED)
            return
        if not os.path.isfile(local_path):
            self.app.show_error_message(
                ErrorMessages.GUEST_UPLOAD_LOCAL_MISSING_TEMPLATE.format(path=local_path)
            )
            return

        remote_path = self.query_one("#guest-upload-dest", Input).value.strip()
        if not remote_path:
            self.app.show_error_message(ErrorMessages.GUEST_UPLOAD_DEST_REQUIRED)
            return

        mode = self.query_one("#guest-upload-mode", Select).value
        if mode not in ("w", "a"):
            mode = "w"

        try:
            total_size = os.path.getsize(local_path)
        except OSError:
            self.app.show_error_message(
                ErrorMessages.GUEST_UPLOAD_LOCAL_MISSING_TEMPLATE.format(path=local_path)
            )
            return

        self._uploading = True
        self._cancel_event = threading.Event()
        self.query_one("#guest-upload-start", Button).disabled = True
        self.query_one("#guest-upload-browse", Button).disabled = True
        progress_bar = self.query_one("#guest-upload-progress", ProgressBar)
        progress_bar.total = total_size
        progress_bar.update(progress=0)
        progress_bar.visible = True

        def on_progress(bytes_written: int, total: int) -> None:
            self.app.call_from_thread(self._on_progress, bytes_written, total)

        def worker() -> None:
            try:
                written = ga_upload_file(
                    self.domain,
                    local_path,
                    remote_path,
                    mode=mode,
                    progress_callback=on_progress,
                    cancel_event=self._cancel_event,
                )
            except UploadCancelled as exc:
                error = exc  # captured: 'exc' is deleted when the except block ends
                logging.info("Upload to %s cancelled: %s", self.vm_name, exc)

                def on_cancelled() -> None:
                    self._finish_upload_ui()
                    if not self.is_mounted:
                        return
                    self.app.show_warning_message(
                        StaticText.GUEST_UPLOAD_CANCELLED_TEMPLATE.format(
                            bytes=humanize_bytes(error.bytes_written),
                            total=humanize_bytes(error.total_bytes),
                        )
                    )

                self.app.call_from_thread(on_cancelled)
                return
            except GuestAgentError as exc:
                error = exc  # captured: 'exc' is deleted when the except block ends
                logging.error("Upload to %s failed: %s", self.vm_name, exc)

                def on_error() -> None:
                    self._finish_upload_ui()
                    if not self.is_mounted:
                        return
                    show_guest_agent_error(self.app, error)

                self.app.call_from_thread(on_error)
                return
            except Exception as exc:
                error = str(exc)
                logging.error("Upload to %s failed: %s", self.vm_name, exc)

                def on_error() -> None:
                    self._finish_upload_ui()
                    if not self.is_mounted:
                        return
                    self.app.show_error_message(
                        ErrorMessages.GUEST_UPLOAD_FAILED_TEMPLATE.format(
                            path=remote_path, error=error
                        )
                    )

                self.app.call_from_thread(on_error)
                return

            def on_done() -> None:
                if not self.is_mounted:
                    return
                progress_bar.update(progress=total_size)
                self.app.show_success_message(
                    SuccessMessages.GUEST_UPLOAD_DONE_TEMPLATE.format(
                        bytes=humanize_bytes(written), path=remote_path
                    )
                )
                self.dismiss()

            self.app.call_from_thread(on_done)

        self.app.worker_manager.run(worker, name=f"guest_upload_{self.vm_name}")

    def _on_progress(self, bytes_written: int, total: int) -> None:
        """Update the progress bar (called on the UI thread)."""
        if not self.is_mounted:
            return
        try:
            self.query_one("#guest-upload-progress", ProgressBar).update(
                progress=bytes_written
            )
        except Exception:
            pass

    def _finish_upload_ui(self) -> None:
        """Re-enable the form after an upload ends with an error or cancel."""
        self._uploading = False
        self._cancel_event = None
        try:
            self.query_one("#guest-upload-start", Button).disabled = False
            self.query_one("#guest-upload-browse", Button).disabled = False
        except Exception:
            pass
