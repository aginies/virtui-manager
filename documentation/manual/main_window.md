# Management Main Window

The **Management Main Window** is the central hub of Virtui Manager, designed for efficiency and rapid control of your virtual infrastructure.

![Management Main Window](images/management.jpg)

## Interface Overview

!!! note
    You can view the application logs at any time by pressing the **`v`** key. This is useful for monitoring background operations and troubleshooting.

The interface is divided into intuitive sections to streamline your workflow:

### Select Servers

Located at the top-left or accessible via keyboard shortcuts, this menu allows you to switch contexts between different hypervisors.

*   **Single Pane of Glass:** View VMs from local KVM instances or remote servers connected via SSH.
*   **Status Indicators:** Instantly see connection health and resource usage for each connected server.

### Server List

The core view displaying your virtual machines.

*   **Card View:** Each VM is represented as a card showing real-time status (Running/Stopped), CPU/Memory usage sparklines, and IP addresses.
*   **Interaction:**
    *   **Double-Click Name:** Double-clicking on a VM's name triggers a background fetch of all VM data, including its full XML configuration. This ensures that tooltips and detailed views have the most up-to-date information.
    *   **Compact View (`k`):** Pressing **`k`** toggles between the detailed "Normal" view and a "Compact" view. The compact view is optimized for high-density environments, showing only the selection checkbox, the VM name (with its server), and the current status.
*   **Visual Cues:**
    *   **The Border/Text:** Indication about the server the VM belongs to.
*   **Navigation:** Use arrow keys to navigate the grid efficiently.

### Server Prefs

Configure server-specific settings directly from the UI without touching config files.

*   **Connection Details:** Edit URI, user, and SSH key paths.
*   **Auto-Connect:** Toggle which servers should connect on startup.
*   **Defaults:** Set default storage pools and network interfaces for new VMs on a per-server basis.

### Bulk CMD

The "Bulk Command" mode puts the power of fleet management at your fingertips.

*   **Multi-Select:** Select multiple VMs manually or use **Pattern Selection** (regex/glob) to target specific groups (e.g., `web-*`).
*   **Mass Actions:** Perform operations like `Start`, `Shutdown`, or `Reboot` on all selected VMs simultaneously.
*   **Efficiency:** Ideal for patching cycles or bringing up environments.
