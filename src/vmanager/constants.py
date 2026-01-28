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
    INFO_CACHE_TTL = 60
    XML_CACHE_TTL = 3600 # 1 hour
    DONT_DISPLAY_DISK_USAGE = 50

class AppInfo:
    """Define app data"""
    name = "virtui-manager"
    namecase = "VirtUI Manager"
    version = "1.1.6"

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
    PMSUSPENDED = "pmsuspended"
    BLOCKED = "blocked"
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
    SELECT_SERVER = "[b][#FFD700]S[/][/]elect Servers"
    MANAGE_SERVERS = "Servers [b][#FFD700]L[/][/]ist"
    SERVER_PREFERENCES = "Server Prefs"
    FILTER_VM = "[b][#FFD700]F[/][/]ilter VM"
    VIEW_LOG = "Log"
    BULK_CMD = "[b][#FFD700]B[/][/]ulk CMD"
    PATTERN_SELECT = "[b][#FFD700]P[/][/]attern Sel"
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
    COMPACT_VIEW = "Compact"


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
    PMSUSPENDED = "PMSuspended"
    BLOCKED = "Blocked"
    LOADING = "Loading"

class SparklineLabels:
    """Constants for sparkline labels"""
    DISK_RW = "Disk R/W {read:.2f}/{write:.2f} MB/s"
    NET_RX_TX = "Net Rx/Tx {rx:.2f}/{tx:.2f} MB/s"
    VCPU = "{cpu} VCPU"
    MEMORY_GB = "{mem} Gb"

class ErrorMessages:
    """Constants for error messages"""
    R_VIEWER_NOT_FOUND = "Remote viewer command not found. Please ensure it is installed."
    CANNOT_OPEN_DISPLAY = "Could not open display. Ensure you are in a graphical session."
    HARD_STOP_WARNING = "This is a hard stop, like unplugging the power cord."
    MIGRATION_LOCALHOST_NOT_SUPPORTED = "Migration from localhost (qemu:///system) is not supported.\nA full remote URI (e.g., qemu+ssh://user@host/system) is required."
    NO_DESTINATION_SERVERS = "No destination servers available."
    DIFFERENT_SOURCE_HOSTS = "Cannot migrate VMs from different source hosts at the same time."
    MIXED_VM_STATES = "Cannot migrate running/paused and stopped VMs at the same time."
    WEBSOCKIFY_NOT_FOUND = "websockify is not installed. 'Web Console' button will be disabled."
    NOVNC_NOT_FOUND = "novnc is not installed. 'Web Console' button will be disabled."
    FAILED_TO_OPEN_CONNECTION = "Failed to open connection to [b]{uri}[/b]"
    NOT_CONNECTED_TO_ANY_SERVER = "Not connected to any server."
    COULD_NOT_CONNECT_TO_SERVER = "Could not connect to {uri}"
    NO_ACTION_TYPE_BULK_MODAL = "No action type received from bulk action modal."
    VM_NOT_FOUND_FOR_EDITING = "Could not find any of the selected VMs for editing."
    VMS_MUST_BE_STOPPED_FOR_BULK_EDITING = "All VMs must be stopped for bulk editing. Running VMs: {running_vms}"
    COULD_NOT_LOAD_DETAILS_FOR_REFERENCE_VM = "Could not load details for reference VM."
    SERVER_DISCONNECTED_AUTOCONNECT_DISABLED = "Server(s) {names} disconnected and autoconnect disabled due to connection failures."
    NO_ACTIVE_SERVERS = "No active servers."
    NO_VMS_IN_CACHE = "No VMs found in cache. Try refreshing first."
    NO_VMS_SELECTED = "No VMs selected."
    ERROR_FETCHING_VM_DATA = "Error fetching VM data: {error}"
    ERROR_DURING_INITIAL_CACHE_LOADING = "Error during initial cache loading: {error}"
    ERROR_ON_VM_DURING_ACTION = "Error on VM [b]{vm_name}[/b] during '{action}': {error}"
    FATAL_ERROR_BULK_ACTION = "A fatal error occurred during bulk action: {error}"
    ERROR_FETCHING_VM_DATA = "Error fetching VM data: {error}"

class DialogMessages:
    """Constants for dialog messages"""
    DELETE_VM_CONFIRMATION = "Are you sure you want to delete '{name}'?"
    DELETE_SNAPSHOT_CONFIRMATION = "Are you sure you want to delete snapshot '{name}'?"
    DELETE_SNAPSHOTS_AND_RENAME = "VM has {count} snapshot(s). To rename, they must be deleted.\nDelete snapshots and continue?"
    EXPERIMENTAL = "Experimental Feature! Still contains bugs, fix in progress. You have been informed"


class QuickMessages:
    """Constants for quick messages"""
    REMOTE_VIEWER_SELECTED = "The remove viewer {viewer} has been selected."
    VM_DATA_LOADED = "VM data loaded. Displaying VMs..."
    FILTER_RUNNING_VMS = "Filter: Running VMs"
    FILTER_ALL_VMS = "Filter: All VMs"
    ALL_VMS_UNSELECTED = "All VMs unselected."
    CACHING_VM_STATE = "Caching VM state for: {vms_list}"


class SuccessMessages:
    """Constants for success messages"""
    REMOTE_VIEWER_SELECTED = "The remove viewer {viewer} has been selected."
    CONNECTED_TO_SERVER = "Connected to [b]{uri}[/b]"
    STATS_LOGGING_DISABLED = "Statistics logging and monitoring disabled."
    STATS_LOGGING_ENABLED = "Statistics logging and monitoring enabled (every 10s)."
    CONFIG_UPDATED = "Configuration updated."
    VMS_SELECTED_BY_PATTERN = "Selected {count} VMs matching pattern."


class ProgressMessages:
    """Constants for success messages"""
    CONNECTING_TO_SERVER = "Connecting to [b]{uri}[/b]..."
    CONFIG_UPDATED_REFRESHING_VM_LIST = "Configuration updated. Refreshing VM list..."
    LOADING_VM_DATA_FROM_REMOTE_SERVERS = "Loading VM data from remote server(s)..."
