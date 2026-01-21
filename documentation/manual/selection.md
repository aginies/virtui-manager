# Selecting and Filtering VMs

Efficiently managing large fleets of Virtual Machines requires powerful selection tools. VirtUI Manager provides multiple ways to target specific VMs for bulk operations or focused management.

![Pattern Selection Interface](images/pattern.jpg)

## Manual Selection

### Card Selection
For quick, individual selections, you can interact directly with the VM cards in the main view.

*   **Click the 'X':** Located in the top-left corner of every VM card is a selection toggle (often marked with an `X` or a checkbox). Clicking this will add or remove the VM from the current selection.
*   **Visual Feedback:** Selected VMs are clearly highlighted, ensuring you know exactly which machines will be affected by subsequent commands.

### Keyboard Shortcuts
Speed up your workflow with global shortcuts:

*   **`Ctrl+A` (Select All):** Instantly selects **all** visible VMs across all connected servers.
*   **`Ctrl+U` (Unselect All):** Clears the current selection, deselecting every VM.

## Advanced Selection Tools

### Pattern Selection (`Pattern Sel`)
The **Pattern Selection** tool (accessible via the top menu or shortcut `p`) allows for precise targeting using text matching.

*   **Glob/Regex Support:** Enter a pattern to match against VM names (e.g., `web-*` for all web servers, `*test*` for test environments).
*   **Preview:** The interface displays a list of VMs that match your pattern in real-time before you confirm.
*   **Server Scope:** You can restrict the pattern match to specific servers (e.g., only select `web-*` VMs on `server-alpha`).

### Filter VM
The **Filter VM** feature (accessible via `f`) allows you to declutter your view.

*   **View Filtering:** Unlike selection (which marks VMs for action), filtering hides VMs that don't match your criteria from the main grid.
*   **Focus:** Use this to focus entirely on a specific subset of your infrastructure (e.g., only show "Running" VMs or VMs matching "database").
