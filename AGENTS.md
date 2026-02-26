# Guidelines for AI Coding Agents

This document provides guidelines for AI coding agents (such as Claude, Copilot, Cursor, etc.) working on the Virtui Manager codebase.

## Critical Rules

### Do Not Commit
- **Never commit changes automatically.** All changes must be reviewed by a human before committing.
- **Never push to remote repositories.** Only humans should push code.
- **Never amend existing commits** without explicit human approval.

### Human Review Required
- All code changes require human review before being committed
- Explain your changes clearly so the human can understand and verify them
- If uncertain about an approach, ask for guidance rather than guessing

## Project Overview

Virtui Manager is a TUI (Terminal User Interface) for libvirt virtual machine management built with Python and Textual. It manages VMs across local and remote hypervisors.

## Technical Constraints

### Threading and Async
- **Never use `time.sleep()` or blocking I/O in the main thread** - this freezes the TUI
- **Use Textual workers** for long-running tasks (cloning, migration, provisioning)
- **Use `@work` decorator** for background operations
- **Use `app.call_from_thread()`** to update UI from worker threads

### Caching System
- The app uses metadata caching to prevent UI freezing
- **Invalidate cache** when modifying VM state: `vm_service.invalidate_vm_state_cache()`
- **Invalidate domain cache** after creating/deleting VMs: `vm_service.invalidate_domain_cache()`

### Text and Localization
- **All user-facing text must go in `src/vmanager/constants.py`**
- Never hardcode strings in Python code, only if technical strings (CPU, UEFI, etc...)
- Use the existing `StaticText`, `ButtonLabels`, `ErrorMessages`, `SuccessMessages` classes

### Security
- **Sanitize sensitive information** (passwords, URIs, secrets) in logs and error messages
- Never log passwords or credentials
- Use password inputs (`password=True`) for sensitive fields

## Code Style

### Imports
```python
# Standard library
import logging
import os

# Third-party
import libvirt
from textual import on, work
from textual.widgets import Button, Input

# Local
from ..constants import StaticText, ErrorMessages
from ..vm_service import VMService
```

### Error Handling
```python
try:
    result = some_operation()
except libvirt.libvirtError as e:
    logging.error(f"Libvirt error: {e}")
    self.app.show_error_message(ErrorMessages.SOME_ERROR_TEMPLATE.format(error=e))
except Exception as e:
    logging.error(f"Unexpected error: {e}")
    raise
```

### Worker Pattern
```python
@work(exclusive=True, thread=True)
def long_running_task(self, param):
    """Background task that doesn't block UI."""
    try:
        result = expensive_operation(param)
        self.app.call_from_thread(self._update_ui, result)
    except Exception as e:
        self.app.call_from_thread(self.app.show_error_message, str(e))
```

## Directory Structure

```
src/vmanager/
├── constants.py          # All text strings
├── config.py             # Configuration management
├── vm_service.py         # VM operations and caching
├── vm_provisioner.py     # VM creation/provisioning
├── modals/               # UI modal dialogs
├── provisioning/         # OS providers and templates
│   ├── providers/        # OS-specific providers
│   └── templates/        # AutoYaST and other templates
└── ...
```

## Testing Requirements

- Test against a live libvirt environment when possible
- Unit tests go in `tests/` directory
- Run tests with `pytest tests/`

## Checks scripts

- All checks scripts are in `checks` directory
- Run checks with `python3 checks` script name
- `remove_unused_constants.py` should never be run automatically

## Common Patterns

### Adding New Text
1. Add to `src/vmanager/constants.py`:
```python
class StaticText:
    MY_NEW_TEXT = "Some new text"
    MY_TEMPLATE = "Value is {value}"
```

2. Use in code:
```python
from .constants import StaticText
label = StaticText.MY_NEW_TEXT
formatted = StaticText.MY_TEMPLATE.format(value=42)
```

### Adding New Modal
1. Create in `src/vmanager/modals/`
2. Inherit from `BaseModal`
3. Use `@on` decorators for event handling
4. Use workers for long operations

### Modifying VM State
```python
# After any VM state change
self.app.vm_service.invalidate_vm_state_cache()
self.app.call_from_thread(self.app.on_vm_data_update)
```

## What NOT to Do

- Do not commit or push changes
- Do not use `time.sleep()` in main thread
- Do not hardcode user-facing strings
- Do not log sensitive information
- Do not make blocking calls in UI thread
- Do not modify `nix/` files without understanding Nix
- Do not create unnecessary files (especially markdown/documentation)
- Do not add emojis unless explicitly requested

## Asking for Help

If you need clarification on:
- Architecture decisions
- Which approach to take
- Whether a change is appropriate

**Ask the human** rather than making assumptions. The human maintainer has final say on all changes.
