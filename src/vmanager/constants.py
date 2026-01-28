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
    VM_INFO_ERROR = "Error getting info for VM '{vm_name}': {error}"
    VM_NOT_FOUND_BY_ID = "Could not find VM with ID [b]{vm_id}[/b]"
    BULK_EDIT_PREP_ERROR = "Error preparing bulk edit: {error}"
    SERVER_CONNECTION_ERROR = "Server [b]{server_name}[/b]: {error_msg}"
    SERVER_FAILED_TO_CONNECT = "Failed to connect to [b]{server_name}[/b]: {error_msg}"
    BULK_ACTION_FAILED_TEMPLATE = "Bulk action [b]{action_type}[/b] failed for {count} VMs."
    PREFERENCES_LAUNCH_ERROR = "Error launching preferences: {error}"
    BULK_ACTION_VM_NAMES_RETRIEVAL_FAILED = "Could not retrieve names for selected VMs."
    VM_CLONE_FAILED_TEMPLATE = "Failed to clone to: {vm_names}"
    NO_SUITABLE_DISKS_FOR_OVERLAY = "No suitable disks found for overlay."
    OVERLAY_NAME_EMPTY_AFTER_SANITIZATION = "Overlay volume name cannot be empty after sanitization."
    ERROR_CREATING_OVERLAY_TEMPLATE = "Error creating overlay: {error}"
    ERROR_PREPARING_OVERLAY_CREATION_TEMPLATE = "Error preparing overlay creation: {error}"
    NO_OVERLAY_DISKS_FOUND = "No overlay disks found."
    ERROR_DISCARDING_OVERLAY_TEMPLATE = "Error discarding overlay: {error}"
    ERROR_PREPARING_DISCARD_OVERLAY_TEMPLATE = "Error preparing discard overlay: {error}"
    NO_DISKS_FOUND_TO_COMMIT = "No disks found to commit."
    ERROR_COMMITTING_DISK_TEMPLATE = "Error committing disk: {error}"
    ERROR_PREPARING_COMMIT_TEMPLATE = "Error preparing commit: {error}"
    INVALID_XML_TEMPLATE = "Invalid XML for '[b]{vm_name}[/b]': {error}. Your changes have been discarded."
    ERROR_GETTING_XML_TEMPLATE = "Error getting XML for VM [b]{vm_name}[/b]: {error}"
    UNEXPECTED_ERROR_OCCURRED_TEMPLATE = "An unexpected error occurred: {error}"
    CONNECTION_INFO_NOT_AVAILABLE = "Connection info not available for this VM."
    REMOTE_VIEWER_FAILED_TO_START_TEMPLATE = "{viewer} failed to start for {domain_name}: {error}"
    ERROR_GETTING_VM_DETAILS_TEMPLATE = "Error getting VM details for [b]{vm_name}[/b]: {error}"
    UNEXPECTED_ERROR_CONNECTING = "An unexpected error occurred while trying to connect."
    ERROR_CHECKING_WEB_CONSOLE_STATUS_TEMPLATE = "Error checking web console status for [b]{vm_name}[/b]: {error}"
    SNAPSHOT_ERROR_TEMPLATE = "Snapshot error for [b]{vm_name}[/b]: {error}"
    NO_SNAPSHOTS_TO_RESTORE = "No snapshots to restore."
    ERROR_FETCHING_SNAPSHOTS_TEMPLATE = "Error fetching snapshots: {error}"
    NO_SNAPSHOTS_TO_DELETE = "No snapshots to delete."
    ERROR_DELETING_VM_TEMPLATE = "Error deleting VM '{vm_name}': {error}"
    VM_NAME_EMPTY_AFTER_SANITIZATION = "VM name cannot be empty after sanitization."
    ERROR_RENAMING_VM_TEMPLATE = "Error renaming VM [b]{vm_name}[/b]: {error}"
    VM_NOT_FOUND_ON_ACTIVE_SERVER_TEMPLATE = "VM [b]{vm_name}[/b] with internal ID [b]{uuid}[/b] not found on any active server."
    ERROR_GETTING_ID_TEMPLATE = "Error getting ID for [b]{vm_name}[/b]: {error}"
    SELECT_AT_LEAST_TWO_SERVERS_FOR_MIGRATION = "Please select at least two servers in 'Select Servers' to enable migration."
    SELECTED_VM_NOT_FOUND_ON_ACTIVE_SERVER_TEMPLATE = "Selected VM with ID [b]{uuid}[/b] not found on any active server."


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


class WarningMessages:
    """Constants for warning messages"""
    COMPACT_VIEW_LOCKED = "Compact view is locked during bulk operations."
    VMS_PER_PAGE_PERFORMANCE_WARNING = "Displaying [b]{vms_per_page}[/b] VMs per page. CPU usage may increase; 9 is recommended for optimal performance."
    VM_NOT_PAUSABLE = "VM '{vm_name}' is not in a pausable state."
    LIBVIRT_XML_NO_EFFECTIVE_CHANGE = "VM [b]{vm_name}[/b]: Libvirt accepted the XML but the configuration remains unchanged. Your changes may have been ignored or normalized away."


class SuccessMessages:
    """Constants for success messages"""
    REMOTE_VIEWER_SELECTED = "The remove viewer {viewer} has been selected."
    CONNECTED_TO_SERVER = "Connected to [b]{uri}[/b]"
    STATS_LOGGING_DISABLED = "Statistics logging and monitoring disabled."
    STATS_LOGGING_ENABLED = "Statistics logging and monitoring enabled (every 10s)."
    CONFIG_UPDATED = "Configuration updated."
    VMS_SELECTED_BY_PATTERN = "Selected {count} VMs matching pattern."
    TERMINAL_COPY_HINT = "In some Terminal use [b]Shift[/b] key while selecting text with the mouse to copy it."
    NO_SERVERS_CONFIGURED = "No servers configured. Please add one via 'Servers List'."
    LOG_LEVEL_CHANGED = "Log level changed to {level}"
    BULK_ACTION_SUCCESS_TEMPLATE = "Bulk action [b]{action_type}[/b] successful for {count} VMs."
    SERVER_CONNECTED = "Connected to [b]{name}[/b]"
    INPUT_SANITIZED = "Input sanitized: '{original_input}' changed to '{sanitized_input}'"
    OVERLAY_CREATED = "Overlay [b]{overlay_name}[/b] created and attached."
    OVERLAY_DISCARDED = "Overlay for [b]{target_disk}[/b] discarded and reverted to base image."
    DISK_COMMITTED = "Disk changes committed successfully."
    VM_CONFIG_UPDATED = "VM [b]{vm_name}[/b] configuration updated successfully."
    NO_XML_CHANGES = "No changes made to the XML configuration."
    SNAPSHOT_CREATED = "Snapshot [b]{snapshot_name}[/b] created successfully."
    SNAPSHOT_RESTORED = "Restored to snapshot [b]{snapshot_name}[/b] successfully."
    SNAPSHOT_DELETED = "Snapshot [b]{snapshot_name}[/b] deleted successfully."
    VM_DELETED = "VM '{vm_name}' deleted successfully."
    VM_RENAMED = "VM '{old_name}' renamed to '{new_name}' successfully."
    VM_RENAME_CANCELLED = "VM rename cancelled."
    VM_RENAME_NO_CHANGE = "New VM name is the same as the old name. No rename performed."
    VM_CLONED = "Successfully cloned to: {vm_names}"



class ProgressMessages:
    """Constants for success messages"""
    CONNECTING_TO_SERVER = "Connecting to [b]{uri}[/b]..."
    CONFIG_UPDATED_REFRESHING_VM_LIST = "Configuration updated. Refreshing VM list..."
    LOADING_VM_DATA_FROM_REMOTE_SERVERS = "Loading VM data from remote server(s)..."
