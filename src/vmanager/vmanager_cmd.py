"""
the Cmd line tool
"""

import cmd
import datetime
import re
import readline
import shlex
import shutil
import subprocess
import sys

import libvirt

from .config import get_log_path, load_config
from .constants import AppInfo, ServerPallette
from .libvirt_utils import get_host_resources, get_network_info
from .network_manager import (
    delete_network,
    list_networks,
    set_network_active,
    set_network_autostart,
)
from .storage_manager import list_storage_pools, list_unused_volumes
from .utils import remote_viewer_cmd
from .vm_actions import (
    clone_vm,
    create_vm_snapshot,
    delete_vm,
    delete_vm_snapshot,
    force_off_vm,
    hibernate_vm,
    pause_vm,
    restore_vm_snapshot,
    start_vm,
    stop_vm,
)
from .vm_queries import get_vm_snapshots
from .vm_service import VMService
from .utils import sanitize_credentials


class CLILogger:
    """A logger that captures stdout/stderr and logs to a file.

    This logger sanitizes sensitive information (like credentials in URIs)
    before writing to the log file to prevent clear-text logging of secrets.
    """

    def __init__(self, filepath, stream):
        self.filepath = filepath
        self.stream = stream
        self.buffer = ""

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, message):
        res = self.stream.write(message)
        self.buffer += message
        if "\n" in self.buffer:
            lines = self.buffer.split("\n")
            complete_lines = lines[:-1]
            self.buffer = lines[-1]

            try:
                with open(self.filepath, "a") as f:
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
                    for line in complete_lines:
                        sanitized_line = sanitize_credentials(line)
                        f.write(f"{timestamp} [CLI] {sanitized_line}\n")
            except Exception:
                pass
        return res

    def flush(self):
        self.stream.flush()
        if self.buffer:
            try:
                with open(self.filepath, "a") as f:
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
                    sanitized_buffer = sanitize_credentials(self.buffer)
                    f.write(f"{timestamp} [CLI] {sanitized_buffer}\n")
            except Exception:
                pass
            self.buffer = ""


class VManagerCMD(cmd.Cmd):
    """VManager command-line interface."""

    prompt = "(" + AppInfo.name + ") "
    intro = f"Welcome to the {AppInfo.namecase} command shell. Type help or ? to list commands.\n"

    def __init__(self, vm_service=None):
        super().__init__()

        # Setup logging
        try:
            log_path = get_log_path()
            sys.stdout = CLILogger(log_path, sys.stdout)
            sys.stderr = CLILogger(log_path, sys.stderr)
            self.stdout = sys.stdout
        except Exception as e:
            print(f"Failed to setup logging: {e}")

        self.config = load_config()
        self.servers = self.config.get("servers", [])
        self.server_names = [s["name"] for s in self.servers]
        self.server_colors = {
            s["name"]: ServerPallette.COLOR[i % len(ServerPallette.COLOR)]
            for i, s in enumerate(self.servers)
        }
        self.vm_service = vm_service if vm_service is not None else VMService()
        self.active_connections = {}
        self.selected_vms = {}

        # Auto-connect to servers
        for server in self.servers:
            if server.get("autoconnect", False):
                try:
                    print(
                        f"Autoconnecting to {self._colorize(server['name'], server['name'])} ({server['uri']})..."
                    )
                    conn = self.vm_service.connect(server["uri"])
                    if conn:
                        self.active_connections[server["name"]] = conn
                        print(
                            f"Successfully connected to '{self._colorize(server['name'], server['name'])}'."
                        )
                    else:
                        print(
                            f"Failed to autoconnect to '{self._colorize(server['name'], server['name'])}'."
                        )
                except Exception as e:
                    print(f"Error autoconnecting to {server['name']}: {e}")

        self.status_map = {
            libvirt.VIR_DOMAIN_NOSTATE: "No State",
            libvirt.VIR_DOMAIN_RUNNING: "Running",
            libvirt.VIR_DOMAIN_BLOCKED: "Blocked",
            libvirt.VIR_DOMAIN_PAUSED: "Paused",
            libvirt.VIR_DOMAIN_SHUTDOWN: "Shutting Down",
            libvirt.VIR_DOMAIN_SHUTOFF: "Stopped",
            libvirt.VIR_DOMAIN_CRASHED: "Crashed",
            libvirt.VIR_DOMAIN_PMSUSPENDED: "Suspended",
        }

        # Categories for help and completion
        self.categories = {
            "Connection": ["connect", "disconnect", "host_info", "virsh"],
            "VM Selection": ["list_vms", "select_vm", "unselect_vm", "status", "vm_info"],
            "VM Operations": [
                "start",
                "stop",
                "force_off",
                "pause",
                "resume",
                "hibernate",
                "delete",
                "clone_vm",
                "view",
            ],
            "Snapshots": ["snapshot_list", "snapshot_create", "snapshot_delete", "snapshot_revert"],
            "Networking": [
                "list_networks",
                "net_start",
                "net_stop",
                "net_delete",
                "net_info",
                "net_autostart",
            ],
            "Storage": ["list_pool", "list_unused_volumes"],
            "Shell/Utils": ["bash", "quit"],
        }
        try:
            import readline

            readline.set_completion_display_matches_hook(self._display_completion_matches)
        except Exception:
            pass

        self._update_prompt()

    def _colorize(self, text, server_name):
        """Wraps text in ANSI escape codes for the server's assigned color."""
        color = self.server_colors.get(server_name)
        if not color:
            return text
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            return f"\033[38;2;{r};{g};{b}m{text}\033[0m"
        except (ValueError, IndexError):
            return text

    def do_virsh(self, args):
        """Start a virsh shell connected to a server.
        Usage: virsh [server_name]"""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        target_server = None

        # If server name provided as argument
        if args:
            server_name = args.strip()
            if server_name in self.active_connections:
                target_server = server_name
            else:
                print(f"Error: Not connected to '{server_name}'.")
                return

        # If no argument and only one connection
        elif len(self.active_connections) == 1:
            target_server = list(self.active_connections.keys())[0]

        # If no argument and multiple connections
        else:
            print("Multiple active connections:")
            servers = list(self.active_connections.keys())
            for i, name in enumerate(servers):
                print(f"  {i + 1}. {name}")

            try:
                choice = input("Select server (number): ")
                idx = int(choice) - 1
                if 0 <= idx < len(servers):
                    target_server = servers[idx]
                else:
                    print("Invalid selection.")
                    return
            except ValueError:
                print("Invalid input.")
                return

        if target_server:
            conn = self.active_connections[target_server]
            try:
                uri = conn.getURI()
                print(f"Connecting to virsh on {target_server} ({uri})...")
                print("Type 'exit' or 'quit' to return to virtui-manager.")

                # Check if virsh is installed
                if not shutil.which("virsh"):
                    print("Error: 'virsh' command not found. Please install libvirt-clients.")
                    return

                self._set_title(f"virsh ({target_server})")
                subprocess.call(["virsh", "-c", uri])
                self._update_prompt()  # Restore title
                print(f"\nReturned from virsh ({target_server}).")
            except libvirt.libvirtError as e:
                print(f"Error getting URI for {target_server}: {e}")
            except Exception as e:
                print(f"Error launching virsh: {e}")

    def complete_virsh(self, text, line, begidx, endidx):
        return self.complete_disconnect(text, line, begidx, endidx)

    def do_bash(self, args):
        """Execute a bash command or start an interactive bash shell.
        Usage: bash [command]"""
        shell = shutil.which("bash") or shutil.which("sh")
        if not shell:
            print("Error: No shell found (bash or sh).")
            return

        try:
            if args:
                subprocess.run([shell, "-c", args])
            else:
                print(f"Starting interactive shell ({shell})...")
                print("Type 'exit' to return to virtui-manager.")
                self._set_title(f"bash ({shell})")
                subprocess.call([shell])
                self._update_prompt()
                print(f"\nReturned from {shell}.")
        except Exception as e:
            print(f"Error executing shell: {e}")

    def _set_title(self, title):
        """Sets the terminal window title."""
        print(f"\033]0;{title}\007", end="", flush=True)

    def _update_prompt(self):
        if self.active_connections:
            server_names = ",".join(
                [self._colorize(name, name) for name in self.active_connections.keys()]
            )

            # Flatten the list of selected VMs from all servers
            all_selected_vms = []
            for server_name, vms in self.selected_vms.items():
                for vm in vms:
                    all_selected_vms.append(self._colorize(vm, server_name))

            if all_selected_vms:
                self.prompt = f"({server_names}) [{','.join(all_selected_vms)}] "
            else:
                self.prompt = f"({server_names}) "

            # Set title without ANSI codes
            plain_server_names = ",".join(self.active_connections.keys())
            self._set_title(f"CLI: {plain_server_names}")
        else:
            self.prompt = "(" + AppInfo.name + ")> "
            self._set_title("Virtui Manager CLI")

    def _display_completion_matches(self, substitution, matches, longest_match_length):
        """Custom display hook for readline completion to show categories."""
        # Check if first match is a known command or "help". This is a heuristic.
        # We flatten categories to check membership.
        all_categorized = set([cmd for cmds in self.categories.values() for cmd in cmds])

        is_command_completion = False
        if matches:
            first = matches[0].strip()
            if first in all_categorized or first == "help" or hasattr(self, f"do_{first}"):
                is_command_completion = True

        if not is_command_completion:
            # Default display
            print()
            self.columnize(matches, displaywidth=80)
            print(self.prompt, end="", flush=True)
            print(readline.get_line_buffer(), end="", flush=True)
            return

        # It IS command completion, categorize them
        print()

        matches_set = set(matches)
        displayed_matches = set()

        for category, cmds in self.categories.items():
            cmds_in_cat = [c for c in cmds if c in matches_set]
            if cmds_in_cat:
                print(f"\033[1;36m{category}:\033[0m")
                self.columnize(sorted(cmds_in_cat), displaywidth=80)
                displayed_matches.update(cmds_in_cat)
                print()

        # Uncategorized matches
        uncategorized = [c for c in matches if c not in displayed_matches]
        if uncategorized:
            print("\033[1;36mOther:\033[0m")
            self.columnize(sorted(uncategorized), displaywidth=80)
            print()

        print(self.prompt, end="", flush=True)
        print(readline.get_line_buffer(), end="", flush=True)

    def _find_domain(self, vm_name):
        """Finds a domain by name across all active connections.
        If multiple are found, it prompts the user to select one.
        Returns a tuple of (virDomain, server_name) or (None, None).
        """
        # Handle completion format VMNAME:UUID:SERVER
        parts = vm_name.split(":")
        if len(parts) == 3:
            name, uuid, server = parts
            if server in self.active_connections:
                try:
                    conn = self.active_connections[server]
                    domain = conn.lookupByUUIDString(uuid)
                    return domain, server
                except libvirt.libvirtError:
                    # Fallback to name search if UUID lookup fails
                    vm_name = name

        found_vms = []  # List of (domain, server_name)
        for server_name, conn in self.active_connections.items():
            try:
                domain = conn.lookupByName(vm_name)
                found_vms.append((domain, server_name))
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    continue
                else:
                    print(f"A libvirt error occurred on server {server_name}: {e}")

        if not found_vms:
            print(f"Error: VM '{vm_name}' not found on any connected server.")
            return None, None

        if len(found_vms) == 1:
            return found_vms[0]

        # Multiple VMs found, prompt user
        print(f"VM '{vm_name}' found on multiple servers:")
        for i, (dom, server_name) in enumerate(found_vms):
            print(f"  {i + 1}. {server_name}")

        try:
            choice = input("Select server (number): ")
            idx = int(choice) - 1
            if 0 <= idx < len(found_vms):
                return found_vms[idx]
            else:
                print("Invalid selection.")
                return None, None
        except (ValueError, IndexError):
            print("Invalid input.")
            return None, None

    def _get_vms_to_operate(self, args):
        vms_to_operate = {}
        args_list = args.split()

        if args_list:
            # Map of name/UUID to list of servers and actual VM name
            vm_lookup = {}
            for server_name, conn in self.active_connections.items():
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
                    if server in self.active_connections:
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
                else:
                    print(f"Warning: VM/UUID '{identifier}' not found on any connected server.")

        else:
            # If no args, use a copy of the pre-selected VMs to avoid modification during iteration
            vms_to_operate = {server: list(vms) for server, vms in self.selected_vms.items()}

        return vms_to_operate

    def do_connect(self, args):
        """Connect to one or more servers.
        Usage: connect <server_name_1> [<server_name_2> ...] | all"""
        server_names_to_connect = args.split()

        if not server_names_to_connect:
            print("Please specify one or more server names.")
            print(f"Available servers: {', '.join(self.server_names)}")
            return

        if "all" in server_names_to_connect:
            server_names_to_connect = self.server_names

        for server_name in server_names_to_connect:
            if server_name in self.active_connections:
                print(f"Already connected to '{server_name}'.")
                continue

            server_info = next((s for s in self.servers if s["name"] == server_name), None)

            if not server_info:
                print(f"Server '{server_name}' not found in configuration.")
                continue

            try:
                print(f"Connecting to {server_name} at {server_info['uri']}...")
                conn = self.vm_service.connect(server_info["uri"])
                if conn:
                    self.active_connections[server_name] = conn
                    print(f"Successfully connected to '{server_name}'.")
                else:
                    print(f"Failed to connect to '{server_name}'.")
            except libvirt.libvirtError as e:
                print(f"Error connecting to {server_name}: {e}")

        self._update_prompt()

    def complete_connect(self, text, line, begidx, endidx):
        """Auto-completion for server names."""
        if not text:
            completions = self.server_names[:]
        else:
            completions = [s for s in self.server_names if s.startswith(text)]
        return completions

    def do_disconnect(self, args):
        """Disconnects from one or more libvirt servers.
        Usage: disconnect [<server_name_1> <server_name_2> ...] | all"""
        if not self.active_connections:
            print("Not connected to any servers.")
            return

        servers_to_disconnect = args.split()
        if not servers_to_disconnect or "all" in servers_to_disconnect:
            servers_to_disconnect = list(self.active_connections.keys())

        for server_name in servers_to_disconnect:
            if server_name in self.active_connections:
                try:
                    conn = self.active_connections[server_name]
                    uri = conn.getURI()
                    self.vm_service.disconnect(uri)
                    del self.active_connections[server_name]
                    if server_name in self.selected_vms:
                        del self.selected_vms[server_name]
                    print(f"Disconnected from '{server_name}'.")
                except libvirt.libvirtError as e:
                    print(f"Error during disconnection from '{server_name}': {e}")
            else:
                print(f"Not connected to '{server_name}'.")

        self._update_prompt()

    def complete_disconnect(self, text, line, begidx, endidx):
        """Auto-completion for disconnecting from connected servers."""
        if not self.active_connections:
            return []

        connected_servers = list(self.active_connections.keys())
        if not text:
            return connected_servers
        else:
            return [s for s in connected_servers if s.startswith(text)]

    def do_list_vms(self, arg):
        """List all VMs on the connected servers with their status."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        for server_name, conn in self.active_connections.items():
            try:
                print(f"\n--- VMs on {self._colorize(server_name, server_name)} ---")
                domains = conn.listAllDomains(0)
                if domains:
                    print(f"{'VM Name':<30} {'Status':<15}")
                    print(f"{'-' * 30} {'-' * 15}")

                    sorted_domains = sorted(domains, key=lambda d: d.name())
                    for domain in sorted_domains:
                        status_code = domain.info()[0]
                        status_str = self.status_map.get(status_code, "Unknown")
                        colored_name = self._colorize(domain.name(), server_name)
                        print(f"{colored_name:<40} {status_str:<15}")
                else:
                    print("No VMs found on this server.")
            except libvirt.libvirtError as e:
                print(f"Error listing VMs on {server_name}: {e}")

    def do_select_vm(self, args):
        """Select one or more VMs from any connected server. Can use patterns with 're:' prefix.
        Usage: select_vm <vm_name_1> <vm_name_2> ...
               select_vm re:<pattern>"""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        arg_list = args.split()
        if not arg_list:
            print("Usage: select_vm <vm_name_1> <vm_name_2> ... or select_vm re:<pattern>")
            return

        # Master list of all VMs from all connected servers
        vm_lookup = {}
        all_names = set()
        for server_name, conn in self.active_connections.items():
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
            except libvirt.libvirtError as e:
                print(f"Could not fetch VMs from {server_name}: {e}")
                continue

        # This will hold the names of the VMs to be selected if only name/UUID is provided
        vms_to_select_names = set()
        invalid_inputs = []

        # Reset selection
        self.selected_vms = {}

        for arg in arg_list:
            if arg.startswith("re:"):
                pattern_str = arg[3:]
                try:
                    pattern = re.compile(pattern_str)
                    matched_vms = {name for name in all_names if pattern.match(name)}
                    if matched_vms:
                        vms_to_select_names.update(matched_vms)
                    else:
                        print(f"Warning: No VMs found matching pattern '{pattern_str}'.")
                except re.error as e:
                    print(f"Error: Invalid regular expression '{pattern_str}': {e}")
                    invalid_inputs.append(arg)
            else:
                parts = arg.split(":")
                # If it's the full format from completion: VMNAME:UUID:SERVER
                if len(parts) == 3:
                    name, uuid, server = parts
                    if server in self.active_connections:
                        if server not in self.selected_vms:
                            self.selected_vms[server] = []
                        if name not in self.selected_vms[server]:
                            self.selected_vms[server].append(name)
                        continue

                clean_id = parts[0].strip()
                target = vm_lookup.get(clean_id) or vm_lookup.get(arg)
                if target:
                    vms_to_select_names.add(target["name"])
                else:
                    invalid_inputs.append(arg)

        # Populate self.selected_vms for the general names in vms_to_select_names
        for vm_name in sorted(list(vms_to_select_names)):
            if vm_name in vm_lookup:
                for server_name in vm_lookup[vm_name]["servers"]:
                    if server_name not in self.selected_vms:
                        self.selected_vms[server_name] = []
                    if vm_name not in self.selected_vms[server_name]:
                        self.selected_vms[server_name].append(vm_name)

        if invalid_inputs:
            print(
                f"Error: The following VMs or patterns were not found or invalid: {', '.join(invalid_inputs)}"
            )

        if self.selected_vms:
            print("Selected VMs:")
            for server, vms in self.selected_vms.items():
                print(f"  on {server}: {', '.join(vms)}")
        else:
            print("No VMs selected.")

        self._update_prompt()

    def complete_select_vm(self, text, line, begidx, endidx):
        """Auto-completion of VM list for select_vm and pattern-based selection."""
        if not self.active_connections:
            return []

        completions = set()
        for server_name, conn in self.active_connections.items():
            try:
                domains = conn.listAllDomains(0)
                for dom in domains:
                    name = dom.name()
                    uuid = dom.UUIDString()
                    completions.add(f"{name}:{uuid}:{server_name}")
            except libvirt.libvirtError:
                continue

        if not text:
            return sorted(list(completions))
        else:
            return sorted([f for f in completions if f.startswith(text)])

    def do_unselect_vm(self, args):
        """Unselect one or more VMs. Can use patterns with 're:' prefix or use 'all' to unselect all.
        Usage: unselect_vm <vm_name_1> <vm_name_2> ...
               unselect_vm re:<pattern>
               unselect_vm all"""
        if not self.selected_vms:
            print("No VMs are currently selected.")
            return

        arg_list = args.split()
        if not arg_list:
            print(
                "Usage: unselect_vm <vm_name_1> <vm_name_2> ... or unselect_vm re:<pattern> or unselect_vm all"
            )
            return

        if "all" in arg_list:
            self.selected_vms = {}
            print("All VMs have been unselected.")
            self._update_prompt()
            return

        # Get a flat list of currently selected VM names
        currently_selected_vms = {
            vm_name for server_vms in self.selected_vms.values() for vm_name in server_vms
        }

        vms_to_unselect = set()
        not_found = []

        for arg in arg_list:
            if arg.startswith("re:"):
                pattern_str = arg[3:]
                try:
                    pattern = re.compile(pattern_str)
                    # Find matches within the currently selected VMs
                    matched_vms = {
                        vm_name for vm_name in currently_selected_vms if pattern.match(vm_name)
                    }
                    if matched_vms:
                        vms_to_unselect.update(matched_vms)
                    else:
                        not_found.append(arg)
                except re.error as e:
                    print(f"Error: Invalid regular expression '{pattern_str}': {e}")
            else:
                if arg in currently_selected_vms:
                    vms_to_unselect.add(arg)
                else:
                    not_found.append(arg)

        if not vms_to_unselect:
            print("No matching VMs to unselect found in the current selection.")
            if not_found:
                print(f"The following VMs/patterns were not found: {', '.join(not_found)}")
            return

        # New dictionary for selected VMs
        new_selected_vms = {}
        for server_name, vm_list in self.selected_vms.items():
            vms_to_keep = [vm for vm in vm_list if vm not in vms_to_unselect]
            if vms_to_keep:
                new_selected_vms[server_name] = vms_to_keep

        self.selected_vms = new_selected_vms

        print(f"Unselected VM(s): {', '.join(sorted(list(vms_to_unselect)))}")
        if not_found:
            print(f"Warning: The following were not found in the selection: {', '.join(not_found)}")

        if self.selected_vms:
            print("Remaining selected VMs:")
            for server, vms in self.selected_vms.items():
                print(f"  on {server}: {', '.join(vms)}")
        else:
            print("No VMs are selected anymore.")

        self._update_prompt()

    def complete_unselect_vm(self, text, line, begidx, endidx):
        """Auto-completion for unselect_vm from the list of selected VMs."""
        if not self.selected_vms:
            return []

        selected_vms_flat = {
            vm_name for vms_list in self.selected_vms.values() for vm_name in vms_list
        }

        if not text:
            completions = list(selected_vms_flat)
        else:
            completions = sorted([f for f in selected_vms_flat if f.startswith(text)])
        return completions

    def do_status(self, args):
        """Shows the status of one or more VMs across any connected server.
        Usage: status [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will show the status of selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_check = self._get_vms_to_operate(args)
        if not vms_to_check:
            return

        for server_name, vm_list in vms_to_check.items():
            print(f"\n--- Status on {self._colorize(server_name, server_name)} ---")
            conn = self.active_connections[server_name]
            print(f"{'VM Name':<30} {'Status':<15} {'vCPUs':<7} {'Memory (MiB)':<15}")
            print(f"{'-' * 30} {'-' * 15} {'-' * 7} {'-' * 15}")

            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    info = domain.info()
                    state_code = info[0]
                    state_str = self.status_map.get(state_code, "Unknown")
                    vcpus = info[3]
                    mem_kib = info[2]  # Current memory
                    mem_mib = mem_kib // 1024
                    colored_name = self._colorize(domain.name(), server_name)
                    print(f"{colored_name:<40} {state_str:<15} {vcpus:<7} {mem_mib:<15}")
                except libvirt.libvirtError as e:
                    print(f"Could not retrieve status for '{vm_name}': {e}")

    def do_vm_info(self, args):
        """Show detailed information about one or more VMs.
        Usage: vm_info [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will show info for the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_check = self._get_vms_to_operate(args)
        if not vms_to_check:
            return

        from .vm_queries import get_domain_info_dict

        for server_name, vm_list in vms_to_check.items():
            print(f"\n--- VM Information on {self._colorize(server_name, server_name)} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    info = get_domain_info_dict(domain, conn)
                    if not info:
                        print(f"Error: Could not retrieve info for '{vm_name}'.")
                        continue

                    print(f"\n[ {self._colorize(vm_name, server_name)} ]")
                    print(f"  UUID:         {info.get('uuid')}")
                    print(f"  Status:       {info.get('status')}")
                    print(f"  Description:  {info.get('description')}")
                    print(f"  CPU:          {info.get('cpu')} cores ({info.get('cpu_model')})")
                    print(f"  Memory:       {info.get('memory')} MiB")
                    print(f"  Machine Type: {info.get('machine_type')}")

                    fw = info.get("firmware", {})
                    fw_str = fw.get("type", "N/A")
                    if fw.get("secure_boot"):
                        fw_str += " (Secure Boot enabled)"
                    print(f"  Firmware:     {fw_str}")

                    print("  Networks:")
                    networks = info.get("networks", [])
                    if not networks:
                        print("    None")
                    for net in networks:
                        network_name = net.get("network", "<unknown>")
                        model = net.get("model", "default")
                        print(
                            f"    - {network_name} (MAC: <redacted>, model: {model})"
                        )

                    # IP addresses if available
                    detail_net = info.get("detail_network", [])
                    if detail_net:
                        print("  IP Addresses:")
                        for iface in detail_net:
                            ipv4_list = iface.get("ipv4", [])
                            ipv6_list = iface.get("ipv6", [])
                            ipv4_count = len(ipv4_list)
                            ipv6_count = len(ipv6_list)
                            print(
                                f"    - <redacted interface> (MAC: <redacted>): "
                                f"{ipv4_count} IPv4 address(es), {ipv6_count} IPv6 address(es)"
                            )

                    print("  Disks:")
                    disks = info.get("disks", [])
                    if not disks:
                        print("    None")
                    for disk in disks:
                        print(
                            f"    - {disk.get('path')} ({disk.get('bus')}, {disk.get('status')}, {disk.get('device_type')})"
                        )

                    # Add video/graphics
                    graphics = info.get("devices", {}).get("graphics", [])
                    if graphics:
                        print("  Graphics:")
                        for g in graphics:
                            g_str = f"{g.get('type')}"
                            if g.get("port"):
                                g_str += f", port: {g.get('port')}"
                            if g.get("autoport") == "yes":
                                g_str += " (autoport)"
                            print(f"    - {g_str}")

                except libvirt.libvirtError as e:
                    print(f"Could not retrieve info for '{vm_name}': {e}")

    def complete_vm_info(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_view(self, args):
        """Launch the graphical viewer (Spice/VNC) for one or more VMs.
        Usage: view [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will launch the viewer for the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_view = self._get_vms_to_operate(args)
        if not vms_to_view:
            return

        for server_name, vm_list in vms_to_view.items():
            conn = self.active_connections[server_name]
            try:
                uri = conn.getURI()
                for vm_name in vm_list:
                    try:
                        domain = conn.lookupByName(vm_name)
                        if not domain.isActive():
                            print(
                                f"Warning: VM '{vm_name}' on {server_name} is not running. Viewer will wait for it to start."
                            )

                        cmd = remote_viewer_cmd(uri, vm_name)
                        if not cmd or not cmd[0]:
                            print(
                                "Error: No remote viewer found (virtui-remote-viewer or virt-viewer)."
                            )
                            return

                        print(f"Launching viewer for '{vm_name}' on {server_name}...")
                        # Launch as background process
                        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except libvirt.libvirtError as e:
                        print(f"Error looking up VM '{vm_name}': {e}")
            except Exception as e:
                print(f"Error processing server {server_name}: {e}")

    def complete_view(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def complete_status(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_start(self, args):
        """Starts one or more VMs.
        Usage: start [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will start the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_start = self._get_vms_to_operate(args)
        if not vms_to_start:
            return

        for server_name, vm_list in vms_to_start.items():
            print(f"\n--- Starting VMs on {server_name} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    if domain.isActive():
                        print(f"VM '{vm_name}' is already running.")
                        continue
                    start_vm(domain)
                    print(f"VM '{vm_name}' started successfully.")
                except libvirt.libvirtError as e:
                    print(f"Error starting VM '{vm_name}': {e}")
                except Exception as e:
                    print(f"An unexpected error occurred with VM '{vm_name}': {e}")

    def complete_start(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_stop(self, args):
        """Stops one or more VMs gracefully (sends shutdown signal).
        For a forced shutdown, use the 'force_off' command.
        Usage: stop [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will stop the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_stop = self._get_vms_to_operate(args)
        if not vms_to_stop:
            return

        for server_name, vm_list in vms_to_stop.items():
            print(f"\n--- Stopping VMs on {server_name} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    if not domain.isActive():
                        print(f"VM '{vm_name}' is not running.")
                        continue

                    stop_vm(domain)
                    print(f"Sent shutdown signal to VM '{vm_name}'.")
                except libvirt.libvirtError as e:
                    print(f"Error stopping VM '{vm_name}': {e}")

    def complete_stop(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_force_off(self, args):
        """Forcefully powers off one or more VMs (like pulling the power plug).
        Usage: force_off [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will force off the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_force_off = self._get_vms_to_operate(args)
        if not vms_to_force_off:
            return

        for server_name, vm_list in vms_to_force_off.items():
            print(f"\n--- Force-off VMs on {server_name} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    if not domain.isActive():
                        print(f"VM '{vm_name}' is not running.")
                        continue
                    force_off_vm(domain)
                    print(f"VM '{vm_name}' forcefully powered off.")
                except libvirt.libvirtError as e:
                    print(f"Error forcefully powering off VM '{vm_name}': {e}")
                except Exception as e:
                    print(f"An unexpected error occurred with VM '{vm_name}': {e}")

    def complete_force_off(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_pause(self, args):
        """Pauses one or more running VMs.
        Usage: pause [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will pause the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_pause = self._get_vms_to_operate(args)
        if not vms_to_pause:
            return

        for server_name, vm_list in vms_to_pause.items():
            print(f"\n--- Pausing VMs on {server_name} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    if not domain.isActive():
                        print(f"VM '{vm_name}' is not running.")
                        continue
                    if domain.info()[0] == libvirt.VIR_DOMAIN_PAUSED:
                        print(f"VM '{vm_name}' is already paused.")
                        continue
                    pause_vm(domain)
                    print(f"VM '{vm_name}' paused.")
                except libvirt.libvirtError as e:
                    print(f"Error pausing VM '{vm_name}': {e}")

    def complete_pause(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_hibernate(self, args):
        """Saves the VM state to disk and stops it (hibernate).
        Usage: hibernate [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will hibernate the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_hibernate = self._get_vms_to_operate(args)
        if not vms_to_hibernate:
            return

        for server_name, vm_list in vms_to_hibernate.items():
            print(f"\n--- Hibernating VMs on {server_name} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    if not domain.isActive():
                        print(f"VM '{vm_name}' is not running.")
                        continue
                    hibernate_vm(domain)
                    print(f"VM '{vm_name}' hibernated.")
                except libvirt.libvirtError as e:
                    print(f"Error hibernating VM '{vm_name}': {e}")
                except Exception as e:
                    print(f"An unexpected error occurred with VM '{vm_name}': {e}")

    def complete_hibernate(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_resume(self, args):
        """Resumes one or more paused VMs.
        Usage: resume [vm_name_1] [vm_name_2] ...
        If no VM names are provided, it will resume the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        vms_to_resume = self._get_vms_to_operate(args)
        if not vms_to_resume:
            return

        for server_name, vm_list in vms_to_resume.items():
            print(f"\n--- Resuming VMs on {server_name} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    state = domain.info()[0]
                    if state == libvirt.VIR_DOMAIN_PAUSED:
                        domain.resume()
                        print(f"VM '{vm_name}' resumed.")
                    elif state == libvirt.VIR_DOMAIN_PMSUSPENDED:
                        domain.pMWakeup(0)
                        print(f"VM '{vm_name}' woken up.")
                    else:
                        print(f"VM '{vm_name}' is not paused or suspended.")
                except libvirt.libvirtError as e:
                    print(f"Error resuming VM '{vm_name}': {e}")

    def complete_resume(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_delete(self, args):
        """Deletes one or more VMs, optionally removing associated storage.
        Usage: delete [--force-storage-delete] [vm_name_1] [vm_name_2] ...
        Use --force-storage-delete to automatically confirm deletion of associated storage.
        If no VM names are provided, it will delete the selected VMs."""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        args_list = args.split()
        force_storage_delete = "--force-storage-delete" in args_list
        if force_storage_delete:
            args_list.remove("--force-storage-delete")

        vms_to_delete = self._get_vms_to_operate(" ".join(args_list))
        if not vms_to_delete:
            return

        # Consolidate all VM names for a single confirmation
        all_vm_names = [vm for vms in vms_to_delete.values() for vm in vms]
        if not all_vm_names:
            return

        vm_list_str = ", ".join(all_vm_names)
        confirm_vm_delete = input(
            f"Are you sure you want to delete the following VMs: {vm_list_str}? (yes/no): "
        ).lower()

        if confirm_vm_delete != "yes":
            print("VM deletion cancelled.")
            return

        delete_storage_confirmed = False
        if force_storage_delete:
            delete_storage_confirmed = True
        else:
            confirm_storage = input(
                "Do you want to delete associated storage for all selected VMs? (yes/no): "
            ).lower()
            if confirm_storage == "yes":
                delete_storage_confirmed = True

        for server_name, vm_list in vms_to_delete.items():
            print(f"\n--- Deleting VMs on {server_name} ---")
            conn = self.active_connections[server_name]
            for vm_name in vm_list:
                try:
                    domain = conn.lookupByName(vm_name)
                    delete_vm(domain, delete_storage_confirmed)
                    print(f"VM '{vm_name}' deleted successfully.")
                    if delete_storage_confirmed:
                        print(f"Associated storage for '{vm_name}' also deleted.")

                    # Unselect the VM if it was selected
                    if (
                        server_name in self.selected_vms
                        and vm_name in self.selected_vms[server_name]
                    ):
                        self.selected_vms[server_name].remove(vm_name)
                        if not self.selected_vms[server_name]:
                            del self.selected_vms[server_name]

                except libvirt.libvirtError as e:
                    print(f"Error deleting VM '{vm_name}': {e}")
                except Exception as e:
                    print(f"An unexpected error occurred with VM '{vm_name}': {e}")

        self._update_prompt()

    def complete_delete(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_clone_vm(self, args):
        """Clones a VM.
        Usage: clone_vm <original_vm_name>"""
        arg_list = args.split()
        if len(arg_list) != 1:
            print("Usage: clone_vm <original_vm_name>")
            return

        original_vm_name = arg_list[0]

        original_vm_domain, original_vm_server_name = self._find_domain(original_vm_name)
        if not original_vm_domain:
            return

        conn = self.active_connections[original_vm_server_name]
        original_vm_name = original_vm_domain.name()

        print(f"Found VM '{original_vm_name}' on server '{original_vm_server_name}'.")

        # Start asking questions
        new_vm_base_name = input(f"Enter the new VM name [clone_of_{original_vm_name}]: ").strip()
        if not new_vm_base_name:
            new_vm_base_name = f"clone_of_{original_vm_name}"

        try:
            num_clones_str = input("How many VM to create? [1]: ").strip()
            num_clones = int(num_clones_str) if num_clones_str else 1
            if num_clones < 1:
                print("Error: Number of VMs must be at least 1.")
                return
        except ValueError:
            print("Error: Invalid number.")
            return

        postfix = ""
        if num_clones > 1:
            postfix = input("Enter postfix name (e.g. '-') [-]: ").strip()
            if not postfix:
                postfix = "-"

        clone_storage_input = input("Clone also the storage? (yes/no) [yes]: ").strip().lower()
        clone_storage = clone_storage_input != "no"

        def log_to_console(message):
            print(f"  -> {message.strip()}")

        for i in range(1, num_clones + 1):
            if num_clones > 1:
                current_new_name = f"{new_vm_base_name}{postfix}{i}"
            else:
                current_new_name = new_vm_base_name

            # Check if VM already exists
            try:
                conn.lookupByName(current_new_name)
                print(
                    f"Error: A VM with the name '{current_new_name}' already exists on server '{original_vm_server_name}'. Skipping."
                )
                continue
            except libvirt.libvirtError as e:
                if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
                    print(
                        f"An error occurred while checking for existing VM '{current_new_name}': {e}"
                    )
                    continue

            try:
                print(
                    f"\n[{i}/{num_clones}] Cloning '{original_vm_name}' to '{current_new_name}' on server '{original_vm_server_name}'..."
                )
                clone_vm(
                    original_vm_domain,
                    current_new_name,
                    clone_storage=clone_storage,
                    log_callback=log_to_console,
                )
                print(f"Successfully cloned '{original_vm_name}' to '{current_new_name}'.")

            except libvirt.libvirtError as e:
                print(f"Error cloning VM '{current_new_name}': {e}")
            except Exception as e:
                print(f"An unexpected error occurred during cloning '{current_new_name}': {e}")

    def complete_clone_vm(self, text, line, begidx, endidx):
        """Auto-completion for the original VM to clone."""
        words = line.split()
        # Only complete the first argument (original_vm_name)
        if len(words) > 2 or (len(words) == 2 and not line.endswith(" ")):
            return []

        return self.complete_select_vm(text, line, begidx, endidx)

    def do_list_unused_volumes(self, args):
        """Lists all storage volumes that are not attached to any VM.
        If pool_name is provided, only checks volumes in that specific pool.
        Usage: list_unused_volumes [pool_name]"""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        pool_name = args.strip() if args else None

        for server_name, conn in self.active_connections.items():
            print(f"\n--- Unused Volumes on {server_name} ---")
            try:
                unused_volumes = list_unused_volumes(conn, pool_name)
                if unused_volumes:
                    print(f"{'Pool':<20} {'Volume Name':<30} {'Path':<50} {'Capacity (MiB)':<15}")
                    print(f"{'-' * 20} {'-' * 30} {'-' * 50} {'-' * 15}")
                    for vol in unused_volumes:
                        pool_name_vol = vol.storagePoolLookupByVolume().name()
                        info = vol.info()
                        capacity_mib = info[1] // (1024 * 1024)
                        print(
                            f"{pool_name_vol:<20} {vol.name():<30} {vol.path():<50} {capacity_mib:<15}"
                        )
                else:
                    print("No unused volumes found on this server.")
            except libvirt.libvirtError as e:
                print(f"Error listing unused volumes on {server_name}: {e}")
            except Exception as e:
                print(f"An unexpected error occurred on {server_name}: {e}")

    def do_list_pool(self, args):
        """Lists all storage pools on the connected servers.
        Usage: list_pool"""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        for server_name, conn in self.active_connections.items():
            print(f"\n--- Storage Pools on {server_name} ---")
            try:
                pools_info = list_storage_pools(conn)
                if pools_info:
                    print(
                        f"{'Pool Name':<30} {'Status':<15} {'Capacity (GiB)':<15} {'Allocation (GiB)':<15} {'Usage %':<10}"
                    )
                    print(f"{'-' * 30} {'-' * 15} {'-' * 15} {'-' * 15} {'-' * 10}")
                    for pool_info in pools_info:
                        capacity_gib = pool_info["capacity"] // (1024 * 1024 * 1024)
                        allocation_gib = pool_info["allocation"] // (1024 * 1024 * 1024)

                        usage_percent = 0.0
                        if capacity_gib > 0:
                            usage_percent = (allocation_gib / capacity_gib) * 100

                        usage_percent_str = f"{usage_percent:.2f}%"

                        print(
                            f"{pool_info['name']:<30} {pool_info['status']:<15} {capacity_gib:<15} {allocation_gib:<15} {usage_percent_str:<10}"
                        )
                else:
                    print("No storage pools found on this server.")
            except libvirt.libvirtError as e:
                print(f"Error listing storage pools on {server_name}: {e}")
            except Exception as e:
                print(f"An unexpected error occurred on {server_name}: {e}")

    def complete_list_unused_volumes(self, text, _, _b, _e):
        """Auto-completion for pool names in list_unused_volumes command."""
        if not self.active_connections:
            return []

        all_pool_names = set()
        for conn in self.active_connections.values():
            try:
                pools_info = list_storage_pools(conn)
                pool_names = {pool_info["name"] for pool_info in pools_info}
                all_pool_names.update(pool_names)
            except libvirt.libvirtError:
                continue

        if not text:
            return list(all_pool_names)
        else:
            return [pool for pool in all_pool_names if pool.startswith(text)]

    def do_help(self, arg):
        """List available commands with "help" or detailed help with "help cmd"."""
        if arg:
            return super().do_help(arg)

        # Find any other commands not in categories
        all_cmds = [name[3:] for name in self.get_names() if name.startswith("do_")]
        categorized_cmds = set([cmd for cmds in self.categories.values() for cmd in cmds])
        uncategorized = [c for c in all_cmds if c not in categorized_cmds and c != "help"]

        categories_to_show = self.categories.copy()
        if uncategorized:
            categories_to_show["Other"] = uncategorized

        print(self.doc_header)

        for category, cmds in categories_to_show.items():
            cmds_to_print = []
            for c in cmds:
                # check if command exists
                if f"do_{c}" in self.get_names():
                    cmds_to_print.append(c)

            if cmds_to_print:
                print(f"\n\033[1;36m{category}:\033[0m")
                self.columnize(sorted(cmds_to_print), displaywidth=80)
        print()

    def do_quit(self, arg):
        """Exit the virtui-manager shell."""
        # Disconnect all connections when quitting
        self.vm_service.disconnect_all()
        print(f"\nExiting {AppInfo.namecase}.")
        return True

    # --- Snapshot Management Commands ---

    def do_snapshot_list(self, args):
        """List all snapshots for a VM.
        Usage: snapshot_list <vm_name>"""
        if not args:
            print("Usage: snapshot_list <vm_name>")
            return

        vm_name = args.strip()
        domain, server_name = self._find_domain(vm_name)
        if not domain:
            return

        try:
            snapshots = get_vm_snapshots(domain)
            vm_display_name = domain.name()
            if not snapshots:
                print(f"No snapshots found for VM '{vm_display_name}' on server '{server_name}'.")
                return

            print(f"\n--- Snapshots for {vm_display_name} on {server_name} ---")
            print(f"{'Snapshot Name':<30} {'Creation Time':<25} {'State':<15}")
            print(f"{'-' * 30} {'-' * 25} {'-' * 15}")

            for snap_info in snapshots:
                name = snap_info.get("name", "N/A")
                creation_time = snap_info.get("creation_time", "N/A")
                state = snap_info.get("state", "N/A")
                print(f"{name:<30} {creation_time:<25} {state:<15}")

        except libvirt.libvirtError as e:
            print(f"Error listing snapshots for VM '{vm_name}': {e}")

    def complete_snapshot_list(self, text, line, begidx, endidx):
        return self.complete_select_vm(text, line, begidx, endidx)

    def do_snapshot_create(self, args):
        """Create a new snapshot for a VM.
        Usage: snapshot_create <vm_name> <snapshot_name> [--description "your description"]"""
        try:
            arg_list = shlex.split(args)
        except ValueError as e:
            print(f"Error parsing arguments: {e}")
            return

        if len(arg_list) < 2:
            print(
                'Usage: snapshot_create <vm_name> <snapshot_name> [--description "your description"]'
            )
            return

        vm_name = arg_list[0]
        snapshot_name = arg_list[1]
        description = ""

        if "--description" in arg_list:
            try:
                desc_index = arg_list.index("--description") + 1
                if desc_index < len(arg_list):
                    description = arg_list[desc_index]
                else:
                    print("Error: --description requires an argument.")
                    return
            except (ValueError, IndexError):
                print("Error parsing --description.")
                return

        domain, server_name = self._find_domain(vm_name)
        if not domain:
            return

        vm_display_name = domain.name()
        print(
            f"Creating snapshot '{snapshot_name}' for VM '{vm_display_name}' on server '{server_name}'..."
        )
        try:
            create_vm_snapshot(domain, snapshot_name, description)
            print("Snapshot created successfully.")
        except libvirt.libvirtError as e:
            print(f"Error creating snapshot: {e}")

    def complete_snapshot_create(self, text, line, begidx, endidx):
        words = line.split()
        if len(words) < 3:
            return self.complete_select_vm(text, line, begidx, endidx)
        return []

    def do_snapshot_delete(self, args):
        """Delete a snapshot from a VM.
        Usage: snapshot_delete <vm_name> <snapshot_name>"""
        arg_list = args.split()
        if len(arg_list) != 2:
            print("Usage: snapshot_delete <vm_name> <snapshot_name>")
            return

        vm_name, snapshot_name = arg_list
        domain, server_name = self._find_domain(vm_name)
        if not domain:
            return

        vm_display_name = domain.name()
        confirm = input(
            f"Are you sure you want to delete snapshot '{snapshot_name}' for VM '{vm_display_name}'? (yes/no): "
        ).lower()
        if confirm != "yes":
            print("Deletion cancelled.")
            return

        print(f"Deleting snapshot '{snapshot_name}'...")
        try:
            delete_vm_snapshot(domain, snapshot_name)
            print("Snapshot deleted successfully.")
        except libvirt.libvirtError as e:
            print(f"Error deleting snapshot: {e}")
        except Exception as e:
            print(f"Error deleting snapshot: {e}")

    def do_snapshot_revert(self, args):
        """Revert a VM to a specific snapshot.
        Usage: snapshot_revert <vm_name> <snapshot_name>"""
        arg_list = args.split()
        if len(arg_list) != 2:
            print("Usage: snapshot_revert <vm_name> <snapshot_name>")
            return

        vm_name, snapshot_name = arg_list
        domain, server_name = self._find_domain(vm_name)
        if not domain:
            return

        vm_display_name = domain.name()
        confirm = input(
            f"Are you sure you want to revert VM '{vm_display_name}' to snapshot '{snapshot_name}'? (yes/no): "
        ).lower()
        if confirm != "yes":
            print("Revert cancelled.")
            return

        print(f"Reverting to snapshot '{snapshot_name}'...")
        try:
            restore_vm_snapshot(domain, snapshot_name)
            print("VM reverted successfully.")
        except libvirt.libvirtError as e:
            print(f"Error reverting to snapshot: {e}")
        except Exception as e:
            print(f"Error reverting to snapshot: {e}")

    def _find_domain_no_prompt(self, vm_name):
        """Finds a domain but doesn't prompt if multiple are found.
        Returns the first one. Used for completion where prompting is not possible.
        """
        # Handle completion format VMNAME:UUID:SERVER
        parts = vm_name.split(":")
        if len(parts) == 3:
            name, uuid, server = parts
            if server in self.active_connections:
                try:
                    conn = self.active_connections[server]
                    domain = conn.lookupByUUIDString(uuid)
                    return domain, server
                except libvirt.libvirtError:
                    # Fallback to name search if UUID lookup fails
                    vm_name = name

        for server_name, conn in self.active_connections.items():
            try:
                domain = conn.lookupByName(vm_name)
                return domain, server_name
            except libvirt.libvirtError:
                continue
        return None, None

    def _complete_snapshot_names(self, text, vm_name):
        """Helper to autocomplete snapshot names for a given VM."""
        if not vm_name:
            return []

        domain, _ = self._find_domain_no_prompt(vm_name)
        if not domain:
            return []

        try:
            snapshots = get_vm_snapshots(domain)
            snapshot_names = [s["name"] for s in snapshots]
            if not text:
                return snapshot_names
            return [name for name in snapshot_names if name.startswith(text)]
        except libvirt.libvirtError:
            return []

    def complete_snapshot_delete(self, text, line, begidx, endidx):
        words = line.split()
        if len(words) == 2 and not line.endswith(" "):
            return self.complete_select_vm(text, line, begidx, endidx)

        if (len(words) == 2 and line.endswith(" ")) or (len(words) == 3 and not line.endswith(" ")):
            vm_name = words[1]
            return self._complete_snapshot_names(text, vm_name)
        return []

    def complete_snapshot_revert(self, text, line, begidx, endidx):
        return self.complete_snapshot_delete(text, line, begidx, endidx)

    # --- Network Management Commands ---

    def _get_target_server_for_network_op(self, net_name, operation_verb):
        """
        Finds servers with the given network and prompts user to select one if multiple are found.
        Returns a tuple of (server_name, connection_object) or (None, None).
        """
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return None, None

        servers_with_net = []
        for server_name, conn in self.active_connections.items():
            try:
                # Check if network exists on this server
                conn.networkLookupByName(net_name)
                servers_with_net.append(server_name)
            except libvirt.libvirtError:
                continue

        if not servers_with_net:
            print(f"Network '{net_name}' not found on any connected server.")
            return None, None

        target_server_name = None
        if len(servers_with_net) == 1:
            target_server_name = servers_with_net[0]
        else:
            print(f"Network '{net_name}' found on multiple servers:")
            for i, name in enumerate(servers_with_net):
                print(f"  {i + 1}. {name}")

            try:
                choice_str = input(
                    f"Select server to {operation_verb} network '{net_name}' on (number): "
                )
                idx = int(choice_str) - 1
                if 0 <= idx < len(servers_with_net):
                    target_server_name = servers_with_net[idx]
                else:
                    print("Invalid selection.")
                    return None, None
            except (ValueError, IndexError):
                print("Invalid input.")
                return None, None

        if target_server_name:
            return target_server_name, self.active_connections[target_server_name]
        return None, None

    def do_list_networks(self, args):
        """List all networks on the connected servers.
        Usage: list_networks"""
        if not self.active_connections:
            print("Not connected to any server. Use 'connect <server_name>'.")
            return

        for server_name, conn in self.active_connections.items():
            print(f"\n--- Networks on {server_name} ---")
            try:
                networks = list_networks(conn)
                if networks:
                    print(f"{'Network Name':<20} {'State':<10} {'Autostart':<10} {'Mode':<15}")
                    print(f"{'-' * 20} {'-' * 10} {'-' * 10} {'-' * 15}")
                    for net in networks:
                        state = "Active" if net["active"] else "Inactive"
                        autostart = "Yes" if net["autostart"] else "No"
                        print(f"{net['name']:<20} {state:<10} {autostart:<10} {net['mode']:<15}")
                else:
                    print("No networks found on this server.")
            except Exception as e:
                print(f"Error listing networks on {server_name}: {e}")

    def do_net_start(self, args):
        """Start a network.
        Usage: net_start <network_name>"""
        if not args:
            print("Usage: net_start <network_name>")
            return

        net_name = args.strip()
        target_server_name, conn = self._get_target_server_for_network_op(net_name, "start")

        if not conn:
            return

        try:
            set_network_active(conn, net_name, True)
            print(f"Network '{net_name}' started on {target_server_name}.")
        except Exception as e:
            print(f"Error starting network '{net_name}' on {target_server_name}: {e}")

    def complete_net_start(self, text, line, begidx, endidx):
        return self._complete_networks(text)

    def do_net_stop(self, args):
        """Stop (destroy) a network.
        Usage: net_stop <network_name>"""
        if not args:
            print("Usage: net_stop <network_name>")
            return

        net_name = args.strip()
        target_server_name, conn = self._get_target_server_for_network_op(net_name, "stop")

        if not conn:
            return

        try:
            set_network_active(conn, net_name, False)
            print(f"Network '{net_name}' stopped on {target_server_name}.")
        except Exception as e:
            print(f"Error stopping network '{net_name}' on {target_server_name}: {e}")

    def complete_net_stop(self, text, line, begidx, endidx):
        return self._complete_networks(text)

    def do_net_delete(self, args):
        """Delete (undefine) a network.
        Usage: net_delete <network_name>"""
        if not args:
            print("Usage: net_delete <network_name>")
            return

        net_name = args.strip()

        target_server_name, conn = self._get_target_server_for_network_op(net_name, "delete")
        if not conn:
            return

        confirm = input(
            f"Are you sure you want to delete network '{net_name}' from server '{target_server_name}'? This cannot be undone. (yes/no): "
        ).lower()
        if confirm != "yes":
            print("Operation cancelled.")
            return

        try:
            delete_network(conn, net_name)
            print(f"Network '{net_name}' deleted from {target_server_name}.")
        except Exception as e:
            print(f"Error deleting network '{net_name}' on {target_server_name}: {e}")

    def complete_net_delete(self, text, line, begidx, endidx):
        return self._complete_networks(text)

    def do_net_info(self, args):
        """Show detailed information about a network.
        Usage: net_info <network_name>"""
        if not args:
            print("Usage: net_info <network_name>")
            return

        net_name = args.strip()
        found = False
        for server_name, conn in self.active_connections.items():
            try:
                info = get_network_info(conn, net_name)
                if info:
                    print(f"\n--- Network '{net_name}' on {server_name} ---")
                    print(f"UUID: {info.get('uuid')}")
                    print(f"Forward Mode: {info.get('forward_mode')}")
                    if info.get("forward_dev"):
                        print(f"Forward Dev: {info.get('forward_dev')}")
                    if info.get("bridge_name"):
                        print(f"Bridge: {info.get('bridge_name')}")
                    if info.get("ip_address"):
                        print(
                            f"IP: {info.get('ip_address')} / {info.get('netmask') or info.get('prefix')}"
                        )
                    print(f"DHCP: {'Enabled' if info.get('dhcp') else 'Disabled'}")
                    if info.get("dhcp") and info.get("dhcp_start"):
                        print(f"DHCP Range: {info.get('dhcp_start')} - {info.get('dhcp_end')}")
                    found = True
            except Exception as e:
                print(f"Error retrieving info for '{net_name}' on {server_name}: {e}")

        if not found:
            print(f"Network '{net_name}' not found on any connected server.")

    def complete_net_info(self, text, line, begidx, endidx):
        return self._complete_networks(text)

    def do_net_autostart(self, args):
        """Set a network to autostart or not.
        Usage: net_autostart <network_name> <on|off>"""
        arg_list = args.split()
        if len(arg_list) != 2 or arg_list[1] not in ["on", "off"]:
            print("Usage: net_autostart <network_name> <on|off>")
            return

        net_name = arg_list[0]
        autostart = arg_list[1] == "on"
        status_verb = "enable" if autostart else "disable"
        status_past_tense = "enabled" if autostart else "disabled"

        target_server_name, conn = self._get_target_server_for_network_op(
            net_name, f"{status_verb} autostart for"
        )

        if not conn:
            return

        try:
            set_network_autostart(conn, net_name, autostart)
            print(
                f"Autostart {status_past_tense} for network '{net_name}' on {target_server_name}."
            )
        except Exception as e:
            print(f"Error setting autostart for network '{net_name}' on {target_server_name}: {e}")

    def complete_net_autostart(self, text, line, begidx, endidx):
        args = line.split()
        if len(args) == 2 and not line.endswith(" "):
            return self._complete_networks(text)
        elif (len(args) == 2 and line.endswith(" ")) or (len(args) == 3 and not line.endswith(" ")):
            return [s for s in ["on", "off"] if s.startswith(text)]
        return []

    def do_host_info(self, args):
        """Show host resource information for connected servers.
        Usage: host_info [server_name]"""
        target_servers = []
        if args:
            server_name = args.strip()
            if server_name in self.active_connections:
                target_servers = [server_name]
            else:
                print(f"Error: Not connected to '{server_name}'.")
                return
        else:
            target_servers = list(self.active_connections.keys())

        if not target_servers:
            print("Not connected to any server.")
            return

        for server_name in target_servers:
            conn = self.active_connections[server_name]
            try:
                info = get_host_resources(conn)
                if info:
                    print(f"\n--- Host Info: {server_name} ---")
                    print(f"CPU Model: {info.get('model')}")
                    print(
                        f"CPUs: {info.get('total_cpus')} ({info.get('nodes')} nodes, {info.get('sockets')} sockets, {info.get('cores')} cores, {info.get('threads')} threads)"
                    )
                    print(f"CPU Speed: {info.get('mhz')} MHz")
                    print(
                        f"Memory: {info.get('total_memory')} GiB total, {info.get('free_memory')} MiB free"
                    )
            except Exception as e:
                print(f"Error retrieving host info for {server_name}: {e}")

    def complete_host_info(self, text, line, begidx, endidx):
        return self.complete_disconnect(text, line, begidx, endidx)

    def _complete_networks(self, text):
        """Helper to autocomplete network names."""
        if not self.active_connections:
            return []

        all_nets = set()
        for conn in self.active_connections.values():
            try:
                nets = list_networks(conn)
                for n in nets:
                    all_nets.add(n["name"])
            except:
                pass

        if not text:
            return list(all_nets)
        return [n for n in all_nets if n.startswith(text)]


def main():
    """Entry point for Virtui Manager command-line interface."""
    cmd_app = VManagerCMD()
    try:
        cmd_app.cmdloop()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt caught. Use 'quit' to exit.")
        cmd_app.intro = ""  # Avoid re-printing intro on resume


if __name__ == "__main__":
    main()
