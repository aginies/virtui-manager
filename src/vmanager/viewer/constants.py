"""
Constants for Remote Viewer

Centralized timeout and delay values to avoid magic numbers throughout the codebase.
All timeout values are in seconds, all delay values are in milliseconds.
"""

# ============================================================================
# SSH Tunnel Constants
# ============================================================================

# Time to wait for SSH tunnel verification before giving up (seconds)
SSH_TUNNEL_VERIFY_TIMEOUT = 5

# Time to wait for SSH process to gracefully terminate (seconds)
SSH_TUNNEL_GRACEFUL_SHUTDOWN_TIMEOUT = 5

# Time to wait for SSH process to die after SIGKILL (seconds)
SSH_TUNNEL_KILL_TIMEOUT = 2

# Delay before attempting to use SSH tunnel after starting (milliseconds)
SSH_TUNNEL_CONNECT_DELAY_MS = 500

# Interval for checking if SSH tunnel is ready (milliseconds)
TUNNEL_VERIFY_CHECK_INTERVAL_MS = 100


# ============================================================================
# UI Notification Constants
# ============================================================================

# Time to display notifications before auto-hiding (seconds)
NOTIFICATION_TIMEOUT_SECONDS = 5


# ============================================================================
# VM State Monitoring Constants
# ============================================================================

# Maximum time to wait for VM to start (seconds)
VM_WAIT_TIMEOUT_SECONDS = 300  # 5 minutes

# Interval for checking if VM has started (seconds)
VM_WAIT_CHECK_INTERVAL_SECONDS = 3

# Delay after VM start event before attempting display connection (milliseconds)
VM_START_CONNECT_DELAY_MS = 1000


# ============================================================================
# Display Connection Constants
# ============================================================================

# Delay before reconnecting after disconnect (milliseconds)
RECONNECT_DELAY_MS = 500


# ============================================================================
# Libvirt Event Processing Constants
# ============================================================================

# Interval for processing libvirt events (milliseconds)
LIBVIRT_EVENT_TICK_INTERVAL_MS = 100


# ============================================================================
# Exported Constants
# ============================================================================

__all__ = [
    'SSH_TUNNEL_VERIFY_TIMEOUT',
    'SSH_TUNNEL_GRACEFUL_SHUTDOWN_TIMEOUT',
    'SSH_TUNNEL_KILL_TIMEOUT',
    'SSH_TUNNEL_CONNECT_DELAY_MS',
    'TUNNEL_VERIFY_CHECK_INTERVAL_MS',
    'NOTIFICATION_TIMEOUT_SECONDS',
    'VM_WAIT_TIMEOUT_SECONDS',
    'VM_WAIT_CHECK_INTERVAL_SECONDS',
    'VM_START_CONNECT_DELAY_MS',
    'RECONNECT_DELAY_MS',
    'LIBVIRT_EVENT_TICK_INTERVAL_MS',
]
