"""
Command Pipeline System for VirtUI Manager

This module provides a command pipeline system that allows chaining commands together
using the pipe operator (|) for efficient VM management workflows.

Example usage:
    select re:web.* | stop | snapshot create backup-$(date) | start
    list_vms running | pause | view
"""

import logging
import re
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import libvirt

from .utils import remote_viewer_cmd
from .vm_actions import (
    start_vm,
    stop_vm,
    force_off_vm,
    pause_vm,
    hibernate_vm,
    create_vm_snapshot,
    delete_vm_snapshot,
    restore_vm_snapshot,
)
from .vm_queries import get_vm_snapshots


def _get_vms_to_operate_from_args(args: str, active_connections) -> Dict[str, List[str]]:
    """Helper function to get VMs to operate on from arguments."""
    vms_to_operate = {}
    args_list = args.split()

    if args_list:
        # Map of name/UUID to list of servers and actual VM name
        vm_lookup = {}
        for server_name, conn in active_connections.items():
            try:
                domains = conn.listAllDomains(0)
                for dom in domains:
                    name = dom.name()
                    uuid = dom.UUIDString()
                    for identifier in [name, uuid]:
                        if identifier not in vm_lookup:
                            vm_lookup[identifier] = {"servers": [], "name": name}
                        if server_name not in vm_lookup[identifier]["servers"]:
                            vm_lookup[identifier]["servers"].append(server_name)
            except libvirt.libvirtError:
                continue

        for identifier in args_list:
            parts = identifier.split(":")
            # If it's the full format from completion: VMNAME:UUID:SERVER
            if len(parts) == 3:
                name, uuid, server = parts
                if server in active_connections:
                    if server not in vms_to_operate:
                        vms_to_operate[server] = []
                    if name not in vms_to_operate[server]:
                        vms_to_operate[server].append(name)
                    continue

            # Fallback to name or UUID lookup
            clean_id = parts[0].strip()
            target = vm_lookup.get(clean_id) or vm_lookup.get(identifier)

            if target:
                vm_name = target["name"]
                for server_name in target["servers"]:
                    if server_name not in vms_to_operate:
                        vms_to_operate[server_name] = []
                    if vm_name not in vms_to_operate[server_name]:
                        vms_to_operate[server_name].append(vm_name)

    return vms_to_operate


class PipelineStage(Enum):
    """Pipeline execution stages for tracking progress."""

    PARSING = "parsing"
    VALIDATION = "validation"
    EXECUTION = "execution"
    COMPLETE = "complete"
    FAILED = "failed"


class PipelineMode(Enum):
    """Pipeline execution modes."""

    NORMAL = "normal"
    DRY_RUN = "dry_run"
    INTERACTIVE = "interactive"


@dataclass
class PipelineContext:
    """Context passed between pipeline commands."""

    selected_vms: Dict[str, List[str]] = field(default_factory=dict)  # server -> vm_names
    last_output: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    stage: PipelineStage = PipelineStage.PARSING
    mode: PipelineMode = PipelineMode.NORMAL
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_selected_vms(self, server: str, vm_names: List[str]) -> None:
        """Add VMs to the selection context."""
        if server not in self.selected_vms:
            self.selected_vms[server] = []
        self.selected_vms[server].extend(
            [vm for vm in vm_names if vm not in self.selected_vms[server]]
        )

    def clear_selection(self) -> None:
        """Clear all selected VMs."""
        self.selected_vms.clear()

    def get_all_selected_vms(self) -> List[Tuple[str, str]]:
        """Get all selected VMs as (server, vm_name) tuples."""
        result = []
        for server, vm_names in self.selected_vms.items():
            for vm_name in vm_names:
                result.append((server, vm_name))
        return result

    def has_selected_vms(self) -> bool:
        """Check if any VMs are selected."""
        return any(vm_names for vm_names in self.selected_vms.values())


@dataclass
class SnapshotParams:
    """Parameters for snapshot operations."""

    action: str
    domain: Any
    vm_name: str
    snapshot_name: str
    description: str
    context: PipelineContext


class PipelineCommand(ABC):
    """Abstract base class for pipeline commands."""

    def __init__(self, command_str: str, args: List[str]):
        self.command_str = command_str
        self.args = args
        self.supports_piping = True  # Whether this command can receive piped input

    @abstractmethod
    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute the command with the given context."""
        pass

    @abstractmethod
    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        """Validate the command. Return list of error messages."""
        pass

    def can_receive_input(self) -> bool:
        """Whether this command can receive input from previous commands in pipeline."""
        return self.supports_piping

    def get_description(self, context: PipelineContext) -> str:  # pylint: disable=unused-argument
        """Get a human-readable description of what this command will do."""
        return f"{self.command_str} {' '.join(self.args)}"


class SelectCommand(PipelineCommand):
    """Pipeline command for VM selection."""

    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        errors = []
        if not self.args:
            errors.append("select command requires VM names or patterns")
        return errors

    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute VM selection."""
        context.clear_selection()

        if not cli_instance.active_connections:
            context.errors.append("Not connected to any server")
            return context

        vm_lookup, all_names = self._build_vm_lookup(cli_instance, context)
        self._process_selection_args(vm_lookup, all_names, context, cli_instance)

        return context

    def _build_vm_lookup(self, cli_instance, context):
        """Build VM lookup table from all connected servers."""
        vm_lookup = {}
        all_names = set()

        for server_name, conn in cli_instance.active_connections.items():
            try:
                domains = conn.listAllDomains(0)
                for dom in domains:
                    name = dom.name()
                    uuid = dom.UUIDString()
                    all_names.add(name)
                    for identifier in [name, uuid]:
                        if identifier not in vm_lookup:
                            vm_lookup[identifier] = {"servers": [], "name": name}
                        if server_name not in vm_lookup[identifier]["servers"]:
                            vm_lookup[identifier]["servers"].append(server_name)
            except (libvirt.libvirtError, OSError) as e:
                context.warnings.append(f"Could not fetch VMs from {server_name}: {e}")
                continue

        return vm_lookup, all_names

    def _process_selection_args(self, vm_lookup, all_names, context, cli_instance):
        """Process selection arguments (regex patterns or direct names)."""
        for arg in self.args:
            if arg.startswith("re:"):
                self._handle_regex_pattern(arg, all_names, vm_lookup, context)
            else:
                self._handle_direct_selection(arg, vm_lookup, context, cli_instance)

    def _handle_regex_pattern(self, arg, all_names, vm_lookup, context):
        """Handle regex pattern selection."""
        pattern_str = arg[3:]
        try:
            pattern = re.compile(pattern_str)
            matched_vms = {name for name in all_names if pattern.match(name)}
            if matched_vms:
                for vm_name in matched_vms:
                    if vm_name in vm_lookup:
                        for server_name in vm_lookup[vm_name]["servers"]:
                            context.add_selected_vms(server_name, [vm_name])
            else:
                context.warnings.append(f"No VMs found matching pattern '{pattern_str}'")
        except re.error as e:
            context.errors.append(f"Invalid regular expression '{pattern_str}': {e}")

    def _handle_direct_selection(self, arg, vm_lookup, context, cli_instance):
        """Handle direct VM name/UUID selection."""
        parts = arg.split(":")
        if len(parts) == 3:
            name, _uuid, server = parts
            if server in cli_instance.active_connections:
                context.add_selected_vms(server, [name])
                return

        # Fallback to name/UUID lookup
        clean_id = parts[0].strip()
        target = vm_lookup.get(clean_id) or vm_lookup.get(arg)

        if target:
            vm_name = target["name"]
            for server_name in target["servers"]:
                context.add_selected_vms(server_name, [vm_name])
        else:
            context.errors.append(f"VM '{arg}' not found on any connected server")

    def get_description(self, context: PipelineContext) -> str:
        if self.args:
            return f"Select VMs: {', '.join(self.args)}"
        return "Select VMs"


class VMOperationCommand(PipelineCommand):
    """Base class for VM operations (start, stop, pause, etc.)."""

    def __init__(self, command_str: str, args: List[str]):
        super().__init__(command_str, args)
        self.operation_map = {
            "start": self._start_vm,
            "stop": self._stop_vm,
            "force_off": self._force_off_vm,
            "pause": self._pause_vm,
            "resume": self._resume_vm,
            "hibernate": self._hibernate_vm,
        }

    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        errors = []
        if self.command_str not in self.operation_map:
            errors.append(f"Unknown VM operation: {self.command_str}")

        # If no args and no selected VMs from pipeline, that's an error
        if not self.args and not context.has_selected_vms():
            errors.append(f"{self.command_str} requires VM selection or names as arguments")

        return errors

    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute VM operation."""
        if not cli_instance.active_connections:
            context.errors.append("Not connected to any server")
            return context

        # Determine VMs to operate on
        vms_to_operate = {}
        if self.args:
            # Use specific VM names from arguments
            vms_to_operate = _get_vms_to_operate_from_args(
                " ".join(self.args), cli_instance.active_connections
            )
        else:
            # Use VMs from pipeline context
            vms_to_operate = dict(context.selected_vms)

        if not vms_to_operate:
            context.errors.append(f"No VMs to operate on for {self.command_str}")
            return context

        operation_func = self.operation_map.get(self.command_str)
        if not operation_func:
            context.errors.append(f"Unknown operation: {self.command_str}")
            return context

        # Execute operation on all target VMs
        success_count = 0
        for server_name, vm_list in vms_to_operate.items():
            conn = cli_instance.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    operation_func(domain, vm_name, context)
                    success_count += 1
                except (libvirt.libvirtError, OSError) as e:
                    context.errors.append(f"Error with VM '{vm_name}' on {server_name}: {e}")

        context.metadata[f"{self.command_str}_success_count"] = success_count
        return context

    def _start_vm(self, domain, vm_name: str, context: PipelineContext):
        """Start a VM."""
        if domain.isActive():
            context.warnings.append(f"VM '{vm_name}' is already running")
            return

        if context.mode == PipelineMode.DRY_RUN:
            context.metadata.setdefault("dry_run_actions", []).append(f"Would start VM '{vm_name}'")
            return

        start_vm(domain)

    def _stop_vm(self, domain, vm_name: str, context: PipelineContext):
        """Stop a VM gracefully."""
        if not domain.isActive():
            context.warnings.append(f"VM '{vm_name}' is not running")
            return

        if context.mode == PipelineMode.DRY_RUN:
            context.metadata.setdefault("dry_run_actions", []).append(f"Would stop VM '{vm_name}'")
            return

        stop_vm(domain)

    def _force_off_vm(self, domain, vm_name: str, context: PipelineContext):
        """Force off a VM."""
        if not domain.isActive():
            context.warnings.append(f"VM '{vm_name}' is not running")
            return

        if context.mode == PipelineMode.DRY_RUN:
            context.metadata.setdefault("dry_run_actions", []).append(
                f"Would force off VM '{vm_name}'"
            )
            return

        force_off_vm(domain)

    def _pause_vm(self, domain, vm_name: str, context: PipelineContext):
        """Pause a VM."""
        if not domain.isActive():
            context.warnings.append(f"VM '{vm_name}' is not running")
            return

        if domain.info()[0] == libvirt.VIR_DOMAIN_PAUSED:
            context.warnings.append(f"VM '{vm_name}' is already paused")
            return

        if context.mode == PipelineMode.DRY_RUN:
            context.metadata.setdefault("dry_run_actions", []).append(f"Would pause VM '{vm_name}'")
            return

        pause_vm(domain)

    def _resume_vm(self, domain, vm_name: str, context: PipelineContext):
        """Resume a paused VM."""
        state = domain.info()[0]
        if state == libvirt.VIR_DOMAIN_PAUSED:
            if context.mode == PipelineMode.DRY_RUN:
                context.metadata.setdefault("dry_run_actions", []).append(
                    f"Would resume VM '{vm_name}'"
                )
                return
            domain.resume()
        elif state == libvirt.VIR_DOMAIN_PMSUSPENDED:
            if context.mode == PipelineMode.DRY_RUN:
                context.metadata.setdefault("dry_run_actions", []).append(
                    f"Would wake up VM '{vm_name}'"
                )
                return
            domain.pMWakeup(0)
        else:
            context.warnings.append(f"VM '{vm_name}' is not paused or suspended")

    def _hibernate_vm(self, domain, vm_name: str, context: PipelineContext):
        """Hibernate a VM."""
        if not domain.isActive():
            context.warnings.append(f"VM '{vm_name}' is not running")
            return

        if context.mode == PipelineMode.DRY_RUN:
            context.metadata.setdefault("dry_run_actions", []).append(
                f"Would hibernate VM '{vm_name}'"
            )
            return

        hibernate_vm(domain)

    def get_description(self, context: PipelineContext) -> str:
        action = self.command_str.replace("_", " ").title()
        if self.args:
            return f"{action} VMs: {', '.join(self.args)}"
        if context.has_selected_vms():
            selected_count = sum(len(vms) for vms in context.selected_vms.values())
            return f"{action} {selected_count} selected VM(s)"
        return f"{action} VMs"


class SnapshotCommand(PipelineCommand):
    """Pipeline command for snapshot operations."""

    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        errors = []
        if len(self.args) < 2:
            errors.append("snapshot command requires action and name")
        elif self.args[0] not in ["create", "delete", "revert", "list"]:
            errors.append(f"Unknown snapshot action: {self.args[0]}")

        if not context.has_selected_vms() and len(self.args) < 3:
            errors.append("snapshot command requires VM selection or VM name as third argument")

        return errors

    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute snapshot operation."""
        if len(self.args) < 2:
            context.errors.append("snapshot command requires action and name")
            return context

        action = self.args[0]
        snapshot_name = self.args[1]
        description = self.args[2] if len(self.args) > 2 else ""

        # Determine VMs to operate on
        vms_to_operate = {}
        if context.has_selected_vms():
            vms_to_operate = dict(context.selected_vms)
        else:
            context.errors.append("No VMs selected for snapshot operation")
            return context

        # Execute snapshot operation
        for server_name, vm_list in vms_to_operate.items():
            conn = cli_instance.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    params = SnapshotParams(
                        action=action,
                        domain=domain,
                        vm_name=vm_name,
                        snapshot_name=snapshot_name,
                        description=description,
                        context=context,
                    )
                    self._execute_snapshot_action(params)
                except (libvirt.libvirtError, OSError) as e:
                    context.errors.append(f"Snapshot error for VM '{vm_name}': {e}")

        return context

    def _execute_snapshot_action(self, params: SnapshotParams):
        """Execute specific snapshot action."""
        if params.context.mode == PipelineMode.DRY_RUN:
            params.context.metadata.setdefault("dry_run_actions", []).append(
                f"Would {params.action} snapshot '{params.snapshot_name}' for VM '{params.vm_name}'"
            )
            return

        if params.action == "create":
            create_vm_snapshot(params.domain, params.snapshot_name, params.description)
        elif params.action == "delete":
            delete_vm_snapshot(params.domain, params.snapshot_name)
        elif params.action == "revert":
            restore_vm_snapshot(params.domain, params.snapshot_name)
        elif params.action == "list":
            snapshots = get_vm_snapshots(params.domain)
            snapshot_names = [s["name"] for s in snapshots]
            params.context.last_output = (
                f"Snapshots for {params.vm_name}: {', '.join(snapshot_names)}"
            )

    def get_description(self, context: PipelineContext) -> str:
        if len(self.args) >= 2:
            action = self.args[0]
            snapshot_name = self.args[1]
            return f"{action.title()} snapshot '{snapshot_name}'"
        return "Snapshot operation"


class WaitCommand(PipelineCommand):
    """Pipeline command for waiting/delays."""

    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        errors = []
        if not self.args:
            errors.append("wait command requires duration in seconds")
        else:
            try:
                float(self.args[0])
            except ValueError:
                errors.append(f"Invalid wait duration: {self.args[0]}")
        return errors

    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute wait command."""
        if not self.args:
            context.errors.append("wait command requires duration")
            return context

        try:
            duration = float(self.args[0])
            if context.mode == PipelineMode.DRY_RUN:
                context.metadata.setdefault("dry_run_actions", []).append(
                    f"Would wait {duration} seconds"
                )
            else:
                time.sleep(duration)
        except ValueError:
            context.errors.append(f"Invalid wait duration: {self.args[0]}")

        return context

    def get_description(self, context: PipelineContext) -> str:
        if self.args:
            return f"Wait {self.args[0]} seconds"
        return "Wait"


class ViewCommand(PipelineCommand):
    """Pipeline command for launching VM viewers."""

    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        errors = []
        if not self.args and not context.has_selected_vms():
            errors.append("view command requires VM selection or names as arguments")
        return errors

    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute view command."""
        if not cli_instance.active_connections:
            context.errors.append("Not connected to any server")
            return context

        # Determine VMs to view
        vms_to_view = {}
        if self.args:
            vms_to_view = _get_vms_to_operate_from_args(
                " ".join(self.args), cli_instance.active_connections
            )
        else:
            vms_to_view = dict(context.selected_vms)

        if not vms_to_view:
            context.errors.append("No VMs to view")
            return context

        # Launch viewers
        for server_name, vm_list in vms_to_view.items():
            conn = cli_instance.active_connections[server_name]
            try:
                uri = conn.getURI()
                for vm_name in vm_list:
                    try:
                        domain = conn.lookupByName(vm_name)
                        if context.mode == PipelineMode.DRY_RUN:
                            context.metadata.setdefault("dry_run_actions", []).append(
                                f"Would launch viewer for VM '{vm_name}'"
                            )
                            continue

                        if not domain.isActive():
                            context.warnings.append(
                                f"VM '{vm_name}' is not running. Viewer will wait for it to start."
                            )

                        cmd = remote_viewer_cmd(uri, vm_name)
                        if cmd and cmd[0]:
                            # Launch as background process
                            # Using Popen without context manager since we don't need to wait
                            subprocess.Popen(  # pylint: disable=consider-using-with
                                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                            )
                        else:
                            context.errors.append("No remote viewer found")
                    except (libvirt.libvirtError, OSError) as e:
                        context.errors.append(f"Error viewing VM '{vm_name}': {e}")
            except (libvirt.libvirtError, OSError) as e:
                context.errors.append(f"Error processing server {server_name}: {e}")

        return context

    def get_description(self, context: PipelineContext) -> str:
        if self.args:
            return f"Launch viewers for: {', '.join(self.args)}"
        if context.has_selected_vms():
            selected_count = sum(len(vms) for vms in context.selected_vms.values())
            return f"Launch viewers for {selected_count} selected VM(s)"
        return "Launch VM viewers"


class InfoCommand(PipelineCommand):
    """Pipeline command for displaying VM information."""

    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        errors = []
        if not self.args and not context.has_selected_vms():
            errors.append("info command requires VM selection or names as arguments")
        return errors

    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute info command."""
        if not cli_instance.active_connections:
            context.errors.append("Not connected to any server")
            return context

        # Determine VMs to get info for
        vms_to_info = {}
        if self.args:
            vms_to_info = _get_vms_to_operate_from_args(
                " ".join(self.args), cli_instance.active_connections
            )
        else:
            vms_to_info = dict(context.selected_vms)

        if not vms_to_info:
            context.errors.append("No VMs to get info for")
            return context

        if context.mode == PipelineMode.DRY_RUN:
            # In dry-run mode, just show what would be done
            for server_name, vm_list in vms_to_info.items():
                for vm_name in vm_list:
                    context.metadata.setdefault("dry_run_actions", []).append(
                        f"Would display info for VM '{vm_name}' on server '{server_name}'"
                    )
            return context

        # Use existing do_vm_info method for each VM
        for server_name, vm_list in vms_to_info.items():
            for vm_name in vm_list:
                try:
                    # Call the existing vm_info command
                    cli_instance.do_vm_info(vm_name)
                except (libvirt.libvirtError, OSError) as e:
                    context.errors.append(f"Error getting info for VM '{vm_name}': {e}")

        return context

    def get_description(self, context: PipelineContext) -> str:
        if self.args:
            return f"Display info for: {', '.join(self.args)}"
        if context.has_selected_vms():
            selected_count = sum(len(vms) for vms in context.selected_vms.values())
            return f"Display info for {selected_count} selected VM(s)"
        return "Display VM information"


class BackupCommand(PipelineCommand):
    """Pipeline command for backup operations."""

    def validate(self, context: PipelineContext, vm_service, cli_instance) -> List[str]:
        errors = []
        if not self.args:
            errors.append("backup command requires action (create, schedule, list, restore)")
            return errors

        action = self.args[0]
        if action not in ["create", "schedule", "list", "restore"]:
            errors.append(f"Invalid backup action: {action}. Use: create, schedule, list, restore")
            return errors

        if (
            action in ["create", "schedule"]
            and not context.has_selected_vms()
            and len(self.args) < 2
        ):
            errors.append(f"backup {action} requires VM selection or VM name as argument")

        return errors

    def execute(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute backup command."""
        if not cli_instance.active_connections:
            context.errors.append("Not connected to any server")
            return context

        if not self.args:
            context.errors.append("backup command requires action")
            return context

        action = self.args[0]

        try:
            if action == "create":
                return self._execute_create(context, vm_service, cli_instance)
            elif action == "schedule":
                return self._execute_schedule(context, vm_service, cli_instance)
            elif action == "list":
                return self._execute_list(context, vm_service, cli_instance)
            elif action == "restore":
                return self._execute_restore(context, vm_service, cli_instance)
            else:
                context.errors.append(f"Unknown backup action: {action}")

        except Exception as e:
            context.errors.append(f"Backup {action} failed: {e}")

        return context

    def _execute_create(
        self, context: PipelineContext, vm_service, cli_instance
    ) -> PipelineContext:
        """Execute backup create command."""
        # Parse arguments: backup create [backup_name] [--type=snapshot] [options]
        backup_name = None
        backup_type = "snapshot"
        compress = False
        encrypt = False
        verify = True

        # Parse options
        for arg in self.args[1:]:
            if not arg.startswith("--"):
                backup_name = arg
            elif arg.startswith("--type="):
                backup_type = arg[7:]
            elif arg == "--compress":
                compress = True
            elif arg == "--encrypt":
                encrypt = True
            elif arg == "--no-verify":
                verify = False

        # Determine VMs to backup
        vms_to_backup = {}
        if context.has_selected_vms():
            vms_to_backup = dict(context.selected_vms)
        elif len(self.args) > 1 and not self.args[1].startswith("--"):
            # VM name might be specified
            vm_name = self.args[1]
            vms_to_backup = _get_vms_to_operate_from_args(vm_name, cli_instance.active_connections)

        if not vms_to_backup:
            context.errors.append("No VMs selected for backup")
            return context

        if context.mode == PipelineMode.DRY_RUN:
            for server_name, vm_list in vms_to_backup.items():
                for vm_name in vm_list:
                    generated_name = backup_name or f"{vm_name}_backup_$(date)"
                    context.metadata.setdefault("dry_run_actions", []).append(
                        f"Would create {backup_type} backup '{generated_name}' for VM '{vm_name}' on server '{server_name}'"
                    )
            return context

        # Execute backups
        from .backup_scheduler import BackupType, BackupOptions
        from .backup_manager import BackupManager

        backup_manager = BackupManager()

        for server_name, vm_list in vms_to_backup.items():
            conn = cli_instance.active_connections[server_name]

            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)

                    # Generate backup name if not provided
                    if not backup_name:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        generated_name = f"{vm_name}_backup_{timestamp}"
                    else:
                        generated_name = backup_name

                    # Map string to enum
                    if backup_type.lower() == "snapshot":
                        bt = BackupType.SNAPSHOT
                    elif backup_type.lower() == "overlay":
                        bt = BackupType.OVERLAY
                    elif backup_type.lower() == "clone":
                        bt = BackupType.CLONE
                    else:
                        bt = BackupType.SNAPSHOT

                    # Create backup options
                    options = BackupOptions(compress=compress, encrypt=encrypt, verify=verify)

                    # Create the backup
                    metadata = backup_manager.create_backup(domain, generated_name, bt, options)

                    # Add to context metadata
                    context.metadata.setdefault("backups_created", []).append(
                        {
                            "vm_name": vm_name,
                            "server_name": server_name,
                            "backup_name": generated_name,
                            "type": backup_type,
                            "metadata": metadata,
                        }
                    )

                except Exception as e:
                    context.errors.append(f"Failed to backup VM '{vm_name}': {e}")

        return context

    def _execute_schedule(
        self, context: PipelineContext, vm_service, cli_instance
    ) -> PipelineContext:
        """Execute backup schedule command."""
        # Parse: backup schedule <pattern> [options]
        if len(self.args) < 2:
            context.errors.append("backup schedule requires schedule pattern")
            return context

        schedule_pattern = self.args[1]
        backup_type = "snapshot"
        keep_count = 7

        # Parse options
        for arg in self.args[2:]:
            if arg.startswith("--type="):
                backup_type = arg[7:]
            elif arg.startswith("--keep="):
                try:
                    keep_count = int(arg[7:])
                except ValueError:
                    context.errors.append(f"Invalid keep count: {arg[7:]}")
                    return context

        # Get VMs to schedule
        vms_to_schedule = {}
        if context.has_selected_vms():
            vms_to_schedule = dict(context.selected_vms)
        else:
            context.errors.append("No VMs selected for backup scheduling")
            return context

        if context.mode == PipelineMode.DRY_RUN:
            for server_name, vm_list in vms_to_schedule.items():
                for vm_name in vm_list:
                    context.metadata.setdefault("dry_run_actions", []).append(
                        f"Would schedule {backup_type} backup for VM '{vm_name}' with pattern '{schedule_pattern}' (keep {keep_count})"
                    )
            return context

        # Create schedules
        from .backup_scheduler import BackupType, BackupOptions, RetentionPolicy

        # Map backup type
        if backup_type.lower() == "snapshot":
            bt = BackupType.SNAPSHOT
        elif backup_type.lower() == "overlay":
            bt = BackupType.OVERLAY
        elif backup_type.lower() == "clone":
            bt = BackupType.CLONE
        else:
            bt = BackupType.SNAPSHOT

        retention = RetentionPolicy(keep_count=keep_count)
        options = BackupOptions()

        scheduled_count = 0
        for server_name, vm_list in vms_to_schedule.items():
            for vm_name in vm_list:
                try:
                    schedule_id = cli_instance.backup_scheduler.add_schedule(
                        vm_name=vm_name,
                        server_name=server_name,
                        backup_type=bt,
                        schedule_pattern=schedule_pattern,
                        retention=retention,
                        options=options,
                    )
                    scheduled_count += 1

                    context.metadata.setdefault("schedules_created", []).append(
                        {
                            "schedule_id": schedule_id,
                            "vm_name": vm_name,
                            "server_name": server_name,
                            "pattern": schedule_pattern,
                            "type": backup_type,
                        }
                    )

                except Exception as e:
                    context.errors.append(f"Failed to schedule backup for VM '{vm_name}': {e}")

        if scheduled_count > 0:
            context.last_output = f"Created {scheduled_count} backup schedule(s)"

        return context

    def _execute_list(self, context: PipelineContext, vm_service, cli_instance) -> PipelineContext:
        """Execute backup list command."""
        # This would list backups for selected VMs or all VMs
        vm_filter = None
        if context.has_selected_vms():
            # Get first VM for filtering
            for server_vms in context.selected_vms.values():
                if server_vms:
                    vm_filter = server_vms[0]
                    break

        try:
            # List schedules
            schedules = cli_instance.backup_scheduler.get_schedules(vm_name=vm_filter)
            context.metadata["backup_schedules"] = len(schedules)

            # List recent jobs
            jobs = cli_instance.backup_scheduler.get_jobs(limit=10)
            if vm_filter:
                jobs = [j for j in jobs if j.vm_name == vm_filter]
            context.metadata["recent_jobs"] = len(jobs)

            context.last_output = f"Found {len(schedules)} schedules and {len(jobs)} recent jobs"

        except Exception as e:
            context.errors.append(f"Failed to list backups: {e}")

        return context

    def _execute_restore(
        self, context: PipelineContext, vm_service, cli_instance
    ) -> PipelineContext:
        """Execute backup restore command."""
        # Parse: backup restore [backup_name|latest]
        backup_name = "latest"
        if len(self.args) > 1:
            backup_name = self.args[1]

        # Get VMs to restore
        vms_to_restore = {}
        if context.has_selected_vms():
            vms_to_restore = dict(context.selected_vms)
        else:
            context.errors.append("No VMs selected for restore")
            return context

        if context.mode == PipelineMode.DRY_RUN:
            for server_name, vm_list in vms_to_restore.items():
                for vm_name in vm_list:
                    context.metadata.setdefault("dry_run_actions", []).append(
                        f"Would restore VM '{vm_name}' from backup '{backup_name}'"
                    )
            return context

        # Execute restores
        restored_count = 0
        for server_name, vm_list in vms_to_restore.items():
            conn = cli_instance.active_connections[server_name]

            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)

                    # Get available snapshots
                    from .vm_queries import get_vm_snapshots

                    snapshots = get_vm_snapshots(domain)
                    backup_snapshots = [s for s in snapshots if "_backup_" in s["name"]]

                    if not backup_snapshots:
                        context.errors.append(f"No backups found for VM '{vm_name}'")
                        continue

                    # Select backup
                    if backup_name == "latest":
                        backup_snapshots.sort(key=lambda s: s["creation_time"], reverse=True)
                        selected_backup = backup_snapshots[0]["name"]
                    else:
                        selected_backup = backup_name

                    # Restore
                    from .vm_actions import restore_vm_snapshot

                    restore_vm_snapshot(domain, selected_backup)
                    restored_count += 1

                    context.metadata.setdefault("restores_completed", []).append(
                        {"vm_name": vm_name, "backup_name": selected_backup}
                    )

                except Exception as e:
                    context.errors.append(f"Failed to restore VM '{vm_name}': {e}")

        if restored_count > 0:
            context.last_output = f"Restored {restored_count} VM(s) from backup"

        return context

    def get_description(self, context: PipelineContext) -> str:
        if not self.args:
            return "backup operation"

        action = self.args[0]
        if action == "create":
            if context.has_selected_vms():
                vm_count = sum(len(vms) for vms in context.selected_vms.values())
                return f"create backup for {vm_count} VM(s)"
            return "create backup"
        elif action == "schedule":
            pattern = self.args[1] if len(self.args) > 1 else "pattern"
            return f"schedule backup with pattern '{pattern}'"
        elif action == "list":
            return "list backups"
        elif action == "restore":
            backup_name = self.args[1] if len(self.args) > 1 else "latest"
            return f"restore from backup '{backup_name}'"
        else:
            return f"backup {action}"


class PipelineParser:
    """Parser for command pipelines."""

    def __init__(self):
        self.command_classes = {
            "select": SelectCommand,
            "select_vm": SelectCommand,
            "start": VMOperationCommand,
            "stop": VMOperationCommand,
            "force_off": VMOperationCommand,
            "pause": VMOperationCommand,
            "resume": VMOperationCommand,
            "hibernate": VMOperationCommand,
            "snapshot": SnapshotCommand,
            "backup": BackupCommand,
            "wait": WaitCommand,
            "view": ViewCommand,
            "info": InfoCommand,
        }

    def parse(self, pipeline_str: str) -> List[PipelineCommand]:
        """Parse a pipeline string into commands."""
        if "|" not in pipeline_str:
            # Single command, not a pipeline
            return self._parse_single_command(pipeline_str.strip())

        # Split on pipes, handling quoted strings
        commands = []
        current_command = ""
        in_quotes = False
        quote_char = None

        i = 0
        while i < len(pipeline_str):
            char = pipeline_str[i]

            if char in ['"', "'"] and (i == 0 or pipeline_str[i - 1] != "\\"):
                if not in_quotes:
                    in_quotes = True
                    quote_char = char
                elif char == quote_char:
                    in_quotes = False
                    quote_char = None
                current_command += char
            elif char == "|" and not in_quotes:
                if current_command.strip():
                    commands.extend(self._parse_single_command(current_command.strip()))
                current_command = ""
            else:
                current_command += char

            i += 1

        # Add final command
        if current_command.strip():
            commands.extend(self._parse_single_command(current_command.strip()))

        return commands

    def _parse_single_command(self, command_str: str) -> List[PipelineCommand]:
        """Parse a single command string."""
        try:
            # Handle variable substitution
            command_str = self._expand_variables(command_str)

            # Parse command and arguments
            parts = shlex.split(command_str)
            if not parts:
                return []

            cmd_name = parts[0]
            args = parts[1:]

            # Handle compound commands like "snapshot create"
            if cmd_name == "snapshot" and args:
                command_class = self.command_classes.get("snapshot")
            else:
                command_class = self.command_classes.get(cmd_name)

            if not command_class:
                raise ValueError(f"Unknown command: {cmd_name}")

            return [command_class(cmd_name, args)]

        except Exception as e:
            raise ValueError(f"Error parsing command '{command_str}': {e}") from e

    def _expand_variables(self, command_str: str) -> str:
        """Expand variables like $(date) in command strings."""
        # Simple variable expansion
        if "$(date)" in command_str:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            command_str = command_str.replace("$(date)", date_str)

        if "$(time)" in command_str:
            time_str = datetime.now().strftime("%H%M%S")
            command_str = command_str.replace("$(time)", time_str)

        return command_str


class PipelineExecutor:
    """Executor for command pipelines."""

    def __init__(self, vm_service, cli_instance):
        self.vm_service = vm_service
        self.cli_instance = cli_instance
        self.parser = PipelineParser()

    def execute_pipeline(
        self, pipeline_str: str, mode: PipelineMode = PipelineMode.NORMAL
    ) -> PipelineContext:
        """Execute a complete pipeline."""
        context = PipelineContext(mode=mode)

        try:
            # Parse pipeline
            context.stage = PipelineStage.PARSING
            commands = self.parser.parse(pipeline_str)

            if not commands:
                context.errors.append("No commands found in pipeline")
                context.stage = PipelineStage.FAILED
                return context

            # Validation phase
            context.stage = PipelineStage.VALIDATION
            all_errors = []

            # Track what commands provide VM selection for pipeline-aware validation
            provides_vm_selection = False

            for cmd_idx, command in enumerate(commands):
                # Check if this command will provide VM selection
                if isinstance(command, SelectCommand):
                    provides_vm_selection = True

                # For commands that can receive piped input, adjust validation context
                if (
                    hasattr(command, "supports_piping")
                    and command.supports_piping
                    and provides_vm_selection
                ):
                    # Create a temporary context that simulates having selected VMs
                    temp_context = PipelineContext(mode=context.mode)
                    temp_context.add_selected_vms("temp", ["vm1"])  # Dummy VM for validation
                    errors = command.validate(temp_context, self.vm_service, self.cli_instance)
                else:
                    errors = command.validate(context, self.vm_service, self.cli_instance)

                for error in errors:
                    all_errors.append(f"Command {cmd_idx + 1} ({command.command_str}): {error}")

            if all_errors:
                context.errors.extend(all_errors)
                context.stage = PipelineStage.FAILED
                return context

            # Show execution plan in dry-run mode
            if mode == PipelineMode.DRY_RUN:
                self._show_execution_plan(commands, context)

            # Interactive confirmation
            if mode == PipelineMode.INTERACTIVE:
                if not self._confirm_execution(commands, context):
                    context.stage = PipelineStage.FAILED
                    context.errors.append("Pipeline execution cancelled by user")
                    return context

            # Execution phase
            context.stage = PipelineStage.EXECUTION
            for cmd_idx, command in enumerate(commands):
                try:
                    logging.info(
                        "Executing pipeline command %s/%s: %s",
                        cmd_idx + 1,
                        len(commands),
                        command.get_description(context),
                    )
                    context = command.execute(context, self.vm_service, self.cli_instance)

                    # Stop execution if there are critical errors
                    if context.errors and not self._should_continue_on_error(command):
                        break

                except Exception as e:
                    error_msg = f"Command {cmd_idx + 1} ({command.command_str}) failed: {e}"
                    context.errors.append(error_msg)
                    logging.error("Command %s (%s) failed: %s", cmd_idx + 1, command.command_str, e)
                    break

            context.stage = PipelineStage.COMPLETE if not context.errors else PipelineStage.FAILED

        except Exception as e:
            context.errors.append(f"Pipeline execution failed: {e}")
            context.stage = PipelineStage.FAILED
            logging.error("Pipeline execution failed: %s", e)

        return context

    def _show_execution_plan(self, commands: List[PipelineCommand], context: PipelineContext):
        """Show what the pipeline would do in dry-run mode."""
        print("\n=== Pipeline Execution Plan ===")
        for i, command in enumerate(commands):
            print(f"{i + 1}. {command.get_description(context)}")
        print("=" * 31)

    def _confirm_execution(self, commands: List[PipelineCommand], context: PipelineContext) -> bool:
        """Ask user to confirm pipeline execution."""
        print("\n=== Pipeline Execution Plan ===")
        for i, command in enumerate(commands):
            print(f"{i + 1}. {command.get_description(context)}")
        print("=" * 31)

        response = input("Execute this pipeline? (yes/no): ").lower().strip()
        return response == "yes"

    def _should_continue_on_error(self, command: PipelineCommand) -> bool:
        """Determine if pipeline should continue after command error."""
        # Most VM operations should stop pipeline on error
        # Only informational commands should continue
        return command.command_str in ["view", "wait"]

    def validate_pipeline(self, pipeline_str: str) -> Tuple[bool, List[str]]:
        """Validate a pipeline without executing it."""
        try:
            commands = self.parser.parse(pipeline_str)
            context = PipelineContext()

            all_errors = []
            provides_vm_selection = False

            for command in commands:
                # Check if this command will provide VM selection
                if isinstance(command, SelectCommand):
                    provides_vm_selection = True

                # For commands that can receive piped input, adjust validation context
                if (
                    hasattr(command, "supports_piping")
                    and command.supports_piping
                    and provides_vm_selection
                ):
                    # Create a temporary context that simulates having selected VMs
                    temp_context = PipelineContext()
                    temp_context.add_selected_vms("temp", ["vm1"])  # Dummy VM for validation
                    errors = command.validate(temp_context, self.vm_service, self.cli_instance)
                else:
                    errors = command.validate(context, self.vm_service, self.cli_instance)

                all_errors.extend(errors)

            return len(all_errors) == 0, all_errors

        except Exception as e:
            return False, [f"Pipeline validation failed: {e}"]
