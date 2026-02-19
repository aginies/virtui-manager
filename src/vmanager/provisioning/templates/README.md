# Provisioning Templates

This directory contains automation templates for OS providers.

## Template Files

- **autoyast.xml**: AutoYaST template for OpenSUSE automated installation
- **unattend.xml**: Unattend.xml template for Windows automated installation

## Template Variables

These templates use Python string formatting with the following variables:

### AutoYaST (OpenSUSE)
- `{language}`: System language (e.g., "en_US")
- `{keyboard}`: Keyboard layout (e.g., "us")
- `{timezone}`: System timezone (e.g., "UTC")
- `{root_password}`: Root password
- `{user_name}`: Regular user name
- `{user_password}`: Regular user password
- `{hostname}`: System hostname (derived from VM name)

### Unattend.xml (Windows)
- `{computer_name}`: Computer name (derived from VM name)
- `{admin_password}`: Administrator password
- `{auto_logon_enabled}`: Auto logon setting ("true" or "false")
- `{timezone}`: System timezone (e.g., "UTC")
- `{language}`: System language (e.g., "en-US")
- `{keyboard}`: Keyboard layout (e.g., "en-US")

## Usage

Templates are automatically loaded by their respective providers:
- OpenSUSEProvider loads `autoyast.xml`
- WindowsProvider loads `unattend.xml`

The providers handle variable substitution and generate the final automation files.