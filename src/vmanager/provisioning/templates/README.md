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

## Custom ISO Repositories

Users can add custom ISO repositories in their config.yaml file using this syntax:

```yaml
custom_ISO_repo:
  - name: Slackware 15
    uri: https://mirrors.slackware.com/slackware/slackware-iso/slackware64-15.0-iso/
  - name: Qubes R4 3.0
    uri: https://mirrors.edge.kernel.org/qubes/iso/
  - name: Windows
    uri: /mnt/install/ISO/win/
  - name: Local ISOs
    uri: file:///home/user/Downloads/isos/
```

### Repository Types Supported:
- **HTTP/HTTPS URLs**: Remote directory listings are scraped for .iso files
- **Local Paths**: Absolute paths to directories containing ISO files  
- **file:// URLs**: File protocol URLs to local directories

### Repository Features:
- Architecture filtering (x86_64, aarch64)
- Parallel detail fetching for remote repositories
- Last-Modified date extraction from HTTP headers
- Automatic SSL context handling for mirrors with certificate issues

## Usage

Templates are automatically loaded by their respective providers:
- OpenSUSEProvider loads `autoyast.xml`
- WindowsProvider loads `unattend.xml`

Custom repositories are processed by VMProvisioner.get_iso_list() which delegates to appropriate handling methods based on the URI scheme.