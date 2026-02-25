"""
Manage the configuration of the tool
"""

import os
import copy
from pathlib import Path

import yaml

from .constants import AppInfo

#    'VMS_PER_PAGE': 4,
DEFAULT_CONFIG = {
    "STATS_INTERVAL": 5,
    "WC_PORT_RANGE_START": 40000,
    "WC_PORT_RANGE_END": 40050,
    "websockify_path": "/usr/bin/websockify",
    "novnc_path": "/usr/share/novnc/",
    "REMOTE_VIEWER": None,
    "REMOTE_WEBCONSOLE": False,
    "VNC_QUALITY": 0,
    "VNC_COMPRESSION": 9,
    "WEBSOCKIFY_BUF_SIZE": 4096,
    "network_models": ["virtio", "e1000", "e1000e", "rtl8139", "ne2k_pci", "pcnet"],
    "sound_models": ["none", "ich6", "ich9", "ac97", "sb16", "usb"],
    "servers": [
        {"name": "Localhost", "uri": "qemu:///system"},
    ],
    "custom_ISO_repo": [],
    "LOG_FILE_PATH": str(Path.home() / ".cache" / AppInfo.name / "vm_manager.log"),
    "LOG_LEVEL": "INFO",
    "ISO_DOWNLOAD_PATH": str(Path.home() / ".cache" / AppInfo.name / "isos"),
    "AUTO_YAST_PORT": 8000,
    "AUTO_INSTALL_PRE_FILL": {
        "root_password": "",
        "username": "",
        "user_password": "",
        "keyboard": "",
        "language": "",
    },
}


def get_log_path() -> Path:
    """
    Returns the path to the log file as specified in the configuration,
    ensuring its parent directory exists.
    """
    config = load_config()
    log_file_path_str = config.get("LOG_FILE_PATH", DEFAULT_CONFIG["LOG_FILE_PATH"])
    log_path = Path(log_file_path_str)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def get_config_paths():
    """Returns the potential paths for the config file."""
    return [
        Path.home() / ".config" / AppInfo.name / "config.yaml",
        Path("/etc") / AppInfo.name / "config.yaml",
    ]


def get_user_config_path():
    """Returns the path to the user's config file."""
    return get_config_paths()[0]


def load_config():
    """
    Loads the configuration from the first found config file.
    If no config file is found, returns the default configuration.
    Merges the loaded configuration with default values to ensure all keys are present.
    """
    config_paths = get_config_paths()
    config_path = None
    user_config = {}

    for path in config_paths:
        if path.exists():
            config_path = path
            break

    if config_path:
        with open(config_path, encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}

    # Start with default config and update with user's config
    config = copy.deepcopy(DEFAULT_CONFIG)
    if user_config:
        config.update(user_config)
        # If user sets a value to null in yaml, it becomes None. Revert to default.
        for key, value in config.items():
            if value is None and key in DEFAULT_CONFIG:
                config[key] = copy.deepcopy(DEFAULT_CONFIG[key])

    # Ensure 'servers' key exists and is a non-empty list
    if not isinstance(config.get("servers"), list) or not config.get("servers"):
        config["servers"] = copy.deepcopy(DEFAULT_CONFIG["servers"])

    return config


def save_config(config):
    """Saves the configuration to the user's config file."""
    config_path = get_config_paths()[0]  # Save to user's config
    os.makedirs(config_path.parent, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)


def get_user_templates_dir():
    """
    Get the user templates directory path.

    Returns:
        Path: Path to ~/.config/virtui-manager/templates/
    """
    templates_dir = Path.home() / ".config" / AppInfo.name / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    return templates_dir


def get_user_templates_dir_for_os(os_name: str):
    """
    Get the user templates directory for a specific OS/distribution.

    Args:
        os_name: Name of the OS/distribution (e.g., "openSUSE", "Windows", "Ubuntu")

    Returns:
        Path: Path to ~/.config/virtui-manager/templates/<os_name>/
    """
    base_dir = get_user_templates_dir()
    os_dir = base_dir / os_name
    os_dir.mkdir(parents=True, exist_ok=True)
    return os_dir
