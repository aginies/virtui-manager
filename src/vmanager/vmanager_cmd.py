"""
the Cmd line tool

SECURITY NOTE:
This module implements comprehensive sanitization of sensitive information to prevent
accidental exposure of passwords, connection URIs, libvirt error details, and other
secrets in command-line output, logs, and error messages. All sensitive data is
redacted with "***" placeholders while preserving enough context for debugging.
"""

import atexit
import cmd
import datetime
import os
from pathlib import Path
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
from .utils import remote_viewer_cmd, sanitize_sensitive_data
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
from .pipeline import PipelineExecutor, PipelineMode


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
                        sanitized_line = sanitize_sensitive_data(line)
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
                    sanitized_buffer = sanitize_sensitive_data(self.buffer)
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

        # Initialize pipeline executor
        self.pipeline_executor = PipelineExecutor(self.vm_service, self)

        # Cache for performance optimization
        self._color_support = None  # Cache color support detection
        self._status_colors_cache = {}  # Cache colored status strings
        self._ansi_escape_regex = None  # Cache regex for ANSI codes

        # Auto-connect to servers
        for server in self.servers:
            if server.get("autoconnect", False):
                try:
                    self._safe_print(
                        f"Autoconnecting to {self._colorize(server['name'], server['name'])} ({self._sanitize_message(server['uri'])})..."
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
                    self._safe_print(f"Error autoconnecting to {server['name']}: {e}")

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
            "Pipelines": ["pipeline"],
            "Shell/Utils": ["bash", "history", "quit"],
        }
        try:
            readline.set_completion_display_matches_hook(self._display_completion_matches)
        except Exception:
            pass

        # Setup persistent command history
        self._setup_history()

        self._update_prompt()

    def emptyline(self):
        """Override emptyline to prevent repeating the last command on empty input."""
        return False

    def onecmd(self, line):
        """Override onecmd to prevent history/!/quit commands from being stored in readline history."""
        line_stripped = line.strip()

        if (
            line_stripped.startswith("history")
            or line_stripped.startswith("!")
            or line_stripped == "quit"
        ):
            try:
                history_length_before = readline.get_current_history_length()
            except:
                history_length_before = 0

            result = super().onecmd(line)

            try:
                history_length_after = readline.get_current_history_length()
                if history_length_after > history_length_before:
                    readline.remove_history_item(history_length_after - 1)
            except:
                pass

            return result
        else:
            return super().onecmd(line)

    def _setup_history(self):
        """Setup persistent command history using readline."""
        try:
            cache_dir = Path.home() / ".cache" / AppInfo.name
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.history_file = cache_dir / "vmanager_cmd_history.log"
            readline.set_history_length(1000)  # Keep last 1000 commands
            if self.history_file.exists():
                try:
                    readline.read_history_file(str(self.history_file))
                except (IOError, OSError) as e:
                    print(f"Warning: Could not load command history: {e}")

            atexit.register(self._save_history)

        except Exception as e:
            print(f"Warning: Could not setup command history: {e}")
            self.history_file = None

    def _save_history(self):
        """Save command history to file with sanitization."""
        if not hasattr(self, "history_file") or self.history_file is None:
            return

        try:
            history_length = readline.get_current_history_length()
            if history_length == 0:
                return

            sanitized_history = []
            for i in range(1, history_length + 1):
                try:
                    cmd = readline.get_history_item(i)
                    if cmd:
                        cmd_stripped = cmd.strip()
                        if (
                            cmd_stripped.startswith("!")
                            or cmd_stripped.startswith("history")
                            or cmd_stripped == "quit"
                        ):
                            continue

                        sanitized_cmd = sanitize_sensitive_data(cmd)
                        sanitized_history.append(sanitized_cmd)
                except:
                    continue

            if sanitized_history:
                if self.history_file.exists():
                    backup_file = self.history_file.with_suffix(".log.bak")
                    try:
                        shutil.copy2(self.history_file, backup_file)
                    except:
                        pass

                with open(self.history_file, "w", encoding="utf-8") as f:
                    for cmd in sanitized_history:
                        f.write(f"{cmd}\n")

        except Exception as e:
            pass

    def _sanitize_message(self, message: str) -> str:
        """
        Comprehensive sanitization of sensitive information for CLI output.

        Uses the shared sanitization function from utils.py for consistent
        security across all modules.

        Args:
            message: The message to sanitize

        Returns:
            Sanitized message with sensitive data replaced by safe placeholders
        """
        return sanitize_sensitive_data(message)

    def _safe_print(self, message: str) -> None:
        """
        Safely print a message with sanitization applied.

        Args:
            message: The message to print
        """
        print(self._sanitize_message(message))

    def _colorize(self, text, server_name, for_prompt=False):
        """Wraps text in ANSI escape codes for the server's assigned color.

        Args:
            text: The text to colorize
            server_name: The server name to look up color for
            for_prompt: If True, wrap ANSI codes in readline ignore markers (\\001 and \\002)
                       to prevent cursor positioning issues.
        """
        color = self.server_colors.get(server_name)
        if not color:
            return text
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)

            prefix = f"\033[38;2;{r};{g};{b}m"
            suffix = "\033[0m"

            if for_prompt:
                prefix = f"\001{prefix}\002"
                suffix = f"\001{suffix}\002"

            return f"{prefix}{text}{suffix}"
        except (ValueError, IndexError):
            return text

    def _colorize_status(self, status_text):
        """Wraps status text in ANSI escape codes based on VM state."""
        # Check if terminal supports colors (cached)
        if not self._supports_colors():
            return status_text

        # Use cached colored status if available
        if status_text in self._status_colors_cache:
            return self._status_colors_cache[status_text]

        status_colors = {
            "Running": "\033[48;2;144;238;144m\033[30m",  # lightgreen background, black text
            "Stopped": "\033[48;2;255;0;0m\033[37m",  # red background, white text
            "Paused": "\033[48;2;255;255;0m\033[30m",  # yellow background, black text
            "Suspended": "\033[48;2;173;216;230m\033[30m",  # lightblue background, black text
            "Blocked": "\033[48;2;255;165;0m\033[30m",  # orange background, black text
            "No State": "\033[48;2;128;128;128m\033[37m",  # grey background, white text
            "Shutting Down": "\033[48;2;128;128;128m\033[37m",  # grey background, white text
            "Crashed": "\033[48;2;255;0;0m\033[37m",  # red background, white text
            "Unknown": "\033[48;2;128;128;128m\033[37m",  # grey background, white text
        }

        color_code = status_colors.get(status_text, "")
        if color_code:
            colored_text = f"{color_code}{status_text}\033[0m"
            # Cache the result
            self._status_colors_cache[status_text] = colored_text
            return colored_text

        # Cache uncolored text too
        self._status_colors_cache[status_text] = status_text
        return status_text

    def _supports_colors(self):
        """Check if the terminal supports ANSI colors (cached)."""
        if self._color_support is not None:
            return self._color_support

        import os

        # Check common environment variables that indicate color support
        term = os.environ.get("TERM", "")
        if "color" in term or term in ["xterm", "xterm-256color", "screen", "tmux"]:
            self._color_support = True
            return True
        # Check if stdout is a TTY
        try:
            self._color_support = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
            return self._color_support
        except (AttributeError, OSError):
            self._color_support = False
            return False

    def _get_display_width(self, text):
        """Calculate display width of text, ignoring ANSI escape codes (cached regex)."""
        import re

        # Cache the regex compilation
        if self._ansi_escape_regex is None:
            self._ansi_escape_regex = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        # Remove ANSI escape sequences to get the actual display width
        clean_text = self._ansi_escape_regex.sub("", text)
        return len(clean_text)

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
                sanitized_uri = self._sanitize_message(uri)
                self._safe_print(f"Connecting to virsh on {target_server} ({sanitized_uri})...")
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
                self._safe_print(f"Error getting URI for {target_server}: {e}")
            except Exception as e:
                self._safe_print(f"Error launching virsh: {e}")

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

    def do_history(self, args):
        """Display command history.
        Usage: history [number]
               history all
               history info

        Shows the last N commands from history. If no number is provided,
        shows the last 20 commands. Use 'history all' to show all commands.
        Use 'history info' to show information about the history file.

        Use !NUMBER to re-execute a command from history (e.g., !15)."""
        try:
            args_cleaned = args.strip().lower()
            if args_cleaned == "info":
                self._show_history_info()
                return

            history_length = readline.get_current_history_length()

            if not args.strip():
                num_to_show = min(20, history_length)
                start_idx = max(1, history_length - num_to_show + 1)
            elif args_cleaned == "all":
                num_to_show = history_length
                start_idx = 1
            else:
                try:
                    num_to_show = int(args.strip())
                    if num_to_show <= 0:
                        print("Error: Number must be positive.")
                        return
                    num_to_show = min(num_to_show, history_length)
                    start_idx = max(1, history_length - num_to_show + 1)
                except ValueError:
                    print(
                        "Error: Invalid number. Use 'history [number]', 'history all', or 'history info'."
                    )
                    return

            if history_length == 0:
                print("No command history available.")
                return

            print(f"Command history (showing last {num_to_show} commands):")
            print("-" * 50)

            for i in range(start_idx, history_length + 1):
                try:
                    cmd = readline.get_history_item(i)
                    if cmd:
                        sanitized_cmd = self._sanitize_message(cmd)
                        print(f"{i:4d}  {sanitized_cmd}")
                except:
                    continue

        except Exception as e:
            print(f"Error accessing command history: {e}")
            print("Note: Command history may not be available in all environments.")

    def _show_history_info(self):
        """Show information about the persistent history file."""
        if not hasattr(self, "history_file") or self.history_file is None:
            print("History file is not configured.")
            return

        print("Command History Information:")
        print(f"  History file: {self.history_file}")
        print(f"  File exists: {self.history_file.exists()}")

        if self.history_file.exists():
            try:
                stat = self.history_file.stat()
                print(f"  File size: {stat.st_size} bytes")
                print(f"  Last modified: {datetime.datetime.fromtimestamp(stat.st_mtime)}")

                with open(self.history_file, "r", encoding="utf-8") as f:
                    line_count = sum(1 for _ in f)
                print(f"  Commands stored: {line_count}")

            except Exception as e:
                print(f"  Error reading file info: {e}")

        try:
            current_length = readline.get_current_history_length()
            print(f"  Current session commands: {current_length}")
            print(f"  History limit: {readline.get_history_length()}")
        except:
            print("  Current session info not available")

    def complete_history(self, text, line, begidx, endidx):
        """Auto-completion for history command."""
        args = line.split()
        if len(args) == 2 and not line.endswith(" "):
            # Complete "all", "info", or common numbers
            options = ["all", "info", "10", "20", "50", "100"]
            return [opt for opt in options if opt.startswith(text)]
        return []

    def _set_title(self, title):
        """Sets the terminal window title."""
        print(f"\033]0;{title}\007", end="", flush=True)

    def _update_prompt(self):
        if self.active_connections:
            server_names = ",".join(
                [
                    self._colorize(name, name, for_prompt=True)
                    for name in self.active_connections.keys()
                ]
            )

            all_selected_vms = []
            for server_name, vms in self.selected_vms.items():
                for vm in vms:
                    all_selected_vms.append(
                        self._colorize(vm, server_name, for_prompt=True)
                    )

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
            print(self.prompt.replace("\001", "").replace("\002", ""), end="", flush=True)
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

        print(self.prompt.replace("\001", "").replace("\002", ""), end="", flush=True)
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
                    self._safe_print(f"A libvirt error occurred on server {server_name}: {e}")

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
                self._safe_print(
                    f"Connecting to {server_name} at {self._sanitize_message(server_info['uri'])}..."
                )
                conn = self.vm_service.connect(server_info["uri"])
                if conn:
                    self.active_connections[server_name] = conn
                    print(f"Successfully connected to '{server_name}'.")
                else:
                    print(f"Failed to connect to '{server_name}'.")
            except libvirt.libvirtError as e:
                self._safe_print(f"Error connecting to {server_name}: {e}")

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
                    self._safe_print(f"Error during disconnection from '{server_name}': {e}")
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
                        colored_status = self._colorize_status(status_str)

                        # Calculate proper spacing considering ANSI codes
                        name_width = self._get_display_width(colored_name)
                        name_padding = 40 - name_width
                        if name_padding < 0:
                            name_padding = 1

                        print(f"{colored_name}{' ' * name_padding}{colored_status}")
                else:
                    print("No VMs found on this server.")
            except libvirt.libvirtError as e:
                self._safe_print(f"Error listing VMs on {server_name}: {e}")

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
                self._safe_print(f"Could not fetch VMs from {server_name}: {e}")
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
                    colored_status = self._colorize_status(state_str)

                    # Calculate proper spacing considering ANSI codes
                    name_width = self._get_display_width(colored_name)
                    name_padding = 40 - name_width
                    if name_padding < 0:
                        name_padding = 1

                    status_width = self._get_display_width(colored_status)
                    status_padding = 15 - status_width
                    if status_padding < 0:
                        status_padding = 1

                    print(
                        f"{colored_name}{' ' * name_padding}{colored_status}{' ' * status_padding}{vcpus:<7} {mem_mib:<15}"
                    )
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
                        print(f"    - {network_name} (MAC: <redacted>, model: {model})")

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
                    self._safe_print(f"Error starting VM '{vm_name}': {e}")
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
                    self._safe_print(f"Error stopping VM '{vm_name}': {e}")

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
                self._safe_print(f"Error cloning VM '{current_new_name}': {e}")
            except Exception as e:
                self._safe_print(
                    f"An unexpected error occurred during cloning '{current_new_name}': {e}"
                )

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
            if arg == "pipeline":
                self._show_pipeline_help()
                return
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

        # Add pipeline help summary
        print(f"\n\033[1;32mPipeline Commands:\033[0m")
        print("  Use | to chain commands together:")
        print("    select re:web.* | stop | snapshot create backup | start")
        print("    pipeline --dry-run select vm1 vm2 | pause")
        print("  Type 'help pipelines' for detailed pipeline documentation.")

        # Add history help summary
        print(f"\n\033[1;32mHistory Commands:\033[0m")
        print("  Use history to view previous commands:")
        print("    history         # Show last 20 commands")
        print("    history 50      # Show last 50 commands")
        print("    history all     # Show all commands")
        print("    !15             # Re-execute command #15 from history")
        print("  Note: 'history', '!' and 'quit' commands are not stored in history")
        print()

    def _show_pipeline_help(self):
        """Show comprehensive pipeline help."""
        help_text = """
=== PIPELINE COMMANDS ===

Pipelines allow you to chain commands together using the pipe operator (|) 
to create powerful, efficient workflows.

BASIC SYNTAX:
  command1 | command2 | command3
  pipeline [OPTIONS] command1 | command2 | command3

PIPELINE OPTIONS:
  --dry-run         Show what would be done without executing
  --interactive, -i Ask for confirmation before execution

SUPPORTED COMMANDS IN PIPELINES:

VM Selection:
  select <vm1> [vm2...]     - Select specific VMs
  select re:<pattern>       - Select VMs matching regex pattern

VM Operations: 
  start                     - Start selected VMs
  stop                      - Gracefully shutdown selected VMs  
  force_off                 - Force shutdown selected VMs
  pause                     - Pause selected VMs
  resume                    - Resume paused/suspended VMs
  hibernate                 - Hibernate selected VMs

Snapshots:
  snapshot create <name> [description]  - Create snapshot
  snapshot delete <name>                - Delete snapshot
  snapshot revert <name>                - Revert to snapshot

Utilities:
  wait <seconds>            - Wait specified seconds
  view                      - Launch VM viewers
  info                      - Display VM information

VARIABLE EXPANSION:
  $(date) - Expands to current date/time (YYYYMMDD_HHMMSS)  

Examples:
  snapshot create backup-$(date)     → backup-20240216_143052

PIPELINE EXAMPLES:

Basic VM lifecycle:
  select re:web.* | stop | snapshot create backup-$(date) | start
  
Information and monitoring:
  select re:win* | info
  select vm1 vm2 | info | view
  
Maintenance workflow:
  select vm1 vm2 | stop | wait 5 | start | view
  
Safe shutdown with backup:
  pipeline -i select re:prod-.* | hibernate | snapshot create maintenance-$(date)
  
Dry-run testing:
  pipeline --dry-run select re:.* | stop | start
        """
        print(help_text)

    def do_quit(self, arg):
        """Exit the virtui-manager shell."""
        # Save command history before exiting
        self._save_history()

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
                        f"CPUs: {info.get('total_cpus')} ({info.get('nodes')} nodes,"
                        "{info.get('sockets')} sockets, {info.get('cores')} cores,"
                        "{info.get('threads')} threads)"
                    )
                    print(f"CPU Speed: {info.get('mhz')} MHz")
                    print(
                        f"Memory: {info.get('total_memory')} GiB total,"
                        "{info.get('free_memory')} MiB free"
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

    def default(self, line):
        """Handle unknown commands, pipeline syntax, or history execution."""
        line = line.strip()
        if not line:
            return

        # Check if this is a history execution command (!NUMBER)
        if line.startswith("!"):
            self._execute_history_command(line)
            return

        # Check if this is a pipeline command (contains |)
        if "|" in line:
            self.handle_pipeline(line)
        else:
            # Unknown single command
            print(f"Unknown command: {line}")
            print(
                "Type 'help' for a list of available commands or 'help <command>'"
                "for specific command help."
            )

    def _execute_history_command(self, line):
        """Execute a command from history using !NUMBER syntax."""
        try:
            # Extract the number after !
            number_str = line[1:].strip()
            if not number_str:
                print("Error: Usage is !NUMBER (e.g., !15)")
                return

            try:
                history_number = int(number_str)
            except ValueError:
                print("Error: Invalid number. Usage is !NUMBER (e.g., !15)")
                return

            # Get the command from history
            try:
                history_cmd = readline.get_history_item(history_number)
                if not history_cmd:
                    print(f"Error: No command found at history position {history_number}")
                    return
            except:
                print(f"Error: Cannot access command at history position {history_number}")
                return

            # Don't re-execute other ! commands to avoid nested execution
            if history_cmd.startswith("!"):
                print(f"Cannot re-execute ! commands: {history_cmd}")
                return

            print(f"Executing from history [{history_number}]: {history_cmd}")

            # Execute the command by calling onecmd
            # This will properly handle the command and add it to history
            self.onecmd(history_cmd)

        except Exception as e:
            print(f"Error executing history command: {e}")

    def handle_pipeline(self, pipeline_str: str):
        """Handle pipeline commands."""
        pipeline_str = pipeline_str.strip()

        # Check for pipeline mode flags
        mode = PipelineMode.NORMAL
        if pipeline_str.startswith("--dry-run "):
            mode = PipelineMode.DRY_RUN
            pipeline_str = pipeline_str[10:]
        elif pipeline_str.startswith("--interactive ") or pipeline_str.startswith("-i "):
            mode = PipelineMode.INTERACTIVE
            pipeline_str = (
                pipeline_str[14:] if pipeline_str.startswith("--interactive") else pipeline_str[3:]
            )

        print(f"Executing pipeline: {pipeline_str}")
        if mode == PipelineMode.DRY_RUN:
            print("(DRY RUN MODE - No changes will be made)")

        # Validate pipeline first
        is_valid, errors = self.pipeline_executor.validate_pipeline(pipeline_str)
        if not is_valid:
            print("Pipeline validation failed:")
            for error in errors:
                print(f"  Error: {error}")
            return

        context = self.pipeline_executor.execute_pipeline(pipeline_str, mode)
        self._display_pipeline_results(context, mode)

        if context.selected_vms:
            self.selected_vms = dict(context.selected_vms)
            self._update_prompt()

    def _display_pipeline_results(self, context, mode: PipelineMode):
        """Display the results of a pipeline execution."""
        print()

        if mode == PipelineMode.DRY_RUN:
            dry_run_actions = context.metadata.get("dry_run_actions", [])
            if dry_run_actions:
                print("=== Actions that would be performed ===")
                for action in dry_run_actions:
                    print(f"  • {action}")
                print()

        if context.warnings:
            print("=== Warnings ===")
            for warning in context.warnings:
                print(f"  Warning: {warning}")
            print()

        if context.errors:
            print("=== Errors ===")
            for error in context.errors:
                print(f"  Error: {error}")
            print()

        if context.stage.value == "complete":
            success_counts = {
                k: v for k, v in context.metadata.items() if k.endswith("_success_count")
            }
            if success_counts:
                print("=== Pipeline Results ===")
                for operation, count in success_counts.items():
                    op_name = operation.replace("_success_count", "").replace("_", " ").title()
                    print(f"  {op_name}: {count} VM(s) processed successfully")

        if context.selected_vms:
            selected_count = sum(len(vms) for vms in context.selected_vms.values())
            print(f"Pipeline completed. {selected_count} VM(s) selected:")
            for server, vms in context.selected_vms.items():
                print(f"  {server}: {', '.join(vms)}")
        elif context.stage.value == "complete":
            print("Pipeline completed successfully.")

        if context.last_output:
            print(f"\nOutput: {context.last_output}")

        print()

    def do_pipeline(self, args):
        """Execute a command pipeline with special options.
        Usage: pipeline [--dry-run|--interactive|-i] <pipeline_commands>

        Examples:
          pipeline select re:web.* | stop | snapshot create backup-$(date) | start
          pipeline --dry-run select vm1 vm2 | pause | view
          pipeline -i list_vms running | hibernate
        """
        if not args:
            print("Usage: pipeline [--dry-run|--interactive|-i] <pipeline_commands>")
            print("\nExamples:")
            print("  pipeline select re:web.* | stop | snapshot create backup-$(date) | start")
            print("  pipeline --dry-run select vm1 vm2 | pause | view")
            print("  pipeline -i list_vms running | hibernate")
            return

        self.handle_pipeline(args)

    def complete_pipeline(self, text, line, begidx, endidx):
        """Auto-completion for pipeline commands."""
        words = line.split()

        if len(words) == 2 and not line.endswith(" "):
            # Completing first argument after "pipeline"
            options = ["--dry-run", "--interactive", "-i"]
            pipeline_commands = ["select"]

            completions = []
            if text.startswith("-"):
                completions = [opt for opt in options if opt.startswith(text)]
            else:
                completions = [cmd for cmd in pipeline_commands if cmd.startswith(text)]

            return completions

        pipeline_start_idx = 1
        for i, word in enumerate(words[1:], 1):
            if not word.startswith("-"):
                pipeline_start_idx = i
                break

        pipeline_words = words[pipeline_start_idx:]
        if not pipeline_words:
            return ["select"]

        pipeline_text = " ".join(pipeline_words)
        pipe_count = pipeline_text.count("|")

        if "|" in pipeline_text:
            current_segment = pipeline_text.split("|")[-1].strip()
        else:
            current_segment = pipeline_text.strip()

        current_words = current_segment.split()
        pipeline_commands = {
            "select": [],
            "start": [],
            "stop": [],
            "force_off": [],
            "pause": [],
            "resume": [],
            "hibernate": [],
            "snapshot": ["create", "delete", "revert"],
            "wait": [],
            "view": [],
            "info": [],
        }

        # If we're at the start of a segment or after a pipe
        if not current_words or (len(current_words) == 1 and not current_segment.endswith(" ")):
            command_text = current_words[0] if current_words else ""
            completions = [cmd for cmd in pipeline_commands.keys() if cmd.startswith(command_text)]

            return completions

        elif len(current_words) >= 1:
            command = current_words[0]

            if command == "select":
                return self.complete_select_vm(text, line, begidx, endidx)

            elif command == "snapshot":
                if len(current_words) == 2 and not current_segment.endswith(" "):
                    operations = ["create", "delete", "revert"]
                    return [op for op in operations if op.startswith(current_words[1])]
                elif len(current_words) >= 2 and current_words[1] in ["delete", "revert"]:
                    return []

            elif command == "wait":
                if len(current_words) == 2 and not current_segment.endswith(" "):
                    wait_times = ["1", "2", "5", "10", "30", "60"]
                    return [t for t in wait_times if t.startswith(current_words[1])]

        return []


def main():
    """Entry point for Virtui Manager command-line interface."""
    cmd_app = VManagerCMD()
    try:
        cmd_app.cmdloop()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt caught. Use 'quit' to exit.")
        cmd_app.intro = ""


if __name__ == "__main__":
    main()
