# Rainbow V Manager - Features

## Overview
Rainbow V Manager is a Textual-based TUI (Terminal User Interface) application for managing QEMU/KVM virtual machines using the libvirt Python API. It provides a comprehensive interface for VM management with features that go beyond basic management.

## Main Interface Features

### Multi-server Management
- Connect to multiple libvirt servers simultaneously
- Transhypervisor view showing VMs from different servers
- Server selection and management interface

### VM Grid Display
- VMs displayed in a responsive grid layout
- Color-coded status indicators (Running, Paused, Stopped)
- CPU and memory usage sparklines for running VMs
- Pagination controls for large VM lists

### VM Management Actions
- Start, Shutdown, Force Off (destroy), Pause, Resume
- Delete VM with optional storage cleanup
- Clone VM functionality
- Rename VM with snapshot handling
- Take, restore, and delete VM snapshots
- View/Edit XML configuration
- Connect to VM via virt-viewer
- Web console access via novnc (when available)

### Advanced Features
- Filter VMs by status (All, Running, Paused, Stopped) and search by name
- Server preferences configuration
- Virsh shell access
- Detailed VM information view
- Web console management with automatic port allocation
- Configuration file management for server lists

## Configure VM Features

### CPU Configuration
- Edit CPU count
- Select CPU model from available models (including host-passthrough and default options)
- CPU model selection is disabled when VM is running

### Memory Configuration
- Edit memory size in MB
- Enable/disable shared memory (disabled when VM is running)

### Firmware Configuration
- Select firmware type (BIOS or UEFI)
- For UEFI firmware:
  - Enable/disable Secure Boot
  - Enable/disable AMD-SEV and AMD-SEV-ES (when supported)
  - Select UEFI file from available options
- Machine type selection (disabled when VM is running)

### Boot Configuration
- Enable/disable boot menu
- Boot device management (though not fully implemented in the visible UI)

### Disk Management
- View all disks in a table format
- Add new disk (create new or attach existing)
- Attach existing disk from storage pools
- Remove disk
- Disable disk
- Enable disk
- Edit disk properties (cache mode and discard mode)
- Disk status indicators (enabled/disabled)

### Network Configuration
- View network interfaces with MAC addresses and IP addresses
- Change network interface to a different network
- View network DNS and gateway information

### VirtIO-FS Configuration
- View existing VirtIO-FS mounts
- Add new VirtIO-FS mount
- Edit existing VirtIO-FS mount
- Delete VirtIO-FS mount
- Requires shared memory to be enabled

### Video Configuration
- Select video model (virtio, qxl, vga, cirrus, bochs, ramfb, none, default)
- Video model selection is disabled when VM is running

### Graphics Configuration
- Select graphics type (VNC, Spice, or None)
- Configure listen type (Address or None)
- Set address (Hypervisor default, Localhost only, All interfaces)
- Enable/disable auto port allocation
- Set port number (when auto port is disabled)
- Enable/disable password protection
- Set password for graphics access
- Apply graphics settings (disabled when VM is running)

### Additional Features
- Tabbed interface for organized configuration
- Toggle between main and extended configuration tabs
- Real-time status indicators
- Confirmation dialogs for destructive actions
- Error handling and user feedback
- VM status validation (prevents configuration changes when VM is running)

## User Interface Features

### Keyboard Shortcuts
- `v` - View Log
- `ctrl+v` - Virsh Shell
- `f` - Filter VM
- `p` - Server Pref
- `m` - Servers List
- `s` - Select Servers
- `q` - Quit

### Visual Elements
- Color-coded server identification
- Status indicators with color coding (Running, Paused, Stopped)
- Sparkline graphs for CPU and memory usage
- Responsive layout that adapts to terminal size
- Tabbed interface for organized information display

## Technical Capabilities

### Connection Management
- Support for multiple libvirt connection types (local, SSH, etc.)
- Automatic detection of virt-viewer, websockify, and novnc availability
- Error handling and logging
- Responsive UI that adapts to terminal size
- Command-line mode support (--cmd flag)

### Requirements
- Minimal terminal size: 34x92
- Python 3.7+
- libvirt
- textual
- pyaml
- virt-viewer (for connecting to VMs)
- websockify and novnc (for web console access)

## User Experience
- Visual feedback through notifications
- Confirmation dialogs for destructive actions
- Loading indicators for long-running operations
- Detailed error messages
- Command-line mode for advanced users

## Warning
This project is part of a SUSE hackweek project, it's not mature, under heavy development, lacks a lot of features, and surely contains tons of bugs. You have been warned. Please report any bugs or ask for specific features.