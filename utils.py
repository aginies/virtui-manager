"""
Utils functions
"""
import logging
from functools import wraps
import socket
from contextlib import closing
import subprocess
from pathlib import Path
import shutil
import os

def find_free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]

def log_function_call(func):
    """
    A decorator that logs the function call and its arguments.
    """
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

def generate_webconsole_keys_if_needed():
    """
    Checks for WebConsole TLS key and certificate and generates them if not found.
    Returns a list of messages to be displayed.
    """
    messages = []
    config_dir = Path.home() / '.config' / 'vmanager'
    key_path = config_dir / 'key.pem'
    cert_path = config_dir / 'cert.pem'

    if check_virt_viewer() and check_websockify() and check_novnc_path():
        if not key_path.exists() or not cert_path.exists():
            messages.append(('info', "WebConsole TLS key/cert not found. Generating..."))
            config_dir.mkdir(parents=True, exist_ok=True)
            command = [
                "openssl", "req", "-x509", "-newkey", "rsa:4096",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-sha256", "-days", "365", "-nodes",
                "-subj", "/CN=localhost"
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
                messages.append(('info', f"Successfully generated WebConsole TLS key and certificate in {config_dir}."))
            except subprocess.CalledProcessError as e:
                error_message = f"Failed to generate WebConsole TLS key/cert: {e.stderr}"
                messages.append(('error', error_message))
            except FileNotFoundError:
                messages.append(('error', "openssl command not found. Please install openssl."))

        return messages

def check_virt_viewer():
    """Checks if virt-viewer is installed."""
    return shutil.which("virt-viewer") is not None

def check_websockify():
    """Checks if websockify is installed."""
    return shutil.which("websockify") is not None

def check_novnc_path():
    """ Check novnc is available"""
    return os.path.exists("/usr/share/novnc") is not None
