# Host Management

VirtUI Manager provides built-in tools to inspect the host system's resources and capabilities directly from the TUI.

## Host Resource Dashboard

The **Host Resource Dashboard** provides a real-time overview of the hypervisor's resource usage and how it's being allocated to virtual machines.

To open the dashboard, press **`H`** (Shift+h) while in the main window.

![Host Resource Dashboard](images/host_resource.png)

### Dashboard Sections

*   **Host Details:** Displays static information about the host hardware, including the CPU model, frequency, and topology (nodes, sockets, cores, threads).
*   **Memory Usage:** Shows the current RAM usage of the host operating system. A progress bar visualizes the ratio of used to total memory.
*   **VM Allocation:**
    *   **Active Allocation:** Shows the total resources (vCPUs and RAM) reserved by currently *running* VMs. This is crucial for avoiding overcommitment of active resources.
    *   **Total Allocation:** Shows the resources reserved if *all* defined VMs (running and stopped) were to be started simultaneously.
    *   **Colors:** The progress bars change color (Green -> Yellow -> Orange -> Red) to indicate allocation levels, warning you when you are approaching or exceeding physical limits.

## Host Capabilities

The **Host Capabilities** viewer allows you to explore the low-level features supported by the hypervisor and host hardware. This is a tree-based view of the XML capabilities returned by Libvirt.

To open the capabilities viewer, press **`h`** while in the main window.

![Host Capabilities Tree](images/host_capabilities.png)

### Using the Tree View

*   **Navigation:** Use the **Up/Down** arrow keys to move through the tree.
*   **Expansion:** Press **Enter** or **Space** to expand or collapse nodes (e.g., `guest`, `host`, `cpu`).
*   **Search:** Type directly into the search bar at the top to filter the tree nodes. This is useful for finding specific CPU flags (e.g., `vmx`, `aes`) or supported device models.

### Key Information Available

*   **Host UUID:** The unique identifier of the host machine.
*   **CPU Features:** Detailed list of supported CPU instructions and security features.
*   **Migration Features:** supported migration schemes (e.g., `live`, `rdma`).
*   **Topology:** NUMA nodes and cache hierarchy.
*   **Guest Support:** Lists all guest architectures (e.g., `x86_64`, `i686`) and machine types supported by this KVM installation.

## Server Preferences

VirtUI Manager allows you to manage host-level resources like storage pools and virtual networks through the **Server Preferences** modal.

To access these settings, select a VM belonging to the server you want to manage, and choose **Server Preferences** (or use the configured shortcut).

### Network Management

The **Network** tab provides a comprehensive view of all virtual networks defined on the host.

![Server Network Management](images/server_network.png)

*   **Network List:** Shows the network name, mode (e.g., `nat`, `route`, `bridge`), active status, and autostart configuration.
*   **Usage Tracking:** Displays which VMs are currently using each network.
*   **Controls:**
    *   **De/Active:** Toggle the operational status of the selected network.
    *   **Autostart:** Enable or disable automatic starting of the network when the host boots.
    *   **Add/Edit/Delete:** Full lifecycle management for virtual networks.

### Storage Management

The **Storage** tab allows you to manage Libvirt storage pools and their volumes.

![Server Storage Management](images/server_storage.png)

*   **Pool Hierarchy:** A tree-based view of all storage pools (e.g., directory, LVM, iSCSI) and the volumes (disk images) they contain.
*   **Volume Details:** Displays the file name, size, and which VM is currently using the volume.
*   **Pool Lifecycle:**
    *   **Activate/Deactivate:** Control the state of storage pools.
    *   **Autostart:** Configure pools to start automatically.
    *   **Add/Delete Pool:** Create or remove storage definitions.
*   **Volume Operations:**
    *   **New Volume:** Create new disk images within a pool.
    *   **Attach Vol:** Directly attach a volume to a virtual machine.
    *   **XML Management:** View or edit the raw XML configuration for pools and volumes.
