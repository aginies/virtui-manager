# Managing VM Disks

This guide explains the functions of the buttons available in the "Disks" tab.

---

### Add Disk

This button allows you to create a **brand new virtual disk** and attach it to the VM.

- You will be prompted to select a **Storage Pool** where the new disk image file will be created.
- You must provide a **Volume Name** for the new disk (e.g., `new-data-disk.qcow2`).
- You must specify its **Size** and **Format** (`qcow2` or `raw`).
- The VM does **not** need to be stopped to add a new disk.

---

### Attach Existing Disk

This attaches a **pre-existing disk image** to your VM. This is useful if you have an existing `.qcow2`, `.raw`, or `.iso` file you want to use.

- The system will first ask you to select a **Storage Pool** that contains the disk volume you want to attach.
- If the disk file is not part of a libvirt storage pool yet, you should first use the "Attach" button in the **Server Preferences -> Storage** tab to make it known to libvirt. This might involve creating a new storage pool for the directory containing your disk file.

---

### Edit Disk

*(This button is enabled only when a disk is selected in the table)*

Allows you to modify properties of an attached disk, such as:
- **Bus Type:** `virtio`, `sata`, `scsi`, etc.
- **Cache Mode:** `none`, `writeback`, etc.
- **Discard Mode:** `unmap`, `ignore`.

> **Important:** The VM must be **stopped** to edit disk properties.

---

### Remove Disk

*(This button is enabled only when a disk is selected)*

This **detaches** the selected disk from the virtual machine.

- **This action does NOT delete the disk image file.** The file remains in its storage pool or on your filesystem.
- It only removes the disk from this specific VM's configuration.
- The VM must be **stopped** to remove most types of disks.

---

### Disable Disk

*(This button is enabled for active disks)*

This temporarily "unplugs" the disk from the VM **without removing its configuration**.

- The disk will become invisible to the guest operating system but remains in the disk list with a `(disabled)` status.
- This is useful for troubleshooting or temporarily preventing access to a disk without fully removing it.

---

### Enable Disk

*(This button is enabled for disabled disks)*

This "re-plugs" a disabled disk back into the VM, making it available to the guest operating system again.
