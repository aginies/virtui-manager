# Command Pipelines

VirtUI Manager provides a powerful command pipeline system that allows chaining multiple operations together using the pipe (`|`) operator. Pipelines enable complex multi-step workflows in a single command, making fleet management and automated operations efficient.

## Pipeline Architecture

The pipeline system consists of two main components:

*   **PipelineParser:** Parses a pipeline string into a list of command objects, handling quoting, variable expansion, and command mapping.
*   **PipelineExecutor:** Orchestrates parsing, validation, and execution of the pipeline, supporting normal, dry-run, and interactive modes.

Pipelines execute commands left-to-right, with each command receiving shared state (selected VMs, metadata, errors) from the previous command via a `PipelineContext` object.

## Pipeline Syntax

### Basic Syntax

```bash
pipeline [options] <command1> | <command2> | <command3>
```

Commands are separated by the pipe (`|`) operator. A pipeline with no pipes is still valid (treated as a single-command pipeline).

### Quoting

Both single (`'...'`) and double (`"..."`) quotes are supported. Quoted strings can contain pipe characters without being split:

```bash
# Quoted argument with spaces
pipeline select web-01 | snapshot create "Weekly backup" | start

# Quoted argument containing a pipe (treated as literal)
pipeline select web-01 | snapshot create 'name|with|pipes'
```

### Compound Commands

Some commands support sub-arguments within a single pipeline segment:

```bash
# Snapshot with name and description
pipeline select web-01 | snapshot create backup-01 "Pre-update snapshot"

# Backup with type and options
pipeline select db-01 | backup create --type=snapshot --compress --encrypt
```

### VM Selection Patterns

Pipelines support multiple VM selection formats:

*   **VM name:** `select web-01`
*   **VM UUID:** `select a1b2c3d4-e5f6-...`
*   **Regex pattern:** `select re:web.*` or `select re:^db-.*$`
*   **Three-part reference:** `select VMNAME:UUID:SERVER` (targets a specific VM on a specific server)
*   **Multiple VMs:** `select web-01 web-02 web-03`

## Supported Commands

### Selection Commands

| Command | Description |
|---------|-------------|
| `select <vm1> [vm2...]` | Select VMs by name, UUID, or pattern |
| `select re:<pattern>` | Select VMs matching a regex pattern |
| `select_vm <vm1> [vm2...]` | Alias for `select` |

### VM Operation Commands

| Command | Description |
|---------|-------------|
| `start` | Start selected VMs |
| `stop` | Graceful shutdown of selected VMs (ACPI) |
| `force_off` | Force power off selected VMs |
| `pause` | Pause selected running VMs |
| `resume` | Resume selected paused/suspended VMs |
| `hibernate` | Hibernate selected running VMs (save state to disk) |

### Snapshot Commands

| Command | Description |
|---------|-------------|
| `snapshot create <name> [description]` | Create a snapshot |
| `snapshot delete <name>` | Delete a snapshot |
| `snapshot revert <name>` | Revert to a snapshot |
| `snapshot list` | List snapshots for selected VMs |

### Backup Commands

| Command | Description |
|---------|-------------|
| `backup create [name] [options]` | Create a backup (options: `--type`, `--compress`, `--encrypt`, `--verify`, `--quiesce`) |
| `backup schedule <cron_expression> [options]` | Schedule a recurring backup |
| `backup list` | List backups for selected VMs |
| `backup restore [options]` | Restore from a backup (options: `--no-verify`, `--force`) |

### Utility Commands

| Command | Description |
|---------|-------------|
| `wait <seconds>` | Pause pipeline execution for N seconds |
| `view` | Launch remote viewer for selected VMs |
| `info` | Display VM information for selected VMs |
| `vm_info` | Alias for `info` |

### Connection Commands

| Command | Description |
|---------|-------------|
| `connect <server|uri> [server2|uri2 ...]` | Connect to one or more servers |

### Variable Expansion

Two variable patterns are supported in pipeline commands:

| Variable | Expansion | Example |
|----------|-----------|---------|
| `$(date)` | Current date/time in `YYYYMMDD_HHMMSS` format | `backup-20260603_143022` |
| `$(time)` | Current time in `HHMMSS` format | `143022` |

Variables are expanded **before** parsing, using `datetime.now()`:

```bash
# Timestamped backup name
pipeline select web-01 | snapshot create backup-$(date)

# Time-only in name
pipeline select db-01 | snapshot create pre-maintenance-$(time)
```

## Execution Modes

### Normal Mode

In normal mode, the pipeline executes all commands sequentially. If an error occurs during a command, the pipeline stops (short-circuits) unless the command is `view` or `wait`, which continue on error.

```bash
pipeline select re:web.* | stop | snapshot create backup-$(date) | start
```

### Dry-Run Mode (`--dry-run`)

Dry-run mode shows what commands would be executed without actually performing any VM operations. Each operation appends a description to the execution plan instead of executing.

```bash
pipeline --dry-run select re:web.* | stop | snapshot create backup-$(date) | start
```

Output shows the planned commands without touching any VMs.

### Interactive Mode (`--interactive` / `-i`)

Interactive mode prompts for confirmation before executing the pipeline. After parsing and validation, the execution plan is printed and the user must type `yes` to proceed.

```bash
pipeline -i select re:prod-.* | hibernate | snapshot create maintenance-$(date)
```

If the user types anything other than `yes`, the pipeline is cancelled.

## Error Handling

### Validation Errors

Validation errors are collected during the validation phase and cause the entire pipeline to fail immediately with stage `FAILED`. Examples:

*   Empty pipeline segments (e.g., `select vm1 | | start`)
*   Trailing pipes
*   Invalid regex patterns
*   VMs not found

### Runtime Errors

Runtime errors occur during command execution:

*   Per-VM errors (e.g., libvirt failures) are collected but do not stop the pipeline for other VMs within the same command.
*   If `context.errors` is non-empty after a command, execution stops (short-circuits).
*   Commands `view` and `wait` continue on error; all other commands halt the pipeline.

### Error Categories

| Error Type | Behavior |
|------------|----------|
| Validation errors | Pipeline fails immediately, no execution |
| Per-VM libvirt errors | Collected, pipeline continues for other VMs |
| Connection errors | Pipeline halts |
| Regex errors | Collected as warnings, pipeline continues |
| Unexpected exceptions | Logged and added to errors, pipeline halts |

### Warnings

Warnings are collected separately in `context.warnings` and never halt execution. They are used for non-critical issues such as:

*   VMs not found for a select pattern
*   Optional operations that failed on specific VMs

## Real-World Examples

### Maintenance Window

Stop all web servers, create a snapshot, perform maintenance, then restart:

```bash
pipeline select re:web-.* | stop | snapshot create maintenance-$(date) "Pre-maintenance" | start
```

### Batch Hibernate

Hibernate all non-production VMs to free resources:

```bash
pipeline select re:dev-.* | select re:test-.* | hibernate
```

### Pre-Update Snapshot

Select specific VMs, create timestamped snapshots, then start them:

```bash
pipeline select db-01 db-02 | snapshot create pre-update-$(date) "Before security update" | start
```

### Dry-Run Planning

Preview a complex pipeline before execution:

```bash
pipeline --dry-run select re:prod-.* | pause | wait 30 | snapshot create backup-$(date) | resume
```

### Backup Selected VMs

Create encrypted, compressed backups for production VMs:

```bash
pipeline select re:prod-.* | backup create --type=snapshot --compress --encrypt --verify
```

### Wait Between Operations

Pause pipeline execution between operations:

```bash
pipeline select web-01 | stop | wait 10 | snapshot create clean-snapshot | start
```

## Limitations

### Command Dependencies

Some commands require VM selection to have been established first. For example, `start` requires VMs to be selected via a preceding `select` command. Validation ensures this dependency is met.

### Backup Sub-Commands in Pipelines

The `backup schedule` sub-command is supported in pipelines but creates a one-time scheduled backup rather than a recurring cron job. True recurring scheduling requires external tooling.

### No Variable Expansion in All Contexts

Variable expansion (`$(date)`, `$(time)`) is performed before `shlex.split()`. It works in command arguments but may not work correctly in all quoting contexts.

### Single Execution

Pipelines execute once. For recurring operations, use external scheduling tools (cron, systemd timers) to invoke the pipeline command repeatedly.
