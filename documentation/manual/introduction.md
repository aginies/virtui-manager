# Introduction to VirtUI Manager

**Command Your Virtual Machines** with unparalleled efficiency. 

VirtUI Manager is a powerful, next-generation terminal-based management solution for QEMU/KVM virtualization. It bridges the gap between the simplicity of command-line tools and the rich functionality of GUI-based solutions like `virt-manager`, offering a comprehensive, keyboard-centric management experience directly from your terminal.

![VirtUI Manager Main Window](images/main.png)

## Why VirtUI Manager?

Managing virtual infrastructure often involves a trade-off between convenience and accessibility. Traditional tools often come with significant drawbacks:

*   **GUI tools (virt-manager)** offer great visualization but require X11 forwarding or a heavy desktop environment, which is slow and resource-intensive over remote connections.
*   **Web interfaces (Cockpit)** can be complex to deploy, feature-incomplete, or lack robust multi-hypervisor support.
*   **CLI tools (virsh)** are fast but lack the intuitive "at-a-glance" overview and ease of use required for complex management tasks.

**VirtUI Manager** solves these challenges by providing a **lightweight, fast, and feature-rich interface** that runs entirely in the terminal.

*   **Intuitive Control:** Navigate complex infrastructures with a streamlined "Actions" menu. Context-aware options appear exactly when you need them.
*   **Scale Without Limits:** Execute commands across hundreds of VMs simultaneously using powerful regex patterns and group filters.
*   **Seamless Mobility:** Migrate workloads between different servers with ease. Our custom migration engine handles storage, snapshots, and overlays.
*   **Zero Dependencies:** No X11 required. Runs perfectly on headless servers and via low-bandwidth SSH connections.

## Key Features

### 🚀 Performance & Architecture
*   **Event-Driven UI:** Uses libvirt events for real-time updates, ensuring ultra-low bandwidth usage and immediate responsiveness.
*   **Smart Caching:** Advanced metadata caching reduces API load by up to 70%, keeping the interface fluid even with massive VM fleets.
*   **Transhypervisor View:** Connect to and manage multiple local or remote libvirt servers simultaneously in one unified dashboard.

### 🛠️ Comprehensive VM Control
*   **Surgical Precision:** Tweak CPU topology, memory, storage, and networking with deep configuration options.
*   **State Mastery:** Full support for snapshots and external disk overlays. Branch your VM states or revert changes with confidence.
*   **Host Intelligence:** Gain deep insights into physical infrastructure with real-time resource monitoring and detailed hardware capability trees (NUMA, CPU topology, cache).

### 📦 Rapid Deployment
*   **Modern Installation:** Experience a streamlined provisioning process with intelligent defaults, UEFI support, and optimized hardware detection.
*   **Automated Provisioning:** Take deployment to the next level with full automated installation support (Debian, Ubuntu, Fedora, Arch Linux, Alpine, openSUSE/SLES), template management, and intelligent auto-prefill.
*   **Instant Cloning:** Scale out instantly with advanced VM cloning, including auto-provisioning of storage.

### 🖥️ Remote & Advanced Access
*   **VirtUI Remote Viewer:** A custom-built, native graphical viewer for VNC/SPICE consoles with support for USB redirection, real-time log monitoring, and snapshot management.
*   **Secure Web Console:** Integrated `noVNC` support via websockify for browser-based access, even over SSH tunnels.
*   **Tmux Integration:** Multitasking mastery by seamlessly launching text consoles in separate Tmux windows.
*   **Command Line Interface:** Power users can leverage `vmanager_cmd`, a dedicated shell-like CLI for rapid management and automated pipelines.

## Comparison

| Feature | VirtUI Manager | Virt-Manager | Virsh (CLI) | Cockpit |
| :--- | :--- | :--- | :--- | :--- |
| **Interface** | TUI / GUI (GTK) | GUI (GTK) | CLI (Text) | Web UI |
| **Remote via SSH** | ✅ Excellent | ⚠️ Slow (X11) | ✅ Excellent | ❌ Setup required |
| **Headless Support** | ✅ Native | ❌ No | ✅ Native | ✅ Native |
| **Multi-Server** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **Resource Usage** | 🟢 Low | 🔴 High | 🟢 Lowest | 🟡 Medium |
| **Installation** | 🟢 Simple (Python) | 🟡 Medium | 🟢 Pre-installed | 🔴 Complex |

## Getting Started

Ready to take control of your virtualization infrastructure?

1.  Check the **[Installation Guide](app_installation.md)** to get set up.
2.  Explore the **[Main Window](main_window.md)** to understand the interface.
3.  Try the **[GUI Console](gui_console.md)** for a modern tabbed experience.
4.  Learn about **[VM Configuration](vm_configuration.md)** to tune your machines.
