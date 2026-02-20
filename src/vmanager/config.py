"""
Manage the configuration of the tool
"""

import os
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
    # User-defined AutoYaST templates
    "user_autoyast_templates": {},
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
    config = DEFAULT_CONFIG.copy()
    if user_config:
        config.update(user_config)
        # If user sets a value to null in yaml, it becomes None. Revert to default.
        for key, value in config.items():
            if value is None and key in DEFAULT_CONFIG:
                config[key] = DEFAULT_CONFIG[key]

    # Ensure 'servers' key exists and is a non-empty list
    if not isinstance(config.get("servers"), list) or not config.get("servers"):
        config["servers"] = DEFAULT_CONFIG["servers"]

    return config


def save_config(config):
    """Saves the configuration to the user's config file."""
    config_path = get_config_paths()[0]  # Save to user's config
    os.makedirs(config_path.parent, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)


def get_user_autoyast_templates():
    """Returns the user's custom AutoYaST templates."""
    config = load_config()
    return config.get("user_autoyast_templates", {})


def save_user_autoyast_template(template_id, template_name, template_content, description=""):
    """Saves a user-defined AutoYaST template to the configuration."""
    config = load_config()

    if "user_autoyast_templates" not in config:
        config["user_autoyast_templates"] = {}

    config["user_autoyast_templates"][template_id] = {
        "name": template_name,
        "description": description,
        "content": template_content,
        "created_at": str(os.path.getmtime(__file__)),  # Simple timestamp
    }

    save_config(config)


def delete_user_autoyast_template(template_id):
    """Deletes a user-defined AutoYaST template from the configuration."""
    config = load_config()

    if "user_autoyast_templates" in config and template_id in config["user_autoyast_templates"]:
        del config["user_autoyast_templates"][template_id]
        save_config(config)
        return True
    return False


def get_user_autoyast_template(template_id):
    """Gets a specific user-defined AutoYaST template."""
    templates = get_user_autoyast_templates()
    return templates.get(template_id)


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
