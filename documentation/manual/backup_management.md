# Backup Management

Backup system supporting snapshot and overlay types with optional compression, encryption, and integrity verification. Backups are managed through both the CLI and the bulk operations interface.

## Backup Types

VirtUI Manager supports two distinct backup strategies, each suited for different use cases:

### Snapshot Backups

Snapshot backups use libvirt's native snapshot API to capture the VM's state at a point in time. This is the recommended and default backup method.

*   **How it works:** Creates a libvirt snapshot with the specified backup name (prefixed with `_backup_` internally for management).
*   **Best for:** Quick restore points before risky operations, full VM state preservation including memory.
*   **Requirements:** VM must have at least one qcow2 disk. QEMU Guest Agent recommended for filesystem quiescing.
*   **Restore:** Restores the VM to the exact state captured at backup time using `restore_vm_snapshot`.

### Overlay Backups

Overlay backups create qcow2 overlay volumes for each disk of the VM, capturing all changes since the overlay was created.

*   **How it works:** Creates thin-provisioned delta images layered on top of the original disk images. Each disk gets its own overlay file.
*   **Best for:** Non-disruptive backups of running VMs, capturing all disk changes without freezing the VM.
*   **Requirements:** VM must have at least one disk. Storage pool must have sufficient space for overlay files.
*   **Restore:** **Not fully implemented.** Currently only verifies overlay files exist; actual merge/restore is a placeholder.

### Comparison

| Feature | Snapshot Backup | Overlay Backup |
|---------|----------------|----------------|
| **VM State** | Full (disk + memory) | Disk only |
| **VM Running** | Yes (with quiesce) | Yes |
| **Storage** | Inside existing disk | New overlay files |
| **Performance** | Fast for small VMs | Depends on disk size |
| **Restore** | Fully implemented | Placeholder only |
| **Cleanup** | By count and age | Not implemented |
| **Recommended for** | General backups | Specialized use cases |

## Backup Options

Each backup can be configured with multiple options:

*   **`--type <snapshot|overlay>`:** Choose the backup type. Default is `snapshot`.
*   **`--compress`:** Enable gzip compression of overlay files. Appends `.gz` extension. Does not apply to snapshot backups.
*   **`--encrypt`:** Enable AES-256-CBC encryption via OpenSSL. Appends `.enc` extension. Requires OpenSSL to be installed. When combined with `--compress`, the pipeline is: original -> gzip -> encrypt.
*   **`--verify`:** Run integrity verification immediately after creation. Checks snapshot existence or file checksums.
*   **`--quiesce`:** Use QEMU Guest Agent to freeze the filesystem before taking a snapshot. Requires the guest agent to be installed and running. Ensures filesystem consistency.

## Creating Backups

### Via CLI

Create a backup using the `backup_create` command:

```bash
# Basic snapshot backup
backup_create myvm-backup

# Snapshot backup with verification
backup_create myvm-backup --type snapshot --verify

# Overlay backup with compression
backup_create myvm-backup --type overlay --compress

# Encrypted snapshot backup with quiesce
backup_create myvm-backup --type snapshot --encrypt --quiesce

# Backup with timestamped name
backup_create myvm-$(date) --type snapshot --compress --verify
```

### Via TUI

1.  Select one or more VMs using the selection tools (manual click, `Ctrl+A`, or pattern selection).
2.  Open **Bulk CMD** (`b`).
3.  Choose **State Management** > **Snapshot** to create a snapshot backup.
4.  Enter a backup name and optionally enable quiesce if the guest agent is available.

## Listing and Checking Backups

### List All Backups

```bash
# List all backups
backup_list

# List backups for a specific VM
backup_list myvm
```

### Check Backup Status

```bash
# Get detailed metadata for a specific backup
backup_status myvm-backup
```

The status output includes:

*   **Backup name** and type (snapshot or overlay)
*   **VM name** and server
*   **Creation timestamp** and duration
*   **Size in bytes** (for overlay backups)
*   **Checksums** (SHA-256, for overlay backups)
*   **Options** used (compress, encrypt, verify, quiesce)

### Available Backups

```bash
# Get a simple list of backup names for restoration
list_available_backups myvm
```

## Restoring Backups

### From Snapshot Backup

Snapshot backups restore the VM to the exact state captured at backup time:

```bash
# Restore with pre-restore verification (default)
backup_restore myvm-backup

# Restore without verification
backup_restore myvm-backup --no-verify

# Force restore without confirmation
backup_restore myvm-backup --force
```

### From Overlay Backup

**Warning:** Overlay backup restore is currently a **placeholder** and not fully implemented. The system verifies that overlay files exist but does not perform the actual merge/restore operation. Use snapshot backups for reliable restoration.

### Pre-Restore Verification

By default, VirtUI Manager verifies the backup integrity before restoring. This checks:

*   For snapshot backups: The snapshot exists and is accessible.
*   For overlay backups: All overlay files exist and checksums match.

If verification fails, the restore is aborted to prevent data corruption.

## Cleanup and Retention Policies

VirtUI Manager supports automated cleanup of old backups based on retention policies:

```bash
# Clean up backups older than 30 days
backup_cleanup --older-than 30

# Clean up snapshot backups only
backup_cleanup --type snapshot

# Clean up overlay backups only
backup_cleanup --type overlay
```

### Retention Policies

Retention policies are defined using two criteria:

*   **`keep_count`:** Maximum number of backups to retain per VM.
*   **`keep_days`:** Maximum age of backups in days.

Both policies are applied: a backup is deleted if it exceeds either the count limit or the age limit.

### How Cleanup Works

1.  **Snapshot cleanup:** Queries VM snapshots, filters those with `_backup_` in the name, sorts by creation time, applies retention policies, then deletes via `delete_vm_snapshot` and removes the metadata JSON file.
2.  **Overlay cleanup:** **Not implemented.** Returns an empty list with a TODO marker.

## Encryption and Security

### Encryption Details

*   **Algorithm:** AES-256-CBC via OpenSSL
*   **Key storage:** `~/.config/virtui-manager/backup_encryption.key`
*   **Key permissions:** `0600` (owner read/write only)
*   **Key generation:** A new 256-bit random key is generated automatically if the key file does not exist. The key is a 64-character hex string generated via `secrets.token_hex(32)`.
*   **Pipeline:** When both compression and encryption are enabled, the pipeline is: original file -> gzip (`.gz`) -> encrypt (`.enc`). The intermediate `.gz` file is deleted after encryption.

### Checksums

*   **Algorithm:** SHA-256
*   **Granularity:** Per-file checksums for overlay backup files
*   **Verification:** Checksums are computed during backup creation and verified during backup verification and pre-restore checks
*   **Storage:** Checksums are stored in the backup metadata JSON file

### Security Considerations

*   The encryption key file should be kept secure and backed up separately. Losing the key means all encrypted backups are unrecoverable.
*   Encrypted backups are stored with `.enc` extension and can only be decrypted with the original key.
*   Consider using different encryption keys for different environments (development vs production).

## Limitations and Known Issues

### Overlay Restore Not Implemented

Overlay backup restore is a placeholder. The system checks that overlay files exist but does not perform the actual merge/restore. Use snapshot backups for reliable restoration of overlay-based backups.

### Overlay Cleanup Not Implemented

Overlay backup cleanup by retention policy is not yet implemented. Overlay backups must be cleaned up manually.

### Snapshot Name Collision

Backups use the `_backup_` substring in snapshot names for identification. Avoid using `_backup_` in your own snapshot names to prevent conflicts during cleanup.

### Encryption Requires OpenSSL

The `--encrypt` option requires the `openssl` command to be installed on the system. If OpenSSL is not available, encryption will fail with an error.

### Quiesce Requires Guest Agent

The `--quiesce` option requires the QEMU Guest Agent to be installed and running in the guest VM. If the agent is not available, the quiesce operation will be skipped silently.
