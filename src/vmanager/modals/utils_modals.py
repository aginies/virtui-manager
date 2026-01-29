"""
Usefull Modal screen
"""
import logging
import os
import pathlib
import re
from typing import Iterable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    DirectoryTree,
    Label,
    LoadingIndicator,
    Log,
    Markdown,
    ProgressBar,
)

from .base_modals import BaseDialog, BaseModal
from ..constants import ButtonLabels


def _sanitize_message(message: str) -> str:
    """
    Escapes brackets in strings that look like file paths or invalid tags.
    Preserves valid Rich markup tags.
    """
    def replacer(match):
        content = match.group(1)
        # Valid closing tags: [/] or [/name]
        if content.startswith('/'):
             if content == '/' or re.fullmatch(r"/[a-zA-Z0-9_-]+", content):
                 return match.group(0)
             # Invalid closing tag -> escape
             return f"\\[{content}]"

        # Rich doesn't support / in style names (except for closing tag prefix).
        if '/' in content and '=' not in content:
             return f"\\[{content}]"

        # If it has . inside, like [file.txt]
        if '.' in content and '=' not in content:
             return f"\\[{content}]"

        return match.group(0)

    # Use regex to find [...] patterns and replace them
    return re.sub(r"\[(.*?)\]", replacer, message)

def show_error_message(app, message: str):
    """Shows an error notification."""
    logging.error(message)
    app.notify(_sanitize_message(message), severity="error", timeout=10, title="Error!")

def show_success_message(app, message: str):
    """Shows a success notification."""
    logging.info(message)
    app.notify(_sanitize_message(message), timeout=10, title="Info")

def show_in_progress_message(app, message: str):
    """Shows an 'In Progress' notification."""
    logging.info(message)
    app.notify(_sanitize_message(message), timeout=5, title="In Progress", severity="inprogress")

def show_quick_message(app, message: str):
    """Shows a quick notification."""
    logging.info(message)
    app.notify(_sanitize_message(message), timeout=2, title="Quick Info")

def show_warning_message(app, message: str):
    """Shows a warning notification."""
    logging.warning(message)
    app.notify(_sanitize_message(message), severity="warning", timeout=10, title="Warning")

class SafeDirectoryTree(DirectoryTree):
    """
    A DirectoryTree that excludes problematic paths like /proc, /sys, and /dev.
    """
    def filter_paths(self, paths: Iterable[pathlib.Path]) -> Iterable[pathlib.Path]:
        """Filters out blacklisted paths to prevent recursion and performance issues."""
        BLACKLIST = ("proc", "sys", "dev")
        return [p for p in paths if not any(part in BLACKLIST for part in p.parts)]

class DirectorySelectionModal(BaseModal[str | None]):
    """A modal screen for selecting a directory."""

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.start_path = path if path and os.path.isdir(path) else os.path.expanduser("~")
        self._selected_path: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="directory-selection-dialog"):
            yield Label(StaticText.SELECT_A_DIRECTORY)
            yield SafeDirectoryTree(self.start_path, id="dir-tree")
            with Horizontal():
                yield Button(ButtonLabels.SELECT, variant="primary", id="select-btn", disabled=True)
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one(SafeDirectoryTree).focus()

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._selected_path = str(event.path)
        self.query_one("#select-btn").disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "select-btn":
            if self._selected_path:
                self.dismiss(self._selected_path)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

class FileSelectionModal(BaseModal[str | None]):
    """A modal screen for selecting a file."""

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        start_dir = path if path and os.path.isdir(path) else os.path.dirname(path) if path else os.path.expanduser("/")
        if not os.path.isdir(start_dir):
             start_dir = os.path.expanduser("/")
        self.start_path = start_dir
        self._selected_path: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="file-selection-dialog", classes="file-selection-dialog"):
            yield Label(StaticText.SELECT_A_FILE)
            yield SafeDirectoryTree(self.start_path, id="file-tree")
            with Horizontal():
                yield Button(ButtonLabels.SELECT, variant="primary", id="select-btn", disabled=True)
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one(SafeDirectoryTree).focus()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._selected_path = str(event.path)
        self.query_one("#select-btn").disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "select-btn":
            if self._selected_path:
                self.dismiss(self._selected_path)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)


class LoadingModal(BaseModal[None]):
    """A modal screen that displays a loading indicator."""

    BINDINGS = [] # Override BaseModal's bindings to prevent user dismissal with escape

    def __init__(self, message: str = "Loading...") -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.message)
            yield LoadingIndicator()

class ProgressModal(BaseModal[None]):
    """A modal that shows a progress bar and logs for a long-running task."""

    BINDINGS = []

    def __init__(self, title: str = "Working...") -> None:
        super().__init__()
        self._title_text = title
        self._progress_bar: ProgressBar | None = None
        self._log: Log | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._title_text, id="progress-title"),
            ProgressBar(total=100, show_eta=True, id="progress-bar"),
            Log(id="progress-log", classes="progress-log", auto_scroll=True),
            id="progress-modal-container",
        )

    def on_mount(self) -> None:
        """Called when the modal is mounted."""
        self._progress_bar = self.query_one(ProgressBar)
        self._log = self.query_one(Log)

    def update_progress(self, progress: float) -> None:
        """Updates the progress bar."""
        if self._progress_bar:
            self._progress_bar.update(progress=progress)

    def add_log(self, message: str) -> None:
        """Adds a message to the log."""
        if self._log:
            self._log.write_line(message)


class ConfirmationDialog(BaseDialog[bool]):
    """A dialog to confirm an action."""

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self):
        yield Vertical(
            Markdown(self.prompt, id="question"),
            Horizontal(
                Button(ButtonLabels.YES, variant="error", id="yes", classes="dialog-buttons"),
                Button(ButtonLabels.NO, variant="primary", id="no", classes="dialog-buttons"),
                id="dialog-buttons",
            ),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel_modal(self) -> None:
        """Cancel the modal."""
        self.dismiss(False)

class InfoModal(BaseModal[None]):
    """A modal that shows an information message."""

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="info-modal-dialog", classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Markdown(self._message, classes="dialog-message")
            with Horizontal(classes="dialog-buttons"):
                yield Button(ButtonLabels.OK, variant="primary", id="ok-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()
