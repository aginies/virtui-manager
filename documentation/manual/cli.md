# Command Line Interface (CLI)

VirtUI Manager includes a powerful interactive shell mode, allowing for scriptable and direct management of your virtualization infrastructure without the TUI overhead.

To launch the CLI mode:
```bash
virtui-manager --cmd
# OR
virtui-manager-cmd
```

## Core Concepts

The CLI operates on a context-based system:

1.  **Connect** to one or more servers.
2.  **Select** the VMs you want to manage.
3.  **Execute** commands on the selection.

The prompt dynamically updates to show your active context:
`(<server_names>) [<selected_vms>]`

**Visual Distinction**: Server names and their associated VMs are color-coded based on a unique palette, making it easy to distinguish between different environments at a glance.

**Logging**: The CLI automatically logs all output and operations to a file (typically `~/.cache/virtui-manager/vm_manager.log`), which is useful for audit trails and debugging. Sensitive data (passwords, URIs) is automatically sanitized in logs and output.

## Connection Management

*   **`connect <server|uri> [server2|uri2 ...]`**
    Connects to specific servers defined in your configuration or directly to a libvirt URI (e.g., `qemu:///system`, `qemu+ssh://user@host/system`). Use `connect all` to connect to every configured server. The CLI also automatically connects to any server marked with `autoconnect: true` in your configuration.
    *   *Examples:*
        *   `connect Localhost ryzen9` (configured names)
        *   `connect qemu:///system` (direct local connection)
        *   `connect qemu+ssh://admin@server.org/system` (direct remote connection)
*   **`disconnect [<server> ...]`**
    Closes connections to specific servers. If no server is specified, it disconnects from **all** active sessions.

## Selection & Targeting

Most commands operate on the "current selection" if no arguments are provided.

*   **`select_vm <name|uuid> [name2|uuid2 ...]`**
    Selects specific VMs by name or UUID from any connected server. Auto-completion uses the format `VMNAME:UUID:SERVER` to help you target the exact instance.
*   **`select_vm re:<pattern>`**
    Selects VMs using regex patterns against their names.
    *   *Example:* `select_vm re:^web-.*` (Selects all VMs starting with "web-")
*   **`unselect_vm <name|uuid> | re:<pattern> | all`**
    Removes VMs from the current selection.
*   **`show_selection`**
    Displays the full list of currently selected VMs. Useful when the prompt is truncated due to many selected VMs.

## VM Operations

All operations can take specific VM names or UUIDs as arguments. If omitted, they apply to the **currently selected VMs**.

*   **`status`**: Shows state, vCPU count, and memory usage.
*   **`vm_info`**: Displays detailed metadata, including UUID, architecture, network interfaces (with IPs if available), and disk paths.
*   **`start`**: Boots up the target VMs.
*   **`stop`**: Sends a graceful shutdown signal (ACPI).
*   **`force_off`**: Immediately cuts power (hard shutdown).
*   **`pause` / `resume`**: Freezes or unfreezes VM execution.
*   **`hibernate`**: Saves the VM state to disk and stops it.
*   **`view`**: Launches the graphical viewer (Spice or VNC) for the target VMs.
*   **`delete [--force-storage-delete]`**: Permanently deletes the VM. Optional flag removes associated disk images. Deleted VMs are automatically removed from the selection.
*   **`clone_vm <source>`**: Launches an interactive wizard to create one or more clones, allowing you to specify naming postfixes and whether to clone the storage.
*   **`installvm [--dryrun|--show] <vm_name> [choice1 choice2 ...]`**: Installs a new Virtual Machine. It can be used interactively or unattended by providing numerical choices as positional arguments.
    *   `--dryrun` / `--show`: Displays a summary of the selected configuration and the command required to run it unattended, without performing the actual installation.
    *   *Note*: Automated installation credentials (passwords, keyboard layout, etc.) are pulled directly from the `AUTO_INSTALL_PRE_FILL` section of your configuration.


```bash
(ryzen7) status 161sles:0379866b-77e0-43c4-a6de-f1f9f33c79b9:ryzen7

--- Status on ryzen7 ---
VM Name                        Status          vCPUs   Memory (MiB)   
------------------------------ --------------- ------- ---------------
161sles                                 Running        6       4096           

(ryzen7) vm_info 161sles:0379866b-77e0-43c4-a6de-f1f9f33c79b9:ryzen7

--- VM Information on ryzen7 ---

[ 161sles ]
  UUID:         0379866b-77e0-43c4-a6de-f1f9f33c79b9
  Status:       Running
  Description:  No description available
  CPU:          6 cores (host-passthrough)
  Memory:       4096 MiB
  Machine Type: pc-q35-10.1
  Firmware:     UEFI (Secure Boot enabled)
  Networks:
    - default (MAC: 52:54:00:e8:85:93, model: virtio)
  IP Addresses:
    - vnet10 (MAC: 52:54:00:e8:85:93): 1 IPv4 address(es), 0 IPv6 address(es)
  Disks:
    - /home/VM_images/161sles.qcow2 (virtio, enabled, disk)
    - /home/VM_images/SLES-16.0-Online-x86_64-GM.install.iso (sata, enabled, cdrom)
  Graphics:
    - spice, port: 5900 (autoport)
```

```bash
(ryzen7) installvm --show fed43

Select VM Type:
  1. SECURE (Secure VM)
  2. COMPUTATION (Computation)
  3. DESKTOP (Desktop (Linux))
  4. WDESKTOP (Windows)
  5. WLDESKTOP (Windows Legacy)
  6. SERVER (Server)
Select VM Type [1-6]: 3

Select Distribution:
  1. openSUSE
  2. Ubuntu
  3. Debian
  4. Fedora
  5. Arch Linux
  6. Alpine Linux
  7. Generic / Custom ISO
Select Distribution [1-7]: 4

Select Version for Fedora:
  1. Fedora 43
  2. Fedora 42
  3. Fedora 41
Select Version [1-3]: 1

Fetching available ISOs for Fedora 43...

Select ISO Image:
  1. [Server] Fedora-Server-netinst-x86_64-43-1.6.iso (2025-10-23 03:26) [Size: 1128 MB]
  2. [Server] Fedora-Server-dvd-x86_64-43-1.6.iso (2025-10-23 03:43) [Size: 3323 MB]
  3. [Workstation] Fedora-Workstation-Live-43-1.6.x86_64.iso (2025-10-23 04:17) [Size: 2615 MB]
  4. [Spins] Fedora-i3-Live-43-1.6.x86_64.iso (2025-10-23 04:04) [Size: 2240 MB]
  5. [Spins] Fedora-Xfce-Live-43-1.6.x86_64.iso (2025-10-23 04:13) [Size: 2686 MB]
  6. [Spins] Fedora-Sway-Live-x86_64-43-1.6.iso (2025-10-23 04:09) [Size: 1970 MB]
  7. [Spins] Fedora-SoaS-Live-43-1.6.x86_64.iso (2025-10-23 04:04) [Size: 2128 MB]
  8. [Spins] Fedora-MiracleWM-Live-43-1.6.x86_64.iso (2025-10-23 04:07) [Size: 2278 MB]
  9. [Spins] Fedora-MATE_Compiz-Live-x86_64-43-1.6.iso (2025-10-23 04:13) [Size: 2819 MB]
  10. [Spins] Fedora-LXQt-Live-43-1.6.x86_64.iso (2025-10-23 04:10) [Size: 2198 MB]
  11. [Spins] Fedora-LXDE-Live-x86_64-43-1.6.iso (2025-10-23 04:07) [Size: 2084 MB]
  12. [Spins] Fedora-KDE-Mobile-Live-43-1.6.x86_64.iso (2025-10-23 04:10) [Size: 2519 MB]
  13. [Spins] Fedora-Cinnamon-Live-x86_64-43-1.6.iso (2025-10-23 04:16) [Size: 2837 MB]
  14. [Spins] Fedora-COSMIC-Live-43-1.6.x86_64.iso (2025-10-23 04:13) [Size: 2893 MB]
  15. [Spins] Fedora-Budgie-Live-43-1.6.x86_64.iso (2025-10-23 04:16) [Size: 2804 MB]
  16. Custom URL/Path
Select ISO [1-16]: 1

Select Network:
  1. testing (Mode: nat)
  2. default (Mode: nat)
Select Network [1-2] (default: 1): 2

Select Storage Pool:
  1. nvram (Capacity: 58 GiB, Used: 83.0%)
  2. VM_images (Capacity: 870 GiB, Used: 97.8%)
  3. NFStmp (Capacity: 0 GiB, Used: 0.0%)
  4. ISO (Capacity: 870 GiB, Used: 97.3%)
  5. win (Capacity: 0 GiB, Used: 0.0%)
  6. default (Capacity: 58 GiB, Used: 83.0%)
Select Pool [1-6] (default: 1): 2

Do you want to use automated installation? (yes/no) [no]: yes

Select Template:
  1. Basic Server (Kickstart) - Basic Fedora server installation with essential packages
  2. Desktop Workstation (Kickstart) - Fedora Workstation with GNOME desktop environment
  3. Development Workstation (Kickstart) - Fedora Workstation with development tools and libraries
  4. Full Server (Kickstart) - Fedora Server product environment
  5. Minimal System (Kickstart) - Minimal Fedora installation with only core packages
Select Template [1-5]: 1

Using Automated Installation Credentials from configuration.

--- Summary of Actions ---
VM Name: fed43
Server: ryzen7
VM Type: DESKTOP
Distribution: Fedora
Version: Fedora 43
ISO: https://download.fedoraproject.org/pub/fedora/linux/releases/43/Server/x86_64/iso/Fedora-Server-netinst-x86_64-43-1.6.iso
Network: default
Storage Pool: VM_images
Automated Install: Yes (Template: kickstart-basic.cfg)

Command to run this unattended:
installvm fed43 3 4 1 1 2 2 yes 1
```

## Network Management

*   **`list_networks`**: Lists all virtual networks on connected servers, showing their state and mode.
*   **`net_start <name>`**: Starts a virtual network.
*   **`net_stop <name>`**: Stops (destroys) a virtual network.
*   **`net_delete <name>`**: Permanently deletes (undefines) a network configuration.
*   **`net_info <name>`**: Shows detailed information (UUID, bridge, IP, DHCP range) for a network.
*   **`net_autostart <name> <on|off>`**: Enables or disables autostart for a network.

```bash
(ryzen7,g.org) net_info default

--- Network 'default' on ryzen7 ---
UUID: 3de11190-4f53-4640-862d-418372fe2b5f
Forward Mode: nat
Bridge: virbr0
IP: 192.168.122.1 / 255.255.255.0
DHCP: Enabled
DHCP Range: 192.168.122.128 - 192.168.122.254

--- Network 'default' on g.org ---
UUID: dcc28a3d-40b7-4ee1-b811-9b5d2533ccea
Forward Mode: nat
Bridge: virbr0
IP: 192.168.122.1 / 255.255.255.0
DHCP: Enabled
DHCP Range: 192.168.122.2 - 192.168.122.254
```

## Snapshot Management

*   **`snapshot_list <vm_name>`**: Lists all snapshots for a specific VM.
*   **`snapshot_create <vm_name> <snapshot_name> [--description "desc"]`**: Creates a new snapshot with an optional description.
*   **`snapshot_delete <vm_name> <snapshot_name>`**: Deletes a specific snapshot.
*   **`snapshot_revert <vm_name> <snapshot_name>`**: Reverts a VM to a previously saved snapshot state.

## Backup Management

VirtUI Manager provides a dedicated backup system that supports multiple strategies and advanced features like compression and encryption.

*   **`backup_create <backup_name> [options] [vm_names]`**: Creates a backup of the specified or selected VMs.
    *   `--type <snapshot|overlay>`: Choose between snapshot-based (default) or disk overlay backups.
    *   `--compress`: Compress the backup files.
    *   `--encrypt`: Encrypt the backup for security.
    *   `--verify`: Verify integrity immediately after creation.
    *   `--quiesce`: Use guest agent to freeze the filesystem for consistency.
    *   *Example:* `backup_create daily-$(date) --compress --verify`
*   **`backup_list [vm_name]`**: Lists all available backups, optionally filtering by VM.
*   **`backup_status <backup_name>`**: Shows detailed metadata for a specific backup (size, duration, checksums).
*   **`backup_restore <backup_name> [options]`**: Restores a VM from a backup.
    *   `--no-verify`: Skip integrity checks before restoration.
    *   `--force`: Bypass confirmation prompts.
*   **`backup_cleanup [--older-than <days>] [--type <type>]`**: Removes old backups according to retention policies.

```bash
(ryzen7,g.org) backup_list
Backup Name                         VM Name         Server          Type       Status     Created             
----------------------------------- --------------- --------------- ---------- ---------- --------------------
maintenance-20260218_213036_sle1... sle16_PRUSA     localhost       snapshot   SUCCESS    2026-02-18 21:30:36 
maintenance-20260218_201335_sle1... sle16_PRUSA     ryzen7          snapshot   SUCCESS    2026-02-18 20:13:35 
backup_test_sle16_PRUSA_ryzen7      sle16_PRUSA     ryzen7          snapshot   SUCCESS    2026-02-18 20:04:18 
```

## Information & Discovery

*   **`list_vms`**: Lists all VMs on all connected servers with their current status.
*   **`list_pool`**: Displays all storage pools, their capacity, and usage.
*   **`list_unused_volumes [pool_name]`**: Finds orphaned disk images that are not attached to any VM.
*   **`host_info [server]`**: Displays host resource information (CPU models, topology, total/free memory).

```bash
(ryzen7,g.org) list_pool
--- Storage Pools on ryzen7 ---
Pool Name                      Status          Capacity (GiB)  Allocation (GiB) Usage %   
------------------------------ --------------- --------------- --------------- ----------
VM_images                      active          870             829             95.29%    
nvram                          active          58              44              75.86%    
win                            inactive        0               0               0.00%     
NFStmp                         inactive        0               0               0.00%     
ISO                            active          870             829             95.29%    
default                        active          58              44              75.86%    

--- Storage Pools on g.org ---
Pool Name                      Status          Capacity (GiB)  Allocation (GiB) Usage %   
------------------------------ --------------- --------------- --------------- ----------
default                        active          899             375             41.71%    
```

## Advanced Tools

*   **`virsh [server]`**: Launches an interactive `virsh` shell connected to the selected server. If multiple servers are connected and no argument is provided, you will be prompted to choose.
*   **`bash [command]`**: Executes a local shell command or starts an interactive bash shell.
*   **`history [number|all|info]`**: Displays the command history. Use `history all` to see all history, `history <number>` for a specific number of recent commands, or `history info` for history file location.
*   **`!<number>`**: Re-executes a command from the history by its number (e.g., `!15` runs command #15).

```bash
(ryzen7,g.org) virsh
Multiple active connections:
  1. ryzen7
  2. g.org
Select server (number): 2
Connecting to virsh on g.org (qemu+ssh:***@ginies.org:999/system?no_tty=1)...
Type 'exit' or 'quit' to return to virtui-manager.
Welcome to virsh, the virtualization interactive terminal.

Type:  'help' for help with commands
       'quit' to quit

virsh # net-list 
 Name      State    Autostart   Persistent
--------------------------------------------
 default   active   yes         yes
```

## Pipelines

The CLI supports command pipelines for chaining multiple operations together using the `|` operator.

### Pipeline Syntax

```bash
pipeline [options] <command1> | <command2> | ...
```

### Pipeline Options

*   **`--dry-run`**: Shows what commands would be executed without actually running them.
*   **`--interactive` / `-i`**: Prompts for confirmation before executing the pipeline.

### Pipeline-Supported Commands

*   **Selection**: `select <vm1> [vm2...]` or `select re:<pattern>`
*   **VM Operations**: `start`, `stop`, `force_off`, `pause`, `resume`, `hibernate`
*   **Snapshots**: `snapshot create <name> [desc]`, `snapshot delete <name>`, `snapshot revert <name>`
*   **Utilities**: `wait <seconds>`, `view`, `info`

### Variable Expansion

*   **`$(date)`**: Expands to current date/time in `YYYYMMDD_HHMMSS` format.
*   **`$(time)`**: Expands to current time in `HHMMSS` format.

### Pipeline Examples

```bash
# Stop all web VMs, create a snapshot, then start them
select re:web.* | stop | snapshot create backup-$(date) | start

# Preview what would happen without executing
pipeline --dry-run select vm1 vm2 | pause

# Interactive confirmation before critical operations
pipeline -i select re:prod-.* | hibernate | snapshot create maintenance-$(date)
```

## Usage Example

```bash
# Connect to infrastructure
(VirtUI)> connect Localhost
(Localhost) >

# Select all web servers using pattern
(Localhost) > select_vm re:web-.*
(Localhost) [web-01,web-02] >

# Check their detailed information
(Localhost) [web-01,web-02] > vm_info

# Start them up
(Localhost) [web-01,web-02] > start
VM 'web-01' started successfully.
VM 'web-02' started successfully.

# Open a graphical console
(Localhost) [web-01] > view
Launching viewer for 'web-01' on Localhost...

# Create a snapshot before maintenance
(Localhost) [web-01,web-02] > snapshot_create web-01 pre-update --description "Before security update"
Snapshot 'pre-update' created for 'web-01'.

# Use pipelines for batch operations
(Localhost) > select re:web-.* | stop | snapshot create maintenance-$(date) | start
```

## Command Reference Summary

| Category | Commands |
|----------|----------|
| Connection | `connect`, `disconnect`, `host_info`, `virsh` |
| VM Selection | `list_vms`, `select_vm`, `unselect_vm`, `status`, `vm_info`, `show_selection` |
| VM Operations | `start`, `stop`, `force_off`, `pause`, `resume`, `hibernate`, `delete`, `clone_vm`, `installvm`, `view` |
| Snapshots | `snapshot_list`, `snapshot_create`, `snapshot_delete`, `snapshot_revert` |
| Backups | `backup_create`, `backup_list`, `backup_status`, `backup_restore`, `backup_cleanup` |
| Networking | `list_networks`, `net_start`, `net_stop`, `net_delete`, `net_info`, `net_autostart` |
| Storage | `list_pool`, `list_unused_volumes` |
| Pipelines | `pipeline` (with `|` chaining) |
| Shell/Utils | `bash`, `history`, `!<number>`, `quit`, `help` |
