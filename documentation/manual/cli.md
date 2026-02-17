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

*   **`connect <server> [server2 ...]`**
    Connects to specific servers defined in your configuration. Use `connect all` to connect to every configured server. The CLI also automatically connects to any server marked with `autoconnect: true` in your configuration.
    *   *Example:* `connect Localhost ryzen9`
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

## Network Management

*   **`list_networks`**: Lists all virtual networks on connected servers, showing their state and mode.
*   **`net_start <name>`**: Starts a virtual network.
*   **`net_stop <name>`**: Stops (destroys) a virtual network.
*   **`net_delete <name>`**: Permanently deletes (undefines) a network configuration.
*   **`net_info <name>`**: Shows detailed information (UUID, bridge, IP, DHCP range) for a network.
*   **`net_autostart <name> <on|off>`**: Enables or disables autostart for a network.

## Snapshot Management

*   **`snapshot_list <vm_name>`**: Lists all snapshots for a specific VM.
*   **`snapshot_create <vm_name> <snapshot_name> [--description "desc"]`**: Creates a new snapshot with an optional description.
*   **`snapshot_delete <vm_name> <snapshot_name>`**: Deletes a specific snapshot.
*   **`snapshot_revert <vm_name> <snapshot_name>`**: Reverts a VM to a previously saved snapshot state.

## Information & Discovery

*   **`list_vms`**: Lists all VMs on all connected servers with their current status.
*   **`list_pool`**: Displays all storage pools, their capacity, and usage.
*   **`list_unused_volumes [pool_name]`**: Finds orphaned disk images that are not attached to any VM.
*   **`host_info [server]`**: Displays host resource information (CPU models, topology, total/free memory).

## Advanced Tools

*   **`virsh [server]`**: Launches an interactive `virsh` shell connected to the selected server. If multiple servers are connected and no argument is provided, you will be prompted to choose.
*   **`bash [command]`**: Executes a local shell command or starts an interactive bash shell.
*   **`history [number|all|info]`**: Displays the command history. Use `history all` to see all history, `history <number>` for a specific number of recent commands, or `history info` for history file location.
*   **`!<number>`**: Re-executes a command from the history by its number (e.g., `!15` runs command #15).

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
| VM Operations | `start`, `stop`, `force_off`, `pause`, `resume`, `hibernate`, `delete`, `clone_vm`, `view` |
| Snapshots | `snapshot_list`, `snapshot_create`, `snapshot_delete`, `snapshot_revert` |
| Networking | `list_networks`, `net_start`, `net_stop`, `net_delete`, `net_info`, `net_autostart` |
| Storage | `list_pool`, `list_unused_volumes` |
| Pipelines | `pipeline` (with `|` chaining) |
| Shell/Utils | `bash`, `history`, `!<number>`, `quit`, `help` |