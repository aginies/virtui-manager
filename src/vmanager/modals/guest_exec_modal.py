"""
Guest command execution modal (QGA guest-exec).
"""
from __future__ import annotations

import logging
import shlex

import libvirt
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Markdown, TextArea

from ..constants import ButtonLabels, DialogMessages, ErrorMessages, StaticText, SuccessMessages
from ..guest_agent import GuestAgentError, ga_exec_wait, is_available
from .base_modals import BaseModal
from .utils_modals import ConfirmationDialog, _confirm_message, show_guest_agent_error

# Cap the amount of output rendered in the UI to keep the TUI responsive.
MAX_OUTPUT_CHARS = 100_000
# Cap the command length shown in the confirmation prompt.
COMMAND_DISPLAY_LIMIT = 160


class GuestExecModal(BaseModal[None]):
    """Modal to run a command inside the guest via the QEMU guest agent."""

    def __init__(self, domain: libvirt.virDomain, vm_name: str) -> None:
        super().__init__()
        self.domain = domain
        self.vm_name = vm_name

    def compose(self) -> ComposeResult:
        with Vertical(id="guest-exec-dialog"):
            yield Label(StaticText.GUEST_EXEC_TITLE_TEMPLATE.format(vm_name=self.vm_name))
            yield Markdown(StaticText.GUEST_EXEC_WARNING)
            yield Label(StaticText.GUEST_EXEC_COMMAND_LABEL)
            yield Input(id="guest-exec-command-input")
            yield Label(StaticText.GUEST_EXEC_TIMEOUT_LABEL)
            yield Input(value="30", id="guest-exec-timeout-input")
            with Horizontal(id="guest-exec-buttons"):
                yield Button(ButtonLabels.RUN_COMMAND, variant="primary", id="guest-exec-run")
                yield Button(ButtonLabels.CANCEL, variant="error", id="guest-exec-cancel")
            yield Label(StaticText.GUEST_EXEC_OUTPUT_TITLE)
            yield TextArea(read_only=True, id="guest-exec-output")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guest-exec-cancel":
            self.dismiss()
        elif event.button.id == "guest-exec-run":
            self._on_run_pressed()

    def _on_run_pressed(self) -> None:
        if not is_available():
            self.app.show_error_message(ErrorMessages.GUEST_AGENT_NOT_AVAILABLE)
            return

        command = self.query_one("#guest-exec-command-input", Input).value.strip()
        if not command:
            self.app.show_error_message(ErrorMessages.GUEST_EXEC_INVALID_COMMAND)
            return
        try:
            tokens = shlex.split(command)
        except ValueError:
            self.app.show_error_message(ErrorMessages.GUEST_EXEC_INVALID_COMMAND)
            return
        if not tokens:
            self.app.show_error_message(ErrorMessages.GUEST_EXEC_INVALID_COMMAND)
            return

        try:
            timeout_s = int(self.query_one("#guest-exec-timeout-input", Input).value.strip())
            if timeout_s <= 0:
                raise ValueError
        except ValueError:
            self.app.show_error_message(ErrorMessages.GUEST_EXEC_TIMEOUT_INVALID)
            return

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._start_exec_worker(command, timeout_s)

        command_display = (
            command if len(command) <= COMMAND_DISPLAY_LIMIT else command[:COMMAND_DISPLAY_LIMIT] + "..."
        )
        prompt = _confirm_message(
            DialogMessages.CONFIRM_GUEST_EXEC_TEMPLATE.format(
                vm_name=self.vm_name, command=command_display
            )
        )
        self.app.push_screen(ConfirmationDialog(prompt), on_confirm)

    def _start_exec_worker(self, command: str, timeout_s: int) -> None:
        output = self.query_one("#guest-exec-output", TextArea)
        run_button = self.query_one("#guest-exec-run", Button)
        output.load_text(StaticText.GUEST_EXEC_RUNNING)
        run_button.disabled = True

        def worker() -> None:
            try:
                status = ga_exec_wait(
                    self.domain,
                    command,
                    overall_timeout=timeout_s + 30,
                )
            except GuestAgentError as exc:
                logging.error("Guest exec failed for %s: %s", self.vm_name, exc)
                error = exc  # captured: 'exc' is deleted when the except block ends

                def on_error() -> None:
                    run_button.disabled = False
                    show_guest_agent_error(self.app, error)

                self.app.call_from_thread(on_error)
                return

            # Log the full output (the UI display may be truncated)
            logging.info(
                "Guest exec %r in %s: exit=%s\nstdout:\n%s\nstderr:\n%s",
                command,
                self.vm_name,
                status["exitcode"],
                status["stdout"],
                status["stderr"],
            )

            def on_done() -> None:
                run_button.disabled = False
                exitcode = status["exitcode"]
                if exitcode is not None:
                    parts = [StaticText.GUEST_EXEC_EXIT_CODE_TEMPLATE.format(code=exitcode)]
                else:
                    parts = [
                        StaticText.GUEST_EXEC_SIGNAL_TEMPLATE.format(signal=status["signal"])
                    ]
                if status["stdout"]:
                    parts.append(self._cap_output(status["stdout"]))
                if status["stderr"]:
                    parts.append(self._cap_output(status["stderr"]))
                if len(parts) == 1:
                    parts.append(StaticText.GUEST_EXEC_NO_OUTPUT)
                if status["truncated"]:
                    parts.append(StaticText.GUEST_EXEC_OUTPUT_TRUNCATED)
                output.load_text("\n".join(parts))
                if exitcode == 0:
                    self.app.show_success_message(
                        SuccessMessages.GUEST_COMMAND_FINISHED_TEMPLATE.format(code=exitcode)
                    )

            self.app.call_from_thread(on_done)

        self.app.worker_manager.run(worker, name=f"guest_exec_{self.vm_name}")

    @staticmethod
    def _cap_output(text: str) -> str:
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        return text[:MAX_OUTPUT_CHARS] + "\n... [truncated in display, full output in log] ..."
