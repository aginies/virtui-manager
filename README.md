# Virtui Manager

A powerful, text-based Terminal User Interface (TUI) application for managing QEMU/KVM virtual machines using the libvirt Python API. 

## Why Virtui Manager?

Managing virtual machines in a terminal environment has never been easier or more powerful. Virtui Manager bridges the gap between the simplicity of command-line tools and the rich functionality of GUI-based solutions, offering the best of both worlds for virtualization administrators.

### The Problem with Traditional Tools
- **Virt-manager** requires X11 forwarding, which is slow, resource-intensive, and often impossible in remote environments
- **GUI-based solutions** are heavy with X dependencies, making them unsuitable for headless servers or low-bandwidth connections
- **Command-line tools** lack the intuitive interface needed for complex VM management tasks
- **Cockpit Machine** is feature incomplete, and needs a lot of depencies. It is not multi hypervisor oriented

### Why Virtui Manager is Different
Virtui Manager solves these challenges with:
- **Lightweight Terminal Interface**: No X11 dependencies, works perfectly over SSH
- **Remote Management**: Efficient low-bandwidth control of remote libvirt servers
- **Rich Feature Set**: Advanced VM management capabilities in a simple, intuitive interface
- **Multi-server Support**: Manage VMs across multiple libvirt servers from a single interface
- **Performance Optimized**: Built-in caching reduces libvirt calls and improves responsiveness
- **Libvirt Event handler**: Only get update on event from libvirt
- **Migration Support**: Live and offline VM migration capabilities and custom migration
- **Bulk Operations**: Execute commands across multiple VMs at once (including configuration)
- **Web Console Access**: Integrated VNC support with novnc over ssh tunnel for remote server

## Who Is This For?

Virtui Manager is ideal for:
- **System Administrators** managing KVM virtualization environments
- **DevOps Engineers** requiring efficient VM management in CI/CD pipelines
- **Remote System Administrators** working in low-bandwidth environments
- **Cloud Operators** managing multiple hypervisor servers
- **IT Professionals** who prefer terminal-based tools for virtualization management

## Requirements

- **Recommended Minimal Terminal Size**: 34x92. **34x128** is the recommended Size
- **Remote Connection**: SSH access to libvirt server (ssh-agent recommended)
- **Python 3.7+**
- **libvirt** with Python bindings
- **Python Dependencies**: textual, pyaml, libvirt-python, markdown-it-py
- **Optional**: virt-viewer, novnc, websockify for enhanced functionality

## Installation

### Clone the Repository
```bash
git clone https://github.com/aginies/virtui-manager.git
cd virtui-manager
```

### openSUSE/SLE Zypper

```bash
zypper in libvirt-python python3-textual python3-pyaml python3-markdown-it-py
```

### Install Python Dependencies
```bash
pip install libvirt-python textual pyaml markdown-it-py
```

### Run the Application
```bash
cd src/vmanager
python3 vmanager.py
```

## Command-Line Interface

In addition to the main TUI application, `vmanager` provides a command-line interface (`vmanager_cmd.py`) for:
- Multi-server management
- Bulk VM operations
- Basic Storage management
- Advanced VM selection with regular expressions
- Tab autocompletion for enhanced usability

Launch the CLI with:
```bash
python3 vmanager_cmd.py
```
Or:
```bash
python3 vmanager.py --cmd
```

## Configuration

Virtui Manager uses a YAML configuration file for customization:
- **User-specific**: `~/.config/virtui-manager/config.yaml`
- **System-wide**: `/etc/virtui-manager/config.yaml`

The configuration file supports the following options:

### Server Configuration
- **servers**: List of libvirt server connections (default: `[{'name': 'Localhost', 'uri': 'qemu:///system'}]`)

### Web Console Settings
- **REMOTE_WEBCONSOLE**: Enable remote web console (default: `False`)
- **WC_PORT_RANGE_START**: Start port for websockify (default: 40000)
- **WC_PORT_RANGE_END**: End port for websockify (default: 40050)
- **websockify_path**: Path to the websockify binary (default: `/usr/bin/websockify`)
- **novnc_path**: Path to noVNC files (default: `/usr/share/novnc/`)
- **WEBSOCKIFY_BUF_SIZE**: Sets the send and receive buffer size for websockify connections, affecting network performance. (default: `4096`)

#### Secure Remote Web Console (WSS)

When `REMOTE_WEBCONSOLE` is enabled, Virtui Manager can use a secure WebSocket connection (`wss://`) if an SSL certificate is available on the remote server. This is highly recommended for security.

To enable secure connections:

1. **Install needed packages**

* [python websockify](https://pypi.org/project/websockify/)
* [novnc](https://novnc.com/info.html)


2.  **Generate a self-signed certificate and key on the remote server:**

    Log in to your remote libvirt server and run the following command. Replace `your.remote.host.com` with the server's actual hostname or IP address. This is important for the browser to trust the certificate.

    ```bash
    openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -sha256 -days 365 -nodes -subj "/CN=your.remote.host.com"
    ```

3.  **Place the generated files in the correct directory on the remote server:**

    `virtui-manager` will automatically detect `cert.pem` and `key.pem` but for remote server the file must be in: 

    -   **System-wide path**: `/etc/virtui-manager/keys/`
        ```bash
        sudo mkdir -p /etc/virtui-manager/keys/
        sudo mv cert.pem key.pem /etc/virtui-manager/keys/
        ```

If the certificate and key are found, `virtui-manager` will automatically start `websockify` with SSL/TLS encryption and use a `wss://` URL. If not, it will default to an unencrypted `ws://` connection.

### VNC Settings
- **VNC_QUALITY**: VNC quality setting (0-10, default: 0)
- **VNC_COMPRESSION**: VNC compression level (default: `9`)

### Performance & Behavior
- **STATS_INTERVAL**: Interval for updating VM info, Status, Statistics (CPU, Memory, I/O) in seconds

### Network & Sound Models

As there is no simple way to get **sound** and **network** model using libvirt API, the user can provides a list in his own configuration file. 

To get a list of model for a machine type you can use the **qemu** command line:
```bash
qemu-system-x86_64 -machine pc-q35-10.1 -audio  model=help
qemu-system-x86_64 -machine pc-q35-10.1 -net  model=help
```

Possible User config parameters:
- **network_models**: List of allowed network models (default: `['virtio', 'e1000', 'e1000e', 'rtl8139', 'ne2k_pci', 'pcnet']`)
- **sound_models**: List of allowed sound models (default: `['none', 'ich6', 'ich9', 'ac97', 'sb16', 'usb']`)

### Example Configuration
```yaml
CACHE_TTL: 300
ISO_DOWNLOAD_PATH: /home/isos
LOG_FILE_PATH: /home/aginies/.cache/virtui-manager/vm_manager.log
REMOTE_VIEWER: null
REMOTE_WEBCONSOLE: true
STATS_INTERVAL: 15
novnc_path: /usr/share/webapps/novnc/
websockify_path: /usr/bin/websockify
VNC_COMPRESSION: 9
VNC_QUALITY: 1
WC_PORT_RANGE_END: 40049
WC_PORT_RANGE_START: 40000
WEBSOCKIFY_BUF_SIZE: 4096
custom_ISO_repo:
- name: Alpine 3.23 x86_64
  uri: https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/x86_64/
- name: Slackware 16
  uri: https://mirrors.slackware.com/slackware/slackware-iso/slackware64-15.0-iso/
- name: Qubes R4 3.0
  uri: https://mirrors.edge.kernel.org/qubes/iso/
servers:
- autoconnect: false
  name: Localhost
  uri: qemu:///system
- autoconnect: false
  name: ryzen9
  uri: qemu+ssh://root@10.0.1.38/system
- autoconnect: false
  name: ryzen7
  uri: qemu+ssh://root@10.0.1.78/system
```

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md)

## AI Assist

AI assistance is used to improve coding efficiency by automating boilerplate, suggesting relevant code completions, and quickly detecting bugs.

## License

This project is licensed under the GPL3 License.

