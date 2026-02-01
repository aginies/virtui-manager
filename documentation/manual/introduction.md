# Introduction to VirtUI Manager

VirtUI Manager is a powerful, text-based **Terminal User Interface (TUI)** application designed for managing QEMU/KVM virtual machines using the **libvirt** Python API.

It bridges the gap between the simplicity of command-line tools and the rich functionality of GUI-based solutions like `virt-manager`, offering a comprehensive management experience directly from your terminal.

![VirtUI Manager Main Window](images/main.png)

## Why VirtUI Manager?

Managing virtual infrastructure often involves a trade-off between convenience and accessibility.

*   **GUI tools (virt-manager)** offer great visualization but require X11 forwarding or a desktop environment, which can be slow, resource-heavy over remote connections.
*   **Web interfaces (Cockpit)** are user-friendly but can be heavy to deploy, feature-incomplete, or lack multi-hypervisor support.
*   **CLI tools (virsh)** are fast and scriptable but lack the intuitive "at-a-glance" overview and ease of use for complex tasks.

**VirtUI Manager** solves these challenges by providing a **lightweight, fast, and feature-rich interface** that runs entirely in the terminal. It is perfect for:

*   **Headless Servers:** Manage VMs directly on the server without needing a graphical environment.
*   **Remote Management via SSH:** Works flawlessly over SSH connections without the lag of X11 forwarding.
*   **Low-Bandwidth Environments:** The text-based interface is incredibly efficient.
*   **Power Users:** Keyboard-centric workflow for rapid management.

## Key Features

### 🚀 Performance & Architecture
*   **Lightweight TUI:** built with `textual`, requiring no X dependencies.
*   **Event-Driven:** Uses libvirt events to update the UI in real-time, minimizing polling overhead.
*   **Smart Caching:** Built-in caching for libvirt calls ensures a responsive interface even with large numbers of VMs.

### 🌐 Multi-Server Management
*   **Single Pane of Glass:** Connect to and manage multiple local or remote libvirt servers simultaneously.
*   **Transhypervisor View:** See VMs from different servers in a unified grid.

### 🛠️ Comprehensive VM Control
*   **Lifecycle Management:** Start, Stop, Pause, Resume, Force Off, and Delete VM, etc...
*   **Configuration:** detailed editing of CPU, Memory, Disks, Networks, and Boot options and mode.
*   **Snapshots & Overlays:** Create, restore, and manage internal snapshots and external disk overlays.
*   **Real-time Monitoring:** Interactive sparklines showing CPU, Memory, Disk I/O, and Network performance with toggleable views.
*   **Compact View Mode:** High-density display for managing large numbers of VMs efficiently.

### 📦 Advanced Operations
*   **Bulk Actions:** Select multiple VMs (using patterns or manual selection) to perform mass start/stop/delete operations or configuration changes.
*   **Migration:** Support for standard live migration and a unique **Custom Migration** mode for moving offline VMs with non-shared storage.
*   **Provisioning:** Built-in wizard for installing new VMs from cached ISOs or online repositories.

### 🖥️ Remote Access
*   **VirtUI Remote Viewer:** A custom-built, native graphical viewer for accessing VM consoles (VNC/SPICE) with support for USB redirection.
*   **Web Console:** Integrated support for `noVNC` to access VM consoles via a web browser, even for remote servers.
*   **Tmux Integration:** Seamlessly open text consoles (`virsh console`) in new tmux windows when running inside a tmux session.

### 📊 Monitoring & Debugging
*   **Real-time Stats:** View CPU, Memory, Disk, and Network usage for each VM with interactive sparkline views.
*   **Event-Driven Updates:** UI updates automatically when VM states change.
*   **Log Viewer:** Built-in log viewer for troubleshooting.
*   **Statistics Logging:** Continuous performance monitoring with detailed libvirt call statistics and cache analysis.
*   **Cache Statistics:** Real-time monitoring of caching efficiency and performance optimization.

## Comparison

| Feature | VirtUI Manager | Virt-Manager | Virsh (CLI) | Cockpit |
| :--- | :---: | :---: | :---: | :---: |
| **Interface** | TUI (Terminal) | GUI (GTK) | CLI (Text) | Web UI |
| **Remote via SSH** | ✅ Excellent | ⚠️ Slow (X11) | ✅ Excellent | ❌ Setup required |
| **Headless Support** | ✅ Native | ❌ No | ✅ Native | ✅ Native |
| **Multi-Server** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **Resource Usage** | 🟢 Low | 🔴 High | 🟢 Lowest | 🟡 Medium |
| **Installation** | 🟢 Simple (Python) | 🟡 Medium | 🟢 Pre-installed | 🔴 Complex |

## Getting Started

Ready to take control of your virtualization infrastructure?

1.  Check the **[Installation Guide](app_installation.md)** to get set up.
2.  Explore the **[Main Window](main_window.md)** to understand the interface.
3.  Learn about **[VM Configuration](vm_configuration.md)** to tune your machines.
