import os
import yaml
from pathlib import Path

DEFAULT_CONFIG = {
    'VMS_PER_PAGE': 4,
    'servers': [
        {'name': 'Localhost', 'uri': 'qemu:///system'},
    ]
}

def get_config_paths():
    """Returns the potential paths for the config file."""
    return [
        Path.home() / '.config' / 'vmanager' / 'config.yaml',
        Path('/etc') / 'vmanager' / 'config.yaml'
    ]

def load_config():
    """
    Loads the configuration from the first found config file.
    If no config file is found, creates a default one.
    Ensures that the 'servers' list always contains the default 'Localhost' server
    if it's missing or empty in the loaded configuration.
    """
    config_paths = get_config_paths()
    config_path = None
    loaded_config = {}

    for path in config_paths:
        if path.exists():
            config_path = path
            break

    if config_path:
        with open(config_path, 'r') as f:
            loaded_config = yaml.safe_load(f) or {} # Ensure it's a dict even if file is empty
    else:
        # No config file found, return the DEFAULT_CONFIG directly
        return DEFAULT_CONFIG


    # Ensure 'servers' key exists and has default if empty or missing
    if not isinstance(loaded_config.get('servers'), list) or not loaded_config.get('servers'):
        loaded_config['servers'] = DEFAULT_CONFIG['servers']

    return loaded_config

def save_config(config):
    """Saves the configuration to the user's config file."""
    config_path = get_config_paths()[0]  # Save to user's config
    os.makedirs(config_path.parent, exist_ok=True)
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

