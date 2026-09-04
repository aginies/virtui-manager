# Automated Installation Architecture

Automated installation system for multiple OS distributions. The system uses a pluggable architecture with OS-specific providers, template management, and an HTTP server for serving configuration files during installation.

## How Automated Installation Works

The automated installation process follows this flow:

1.  **Template Selection:** User selects an automation template (Kickstart, Preseed, AutoYaST, etc.) during VM creation.
2.  **Template Generation:** The `AutomationEngine` generates the appropriate automation configuration file based on the template and user-provided credentials.
3.  **HTTP Server:** An `AutoHTTPServer` starts in a background thread, serving the configuration file from a temporary directory.
4.  **Kernel Boot:** The VM is configured with a direct kernel boot that includes the automation URL as a kernel parameter.
5.  **Unattended Install:** The guest OS installer fetches the configuration from the HTTP server and performs the installation without user interaction.
6.  **Completion:** After installation, the VM reboots and the HTTP server is cleaned up automatically.

### HTTP Server Details

The `AutoHTTPServer` (in `auto_http_server.py`) is a lightweight HTTP server that:

*   Runs in a **daemon background thread**, non-blocking the main TUI.
*   Supports **auto-port selection** (port `0` lets the OS pick an available port).
*   Serves files from a specified directory using Python's `http.server`.
*   Logs to the application logger and suppresses noisy "Bad request" messages.
*   Supports **context manager** usage (`with AutoHTTPServer(...) as server:`).
*   Is tracked globally in `_ACTIVE_SERVERS` and cleaned up via `atexit`.
*   Provides `get_url(filename)` to construct HTTP URLs for served files.

For **remote connections** (`qemu+ssh://`), a `RemoteAutoHTTPServer` is used instead.
It pushes the automation files to the remote host via SSH and starts
`python3 -m http.server` there, so the VM reaches the automation files on the
hypervisor itself rather than on the machine running VirtUI Manager. It requires
`python3` on the remote host and exposes the same `start`/`stop`/`get_url` interface.

```python
# Example usage
with AutoHTTPServer(serve_dir, port=0) as server:
    url = server.get_url("kickstart-basic.cfg")
    # url = "http://localhost:8000/kickstart-basic.cfg"
```

## OS Provider Architecture

### Libosinfo Integration

VirtUI Manager integrates with `libosinfo` to discover and catalog supported operating systems:

*   **`LibosinfoManager`:** Scans the libosinfo database for OSes with valid media URLs.
*   **`OSType` enum:** Defines supported OS types: `LINUX`, `OPENSUSE`, `WINDOWS`, `UBUNTU`, `DEBIAN`, `FEDORA`, `ARCHLINUX`, `ALPINE`, `GENERIC`.
*   **`OSVersion` frozen dataclass:** Represents a specific OS version with `os_type`, `version_id`, `display_name`, `architecture`, `is_evaluation`, and `eol_date`.

### Provider Registry

The `ProviderRegistry` provides a unified interface for OS discovery:

*   **`get_supported_os_types()`:** Returns unique OS types from the catalog.
*   **`get_supported_versions(os_type)`:** Filters catalog by OS type.
*   **`get_all_supported_versions()`:** Groups catalog by OS type.
*   **`find_version(os_type, version_id)`:** Looks up a specific version.
*   **`is_supported(os_type)`:** Legacy check for backward compatibility.

## Template System

### Built-in Templates

VirtUI Manager includes 30+ built-in automation templates across multiple formats:

#### Fedora (Kickstart)

| Template | Description |
|----------|-------------|
| `kickstart-basic.cfg` | Basic Fedora server with essential packages |
| `kickstart-minimal.cfg` | Minimal Fedora installation with core packages |
| `kickstart-server.cfg` | Fedora Server product environment |
| `kickstart-desktop.cfg` | Fedora Workstation with GNOME desktop |
| `kickstart-development.cfg` | Development workstation with tools and libraries |

#### Ubuntu (Autoinstall / Preseed)

| Template | Description |
|----------|-------------|
| `autoinstall-basic.yaml` | Cloud-config autoinstall with openssh, curl, vim, htop |
| `autoinstall-minimal.yaml` | Minimal with only openssh-server |
| `autoinstall-desktop.yaml` | Desktop with ubuntu-desktop-minimal, LVM, GNOME |
| `preseed-basic.cfg` | Debian preseed with EFI+swap+root partition |
| `preseed-minimal.cfg` | Minimal preseed, only openssh-server |
| `preseed-desktop.cfg` | Desktop with LVM, GNOME, and productivity tools |

#### openSUSE/SLES (AutoYaST / Agama)

| Template | Description |
|----------|-------------|
| `autoyast-basic.xml` | Basic server with EFI, Btrfs, base patterns |
| `autoyast-minimal.xml` | Minimal install, only minimal_base pattern |
| `autoyast-server.xml` | Full server with file_server, Samba, NFS |
| `autoyast-desktop.xml` | Desktop with GNOME, Firefox, productivity tools |
| `autoyast-development.xml` | Development with gcc, cmake, Python, Node.js |
| `autoyast-server-sle.xml` | SLES with SCC registration, all modules |
| `agama-minimal.json` | Agama minimal with 512MB EFI + swap + root |
| `agama-basic.json` | Agama basic with base patterns |
| `agama-server.json` | Agama server with Samba, NFS, Firewalld |
| `agama-desktop.json` | Agama desktop with GNOME |
| `agama-development.json` | Agama development with build tools |
| `agama-server-sles.json` | Agama SLES with registration code |

#### Arch Linux (archinstall JSON)

| Template | Description |
|----------|-------------|
| `archinstall-basic.json` | Basic with systemd-boot/GRUB, openssh, git, vim |
| `archinstall-user-credentials.json` | Root and user password credentials |

#### Alpine Linux (answers.txt)

| Template | Description |
|----------|-------------|
| `alpine-answers-basic.txt` | Basic installation |
| `alpine-answers-gnome.txt` | With GNOME desktop |
| `alpine-answers-lxqt.txt` | With LXQt desktop |
| `alpine-answers-mate.txt` | With MATE desktop |
| `alpine-answers-plasma.txt` | With KDE Plasma desktop |
| `alpine-answers-sway.txt` | With Sway compositor |
| `alpine-answers-xfce.txt` | With Xfce desktop |

### User Templates

Users can create, edit, import, and export custom templates:

*   **Storage:** User templates are stored in `~/.config/virtui-manager/templates/` (global) or `~/.config/virtui-manager/templates/<os_name>/` (OS-specific).
*   **Import:** Templates can be imported from external files.
*   **Export:** Templates can be exported to files for sharing.
*   **Edit:** Templates can be edited in the system's default editor via tmux.
*   **Validation:** Templates are validated against the appropriate schema (JSON, YAML, XML, preseed).

### Template Manager

The `AutoYaSTTemplateManager` handles all template operations:

*   **Discovery:** `get_all_templates()`, `get_builtin_templates()`, `get_user_templates()`
*   **CRUD:** `save_template()`, `delete_template()`, `export_template()`, `import_template()`
*   **Validation:** `validate_content()` (JSON/YAML/Preseed/XML), `validate_template()`
*   **Editing:** `edit_template_in_tmux()`, `view_template_in_tmux()`

## Supported Automation Formats

### Kickstart (Fedora/RHEL/CentOS)

*   **Format:** `.cfg` files
*   **Kernel parameter:** `inst.ks=http://host:port/kickstart.cfg`
*   **Features:** Package selection, partitioning, user creation, network configuration, post-install scripts

### Preseed (Debian/Ubuntu)

*   **Format:** `.cfg` files
*   **Kernel parameter:** `auto=true preseed/url=http://host:port/preseed.cfg`
*   **Features:** Partitioning, package selection, user creation, mirror configuration

### AutoYaST (openSUSE/SLES)

*   **Format:** XML files
*   **Kernel parameter:** `autoyast=http://host:port/autoyast.xml`
*   **Features:** Pattern-based software selection, SCC registration, partitioning, network configuration

### Agama (openSUSE/SLES)

*   **Format:** JSON files
*   **Kernel parameter:** `inst.auto=http://host:port/agama.json inst.insecure=1`
*   **Features:** Modern web-based installer configuration, auto-partitioning

### Cloud-Init Autoinstall (Ubuntu)

*   **Format:** YAML files (`user-data`, `meta-data`)
*   **Kernel parameter:** `autoinstall ds=nocloud-net;s=http://host:port/`
*   **Features:** Cloud-init based automation, snap package management

### Archinstall (Arch Linux)

*   **Format:** JSON files with setup script
*   **Kernel parameter:** `script=http://host:port/archinstall-setup.sh`
*   **Features:** JSON-based configuration, systemd-boot/GRUB selection

### Alpine Answers (Alpine Linux)

*   **Format:** Text files
*   **Kernel parameter:** `alpine_repo=... apkovl=http://host:port/alpine-answers.apkovl.tar.gz`
*   **Features:** Interactive installer answer file, APK repository configuration

## Direct Kernel Boot

For certain distributions (Arch Linux, Debian, Ubuntu), VirtUI Manager supports direct kernel boot instead of ISO boot:

1.  **Kernel and Initrd:** The kernel and initrd images are downloaded from the distribution's release server.
2.  **Kernel Parameters:** Boot parameters are constructed based on the OS type and automation configuration.
3.  **HTTP Server:** The automation configuration is served via the HTTP server.
4.  **Boot Order:** The VM boots from the kernel/initrd with the automation URL as a kernel parameter.

### Kernel Parameter Examples

| Distribution | Kernel Parameter |
|-------------|-----------------|
| Fedora | `inst.ks=http://host:port/kickstart.cfg` |
| Ubuntu | `autoinstall ds=nocloud-net;s=http://host:port/` |
| Debian | `auto=true preseed/url=http://host:port/preseed.cfg` |
| Arch Linux | `script=http://host:port/archinstall-setup.sh ip=dhcp` |
| Alpine | `alpine_repo=http://dl-cdn.alpinelinux.org/alpine/v3.23/main apkovl=http://host:port/alpine.apkovl.tar.gz` |
| openSUSE | `autoyast=http://host:port/autoyast.xml netsetup=dhcp` |

## Auto-Installation Credentials

### Pre-fill Configuration

Default credentials can be pre-configured in `~/.config/virtui-manager/config.yaml`:

```yaml
AUTO_INSTALL_PRE_FILL:
  root_password: "your_secure_root_password"
  username: "your_preferred_username"
  user_password: "your_secure_user_password"
  keyboard: "us"
  language: "English (US)"
```

These values automatically populate the installation fields when a template is selected.

### Password Hashing

Passwords are hashed using a multi-strategy approach:

1.  **`mkpasswd`:** Tries the `mkpasswd` command first.
2.  **`openssl`:** Falls back to OpenSSL if `mkpasswd` is unavailable.
3.  **`crypt` module:** Uses Python's `crypt` module as a last resort.

The hashing algorithm is SHA-512 with salt.

### SCC Registration (SUSE Products)

For SUSE-based installations, SCC registration credentials can be pre-configured:

```yaml
SUSE_SCC:
  scc_email: "your-email@company.com"
  scc_reg_code: "your-scc-registration-code"
  scc_ha_reg_code: "your-scc-ha-registration-code"
  scc_hpc_reg_code: "your-scc-hpc-registration-code"
  scc_product_arch: "x86_64"
```

These credentials are automatically included in AutoYaST and Agama templates for SLES installations.

## Secure Boot and Firmware

### Secure Boot Per-OS Behavior

Secure Boot is **automatically disabled** for certain distributions during installation to prevent installation failures:

| OS Type | Secure Boot | Reason |
|---------|------------|--------|
| Arch Linux | Disabled | Installation media does not support Secure Boot |
| Debian | Disabled | Installation media does not support Secure Boot |
| Alpine | Disabled | Installation media does not support Secure Boot |
| Ubuntu | Enabled | Installation media supports Secure Boot |
| Fedora | Enabled | Installation media supports Secure Boot |
| openSUSE/SLES | Enabled | Installation media supports Secure Boot |
| Windows | Enabled | Required for Windows 11 |

### NVRAM Setup

UEFI NVRAM is automatically configured during provisioning:

1.  **Firmware Selection:** `select_best_firmware()` chooses the best firmware based on scoring (architecture, machine type, secure boot, NVRAM template availability).
2.  **NVRAM Template:** The NVRAM template is cloned from the firmware directory to a storage pool (prefers the `nvram` pool if available).
3.  **Format:** NVRAM is created in `raw` format to avoid conversion errors with libvirt nvram templates.

## Limitations

### Provider Directory Empty

The `provisioning/providers/` directory contains no Python source files (only `__pycache__/`). The provider functionality is handled by `ProviderRegistry` and `LibosinfoManager` instead.

### Limited OS Support

Not all distributions support all automation formats. The following table shows supported combinations:

| Distribution | Kickstart | Preseed | AutoYaST | Agama | Cloud-Init | archinstall | Alpine Answers |
|-------------|-----------|---------|----------|-------|------------|-------------|----------------|
| Fedora | Yes | No | No | No | No | No | No |
| Ubuntu | No | Yes | No | No | Yes | No | No |
| Debian | No | Yes | No | No | No | No | No |
| openSUSE | No | No | Yes | Yes | No | No | No |
| SLES | No | No | Yes | Yes | No | No | No |
| Arch Linux | No | No | No | No | No | Yes | No |
| Alpine | No | No | No | No | No | No | Yes |
| Windows | No | No | No | No | No | No | No |

### Auto-Install Enforces UEFI

Automated installation enforces UEFI firmware (BIOS is disabled). This is required for the kernel boot mechanism and automation URL delivery.

### Configure Before Install Disabled

When automated installation is enabled, "Configure before install" is disabled since the template handles all configuration.

### Arch Linux Hang Workaround

During Arch Linux automated installation, if the process hangs while "waiting for system to be online", open another console with `Alt+Ctrl+F2`, login as `root` (no password), and run: `systemctl start network-online`. Then return with `Alt+Ctrl+F1`.
