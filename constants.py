"""
Shared constants for the application.
"""

class VmAction:
    """Defines constants for VM action types."""
    START = "start"
    STOP = "stop"
    FORCE_OFF = "force_off"
    PAUSE = "pause"
    DELETE = "delete"

class VmStatus:
    """Defines constants for VM status filters."""
    DEFAULT = "default"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    SELECTED = "selected"
