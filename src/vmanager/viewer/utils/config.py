"""
Configuration Manager

Handles loading and saving of viewer state/configuration.
"""

import json
import os
from typing import Dict, Any


class ConfigManager:
    """Manages persistent configuration and state for the remote viewer."""

    DEFAULT_CONFIG = {
        "fullscreen": False,
        "scaling": True,  # Enable by default for better user experience
        "smoothing": True,
        "lossy_encoding": False,
        "view_only": False,
        "vnc_depth": 0,
    }

    def __init__(self, verbose=False):
        """
        Initialize the configuration manager.

        Args:
            verbose: Whether to print verbose output
        """
        self.verbose = verbose
        self._config_path = None

    def get_config_path(self) -> str:
        """
        Get the path to the configuration file.

        Creates the config directory if it doesn't exist.

        Returns:
            Path to the config file
        """
        if self._config_path is None:
            config_dir = os.path.join(os.path.expanduser("~"), ".config", "virtui-manager")
            os.makedirs(config_dir, exist_ok=True)
            self._config_path = os.path.join(config_dir, "remote-viewer-state.json")

        return self._config_path

    def load_state(self) -> Dict[str, Any]:
        """
        Load saved state from configuration file.

        Returns:
            Dictionary containing configuration values, with defaults for missing keys
        """
        try:
            with open(self.get_config_path()) as f:
                data = json.load(f)
                # Merge with defaults to ensure all keys exist
                return {**self.DEFAULT_CONFIG, **data}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            if self.verbose:
                print(f"Could not load config (using defaults): {e}")
            return self.DEFAULT_CONFIG.copy()

    def save_state(self, state: Dict[str, Any]) -> bool:
        """
        Save state to configuration file.

        Args:
            state: Dictionary containing configuration values to save

        Returns:
            True if save was successful, False otherwise
        """
        try:
            # Only save keys that are in DEFAULT_CONFIG
            filtered_state = {k: state.get(k, v) for k, v in self.DEFAULT_CONFIG.items()}

            with open(self.get_config_path(), "w") as f:
                json.dump(filtered_state, f, indent=2)
            return True
        except Exception as e:
            if self.verbose:
                print(f"Failed to save state: {e}")
            return False


__all__ = ['ConfigManager']
