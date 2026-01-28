"""
Main Webconsole management functions
"""
import json
import logging
import os
import signal
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import partial
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse

import libvirt

from .constants import AppInfo
from .events import VmCardUpdateRequest
from .config import load_config, get_log_path
from .vm_queries import get_vm_graphics_info
from .modals.vmcard_dialog import WebConsoleDialog
from .utils import generate_webconsole_keys_if_needed
from .libvirt_utils import get_internal_id


class WebConsoleManager:
    """Manages websockify processes and SSH tunnels for web console access."""

    SESSION_FILE = Path.home() / ".config" / AppInfo.name / "console_sessions.json"

    # Optimized websockify wrapper to limit TCP buffers for high concurrency/slow connections
    _OPTIMIZED_WEBSOCKIFY_WRAPPER = """
import socket, sys
from websockify import websocketproxy as wp
class O(wp.ProxyRequestHandler):
    def do_proxy(self, t):
        buf_size = {buf_size}
        for s in [self.request, t]:
            try: s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buf_size)
            except: pass
            try: s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buf_size)
            except: pass
        super().do_proxy(t)
wp.ProxyRequestHandler = O
wp.websockify_init()
"""

    def __init__(self, app):
        self.app = app
        self.config = load_config()
        self._lock = RLock()
        self._ensure_session_file()

    def _ensure_session_file(self):
        if not self.SESSION_FILE.parent.exists():
            self.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not self.SESSION_FILE.exists():
            with open(self.SESSION_FILE, 'w') as f:
                json.dump({}, f)

    def load_sessions(self):
        with self._lock:
            try:
                with open(self.SESSION_FILE, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}

    def save_session(self, uuid, data):
        with self._lock:
            logging.info(f"[save_session] Saving session for {uuid}: {data}")
            sessions = self.load_sessions()
            sessions[uuid] = data
            with open(self.SESSION_FILE, 'w') as f:
                json.dump(sessions, f, indent=4)

    def remove_session(self, uuid):
        with self._lock:
            logging.info(f"[remove_session] Removing session for {uuid}")
            sessions = self.load_sessions()
            if uuid in sessions:
                del sessions[uuid]
                with open(self.SESSION_FILE, 'w') as f:
                    json.dump(sessions, f, indent=4)

    @staticmethod
    def is_remote_connection(uri: str) -> bool:
        """Determines if the connection URI is for a remote qemu+ssh host."""
        if not uri:
            return False
        parsed_uri = urlparse(uri)
        return (
            parsed_uri.hostname not in (None, "localhost", "127.0.0.1")
            and parsed_uri.scheme == "qemu+ssh"
        )


    def is_running(self, uuid: str) -> bool:
        """Check if a web console process is running for a given VM UUID using stored session."""
        sessions = self.load_sessions()
        if uuid not in sessions:
            return False

        session = sessions[uuid]
        pid = session.get('pid')
        if not pid:
            self.remove_session(uuid)
            return False

        # Check if process exists (for local process ID)
        try:
            os.kill(pid, 0)
        except OSError:
            # Process is dead
            self.remove_session(uuid)
            return False

        return True

    def start_console(self, vm, conn):
        """Starts a web console for a given VM."""
        self.config = load_config()
        logging.info(f"Web console requested for VM: {vm.name()}")
        uuid = get_internal_id(vm, conn)
        if '?' in uuid:
            uuid = uuid.split('?')[0]
        vm_name = vm.name()

        # Check for existing valid session
        if self.is_running(uuid):
            sessions = self.load_sessions()
            session = sessions[uuid]
            url = session.get('url')

            # Simple stop callback (manual stop)
            stopper_worker = partial(self.stop_console, uuid, vm_name)
            def on_dialog_dismiss(result):
                if result == "stop":
                    self.app.worker_manager.run(
                        stopper_worker, name=f"stop_console_{uuid}"
                    )

            self.app.call_from_thread(self.app.push_screen, WebConsoleDialog(url), on_dialog_dismiss)
            return

        # Start new session
        try:
            xml_content = vm.XMLDesc(0)
            root = ET.fromstring(xml_content)
            graphics_info = get_vm_graphics_info(root)

            if graphics_info.get('type') != 'vnc':
                self.app.call_from_thread(self.app.show_error_message, ErrorMessages.WEBCONSOLE_VNC_ONLY)
                return

            vnc_port = graphics_info.get('port')
            if not vnc_port or vnc_port == '-1':
                self.app.call_from_thread(self.app.show_error_message, ErrorMessages.COULD_NOT_DETERMINE_VNC_PORT)
                return

            is_remote_ssh = WebConsoleManager.is_remote_connection(conn.getURI())

            if is_remote_ssh and self.config.get('REMOTE_WEBCONSOLE', False):
                self._launch_remote_websockify(uuid, vm_name, conn, int(vnc_port), graphics_info)
            else:
                vnc_target_host, vnc_target_port, ssh_info = self._setup_ssh_tunnel(
                    uuid, conn, vm_name, int(vnc_port), graphics_info
                )
                if vnc_target_host and vnc_target_port:
                    self._launch_websockify(uuid, vm_name, vnc_target_host, vnc_target_port, ssh_info)

        except (libvirt.libvirtError, FileNotFoundError, Exception) as e:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.FAILED_TO_START_WEBCONSOLE_TEMPLATE.format(error=e))
            logging.error(f"Error during web console startup for VM {vm_name}: {e}", exc_info=True)

    def stop_console(self, uuid: str, vm_name: str):
        """Stops the websockify process and any associated SSH tunnel."""
        sessions = self.load_sessions()
        if uuid not in sessions:
            return

        session = sessions[uuid]
        pid = session.get('pid')
        ssh_info = session.get('ssh_info', {})

        # Kill local process (websockify or ssh tunnel leader)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                logging.info(f"Terminated local process {pid} for {vm_name}")
            except OSError as e:
                logging.warning(f"Failed to terminate process {pid} for {vm_name}: {e}")

        # Handle remote cleanup if needed
        remote_pid = ssh_info.get("remote_pid")
        remote_host = ssh_info.get("remote_host")
        ssh_port = ssh_info.get("ssh_port")
        control_socket = ssh_info.get("control_socket")

        if remote_pid and remote_host:
            try:
                logging.info(f"Killing remote websockify process {remote_pid} on {remote_host}")
                subprocess.run(
                    ["ssh", remote_host, f"kill {remote_pid}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
            except Exception as e:
                logging.warning(f"Failed to kill remote websockify {remote_pid} on {remote_host}: {e}")

        # Stop SSH tunnel if exists
        self._stop_ssh_tunnel(vm_name, ssh_info)

        self.remove_session(uuid)
        self.app.post_message(VmCardUpdateRequest(uuid))
        self.app.call_from_thread(self.app.show_success_message, SuccessMessages.WEBCONSOLE_STOPPED)

    def _get_next_available_port(self, remote_host: str | None = None) -> int | None:
        """
        Finds the next available port by checking active sessions and OS availability.

        Args:
            remote_host: "user@host" for remote connections, or None for local connections.

        Returns:
            int: The next available port number, or None if no port is available.
        """
        sessions = self.load_sessions()
        used_ports = set()

        # Identify ports already reserved by our sessions
        for session in sessions.values():
            stype = session.get('type')

            if remote_host:
                # For remote: only care about ports used on THAT specific remote host
                if stype == 'remote':
                    ssh_info = session.get('ssh_info', {})
                    if ssh_info.get('remote_host') == remote_host:
                        port = session.get('port')
                        if port:
                            used_ports.add(int(port))
            else:
                if stype == 'local':
                    port = session.get('port')
                    if port:
                        used_ports.add(int(port))

        start_port = int(self.app.WC_PORT_RANGE_START)
        end_port = int(self.app.WC_PORT_RANGE_END)

        for port in range(start_port, end_port + 1):
            if port in used_ports:
                continue

            if remote_host:
                # Remote: We can't easily check OS availability without an extra SSH call per port.
                return port
            else:
                # Local: Check OS availability
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.bind(('', port))
                        return port
                except OSError:
                    continue

        return None

    def _launch_remote_websockify(self, uuid: str, vm_name: str, conn, vnc_port: int, graphics_info: dict):
        """
        Launches websockify on the remote server via SSH and shows the console dialog.

        For secure WSS connections, this function checks for `cert.pem` and `key.pem` on the
        remote host in `~/.config/virtui-manager/` and `/etc/virtui-manager/keys/`.
        Refer to README.md for instructions on generating these files.
        """
        logging.info(f"Launching remote websockify for VM: {vm_name}")

        # Parse SSH connection details
        parsed_uri = urlparse(conn.getURI())
        user = parsed_uri.username
        host = parsed_uri.hostname
        ssh_port = parsed_uri.port or 22  # Get SSH port from URI
        remote_user_host = f"{user}@{host}" if user else host

        # Determine target VNC host on the remote server
        vnc_target_host = graphics_info.get('listen', '127.0.0.1')
        if vnc_target_host in ['0.0.0.0', '::']:
            vnc_target_host = '127.0.0.1'

        # Find a free port for websockify on the remote server.
        web_port = self._get_next_available_port(remote_user_host)
        if not web_port:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.NO_FREE_WEBCONSOLE_PORT)
            return

        remote_websockify_path = self.config.get('websockify_path', '/usr/bin/websockify')
        buf_size = self.config.get("WEBSOCKIFY_BUF_SIZE", 4096)

        # Assume remote config directory for certs
        remote_config_dir = "~/.config/" + AppInfo.name
        remote_cert_file = f"{remote_config_dir}/cert.pem"
        remote_key_file = f"{remote_config_dir}/key.pem"

        # Fallback system directory
        system_cert_dir = "/etc/" + AppInfo.name + "/keys"
        system_cert_file = f"{system_cert_dir}/cert.pem"
        system_key_file = f"{system_cert_dir}/key.pem"
        url_scheme = "http"

        remote_novnc_path = self.config.get("novnc_path") # Use config as Default

        # Initialize variable to avoid UnboundLocalError
        remote_websockify_cmd_list = [
            "python3", "-c", f"\"{self._OPTIMIZED_WEBSOCKIFY_WRAPPER.format(buf_size=buf_size)}\"",
            "--run-once", "--verbose", str(web_port),
            f"{vnc_target_host}:{vnc_port}", "--web", remote_novnc_path
        ]
        cert_status = ""

        remote_config_check_cmd = (
            f"if [ -f {remote_cert_file} ] && [ -f {remote_key_file} ]; then echo 'user_cert'; "
            f"elif [ -f {system_cert_file} ] && [ -f {system_key_file} ]; then echo 'system_cert'; "
            "else echo 'no_cert'; fi; "
            "if [ -d /usr/share/novnc ]; then echo '/usr/share/novnc'; "
            "elif [ -d /usr/share/webapps/novnc ]; then echo '/usr/share/webapps/novnc'; "
            "else echo '/usr/share/novnc'; fi"
        )

        try:
            check_result = subprocess.run(
                ["ssh", remote_user_host, remote_config_check_cmd],
                capture_output=True, text=True, check=True, timeout=5
            )
            stdout_lines = check_result.stdout.strip().split('\n')
            if stdout_lines:
                cert_status = stdout_lines[0]
                if "user_cert" in cert_status:
                    url_scheme = "https"
                    self.app.call_from_thread(self.app.show_success_message, SuccessMessages.REMOTE_USER_CERT_FOUND)
                    remote_websockify_cmd_list.extend(["--cert", remote_cert_file, "--key", remote_key_file])
                elif "system_cert" in cert_status:
                    url_scheme = "https"
                    self.app.call_from_thread(self.app.show_success_message, SuccessMessages.REMOTE_SYSTEM_CERT_FOUND)
                    remote_websockify_cmd_list.extend(["--cert", system_cert_file, "--key", system_key_file])
                else:
                    # Attempt to generate keys remotely
                    self.app.call_from_thread(self.app.show_success_message, SuccessMessages.NO_REMOTE_CERT_FOUND_GENERATING)
                    gen_msgs = generate_webconsole_keys_if_needed(config_dir="/etc/" + AppInfo.name + "/keys", remote_host=remote_user_host)

                    failed_gen = False
                    for level, msg in gen_msgs:
                        if level == 'error':
                            self.app.call_from_thread(self.app.show_error_message, msg)
                            failed_gen = True
                        else:
                            self.app.call_from_thread(self.app.show_success_message, msg)

                    if not failed_gen:
                        # Re-check or assume success and use the path
                        url_scheme = "https"
                        remote_websockify_cmd_list.extend(["--cert", system_cert_file, "--key", system_key_file])
                    else:
                        self.app.call_from_thread(self.app.show_error_message, ErrorMessages.REMOTE_KEY_GENERATION_FAILED)

                if len(stdout_lines) > 1:
                    remote_novnc_path = stdout_lines[1]
                    # Update the novnc path in the command list
                    # We need to find the '--web' argument index and update the next one
                    try:
                        web_arg_index = remote_websockify_cmd_list.index("--web")
                        if web_arg_index + 1 < len(remote_websockify_cmd_list):
                            remote_websockify_cmd_list[web_arg_index + 1] = remote_novnc_path
                    except ValueError:
                        pass 

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            logging.warning(f"Could not check for remote certs/novnc: {e}. Proceeding without SSL options and default novnc path.")
            self.app.call_from_thread(self.app.show_success_message, SuccessMessages.NO_REMOTE_CERT_CHECK_INSECURE)

        # Build SSH command with custom port if needed
        ssh_base_args = ["ssh"]
        if ssh_port != 22:
            ssh_base_args.extend(["-p", str(ssh_port)])
        ssh_base_args.append(remote_user_host)

        remote_websockify_cmd_str = " ".join(remote_websockify_cmd_list)

        # Wrap command to capture PID and redirect stderr to stdout
        # "exec" replaces the shell, so $$ is the shell's PID which becomes websockify's PID
        remote_cmd = f"echo $$; exec {remote_websockify_cmd_str} 2>&1"

        ssh_command_list = ssh_base_args[:-1] + [
            remote_user_host,
            remote_cmd
        ]

        logging.info(f"Executing remote websockify command: {remote_cmd}")

        # Start detached process
        proc = subprocess.Popen(
            ssh_command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            start_new_session=True,  # Detach from parent
            close_fds=True
        )

        # Read the first line to get the remote PID
        remote_pid = None
        try:
            pid_line = proc.stdout.readline()
            if pid_line:
                try:
                    remote_pid = int(pid_line.strip())
                    logging.info(f"Remote websockify PID: {remote_pid}")
                except ValueError:
                    logging.warning(f"Failed to parse remote PID, got: {pid_line.strip()}")
            else:
                logging.warning("Failed to get remote PID: stdout is empty")
        except Exception as e:
            logging.error(f"Error reading remote PID: {e}")
        finally:
            # Close output stream to allow full detachment
            proc.stdout.close()

        if remote_pid is None:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.FAILED_TO_START_REMOTE_WEBSOCKIFY_TEMPLATE.format(vm_name=vm_name))
            try:
                proc.terminate()
            except:
                pass
            return

        # Check if the remote process is still alive after a short delay
        time.sleep(1)
        try:
            check_alive_cmd = ["ssh", remote_user_host, f"kill -0 {remote_pid}"]
            result = subprocess.run(check_alive_cmd, check=False, capture_output=True, timeout=5)
            if result.returncode != 0:
                # Process is not alive
                logging.error(f"Remote websockify process {remote_pid} on {remote_user_host} crashed after launch.")
                self.app.call_from_thread(self.app.show_error_message, ErrorMessages.REMOTE_WEBCONSOLE_CRASHED_TEMPLATE.format(vm_name=vm_name))
                # Clean up the local ssh process
                try:
                    proc.terminate()
                except:
                    pass
                return
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logging.warning(f"Failed to check remote process status for {remote_pid} on {remote_user_host}: {e}")
            # Can't verify, so we assume it's running and let it fail later if it's not.

        # Find a free LOCAL port for the SSH tunnel
        local_tunnel_port = self._get_next_available_port(None)
        if not local_tunnel_port:
            # Clean up remote websockify
            try:
                subprocess.run(
                    ssh_base_args + [f"kill {remote_pid}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
            except:
                pass
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.NO_FREE_LOCAL_PORT_FOR_SSH_TUNNEL)
            return

        # Create control socket for the tunnel
        raw_uuid = uuid.split('@')[0] if '@' in uuid else uuid
        socket_name = f"wc_tunnel_{raw_uuid}_{datetime.now().strftime('%Y%m%d%H%M%S')}.sock"
        control_socket = os.path.join("/tmp", socket_name)

        # SSH tunnel command: forward local_tunnel_port to remote web_port
        tunnel_cmd = ["ssh", "-M", "-S", control_socket, "-f", "-N"]
        if ssh_port != 22:
            tunnel_cmd.extend(["-p", str(ssh_port)])
        tunnel_cmd.extend([
            "-L", f"{local_tunnel_port}:127.0.0.1:{web_port}",
            remote_user_host
        ])

        try:
            subprocess.run(
                tunnel_cmd,
                check=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logging.info(f"SSH tunnel created: localhost:{local_tunnel_port} -> {remote_user_host}:{web_port}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            # Clean up remote websockify
            try:
                subprocess.run(
                    ssh_base_args + [f"kill {remote_pid}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
            except:
                pass
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.FAILED_TO_CREATE_SSH_TUNNEL_TEMPLATE.format(error=e))
            return



        quality = self.config.get('VNC_QUALITY', 0)
        compression = self.config.get('VNC_COMPRESSION', 9)
        # Use localhost with the LOCAL tunnel port
        url = f"{url_scheme}://localhost:{local_tunnel_port}/vnc.html?path=websockify&quality={quality}&compression={compression}"

        ssh_info = {"remote_pid": remote_pid, "remote_host": remote_user_host, "ssh_port": ssh_port, "control_socket": control_socket}

        # Save session
        session_data = {
            "pid": proc.pid,  # Local SSH PID
            "url": url,
            "ssh_info": ssh_info,
            "port": local_tunnel_port,
            "type": "remote"
        }
        self.save_session(uuid, session_data)
        self.app.post_message(VmCardUpdateRequest(uuid))

        stopper_worker = partial(self.stop_console, uuid, vm_name)
        def on_dialog_dismiss(result):
            if result == "stop":
                self.app.worker_manager.run(
                    stopper_worker, name=f"stop_console_{uuid}"
                )

        self.app.call_from_thread(
            self.app.push_screen,
            WebConsoleDialog(url),
            on_dialog_dismiss
        )

    def _setup_ssh_tunnel(self, uuid: str, conn, vm_name: str, vnc_port: int, graphics_info: dict) -> tuple[str | None, int | None, dict]:
        """Sets up an SSH tunnel for remote connections if needed."""
        is_remote_ssh = WebConsoleManager.is_remote_connection(conn.getURI())

        vnc_target_host = graphics_info.get('listen', '127.0.0.1')
        if vnc_target_host in ['0.0.0.0', '::']:
            vnc_target_host = '127.0.0.1'

        if not is_remote_ssh:
            return vnc_target_host, vnc_port, {}

        self.app.call_from_thread(self.app.show_success_message, SuccessMessages.REMOTE_CONNECTION_SSH_TUNNEL_SETUP)
        parsed_uri = urlparse(conn.getURI())
        user = parsed_uri.username
        host = parsed_uri.hostname
        remote_user_host = f"{user}@{host}" if user else host

        raw_uuid = uuid.split('@')[0]
        socket_name = f"vmanager_ssh_{raw_uuid}_{datetime.now().strftime('%Y%m%d%H%M%S')}.sock"
        control_socket = os.path.join("/tmp", socket_name)

        tunnel_port = self._get_next_available_port(None)
        if not tunnel_port:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.NO_FREE_LOCAL_PORT_FOR_SSH_TUNNEL)
            return None, None, {}


        ssh_cmd = [
            "ssh", "-M", "-S", control_socket, "-f", "-N",
            "-L", f"{tunnel_port}:{vnc_target_host}:{vnc_port}", remote_user_host
        ]

        try:
            # Detach SSH tunnel process
            subprocess.run(
                ssh_cmd,
                check=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logging.info(f"SSH tunnel created for VM {vm_name} via {control_socket}")
            return '127.0.0.1', tunnel_port, {"control_socket": control_socket}
        except FileNotFoundError:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.SSH_COMMAND_NOT_FOUND)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.FAILED_TO_CREATE_SSH_TUNNEL_GENERIC)
            logging.error(f"SSH tunnel command failed: {' '.join(ssh_cmd)}")

        return None, None, {}


    def _get_local_novnc_path(self) -> str:
        """Returns the local novnc path, checking fallbacks."""
        path = self.config.get("novnc_path")
        if path and os.path.exists(path):
            return path
        if os.path.exists("/usr/share/novnc"):
            return "/usr/share/novnc"
        if os.path.exists("/usr/share/webapps/novnc"):
            return "/usr/share/webapps/novnc"
        return "/usr/share/novnc" # Default fallback

    def _launch_websockify(self, uuid: str, vm_name: str, host: str, port: int, ssh_info: dict):
        """Launches the websockify process and shows the console dialog."""
        web_port = self._get_next_available_port(None)
        if not web_port:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.NO_FREE_WEBCONSOLE_PORT)
            return

        websockify_path = self.config.get('websockify_path', '/usr/bin/websockify')
        novnc_path = self._get_local_novnc_path()
        buf_size = self.config.get("WEBSOCKIFY_BUF_SIZE", 4096)

        #websockify_path, "--run-once", str(web_port),
        websockify_cmd = [
            "python3", "-c", self._OPTIMIZED_WEBSOCKIFY_WRAPPER.format(buf_size=buf_size),
            "--run-once", str(web_port),
            f"{host}:{port}", "--web", novnc_path
        ]

        config_dir = Path.home() / '.config' / AppInfo.name
        cert_file = config_dir / 'cert.pem'
        key_file = config_dir / 'key.pem'
        url_scheme = "http"

        log_file_path = get_log_path()
        with open(log_file_path, 'a') as log_file_handle:
            if cert_file.exists() and key_file.exists():
                websockify_cmd.extend(["--cert", str(cert_file), "--key", str(key_file)])
                url_scheme = "https"
                self.app.call_from_thread(self.app.show_success_message, SuccessMessages.LOCAL_CERT_FOUND)

            # Detach local process
            proc = subprocess.Popen(
                websockify_cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_file_handle,
                stdin=subprocess.DEVNULL,
                start_new_session=True, # Detach from parent
                close_fds=True
            )

            url = f"{url_scheme}://localhost:{web_port}/vnc.html?path=websockify"

            # Save session
            session_data = {
                "pid": proc.pid,
                "url": url,
                "ssh_info": ssh_info,
                "port": web_port,
                "type": "local"
            }
            self.save_session(uuid, session_data)
            self.app.post_message(VmCardUpdateRequest(uuid))

            stopper_worker = partial(self.stop_console, uuid, vm_name)
            def on_dialog_dismiss(result):
                if result == "stop":
                    self.app.worker_manager.run(
                        stopper_worker, name=f"stop_console_{uuid}"
                    )

            self.app.call_from_thread(
                self.app.push_screen,
                WebConsoleDialog(url),
                on_dialog_dismiss
            )

    def _stop_ssh_tunnel(self, vm_name: str, ssh_info: dict):
        """Stops the SSH tunnel using its control socket."""
        control_socket = ssh_info.get("control_socket")
        if not control_socket:
            return
        try:
            stop_cmd = ["ssh", "-S", control_socket, "-O", "exit", "dummy-host"]
            subprocess.run(stop_cmd, check=True, timeout=5, capture_output=True)
            logging.info(f"SSH tunnel stopped for VM {vm_name} using socket {control_socket}")
        except FileNotFoundError:
            self.app.call_from_thread(self.app.show_error_message, ErrorMessages.SSH_COMMAND_NOT_FOUND)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logging.warning(f"Could not stop SSH tunnel cleanly for VM {vm_name}: {e.stderr.decode() if e.stderr else e}")
        finally:
            if os.path.exists(control_socket):
                os.remove(control_socket)
