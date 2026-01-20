# VM Installation

Virtui Manager provides a streamlined wizard for provisioning new virtual machines, with a focus on ease of use for OpenSUSE distributions while supporting custom ISOs.

To start the installation wizard, press **`i`** on your keyboard while in the main window.

![VM Installation Wizard](images/install.jpg)

## The Installation Wizard

The wizard guides you through the necessary steps to configure your new VM.

### Basic Configuration

*   **VM Name:**
    *   Enter a unique name for your virtual machine.
    *   *Note:* The name will be automatically sanitized to ensure compatibility (e.g., spaces replaced with hyphens).

*   **VM Type:**
    *   Select a preset profile that automatically adjusts hardware resources (CPU, RAM, Disk) based on the intended use case.
    *   **Desktop:** Balanced resources for general use (Default).
    *   **Server:** More CPU/RAM, larger disk, optimized for workloads.
    *   **Computation:** High CPU/RAM ratio for compute-intensive tasks.
    *   **Secure:** Minimal resources, hardened configuration.

*   **Distribution:**
    *   Choose the operating system source.
    *   **Cached ISOs:** Select from ISO images already downloaded to your local cache.
    *   **OpenSUSE Variants:** Select a specific OpenSUSE distribution (e.g., Leap, Tumbleweed, Slowroll) to automatically fetch the latest ISO.
    *   **Custom:** Use a local ISO file from your file system.

*   **ISO Image (Repo):**
    *   If you selected a distribution or "Cached ISOs", pick the specific image version from the dropdown. New images will be downloaded automatically to the configured ISO path.

### Custom ISO Options
*Visible only when "Custom" distribution is selected.*

*   **Custom ISO (Local Path):** Enter the full path or browse for a local `.iso` file.
*   **Validate Checksum:** Optionally verify the integrity of the ISO file using a SHA256 checksum before installation.

### Expert Mode

Click the "Expert Mode" header to reveal advanced hardware settings. These default to values based on the selected **VM Type** but can be overridden.

*   **Memory (MB):** Amount of RAM allocated to the VM.
*   **CPUs:** Number of virtual CPU cores.
*   **Disk Size (GB):** Size of the primary hard disk.
*   **Disk Format:**
    *   `qcow2`: (Default) Supports snapshots and dynamic allocation.
    *   `raw`: Better performance, but consumes full space immediately and lacks snapshot support.
*   **Firmware:**
    *   **UEFI:** (Checked by default) Modern boot firmware. Uncheck for Legacy BIOS.

### Storage

*   **Storage Pool:** Select the libvirt storage pool where the VM's disk image will be created. Defaults to `default`.

## Starting the Installation

1.  Review your settings.
2.  Click **Install**.
3.  The wizard will download the ISO (if necessary), create the disk image, and define the VM.
4.  Once provisioned, the VM will start automatically, and the remote viewer will launch to display the installation console.
