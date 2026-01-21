# Command Line Interface (CLI)

VirtUI Manager includes a powerful interactive shell mode, allowing for scriptable and direct management of your virtualization infrastructure without the TUI overhead.

To launch the CLI mode:
```bash
virtui-manager --cmd
```

## Core Concepts

The CLI operates on a context-based system:
1.  **Connect** to one or more servers.
2.  **Select** the VMs you want to manage.
3.  **Execute** commands on the selection.

The prompt dynamically updates to show your active context:
`(<server_names>) [<selected_vms>]`

## Connection Management

*   **`connect <server> [server2 ...]`**
    Connects to specific servers defined in your configuration. Use `connect all` to connect to every configured server.
    *   *Example:* `connect Localhost ryzen9`
*   **`disconnect <server> [server2 ...]`**
    Closes connections to specific servers. Use `disconnect all` to close all sessions.

## Selection & Targeting

Most commands operate on the "current selection" if no arguments are provided.

*   **`select_vm <name> [name2 ...]`**
    Selects specific VMs by name from any connected server.
*   **`select_vm re:<pattern>`**
    Selects VMs using regex patterns.
    *   *Example:* `select_vm re:^web-.*` (Selects all VMs starting with "web-")
*   **`unselect_vm <name> | re:<pattern> | all`**
    Removes VMs from the current selection.

## VM Operations

All operations can take specific VM names as arguments. If omitted, they apply to the **currently selected VMs**.

*   **`status`**: Shows state, vCPU count, and memory usage.
*   **`start`**: Boots up the target VMs.
*   **`stop`**: Sends a graceful shutdown signal (ACPI).
*   **`force_off`**: Immediately cuts power (hard shutdown).
*   **`pause` / `resume`**: Freezes or unfreezes VM execution.
*   **`delete [--force-storage-delete]`**: Permanently deletes the VM. Optional flag removes associated disk images.
*   **`clone_vm <source> <new_name>`**: Creates a full clone of a VM on the same server.

## Information & Discovery

*   **`list_vms`**: Lists all VMs on all connected servers with their current status.
*   **`list_pool`**: Displays all storage pools, their capacity, and usage.
*   **`list_unused_volumes [pool_name]`**: Finds orphaned disk images that are not attached to any VM.

## Usage Example

```bash
# Connect to infrastructure
(VirtUI)> connect Localhost
(Localhost) >

# Select all web servers
(Localhost) > select_vm re:web-.*
(Localhost) [web-01,web-02] >

# Check their status
(Localhost) [web-01,web-02] > status

--- Status on Localhost ---
VM Name                        Status          vCPUs   Memory (MiB)   
------------------------------ --------------- ------- ---------------
web-01                         Stopped         2       4096           
web-02                         Stopped         2       4096           

# Start them up
(Localhost) [web-01,web-02] > start
VM 'web-01' started successfully.
VM 'web-02' started successfully.
```
