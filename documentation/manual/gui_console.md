# VirtUI Manager GUI Console

The **VirtUI Manager GUI Console** is a GTK-based application that provides a modern, tabbed interface for managing your virtualization infrastructure. It embeds both the TUI interface and the CLI tool into a single, cohesive window.

To launch the GUI Console:
```bash
virtui-gui
```

## Features

### Tabbed Management
The GUI Console allows you to run multiple instances of VirtUI Manager or the CLI tool in separate tabs. This is perfect for:
*   Managing different sets of servers in separate tabs.
*   Keeping a CLI open alongside the main management window for quick `virsh` commands.
*   Monitoring logs or statistics in an independent tab.

### Keyboard Shortcuts
The GUI Console supports familiar shortcuts for navigating between tabs:
*   **`Ctrl + PageUp`**: Switch to the previous tab.
*   **`Ctrl + PageDown`**: Switch to the next tab.

### Dynamic Menu
The **Settings Menu** (represented by the "hamburger" icon) provides quick access to:
*   **Font Size**: Adjust the terminal font size on the fly for all tabs.
*   **Custom Font**: Select any monospace font installed on your system.
*   **New VManager Tab**: Open a new tab running the TUI interface.
*   **New Command Line Tab**: Open a new tab running the interactive CLI.

### Automatic Title Updates
Tabs automatically update their labels to reflect their current state:
*   **CLI Tabs**: Show the names of the currently connected servers.
*   **Virsh Shell**: Indicates when a `virsh` shell is active and which server it's connected to.

### System Integration
*   **Default Fonts**: Automatically uses your system's default monospace font settings.
*   **Scrollback**: Supports up to 10,000 lines of scrollback history per tab.
*   **Graceful Exit**: The application prevents accidental closure and will only quit when the last active tab is closed or when explicitly told to do so.

## Advantages over raw Terminal
While VirtUI Manager is designed to work in any terminal, the GUI Console offers several integrated benefits:
1.  **Zero Configuration**: No need to configure terminal multiplexers like `tmux` for basic tabbed usage (though `tmux` integration is still supported).
2.  **Visual Consistency**: Consistent font and styling regardless of your default terminal emulator settings.
3.  **Encapsulation**: Keeps your virtualization management tools in a dedicated window, separate from your other terminal work.
