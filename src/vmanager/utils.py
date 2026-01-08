"""
Utils functions
"""
import logging
from functools import wraps, lru_cache
import socket
import subprocess
from pathlib import Path
import shutil
import os
import re
from typing import List, Tuple, Union
from urllib.parse import urlparse
from constants import AppInfo


def find_free_port(start: int, end: int) -> int:
    """
    Find a free port in the specified range.

    Args:
        start (int): Starting port number
        end (int): Ending port number

    Returns:
        int: A free port number

    Raises:
        IOError: If no free port is found in the range
        TypeError: If inputs are not integers
        ValueError: If start > end
    """
    # Input validation
    if not isinstance(start, int) or not isinstance(end, int):
        raise TypeError("Start and end must be integers")
    if start > end:
        raise ValueError("Start port must be less than or equal to end port")

    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise IOError(f"Could not find a free port in the range {start}-{end}")


def log_function_call(func) -> callable:
    """
    A decorator that logs the function call and its arguments.

    Args:
        func: The function to be decorated

    Returns:
        function: The wrapped function with logging

    Raises:
        TypeError: If func is not callable
    """
    if not callable(func):
        raise TypeError("func must be callable")

    @wraps(func)
    def wrapper(*args, **kwargs):
        logging.info(f"Calling {func.__name__} with args: {args}, kwargs: {kwargs}")
        try:
            result = func(*args, **kwargs)
            logging.info(f"{func.__name__} returned: {result}")
            return result
        except Exception as e:
            logging.error(f"Exception in {func.__name__}: {e}")
            raise
    return wrapper


def generate_webconsole_keys_if_needed(config_dir: Path = None, remote_host: str = None) -> List[Tuple[str, str]]:
    """
    Checks for WebConsole TLS key and certificate and generates them if not found.
    Can generate locally or on a remote host via SSH.

    Args:
        config_dir (Path, optional): Directory to check/generate keys in. Defaults to ~/.config/virtui-manager.
        remote_host (str, optional): SSH host string (user@host) for remote generation.

    Returns:
        list: A list of tuples containing (level, message) for display
    """
    messages = []
    if config_dir is None:
        config_dir = Path.home() / '.config' / AppInfo.name

    # If remote, we treat config_dir as a string path on the remote
    if remote_host:
        key_path = f"{config_dir}/key.pem"
        cert_path = f"{config_dir}/cert.pem"
    else:
        key_path = config_dir / 'key.pem'
        cert_path = config_dir / 'cert.pem'

    # Only proceed if required tools are available (local check only makes sense for local gen)
    if not remote_host and not (check_websockify() and check_novnc_path()):
        messages.append(('info', "WebConsole tools not available locally. Skipping key generation."))
        return messages

    # Check existence
    exists = False
    if remote_host:
        try:
            subprocess.run(
                ["ssh", remote_host, f"test -f {key_path} && test -f {cert_path}"],
                check=True, timeout=5
            )
            exists = True
        except:
            exists = False
    else:
        exists = key_path.exists() and cert_path.exists()

    if not exists:
        messages.append(('info', f"WebConsole TLS key/cert not found on {'remote' if remote_host else 'local'}. Generating..."))

        gen_cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:4096",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-sha256", "-days", "365", "-nodes",
            "-subj", "/CN=localhost"
        ]

        try:
            if remote_host:
                # Ensure directory exists first
                subprocess.run(["ssh", remote_host, f"mkdir -p {config_dir}"], check=True, timeout=5)
                # Run openssl remotely
                # We need to quote the command for ssh
                remote_cmd = " ".join(gen_cmd)
                subprocess.run(
                    ["ssh", remote_host, remote_cmd],
                    check=True, capture_output=True, text=True, timeout=30
                )
            else:
                config_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    gen_cmd,
                    check=True, capture_output=True, text=True, timeout=30
                )

            messages.append(('info', f"Successfully generated WebConsole TLS key and certificate in {config_dir} on {'remote' if remote_host else 'local'}."))

        except subprocess.TimeoutExpired:
            error_message = "Failed to generate WebConsole TLS key/cert: Operation timed out"
            messages.append(('error', error_message))
        except subprocess.CalledProcessError as e:
            error_message = f"Failed to generate WebConsole TLS key/cert: {e.stderr.strip() if e.stderr else str(e)}"
            messages.append(('error', error_message))
        except FileNotFoundError:
            messages.append(('error', "openssl command not found. Please install openssl."))
        except Exception as e:
            error_message = f"Unexpected error generating WebConsole keys: {str(e)}"
            messages.append(('error', error_message))

    return messages


def check_virt_viewer() -> bool:
    """
    Checks if virt-viewer is installed.

    Returns:
        bool: True if virt-viewer is installed, False otherwise

    Raises:
        Exception: For unexpected errors during check
    """
    try:
        return shutil.which("virt-viewer") is not None
    except Exception as e:
        logging.error(f"Error checking virt-viewer: {e}")
        return False


def check_firewalld() -> bool:
    """
    Checks if firewalld is installed.

    Returns:
        bool: True if firewalld is installed, False otherwise

    Raises:
        Exception: For unexpected errors during check
    """
    try:
        return shutil.which("firewalld") is not None
    except Exception as e:
        logging.error(f"Error checking firewalld: {e}")
        return False


def check_novnc_path() -> bool:
    """
    Check if novnc is available.

    Returns:
        bool: True if novnc path exists, False otherwise

    Raises:
        Exception: For unexpected errors during check
    """
    try:
        return os.path.exists("/usr/share/novnc") or os.path.exists("/usr/share/webapps/novnc")
    except Exception as e:
        logging.error(f"Error checking novnc path: {e}")
        return False


def check_websockify() -> bool:
    """
    Checks if websockify is installed.

    Returns:
        bool: True if websockify is installed, False otherwise

    Raises:
        Exception: For unexpected errors during check
    """
    try:
        return shutil.which("websockify") is not None
    except Exception as e:
        logging.error(f"Error checking websockify: {e}")
        return False


def check_is_firewalld_running() -> Union[str, bool]:
    """
    Check if firewalld is running.

    Returns:
        str or bool: 'active' if running, 'inactive' if stopped, False if not installed or error

    Raises:
        Exception: For unexpected errors during check
    """
    if not check_firewalld():
        return False

    try:
        result = subprocess.run(
            ["systemctl", "is-active", "firewalld"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False
    except subprocess.CalledProcessError:
        return False
    except Exception as e:
        logging.error(f"Error checking firewalld status: {e}")
        return False

@lru_cache(maxsize=128)
def extract_server_name_from_uri(server_name: str) -> str:
    """
    Extract server name from URI for display.

    Args:
        server_name (str): The connection URI

    Returns:
        str: Extracted server name for display

    Raises:
        TypeError: If server_name is not a string
    """
    # Input validation
    if not isinstance(server_name, str):
        raise TypeError("server_name must be a string")

    if not server_name:
        return "Unknown"

    if server_name == 'qemu:///system':
        return 'Local'

    try:
        parsed_uri = urlparse(server_name)
        # netloc contains user:pass@host:port
        netloc = parsed_uri.netloc
        if not netloc:
            # Fallback for simple names without a scheme
            return server_name

        # Strip user info if it exists
        if '@' in netloc:
            netloc = netloc.split('@', 1)[1]

        # Strip port if it exists
        if ':' in netloc:
            netloc = netloc.split(':', 1)[0]

        return netloc if netloc else "Unknown"

    except Exception:
        # Fallback to original logic if parsing fails for any reason
        if server_name.startswith('qemu+ssh://'):
            if '@' in server_name:
                server_display = server_name.split('@')[1].split('/')[0]
            else:
                server_display = server_name.split('://')[1].split('/')[0]
        elif server_name.startswith('qemu+tcp://') or server_name.startswith('qemu+tls://'):
            server_display = server_name.split('://')[1].split('/')[0]
        else:
            server_display = server_name

        return server_display if server_display else "Unknown"

@lru_cache(maxsize=128)
def natural_sort_key(text):
    """
    Convert a string into a list for natural sorting.
    'A10' becomes ['A', 10], 'A2' becomes ['A', 2]
    This ensures A2 comes before A10.
    """
    def tryint(s):
        try:
            return int(s)
        except ValueError:
            return s.lower()

    return [tryint(c) for c in re.split('([0-9]+)', text)]

@lru_cache(maxsize=128)
def format_server_names(server_uris: tuple[str]) -> str:
    """
    Format server URIs into display names.
    Takes tuple (immutable) for caching.
    """
    names = [extract_server_name_from_uri(uri) for uri in server_uris]
    return ", ".join(sorted(set(names)))

_server_color_cache = {}
_color_index = 0

@lru_cache(maxsize=64)
def get_server_color_cached(uri: str, palette: tuple) -> str:
    """
    Get consistent color for a server URI.
    Uses global cache to avoid instance issues.
    """
    global _server_color_cache, _color_index

    if uri not in _server_color_cache:
        color = palette[_color_index % len(palette)]
        _server_color_cache[uri] = color
        _color_index += 1

    return _server_color_cache[uri]
