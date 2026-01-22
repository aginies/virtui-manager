"""
Modal to show how to use VirtIO-FS.
"""
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Button, Markdown
from textual import on
from .base_modals import BaseModal

HOW_TO_VIRTIOFS_TEXT = """
# Using VirtIO-FS for Host-Guest File Sharing

VirtIO-FS is a high-performance shared filesystem that lets you share a directory from your host machine directly with a guest VM.

---

### Host Prerequisites

1.  **Shared Memory:** VirtIO-FS requires shared memory to be enabled for the VM. You can enable this in the **"Mem"** tab.
2.  **Permissions:** The user running QEMU/libvirt on the host must have the necessary permissions to read (and write, if needed) the source directory you want to share.

---

### Adding a VirtIO-FS Mount

- **Source Path:** The absolute path to the directory on your **host machine** that you want to share.
- **Target Path:** This is a "mount tag" or a label that the guest VM will use to identify the shared directory. It is **not** a path inside the guest. For example, you could use `shared-data`.

---

### Mounting in a Linux Guest

Most modern Linux distributions include the necessary VirtIO-FS drivers.

**1. Create a Mount Point:**
This is the directory inside your VM where the shared files will appear.

```bash
sudo mkdir /mnt/my_host_share
```

**2. Mount the Share:**
Use the `mount` command with the filesystem type `virtiofs` and the **Target Path (mount tag)** you defined.

```bash
sudo mount -t virtiofs 'your-target-path' /mnt/my_host_share
```
*(Replace `'your-target-path'` with the actual tag you set)*

**3. Automount on Boot (Optional):**
To make the share available automatically every time the VM boots, add an entry to `/etc/fstab`:

```
your-target-path /mnt/my_host_share virtiofs defaults,nofail 0 0
```
> The `nofail` option is recommended to prevent boot issues if the share is not available.

---

### Mounting in a Windows Guest

**1. Install Drivers:**
You must install the VirtIO-FS drivers in the Windows guest. These are included in the **"VirtIO-Win Guest Tools"** package, which you can typically download as an ISO file.
- Download the latest stable `virtio-win.iso` from the [Fedora VirtIO-Win project](https://github.com/virtio-win/virtio-win-pkg-scripts/blob/master/README.md).
- Attach the ISO to your VM as a CD-ROM.
- Open the CD-ROM in Windows and run the `virtio-win-guest-tools.exe` installer, ensuring the **"VirtIO-FS"** feature is selected.

**2. Access the Share:**
After installation and a reboot, the VirtIO-FS service will start. The shared folder will automatically appear as a network drive in **This PC** (or My Computer). The drive will be named after the **Target Path (mount tag)** you set.
"""

class HowToVirtIOFSModal(BaseModal[None]):
    """A modal to display instructions for using VirtIO-FS."""

    def compose(self) -> ComposeResult:
        with Vertical(id="howto-virtiofs-dialog"):
            with ScrollableContainer(id="howto-virtiofs-content"):
                yield Markdown(HOW_TO_VIRTIOFS_TEXT, id="howto-virtiofs-markdown")
        with Horizontal(id="dialog-buttons"):
            yield Button("Close", id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
