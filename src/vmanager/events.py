"""
Defines custom Message classes for the application.
"""
from textual.message import Message

class VMNameClicked(Message):
    """Posted when a VM's name is clicked."""

    def __init__(self, vm_name: str, vm_uuid: str, internal_id: str = None) -> None:
        super().__init__()
        self.vm_name = vm_name
        self.vm_uuid = vm_uuid
        self.internal_id = internal_id or vm_uuid

class VMSelectionChanged(Message):
    """Posted when a VM's selection state changes."""
    """Event emitted when a VM is selected or deselected. Uses internal_id (UUID@URI)."""
    def __init__(self, vm_uuid: str, is_selected: bool, internal_id: str = None) -> None:
        self.internal_id = internal_id or vm_uuid
        self.is_selected = is_selected
        super().__init__()

class VmActionRequest(Message):
    """Posted when a user requests an action on a VM (start, stop, etc.)."""

    def __init__(self, internal_id: str, action: str, delete_storage: bool = False) -> None:
        self.internal_id = internal_id
        self.action = action
        self.delete_storage = delete_storage
        super().__init__()

class VmCardUpdateRequest(Message):
    """Posted when a specific VM card needs to be updated."""

    def __init__(self, internal_id: str) -> None:
        self.internal_id = internal_id
        super().__init__()

class VMCardRemoved(Message):
    """Posted when a VM card needs to be removed from the UI."""
    def __init__(self, internal_id: str) -> None:
        self.internal_id = internal_id
        super().__init__()
