"""
Shared constants for the application.
"""

class ServerPallette:
    """ Color for server"""
    COLOR = [
        "#33FF57",  # Green
        "#F333FF",  # Magenta
        "#3357FF",  # Blue
        "#FF8C33",  # Orange
        "#FF33A1",  # Pink
        "#F3FF33",  # Yellow
        "#33FF8C",  # Mint
        "#FF5733",  # Red-Orange
        "#33FFF3",  # Cyan
        "#A133FF",  # Purple
        "#FF3333",  # Bright Red
        "#33FFDA",  # Aqua
        "#FFB833",  # Amber
        "#8C33FF",  # Violet
        "#33A1FF",  # Sky Blue
        "#33FFB8",  # Spearmint
        "#FFD133",  # Golden Yellow
        "#33FFA5",  # Light Green
        "#C70039",  # Deep Red
        "#00B8FF",  # Electric Blue
    ]

class AppCacheTimeout:
    """ All Cache timeout value"""
    CACHE_TTL = 30
    INFO_CACHE_TTL = 5
    DETAILS_CACHE_TTL = 300
    XML_CACHE_TTL = 600
    DONT_DISPLAY_DISK_USAGE = 50

class AppInfo:
    """Define app data"""
    name = "virtui-manager"
    namecase = "Virtui Manager"
    version = "0.7.0"

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

class ButtonLabels:
    """Constants for button labels"""
    START = "Start"
    SHUTDOWN = "Shutdown"
    FORCE_OFF = "Force Off"
    PAUSE = "Pause"
    RESUME = "Resume"
    CONFIGURE = "Configure"
    WEB_CONSOLE = "Web Console"
    CONNECT = "Connect"
    SNAPSHOT = "Snapshot"
    RESTORE_SNAPSHOT = "Restore Snapshot"
    DELETE_SNAPSHOT = "Del Snapshot"
    DELETE = "Delete"
    CLONE = "! Clone !"
    MIGRATION = "! Migration !"
    VIEW_XML = "View XML"
    RENAME = "Rename"
    SELECT_SERVER = "Select Servers"
    MANAGE_SERVERS = "Servers List"
    SERVER_PREFERENCES = "Server Prefs"
    FILTER_VM = "Filter VM"
    VIEW_LOG = "Log"
    BULK_CMD = "Bulk CMD"
    PATTERN_SELECT = "Pattern Sel"
    CONFIG = "Config"
    PREVIOUS_PAGE = "Previous Page"
    NEXT_PAGE = "Next Page"
    YES = "Yes"
    NO = "No"
    CANCEL = "Cancel"
    CREATE = "Create"
    CHANGE = "Change"
    STOP = "Stop"
    CLOSE = "Close"
    CREATE_OVERLAY = "New Overlay"
    COMMIT_DISK = "Commit Disk"
    DISCARD_OVERLAY = "Discard Overlay"
    SNAP_OVERLAY_HELP = "Help"

class ButtonIds:
    """Constants for button IDs"""
    START = "start"
    SHUTDOWN = "shutdown"
    STOP = "stop" # FORCE OFF
    PAUSE = "pause"
    RESUME = "resume"
    CONFIGURE_BUTTON = "configure-button"
    WEB_CONSOLE = "web_console"
    CONNECT = "connect"
    SNAPSHOT_TAKE = "snapshot_take"
    SNAPSHOT_RESTORE = "snapshot_restore"
    SNAPSHOT_DELETE = "snapshot_delete"
    CREATE_OVERLAY = "create_overlay"
    COMMIT_DISK = "commit_disk"
    DISCARD_OVERLAY = "discard_overlay"
    SNAP_OVERLAY_HELP = "snap_overlay_help"
    DELETE = "delete"
    CLONE = "clone"
    MIGRATION = "migration"
    XML = "xml"
    RENAME_BUTTON = "rename-button"
    SELECT_SERVER_BUTTON = "select_server_button"
    MANAGE_SERVERS_BUTTON = "manage_servers_button"
    SERVER_PREFERENCES_BUTTON = "server_preferences_button"
    FILTER_BUTTON = "filter_button"
    VIEW_LOG_BUTTON = "view_log_button"
    BULK_SELECTED_VMS = "bulk_selected_vms"
    PATTERN_SELECT_BUTTON = "pattern_select_button"
    CONFIG_BUTTON = "config_button"
    PREV_BUTTON = "prev-button"
    NEXT_BUTTON = "next-button"
    YES = "yes"
    NO = "no"
    CANCEL = "cancel"
    CREATE = "create"
    CHANGE = "change"
    CLOSE = "close"

class TabTitles:
    """Constants for tab titles"""
    MANAGE = "Manage"
    OTHER = "Other"
    SNAPSHOT = "Snapshot"
    SNAPSHOTS = "Snapshots"
    OVERLAY = "Overlay"
    SNAP_OVER_UPDATE = "Updating Data..."

class StatusText:
    """Constants for status text"""
    STOPPED = "Stopped"
    RUNNING = "Running"
    PAUSED = "Paused"
    LOADING = "Loading"

class SparklineLabels:
    """Constants for sparkline labels"""
    DISK_RW = "Disk R/W {read:.2f}/{write:.2f} MB/s"
    NET_RX_TX = "Net Rx/Tx {rx:.2f}/{tx:.2f} MB/s"
    VCPU = "{cpu} VCPU"
    MEMORY_GB = "{mem} Gb"

class ErrorMessages:
    """Constants for error messages"""
    VIRT_VIEWER_NOT_FOUND = "virt-viewer command not found. Please ensure it is installed."
    CANNOT_OPEN_DISPLAY = "Could not open display. Ensure you are in a graphical session."
    HARD_STOP_WARNING = "This is a hard stop, like unplugging the power cord."
    MIGRATION_LOCALHOST_NOT_SUPPORTED = "Migration from localhost (qemu:///system) is not supported.\nA full remote URI (e.g., qemu+ssh://user@host/system) is required."
    NO_DESTINATION_SERVERS = "No destination servers available."
    DIFFERENT_SOURCE_HOSTS = "Cannot migrate VMs from different source hosts at the same time."
    MIXED_VM_STATES = "Cannot migrate running/paused and stopped VMs at the same time."
    WEBSOCKIFY_NOT_FOUND = "websockify is not installed. 'Web Console' button will be disabled."
    NOVNC_NOT_FOUND = "novnc is not installed. 'Web Console' button will be disabled."

class DialogMessages:
    """Constants for dialog messages"""
    DELETE_VM_CONFIRMATION = "Are you sure you want to delete '{name}'?"
    DELETE_SNAPSHOT_CONFIRMATION = "Are you sure you want to delete snapshot '{name}'?"
    DELETE_SNAPSHOTS_AND_RENAME = "VM has {count} snapshot(s). To rename, they must be deleted.\nDelete snapshots and continue?"
    EXPERIMENTAL = "Experimental Feature! not yet fully tested!"
