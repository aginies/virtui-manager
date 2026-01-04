"""
Modal to show how overlay disks work.
"""
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Button, Markdown
from textual import on
from modals.base_modals import BaseModal

HOW_TO_OVERLAY_TEXT = """
# Understanding Snapshots and Disk Overlays

Virtual machines in this manager support two ways to preserve states and manage changes: **Snapshots** and **Disk Overlays**. While they share similar goals, they work differently and are suited for different use cases.

---

### 1. Snapshots (Internal)
Snapshots are managed directly by libvirt and are typically stored **inside** the disk image (if using QCOW2 format).

*   **How they work:** When you take a snapshot, libvirt records the current state of the VM's disks and, if the VM is running, its memory (RAM). You can have many snapshots for a single VM, creating a timeline you can jump back and forth in.
*   **Operations:**
    *   **Take Snapshot:** Creates a new restore point.
    *   **Restore Snapshot:** Reverts the VM to a previous state.
    *   **Delete Snapshot:** Removes the restore point from the timeline.
*   **Best for:** Quick restore points before risky operations, and preserving the full "live" state of a running VM.

### 2. Disk Overlays (External)
Overlays are **new files** created on top of a base disk image. This is also known as "External Snapshots" or "Backing Files".

*   **Key Concepts:**
    *   **Base Image (Backing File):** The original disk image that becomes read-only.
    *   **Overlay Image:** A new QCOW2 file that records only the changes made *after* its creation.
    *   **Backing Chain:** The relationship between layers (e.g., Base -> Overlay 1 -> Overlay 2).
*   **Operations:**
    *   **New Overlay:** Freezes the current disk and starts a new layer.
    *   **Discard Overlay (Revert):** Deletes the overlay file and reverts to the base image.
    *   **Commit Disk (Merge):** Merges changes from the overlay into the base image, making them permanent.
*   **Best for:** Maintaining "Golden Images", branching multiple VMs from a single base, and isolating large changes in separate files.

---

### Comparison: Snapshot vs. Overlay

| Feature | Snapshots (Internal) | Disk Overlays (External) |
| :--- | :--- | :--- |
| **Storage** | Inside the existing disk file | In a new, separate file |
| **VM State** | Can include RAM (Live state) | Disk only (requires VM stop) |
| **Management** | Timeline (multiple points) | Layered (Base + Changes) |
| **Primary Use** | Quick restore points | Permanent branching / Golden images |

---

### How Overlays Work (Technical)

1.  **Creation:** When you create a "New Overlay", the current disk image is set as the backing file for a newly created QCOW2 file. The VM is then updated to point to this new overlay file instead of the original disk.
2.  **Read Operations:** When the VM needs to read data, it first checks the overlay. If the data has been modified, it reads from the overlay. If not, it transparently reads from the backing file.
3.  **Write Operations:** All writes are directed to the overlay file. The backing file remains untouched and pristine.
"""

class HowToOverlayModal(BaseModal[None]):
    """A modal to display instructions for disk overlays."""

    def compose(self) -> ComposeResult:
        with Vertical(id="howto-overlay-dialog", classes="howto-dialog"):
            with ScrollableContainer(id="howto-overlay-content"):
                yield Markdown(HOW_TO_OVERLAY_TEXT, id="howto-overlay-markdown")
        with Horizontal(id="dialog-buttons"):
            yield Button("Close", id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
