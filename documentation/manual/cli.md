# Command Line Interface (CLI)

Interactive shell for VM management. Scriptable, no TUI overhead.

To launch the CLI mode:
```bash
virtui-manager --cmd
# OR
virtui-manager-cmd
# OR
vmc
```

## Quick Launch

Run a single command non-interactively:
```bash
vmc -c "connect localhost"
vmc -c "list_vms"
vmc -c "select_vm web-01 | start"
```

`-c, --command <command>` — execute one command and exit. Prompts auto-select first option.

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
*   **`list_servers`**
    Lists all servers defined in your configuration (name, URI, autoconnect flag), without connecting to any of them.
*   **`list_connected_servers`**
    Lists the servers you are currently connected to, with their URIs.

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
*   **`vm_stats`**: Shows live runtime statistics (CPU%, memory%, disk and network I/O) for the target VMs.
*   **`start`**: Boots up the target VMs.
*   **`stop`**: Sends a graceful shutdown signal (ACPI).
*   **`force_off`**: Immediately cuts power (hard shutdown).
*   **`reboot`**: Sends an ACPI reboot signal to the target VMs (they must be running).
*   **`pause` / `resume`**: Freezes or unfreezes VM execution.
*   **`hibernate`**: Saves the VM state to disk and stops it.
*   **`view`**: Launches the graphical viewer (Spice or VNC) for the target VMs.
*   **`delete [--force-storage-delete]`**: Permanently deletes the VM. Optional flag removes associated disk images. Deleted VMs are automatically removed from the selection.
*   **`clone_vm <source>`**: Launches an interactive wizard to create one or more clones, allowing you to specify naming postfixes and whether to clone the storage.
*   **`rename_vm <vm_name> <new_name>`**: Renames a stopped VM (snapshots and NVRAM are preserved).
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
*   **`network_usage <name>`**: Lists the VMs that use a network.
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
*   **`list_volumes [pool_name]`**: Lists all storage volumes in a pool (name, type, capacity, allocation).
*   **`list_unused_volumes [pool_name]`**: Finds orphaned disk images that are not attached to any VM.
*   **`volume_usage <pool_name> <volume_name>`**: Lists the VMs that use a storage volume.
*   **`host_info [server]`**: Displays host resource information (CPU models, topology, total/free memory).
*   **`host_capabilities [server]`**: Displays hypervisor capabilities (architecture, CPU models, latest machine types, supported video models).
*   **`list_isos [distro [version]]`**: Lists available ISOs for a distribution and version (e.g. `list_isos openSUSE 15.6`). Distro names with spaces work as-is (e.g. `list_isos Arch Linux`). If no distro is given, the supported distros are listed; if no version is given, the supported versions are listed.
*   **`list_cached_isos`**: Lists ISOs already downloaded to the local cache.

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

## Config-Driven Deployment

Deploy a single VM from a declarative JSON file instead of answering the
`installvm` prompts. The JSON describes every choice (server, hardware, ISO,
network, storage, boot, graphics and the auto-install settings), so the deploy
runs fully non-interactively. This works for local and remote servers alike.

*   **`prepare_json [output_path] [--init]`** (interactive, inside the CLI): walks you through the real decisions — probes the connected server for active networks, storage pools, cached/pool ISOs and the distribution ISO list, and lists the available auto-install templates — then writes a ready-to-run JSON file. Use `--init` to write an example skeleton without connecting to any server.
*   **`vmc --json <file.json> [--dryrun]`** (outside the CLI): loads the JSON, connects to the server named in it and provisions the VM. `--dryrun` prints a summary of what would happen without provisioning anything.
*   **`deploy_vm <file_or_name> [--dryrun|--show]`** (interactive, inside the CLI): the in-shell equivalent of `vmc --json` — loads the JSON, connects to the server named in it and provisions the VM. `<file_or_name>` may be a full path or a bare filename looked up in `~/.config/virtui-manager/deploy/` (tab-completes the available files).

The hypervisor a deploy goes to is the `server` field of the file, so a deploy
needs no `-u/--uri`. That flag is how one file goes somewhere else: given `-u`,
the deploy connects to the uri instead of the file's `server`, and the `server`
of the answer names the hypervisor the machine was really made on, so you can
see the override took effect.

```bash
# The file says "server": "Localhost", so this goes there ...
$ vmc --json ~/deploy/web01.json
# ... and this builds the same machine on another hypervisor, with no edit
$ vmc -u qemu+ssh://root@build-box/system --json ~/deploy/web01.json
{"ok": true, "command": "deploy", "data": {"domain": "web01", "server": "build-box", ...}}
```

When the config enables an unattended install (`autoinstall.enabled`), the deploy prints the location and HTTP URL of the generated auto-install profile right after provisioning finishes. The file is served from a temporary directory while the VM installs, so you can inspect it (packages, SCC, baked-in post-install commands) if the auto-install does not behave as expected.

```bash
# 1. Generate a config (interactive, inside the vmc prompt)
(vmc) prepare_json ~/deploy/web01.json

# 2. Review / edit the file, then deploy it — either outside the CLI ...
$ vmc --dryrun --json ~/deploy/web01.json
$ vmc --json ~/deploy/web01.json
# ... or from inside the CLI (bare filename works for files in the deploy dir)
(vmc) deploy_vm web01.json
```

Example `autoinstall` block (openSUSE/SLES, the only distros currently supported
for extra packages and SCC registration):

```json
{
  "server": "Localhost",
  "vm_name": "web01",
  "vm_type": "server",
  "boot_uefi": true,
  "graphics": "vnc",
  "network": "default",
  "storage_pool": "default",
  "iso": { "source": "url", "url": "https://example.com/opensuse-leap.iso" },
  "autoinstall": {
    "enabled": true,
    "template": "autoyast-server.xml",
    "packages": ["ssh", "vim"],
    "username": "admin",
    "user_password": "CHANGE_ME",
    "root_password": "CHANGE_ME",
    "hostname": "web01",
    "timezone": "UTC",
    "keyboard": "us",
    "language": "English (US)",
    "scc": { "scc_email": "", "scc_reg_code": "" }
  },
  "post_install": {
    "commands": ["systemctl enable foo", "echo done > /var/log/deploy.log"]
  },
  "start": true
}
```

`vm_type` selects a *profile* — `secure`, `computation`, `desktop`,
`low_resource`, `wdesktop`, `wldesktop` or `server` — and the memory, disk and
vCPU are taken from that profile, so you do not have to specify them. The memory
and vCPU a profile means are written down once, in `VM_TYPE_DEFAULTS`
(`src/vmanager/vm_provisioner.py`), and the interactive *Install VM* screen
pre-fills its expert settings from that same table, so the two cannot drift
apart; the disk size a profile gets is the neighbouring `DEFAULT_DISK_SIZE_GB`.
To set them manually, use `"vm_type": "custom"` and add `memory_mb`, `vcpu` and
`disk_size_gb` (all required in that case). You may also add these fields to a
profile to override its defaults.

The `iso.source` field can be `url`, `cached`, `pool` or `repository` (mirroring
the ISO sources offered by `installvm`). `autoinstall.packages` adds extra
packages on top of the template defaults, and `autoinstall.scc` registers the
installed system with the SUSE Customer Center.

### Post-install commands

`post_install` runs shell commands **during the unattended install**, by baking
them into the auto-install template's post-install script (AutoYaST
`<scripts><chroot-scripts>` for AutoYaST templates, `scripts.post` for Agama
templates). Because the commands run as part of the install itself, no guest
agent or SSH credentials are needed afterwards — which makes this a good fit for
deploy-then-test workflows (e.g. reproduce a bug automatically on a fresh VM).

*   `commands` — a list of shell strings, run in order as root via the system
    shell.

`post_install` requires `autoinstall` to be enabled (there is no template to
bake the script into otherwise). Each command is written verbatim, so commas,
quotes and shell redirection are safe. The commands run inside the installed
system after the packages are installed but before the first boot, so they can
enable services and write files but cannot start them (systemd is not running
yet).

### Booting a disk instead of installing

A deployment does not have to install anything. `disk.volume` names a volume in
the storage pool — one that `volume upload` put there, or one that
`volume create-overlay` made from a base — and the machine boots from it:

```json
{
  "server": "Localhost",
  "vm_name": "worker01",
  "vm_type": "custom",
  "memory_mb": 2048,
  "vcpu": 2,
  "disk_size_gb": 20,
  "storage_pool": "default",
  "disk": { "volume": "worker01-disk" },
  "seed": { "volume": "worker01-seed" },
  "metadata": { "mcp-sidekick": "instance", "instance": "worker01" },
  "start": true
}
```

*   **`disk.volume`** — the name of the volume in `storage_pool` to boot from.
    With `disk.backing_volume`, the disk is created first as a qcow2 overlay of
    that base volume, so a machine starts from a shared base and writes nowhere
    else.
*   **`seed.volume`** — a volume attached as a second, read-only `cdrom`.
    Whatever is in it (cloud-init, Ignition, a tarball) is what the image on the
    disk reads on first boot. It belongs to the machine rather than to the way the
    machine was made, so both halves of the deploy file take it: a machine being
    installed carries the disc alongside its install media, and reads it when it
    first boots the disk it was installed onto. There it is data only — the media
    is what boots — and `boot` decides the order for a machine that boots a volume
    it already has. A seed the pool has not got is a missing resource (status 5),
    said before anything is defined.
*   **`metadata`** — strings stored on the domain, in its own namespace, so a
    caller can find its machines again and know what each is for. Both halves of
    the deploy file keep them, booting a volume and installing from media, and
    `domain metadata <name>` reads them back. A key becomes an element name, so
    one XML cannot use is refused as a bad file (status 3) before anything is
    defined; a value cannot escape the element it is written into.
*   **`boot`** — `disk` (default when there is no installer) or `cdrom`; with a
    seed present this decides which of the two the firmware tries first.

An `iso` and a `disk.volume` are two ways of making a machine and cannot both
be in one file; `autoinstall` needs an `iso`, since there is nothing to answer
for a disk that is already installed. This path asks for no screen: pass
`graphics` (`vnc` or `spice`) when you want one. Any other word there is a bad
deploy file (status 3) rather than a device libvirt will refuse later; `null` is
the way to ask for no screen.

What goes into the machine's XML is escaped on the way in — pool and volume
paths, the network's name, the firmware paths — so a pool directory holding a
`&` or a quote boots a machine instead of producing XML that does not parse.

The volumes have to be in the pool before the deploy runs, and libvirt has to
see them — `volume upload` and `volume create-overlay` do that, or the files
were put in the pool's directory and the pool refreshed.

`--dryrun` asks the question and changes nothing, on either route: no pool is
made or started, no overlay volume is created, no domain is defined. The summary
says what would be made — a disk that is not in the pool yet is reported as the
overlay of its base that the deploy would create, and a pool that is defined but
not started is reported as one the deploy would start. While a pool is down its
volumes cannot be read at all, so a dry run does not answer whether they are in
it and does not refuse a deploy for a volume it was never allowed to see; a
volume a started pool does not have is still the deploy that cannot happen, and
is answered 5 as it is without `--dryrun`.

Both routes print the same summary, so a line means the same thing whichever
machine you asked about and sits in the same place: `VM Name`, `Server`, the
hardware (`Memory: 2048 MB, vCPU: 2`), what the machine boots from, `Seed CD-ROM`
when it has one, `Network`, `Storage Pool`, `Boot Mode`, `Graphics` — `none` for a
file that asked for no screen — and last whether the machine would be started once
it is defined or left stopped, which is the file's `start`. Each route then adds
what only it can answer: an install names the `VM Type` profile, the `ISO`, the
size it would give the disk, and what the automated install would be fed; a machine
booted from a volume names the volume, says whether it would be made as an overlay
of a base, and names the `Boot order` the firmware would try — but no disk size,
because the disk it boots already exists and the size in the file is ignored.
Anything the deploy would *do*, rather than what the machine would *be*, is written
as what would happen — `Storage pool: default would be started`,
`would be made as an overlay of: base` — so a summary cannot be misread as a change
the dry run already made.

### Managing deployment JSON files

*   **`list_json`** (inside the CLI): lists all deployment JSON files you have
    created with `prepare_json` (from `~/.config/virtui-manager/deploy/`),
    showing the VM name, type, size and modification date.
*   **`show_json <file>`** (inside the CLI): pretty-prints a deployment JSON
    file. `<file>` may be a full path or a bare filename looked up in the
    deploy directory (tab-completes the available files).
*   **`deploy_vm <file_or_name> [--dryrun|--show]`** (inside the CLI): deploys the VM
    described by a deployment JSON file — the in-shell equivalent of
    `vmc --json`. `<file_or_name>` may be a full path or a bare filename looked
    up in the deploy directory (tab-completes the available files); the server
    is connected automatically from the `server` field in the JSON.

```bash
(vmc) list_json
Deployment JSON files in /home/user/.config/virtui-manager/deploy:

File                     VM Name            Type             Size  Modified
--------------------------------------------------------------------------
web01.json               web01              server           789 B  2026-11-03 12:00

(vmc) show_json web01.json
--- /home/user/.config/virtui-manager/deploy/web01.json ---
{
  "server": "Localhost",
  "vm_name": "web01",
  "vm_type": "server",
  ...
}

(vmc) deploy_vm web01.json
```

## Machine-Readable Output

`--machine` turns `vmc` into something a program can drive. Reading the prose
of `vmc -c "deploy"` is not a contract, and neither is an exit status of 0 for
a deployment that failed: in machine mode one JSON object goes to stdout,
everything a command prints goes to stderr as a log line, and the exit status
says what happened.

```bash
# Deploy from a JSON file and get the result as JSON
$ vmc --machine --json ~/deploy/web01.json
{"ok": true, "command": "deploy", "data": {"domain": "web01", "server": "localhost", ...}}

# Ask what this vmc is, and which commands it answers to
$ vmc --machine -c "machine info"
{"ok": true, "command": "machine info", "data": {"protocol": 1, "name": "virtui-manager", "version": "3.4.0", "commands": ["machine"]}}

# A deployment can be tried out the same way
$ vmc --machine --dryrun --json ~/deploy/web01.json
{"ok": true, "command": "deploy", "data": {"domain": "web01", "dryrun": true, ...}}
```

*   stdout carries **only** the JSON objects below — no progress bar, no
    connection chatter, no libvirt noise. Those go to stderr, and are still
    written to the log file.
*   Messages and data are sanitized the same way as the log: a password or the
    credentials in a hypervisor URI never reach the answer.
*   `--machine` needs `-c/--command` or `--json`; without one it is a usage
    error (status 2).

### The answer

The last object on stdout is the result of the call:

```json
{"ok": true, "command": "deploy", "data": {"domain": "web01", "storage_pool": "default", "firmware": "uefi", "running": true}}
```

```json
{"ok": false, "command": "deploy", "code": "already_exists", "message": "VM 'web01' already exists."}
```

What a `deploy` answers, in `data`:

| Field | Type | Meaning |
| --- | --- | --- |
| `domain` | string | the machine the command made. |
| `server` | string | the hypervisor it was actually made on — the file's `server`, or the `-u` that overrode it. |
| `storage_pool` | string | the pool its disk lives in. |
| `network` | string | the network it is attached to. |
| `firmware` | string | `uefi` or `bios`. |
| `graphics` | string or null | the screen the machine was given: `vnc` or `spice`, or `null` when the file asked for no screen by setting `graphics` to `null` — an absent `graphics` is not that, it means `vnc`. It is the file's value read back, so an answer can be pasted into the next file; the word `none` is what a *summary* puts where there is no screen, and a file that holds it is refused as a bad `graphics`. |
| `memory_mb` | number | memory in megabytes. |
| `vcpu` | number | virtual CPUs. |
| `disk_gb` | number | the disk size the file asked for, in gigabytes. A deploy that took its size from the `vm_type` profile answers no `disk_gb`, and the machine is not diskless for all that: the number it got is the profile's. A deploy that boots a volume answers no `disk_gb` either, because the size belongs to the volume it was handed. |
| `volume` | string | the disk this machine boots, when the deploy boots the volume the file names (`disk.volume`) instead of installing — a deploy that installs makes its own disk and answers no `volume`. |
| `automated_install` | boolean | whether an auto-install profile was generated. |
| `seed` | string | the seed volume attached, when the file named one. |
| `autoinstall` | object | where that profile is and the URL the guest was told to fetch it from: `directory`, `file`, `url`. Only there when one was generated, never an empty object. |
| `dryrun` | boolean | `true` when nothing was changed. |
| `running` | boolean | whether the machine runs as the command answers — `true` or `false` on both routes, never a number. |

A deploy that boots a volume has no `autoinstall` to report: nothing was
generated and nothing was served. The same three values are written to the log at
info, so a run's log says what was served even when nobody was watching the
terminal — which is the case for a caller whose install then failed, the moment
the profile is most wanted.

These are the two answers to two files naming one machine with `graphics` set to
`null`, one booting `16-gnome.qcow2` and one installing from an ISO — dry runs on
a live hypervisor, so nothing was changed and `running` is `false`:

```json
{"ok": true, "command": "deploy", "data": {"domain": "tt-x-probe", "server": "Local", "storage_pool": "fcrozat-images", "network": "default", "firmware": "uefi", "graphics": null, "memory_mb": 4096, "vcpu": 4, "automated_install": false, "dryrun": true, "running": false, "volume": "16-gnome.qcow2"}}
{"ok": true, "command": "deploy", "data": {"domain": "tt-x-probe", "server": "Local", "storage_pool": "fcrozat-images", "network": "default", "firmware": "uefi", "graphics": null, "memory_mb": 4096, "vcpu": 4, "automated_install": false, "dryrun": true, "running": false}}
```

One `graphics` for one file, and the volume only on the route that was handed
one. Both routes print `Graphics: none` in the summary above these lines: that
line is prose, and the field a program reads back is `null` because `null` is
what the file said.

### Progress events

Before the answer, a long command may report what it is doing, one event per line:

```json
{"event": "progress", "stage": "install", "message": "Downloading the ISO", "percent": 45}
```

`stage` says which part of the command is talking. These are the words it holds,
and all of them: a program switches on the stage the way it switches on an exit
status, so no command gets to add one quietly.

| Stage | Emitted by |
| --- | --- |
| `install` | a deploy building a machine from install media. |
| `boot` | a deploy booting a machine from a volume the pool already holds. |
| `upload` | `volume upload`, as the bytes go into the pool. |
| `start` | `domain start`, once the machine is on. |
| `shutdown` | `domain shutdown`, once the guest has been asked. |
| `reboot` | `domain reboot`, once the guest has been asked. |
| `destroy` | `domain destroy`, once the machine is off. |

The words are the set in `MachineProgressStage` in `src/vmanager/constants.py`, and
every command takes its stage from there. A stage added to the set needs its row
here: `tests/test_cli_docs.py` reads the commands, the set and this table, and fails
when they stop saying the same thing — a stage emitted and not listed leaves a
script branching on a word it was never told about. `domain undefine` reports nothing
at all, because the machine it removes has no state left to report afterwards.

`message` is the detail — text for a human, never the stage repeated — and `percent`
is how far along the command thinks it is, from 0 to 100.

`message` is not a word to branch on, and it does not pretend to be one: `stage` is
English in every environment because a program switches on it, while `message` is the
project's own prose and, on a deploy, is translated — so the same step can arrive in
the caller's language. What the two deploy routes do share is the vocabulary: a
machine built from install media and a machine booted from a volume both report
`Defining VM` while they define one, and `Provisioning Complete` when they are done,
so watching a machine come into being takes one set of words, not one per route.

It is a number for a progress bar, so it only ever rises within one command: a
step that would fall behind the highest percent already sent is reported at that
percent, and a stage repeated without going further — finding the same ISO a
second time — is not reported at all. And it is the only percentage on an event:
a message does not carry one of its own to compete with it.

### Resource commands

The commands a program needs to prepare a machine, and to ask what the
hypervisor has, answer the same way as `deploy` does — one JSON object, an exit
status that means something:

```bash
$ vmc -u qemu:///system --machine -c "host capabilities"
{"ok": true, "command": "host capabilities", "data": {"hostname": "h1", "architectures": ["x86_64", "aarch64"], "host_cpus": 8, "memory_mb": 32768, "uefi": true}}

$ vmc -u qemu:///system --machine -c "pool list"
{"ok": true, "command": "pool list", "data": {"pools": [{"name": "default", "state": "running", "path": "/var/lib/libvirt/images", ...}]}}

# Put an image in a pool, then make a machine that starts from it and writes nowhere else
$ vmc --machine -c "volume upload --pool default --name base.qcow2 --file /tmp/base.qcow2 --format qcow2"
$ vmc --machine -c "volume create-overlay --pool default --name worker01 --backing base.qcow2 --size 20"

# A name that is taken is status 6, and the machine that is not there is status 5
$ vmc --machine -c "pool create sidekick --path /srv/sidekick"
{"ok": false, "command": "pool create", "code": "already_exists", "message": "A pool named 'sidekick' already exists."}
```

| Command | Answers with |
| --- | --- |
| `host capabilities` | hostname, the architectures guests can have, host CPUs, memory, UEFI. |
| `host resources` | the same CPUs and memory, plus what a new machine could be given. |
| `pool list` / `pool info <name>` | the pools and their state, path and free space. |
| `pool create <name> [--path D]` | the pool it made. Without `--path`, a directory named after the pool. |
| `volume list --pool P` / `volume info --pool P --name V` | volumes with their size, format and path. |
| `volume upload --pool P --name V --file F [--format raw\|qcow2] [--force]` | the volume, with the bytes it wrote. Streams; a whole image is never in memory. |
| `volume create-overlay --pool P --name V --backing B [--size GB] [--force]` | the new volume, whose backing file is `B`. |
| `volume delete --pool P --name V [--force]` | what happened to the volume. |
| `volume rename --pool P --name V --new-name W [--force]` | status 9, and the reason: the libvirt binding cannot rename a volume. |
| `domain list [--prefix X]` | the domains and their state; `--prefix` keeps the names that start with it. |
| `domain state <name>` | whether it runs, and whether libvirt has it at all. |
| `domain address <name>` | its lease address, `ipv4` null when it has none yet. |
| `domain metadata <name>` | the strings the caller stored on it, empty when it stored none. |
| `domain start\|shutdown\|destroy\|reboot <name>` | the domain, after it was told. |
| `domain undefine <name>` | the domain, `undefined`; a running one is refused (status 7). |
| `network list` / `network info <name>` | the networks and their state, and `active` for a network without libvirt state. |
| `machine info` | protocol version, name, version, and every command this vmc answers. |

`--force` takes no value: written means yes, and `--force yes` is a bad argument
(status 2) rather than a yes. Where it is offered it means the name is the
caller's to overwrite — `upload` and `create-overlay` delete the volume standing
on the name and make the new one in its place — and without it a name in use is
status 6. A flag the command
has never heard of is status 2 too, naming the flag.

`volume delete` will not remove a volume a running machine is using — that is
status 7, not a libvirt traceback.

A command that changed the hypervisor is followed by the tool forgetting what it
remembered about it: the state of the machine it told, and the volumes of the
pool it wrote to along with the space those volumes leave. So a caller that asks
for two things in one session — start a machine, then ask its state — is answered
with what is true now, not with what was cached before the first command. A
command that refused changed no hypervisor and forgets nothing.

### Choosing the hypervisor

`-u/--uri` says which hypervisor the call means, and connects to that one
only — the servers marked `autoconnect` in the configuration are left alone, so
what a command sees does not depend on the local configuration:

```bash
$ vmc -u qemu:///system --machine -c "machine info"
$ vmc -u qemu+ssh://root@h1/system --machine --json ~/deploy/web01.json
```

With `--json` the uri **takes precedence over** the `server` field of the
deployment file: the file says what machine to build, `-u` says which
hypervisor builds it, and one file can be deployed to as many of them as you
like. Without `-u`, `--json` connects to the `server` of the file, and `-c` to
the configured servers as before.

`-u` replaces the `server`, it does not remove the requirement: a file with no
`server` is refused as a bad deployment file (status 3) even when `-u` names a
hypervisor. Whether a file is a valid one is decided by its own bytes, not by
the flags of the call that happens to read it, so the same file is not good in
one call and rejected in the next. The `server` of the answer is the hypervisor
the command actually used, so a caller can check the override took effect rather
than take it on trust. A `-u` the hypervisor refuses is status 4. Like every other message, the uri is sanitized:
credentials it carries are not repeated back.

### Exit statuses

| Status | Meaning |
| --- | --- |
| 0 | The call succeeded. |
| 2 | Unknown command, unknown flag, or a value that cannot be parsed. |
| 3 | A deployment file is missing, malformed or invalid. |
| 4 | The hypervisor cannot be reached. |
| 5 | No such domain, volume, pool or network. |
| 6 | The name is taken. |
| 7 | The hypervisor refused, or the operation failed. |
| 8 | A wait ran out — install media that stops downloading, for instance. |
| 9 | The command has no machine-readable result in this version of vmc. |

Status 2 covers three mistakes, and the message names the word it stumbled on
(`machine info has no --verbose flag.`, `--size must be a number of gigabytes
(got 'abc').`), so a caller can tell which argument to fix. A flag a command
does not take is answered as a mistake rather than ignored: no command reports
success for a flag it did not act on.

Status 5 is the resource's own answer: a deploy naming a storage pool that is
not there leaves 5, the same status `pool info` gives for that pool.

Status 9 is how a caller tells a command that does not exist *here* from a
command that ran and failed: a command gains a machine-readable result one at
a time, and until then it is reported as unsupported rather than as a success
with an empty payload. A word that is not a command at all is status 2, not 9.
`machine info` lists the commands this vmc answers to.

`volume rename` is a status 9 that will not change: the libvirt binding has no
way to rename a volume, so the message says so and names `virsh vol-rename` as
the way that does work, rather than failing as an unknown error.

## Advanced Tools

*   **`virsh [server]`**: Launches an interactive `virsh` shell connected to the selected server. If multiple servers are connected and no argument is provided, you will be prompted to choose.
*   **`bash [command]`**: Executes a local shell command or starts an interactive bash shell.
*   **`history [number|all|info]`**: Displays the command history. Use `history all` to see all history, `history <number>` for a specific number of recent commands, or `history info` for history file location.
*   **`!<number>`**: Re-executes a command from the history by its number (e.g., `!15` runs command #15).
*   **`version`**: Displays the version of VirtUI Manager (same version as the TUI).

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
| Connection | `connect`, `disconnect`, `list_servers`, `list_connected_servers`, `host_info`, `host_capabilities`, `virsh` |
| VM Selection | `list_vms`, `select_vm`, `unselect_vm`, `status`, `vm_info`, `vm_stats`, `show_selection` |
| VM Operations | `start`, `stop`, `force_off`, `reboot`, `pause`, `resume`, `hibernate`, `delete`, `clone_vm`, `rename_vm`, `installvm`, `view` |
| Snapshots | `snapshot_list`, `snapshot_create`, `snapshot_delete`, `snapshot_revert` |
| Backups | `backup_create`, `backup_list`, `backup_status`, `backup_restore`, `backup_cleanup` |
| Networking | `list_networks`, `net_start`, `net_stop`, `net_delete`, `net_info`, `network_usage`, `net_autostart` |
| Storage | `list_pool`, `list_volumes`, `list_unused_volumes`, `volume_usage`, `delete_volume` |
| Installation | `prepare_json`, `deploy_vm`, `list_json`, `show_json`, `list_isos`, `list_cached_isos` |
| Pipelines | `pipeline` (with `|` chaining) |
| Shell/Utils | `bash`, `history`, `!<number>`, `version`, `about`, `quit`, `help` |
