# Remote Viewer Architecture

The VirtUI Remote Viewer is a GTK3-based graphical application for viewing VM consoles via VNC and SPICE protocols. This chapter covers the internal architecture, component interactions, and design patterns used in the viewer implementation.

## Architecture Overview

The Remote Viewer is organized into a modular architecture with four main layers:

```
RemoteViewer (Application)
    ├── Managers
    │   ├── DisplayManager    - VNC/SPICE protocol handling
    │   ├── SSHTunnelManager  - SSH tunnel lifecycle
    │   ├── ConfigManager     - Settings persistence
    │   └── NotificationManager - User notifications
    ├── Handlers
    │   ├── PowerHandler      - VM power operations
    │   ├── ClipboardHandler  - Clipboard synchronization
    │   ├── DisplayHandler    - Display settings and UI
    │   └── VMStateHandler    - VM state monitoring
    └── UI Components
        ├── MainWindowBuilder - Window and widget construction
        ├── ConsoleTab        - Serial console
        ├── SnapshotTab       - Snapshot management
        ├── USBTab            - USB passthrough
        └── Menus             - Toolbar and context menus
```

### Key Design Principles

*   **Protocol abstraction:** VNC and SPICE are handled through a common `DisplayManager` interface.
*   **Event-driven:** GTK signal handlers and libvirt events drive state changes.
*   **Non-blocking:** SSH tunnel verification and display connections use GLib timeouts instead of blocking waits.
*   **State persistence:** Display settings, font, and fullscreen state are saved and restored automatically.

## Display Protocol Handling

### Protocol Detection

The `DisplayManager` detects the graphics protocol from the VM's libvirt XML:

1.  **SPICE:** Checks for `<graphics type='spice'>` in the XML. Requires `SpiceClientGtk` and `SpiceClientGLib` GTK libraries.
2.  **VNC:** Checks for `<graphics type='vnc'>` in the XML. Uses `GtkVnc` library.
3.  **Fallback:** If neither is found, a "No Display Available" placeholder is shown.

```python
# Protocol detection flow
xml_desc = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
root = ET.fromstring(xml_desc)

# Check SPICE first (if available)
spice_node = root.find(".//graphics[@type='spice']")
if spice_node is not None and SPICE_AVAILABLE:
    protocol = "spice"

# Check VNC
vnc_node = root.find(".//graphics[@type='vnc']")
if vnc_node is not None:
    protocol = "vnc"
```

### VNC Display Initialization

VNC displays are created using `GtkVnc.Display`:

```python
self.vnc_display = GtkVnc.Display()
self.vnc_display.set_pointer_local(True)
self.vnc_display.set_scaling(settings.scaling_enabled)
self.vnc_display.set_smoothing(settings.smoothing_enabled)
self.vnc_display.set_keep_aspect_ratio(True)
self.vnc_display.set_lossy_encoding(settings.lossy_encoding_enabled)
self.vnc_display.set_read_only(settings.view_only_enabled)
self.vnc_display.set_keyboard_grab(True)
self.vnc_display.set_pointer_grab(True)
```

**VNC-specific settings:**

| Setting | Method | Description |
|---------|--------|-------------|
| Scaling | `set_scaling(bool)` | Resize guest display to fit window |
| Smoothing | `set_smoothing(bool)` | Interpolation for scaled display |
| Lossy encoding | `set_lossy_encoding(bool)` | JPEG compression for bandwidth |
| Read-only | `set_read_only(bool)` | Disable keyboard/mouse input |
| Color depth | `set_depth(DisplayDepthColor)` | 8-bit, 16-bit, or 24-bit |

### SPICE Display Initialization

SPICE displays use the `SpiceClientGLib.Session` and `SpiceClientGtk.Display`:

```python
self.spice_session = SpiceClientGLib.Session()
GObject.Object.connect(self.spice_session, "channel-new", self.on_spice_channel_new)
self.spice_gtk_session = SpiceClientGtk.GtkSession.get(self.spice_session)
self.spice_gtk_session.set_property("auto-clipboard", True)
self.display_widget = SpiceClientGtk.Display(session=self.spice_session)
```

**SPICE-specific features:**

*   **Auto-clipboard:** Enabled by default via `SpiceClientGtk.GtkSession`.
*   **Keyboard grab:** Via `grab-keyboard` property.
*   **Channel events:** Monitored via `channel-event` signal for connection state.
*   **Agent status:** Monitored via `notify::agent-connected` signal.

### Protocol-Specific UI Visibility

Some UI elements are shown or hidden based on the active protocol:

| UI Element | VNC | SPICE |
|-----------|-----|-------|
| Color depth settings | Visible | Hidden |
| Lossy encoding toggle | Visible | Hidden |
| Clipboard push/pull | Via VNC signals | Via SPICE channels |
| Keyboard grab | Via `set_keyboard_grab` | Via `grab-keyboard` property |

## SSH Tunnel Architecture

### Tunnel Lifecycle

The `SSHTunnelManager` manages the complete SSH tunnel lifecycle:

```
Setup → Start → Verify → Active → Stop
```

1.  **Setup:** Parses the `qemu+ssh://` URI to extract gateway and port. Allocates a free local port.
2.  **Start:** Launches SSH with `-N -C` (no remote command, compression). Uses `BatchMode=yes` to fail immediately if password is needed.
3.  **Verify:** Non-blocking async verification using `GLib.timeout_add()` with `TUNNEL_VERIFY_CHECK_INTERVAL_MS` (100ms) intervals.
4.  **Active:** Tunnel is marked active when the local port is confirmed listening.
5.  **Stop:** Graceful termination (`SIGTERM`) followed by force kill (`SIGKILL`) if needed.

### SSH Command Construction

```bash
ssh -N -C \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=accept-new \
    -o ExitOnForwardFailure=yes \
    -L {local_port}:{remote_host}:{remote_port} \
    {gateway} \
    -p {gateway_port}
```

**SSH options explained:**

| Option | Purpose |
|--------|---------|
| `-N` | No remote command (tunnel only) |
| `-C` | Enable compression |
| `BatchMode=yes` | Fail immediately if password/passphrase needed |
| `ConnectTimeout=10` | 10-second connection timeout |
| `StrictHostKeyChecking=accept-new` | Accept new host keys, reject changed ones |
| `ExitOnForwardFailure=yes` | Exit if port forwarding fails |

### Tunnel Verification

Verification is non-blocking and uses GLib callbacks:

```python
def check_tunnel_ready() -> bool:
    # Check if process died
    if self.ssh_tunnel_process.poll() is not None:
        # Process exited - log error
        return False

    # Check if local port is listening
    try:
        with socket.socket() as s:
            s.settimeout(0.1)
            result = s.connect_ex(("localhost", self.ssh_tunnel_local_port))
            if result == 0:
                self.ssh_tunnel_active = True
                return False  # Stop checking
    except Exception:
        pass

    # Check timeout
    if elapsed >= timeout:
        self.ssh_tunnel_active = True  # Proceed anyway
        return False

    return True  # Continue checking

GLib.timeout_add(TUNNEL_VERIFY_CHECK_INTERVAL_MS, check_tunnel_ready)
```

### Tunnel Timeout Values

| Constant | Value | Purpose |
|----------|-------|---------|
| `SSH_TUNNEL_VERIFY_TIMEOUT` | 5 seconds | Max time for tunnel verification |
| `SSH_TUNNEL_GRACEFUL_SHUTDOWN_TIMEOUT` | 5 seconds | Wait for SIGTERM before SIGKILL |
| `SSH_TUNNEL_KILL_TIMEOUT` | 2 seconds | Wait for SIGKILL to take effect |
| `TUNNEL_VERIFY_CHECK_INTERVAL_MS` | 100 ms | Check interval |
| `SSH_TUNNEL_CONNECT_DELAY_MS` | 500 ms | Delay before connecting after tunnel start |

## Handler System

### PowerHandler

Handles VM power operations (start, stop, pause, resume, hibernate, reboot, force off):

*   **State sensitivity:** Each operation checks if the VM is in a valid state for the operation.
*   **Original UUID tracking:** Stores the original domain UUID to prevent operations on cloned/renamed domains.
*   **Display reconnection:** After power operations, triggers display reconnection via callback.

### ClipboardHandler

Manages clipboard synchronization between host and guest:

*   **VNC:** Uses `vnc-server-cut-text` signal from `GtkVnc.Display`.
*   **SPICE:** Uses `main-clipboard-selection-grab` and `main-clipboard-selection-data` signals from `SpiceClientGLib.MainChannel`.
*   **Host clipboard:** Monitors host clipboard owner-change events via `Gtk.Clipboard.connect("owner-change")`.
*   **Operations:** Type (host to guest), Push (host to guest), Pull (guest to host).

### DisplayHandler

Manages display settings and UI state:

*   **Settings:** Scaling, smoothing, lossy encoding, view-only mode, color depth.
*   **Fullscreen:** Toggle fullscreen via `fs_button` and `on_fs_button_toggled`.
*   **Screenshot:** Capture current display to file.
*   **Send keys:** Send special key combinations (Ctrl+Alt+Del, etc.).
*   **Boot device:** Change first boot device for next startup.

### VMStateHandler

Monitors VM state changes via libvirt events:

*   **Event registration:** Registers callbacks for domain lifecycle events (started, stopped, paused, resumed).
*   **Shutdown detection:** Checks if display disconnection is due to VM shutdown or network issue.
*   **Info bar:** Updates the info bar with state change notifications.
*   **Reconnect:** Triggers display reconnection after state changes.

## UI Components

### MainWindowBuilder

Constructs the main viewer window and all UI components:

*   **Window:** GTK `ApplicationWindow` with header bar.
*   **Toolbar:** Settings menu, boot menu, power menu, send keys, clipboard, screenshot, fullscreen, logs toggle.
*   **View container:** `Gtk.ScrolledWindow` containing the display widget.
*   **Notebook:** Tabbed interface with Console, Snapshots, USB, and Logs tabs.
*   **Info bar:** `Gtk.InfoBar` for notifications and status messages.

### ConsoleTab

Provides a serial console interface:

*   **Widget:** `Vte.Terminal` (GTK VT emulator).
*   **Connection:** Connects to the VM's serial port via `virsh console`.
*   **Help:** Built-in help for configuring guest OS serial console output.

### SnapshotTab

Manages VM snapshots:

*   **List:** Displays all snapshots with name, state, creation time, and description.
*   **Create:** Creates new snapshots with optional quiesce (requires guest agent).
*   **Restore:** Reverts to a selected snapshot (VM must be stopped).
*   **Delete:** Removes selected snapshots.

### USBTab

Manages USB device passthrough:

*   **Available devices:** Lists host USB devices available for passthrough.
*   **Attached devices:** Lists USB devices currently attached to the VM.
*   **Attach/Detach:** Buttons to move devices between available and attached lists.

### Menus

*   **Settings menu:** Display scaling, smoothing, lossy encoding, view-only, color depth, font size.
*   **Boot menu:** Select first boot device (hard disk, CD-ROM, network).
*   **Power menu:** Start, pause, resume, hibernate, shutdown, reboot, force off.
*   **Send keys menu:** Ctrl+Alt+Del, Ctrl+Alt+F1, and other key combinations.
*   **Clipboard menu:** Type, push, pull operations.

## Connection Management

### Connection Flow

```
1. get_display_info() - Extract protocol, host, port, password from XML
2. init_display() - Create VNC or SPICE display widget
3. connect() - Establish connection to display server
4. _connect_vnc() or _connect_spice() - Protocol-specific connection
5. Signal handlers - vnc-connected, vnc-disconnected, spice channel events
```

### Reconnection Logic

*   **VNC disconnect:** `on_vnc_disconnected` checks if `reconnect_pending` is set. If so, schedules reconnection after `RECONNECT_DELAY_MS` (500ms).
*   **SPICE channel close:** `on_spice_channel_event` handles `CLOSED` and error events.
*   **Auto-reconnect:** After snapshot restore, `_reconnect_display()` schedules reconnection via `GLib.timeout_add(500, ...)`.

### Password Handling

*   **XML password:** Extracted from `<graphics passwd='...'>` in VM XML.
*   **Pending password:** Stored in `self._pending_password` and used when the display requests credentials.
*   **Dialog prompt:** If no password is available, a GTK dialog prompts the user for the password.

## State Persistence

### Saved Settings

The `ConfigManager` saves and restores the following settings:

| Setting | Type | Default |
|---------|------|---------|
| `fullscreen` | bool | False |
| `scaling` | bool | True |
| `smoothing` | bool | True |
| `lossy_encoding` | bool | False |
| `view_only` | bool | False |
| `vnc_depth` | int | 0 (auto) |
| `width` | int | 1200 |
| `height` | int | 800 |
| `font_name` | str | System default |
| `font_size` | int | 12 |

### Save Trigger

State is saved automatically:

*   After display settings changes (scaling, smoothing, lossy encoding, view-only, depth).
*   After fullscreen toggle.
*   On application shutdown (`do_shutdown()` calls `_cleanup_resources()` which triggers save).

## Event Loop Integration

### Libvirt Event Processing

The viewer runs a libvirt event loop ticker:

```python
GLib.timeout_add(LIBVIRT_EVENT_TICK_INTERVAL_MS, self._libvirt_event_tick)

def _libvirt_event_tick(self):
    try:
        libvirt.virEventRunDefaultImpl()
    except Exception:
        pass
    return True  # Continue ticking
```

`LIBVIRT_EVENT_TICK_INTERVAL_MS` is 100ms. This ensures libvirt events (domain state changes, network events) are processed regularly.

### GLib Integration

All async operations use GLib timeout and idle callbacks instead of threading:

*   **Tunnel verification:** `GLib.timeout_add()` with check callback.
*   **Display reconnection:** `GLib.timeout_add()` after snapshot restore.
*   **SPICE delayed connection:** `GLib.timeout_add()` after SSH tunnel start.
*   **Notification auto-hide:** `GLib.timeout_add()` with `NOTIFICATION_TIMEOUT_SECONDS`.

## Constants Reference

All timeout and delay values are centralized in `viewer/constants.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `SSH_TUNNEL_VERIFY_TIMEOUT` | 5s | Tunnel verification timeout |
| `SSH_TUNNEL_GRACEFUL_SHUTDOWN_TIMEOUT` | 5s | SIGTERM wait time |
| `SSH_TUNNEL_KILL_TIMEOUT` | 2s | SIGKILL wait time |
| `SSH_TUNNEL_CONNECT_DELAY_MS` | 500ms | Delay after tunnel start |
| `TUNNEL_VERIFY_CHECK_INTERVAL_MS` | 100ms | Verification check interval |
| `NOTIFICATION_TIMEOUT_SECONDS` | 5s | Notification auto-hide |
| `VM_START_CONNECT_DELAY_MS` | 1000ms | Delay after VM start before connect |
| `RECONNECT_DELAY_MS` | 500ms | Reconnection delay |
| `LIBVIRT_EVENT_TICK_INTERVAL_MS` | 100ms | Event loop interval |

## Limitations

### SPICE Library Dependency

SPICE support requires `SpiceClientGtk` and `SpiceClientGLib` GTK libraries. If these are not installed, `SPICE_AVAILABLE` is `False` and the viewer falls back to VNC-only mode.

### VNC Depth Only for VNC

Color depth settings (8-bit, 16-bit, 24-bit) only apply to VNC displays. SPICE displays ignore this setting.

### Lossy Encoding Only for VNC

Lossy (JPEG) encoding only applies to VNC displays. SPICE displays handle compression internally.

### No Multi-Display Support

The viewer connects to a single display per VM session. Multiple simultaneous viewer sessions for the same VM are not supported.

### Clipboard Requires Guest Agent for SPICE

SPICE clipboard synchronization requires the SPICE guest agent to be running in the VM. Without the agent, clipboard operations may not work reliably.
