"""
Shared constants for the application.
"""

class AppInfo:
    """Define app data"""
    name = "virtui-manager"
    version = "0.5.0"

class VmAction:
    """Defines constants for VM action types."""
    START = "start"
    STOP = "stop"
    FORCE_OFF = "force_off"
    PAUSE = "pause"
    RESUME = "resume"
    DELETE = "delete"

class VmStatus:
    """Defines constants for VM status filters."""
    DEFAULT = "default"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    SELECTED = "selected"
