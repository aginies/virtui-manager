# Installation

## Prerequisites

VirtUI Manager is a Python-based application that leverages `libvirt` for virtualization management and `textual` for its terminal user interface.

### System Requirements

*   **Operating System:** Linux (tested on openSUSE, Fedora, Ubuntu, Arch)
*   **Python:** 3.7+
*   **Virtualization:** KVM/QEMU and Libvirt installed and running.
*   **Access:** Your user must have permissions to manage libvirt (usually part of the `libvirt` group).


## OpenSUSE / SLE Installation

VirtUI Manager is available as a package in the [Virtualization](https://build.opensuse.org/package/show/Virtualization/virtui-manager) repository. Choose the right repository, go to it and download packages and install them.

* Repository:
  * [15.6 repo](https://download.opensuse.org/repositories/Virtualization/15.6/noarch/)
  * [15.7 repo](hhttps://download.opensuse.org/repositories/Virtualization/15.7/noarch/)
  * [16 repo](https://download.opensuse.org/repositories/Virtualization/16.0/noarch/)
  * [Slowroll](https://download.opensuse.org/repositories/Virtualization/openSUSE_Slowroll/noarch/)
  * [Tumbleweed](https://download.opensuse.org/repositories/Virtualization/openSUSE_Tumbleweed/)
* Search for **virtui**
* Download the rpm packages: **virtui-manager**, **virtui-manager-doc**, **virtui-remote-viewer**
* install the packages with zypper.

```bash
sudo zypper in virtui-*.rpm
```

!!! note
    You can also add the repository but these means all the packages from this repository will be used later on update of the system, if you dont want that you need to remove it after the installation of the packages.

## Generic / Virtual Environment (Pip)

If your distribution doesn't package these libraries or you prefer a virtual environment:

```bash
pip3 install virtui-manager
```

Now **virtui-manager**, **virtui-manager-cmd**, **virtui-remote-cmd**, **virtui-gui** will be available from Command line.

## Flatpak installation

Flatpak doesn't accept anymore **console** application. Even this one is providing a **Terminal GTK** wrapper this app is not really a candidate for flathub.
As some user prefer container enviroment, github as been setup to build a flatpak app, so everything is built on [github](https://github.com/aginies/virtui-manager/actions/workflows/flatpak.yml). Download the flaptpak file, and install it on your system, for Version 1.6.1:

```bash
wget https://github.com/aginies/virtui-manager/releases/download/1.6.1/virtui-manager.flatpak
flatpak install virtui-manager.flatpak
```

To run it, use **flatpak run** or search for the **VirtUI Manager** app.
```bash
flatpak run io.github.aginies.virtui-manager
```

## Nix Package

This project includes comprehensive Nix package definitions for easy installation and development. The Nix files are located in the `nix/` directory.

### Prerequisites

Ensure you have Nix installed with flakes enabled. Add the following to your Nix configuration (`~/.config/nix/nix.conf` or `/etc/nix/nix.conf`):

```
experimental-features = nix-command flakes
```

### Quick Install (Flake)

Run VirtUI Manager directly without installing:

```bash
nix run github:aginies/virtui-manager
```

Or install it to your profile:

```bash
nix profile install github:aginies/virtui-manager
```

### Building Locally

Clone the repository and build:

```bash
git clone https://github.com/aginies/virtui-manager.git
cd virtui-manager/nix

# Build the package
nix build

# Run directly
nix run
```

### Traditional Nix (without flakes)

If you prefer not to use flakes:

```bash
cd virtui-manager/nix

# Build
nix-build default.nix

# Run the result
./result/bin/virtui-manager
```

### Development Shell

Enter a fully configured development environment with all dependencies, testing tools (pytest, pytest-cov, pytest-asyncio), and code quality tools (black, ruff, mypy):

**Using flakes:**
```bash
cd virtui-manager/nix
nix develop
```

**Without flakes:**
```bash
cd virtui-manager/nix
nix-shell shell.nix
```

Once in the development shell, you'll have access to:

| Command | Description |
|---------|-------------|
| `pytest tests/` | Run tests |
| `black src/` | Format code |
| `ruff check src/` | Lint code |
| `mypy src/` | Type check |
| `python -m pip install -e .` | Install in editable mode |

### NixOS Configuration

To add VirtUI Manager to your NixOS configuration:

```nix
# In your flake.nix inputs
inputs.virtui-manager.url = "github:aginies/virtui-manager";

# In your configuration
environment.systemPackages = [
  inputs.virtui-manager.packages.${pkgs.system}.default
];
```

### Home Manager

For Home Manager users:

```nix
# In your home.nix or flake
home.packages = [
  inputs.virtui-manager.packages.${pkgs.system}.default
];
```

### Package Details

The Nix package includes:

*   **Dependencies**: libvirt-python, textual, pyyaml, markdown-it-py
*   **Optional**: websockify (for webconsole support)
*   **Platforms**: Linux only
*   **License**: GPL-3.0+

## Installation Steps from Source Code

### Devel version: Clone the Repository

This is possible to test latest version from github

#### Get the latest source code from GitHub:

```bash
git clone https://github.com/aginies/virtui-manager.git
cd virtui-manager
```

#### Launch the devel version

```bash
cd src/vmanager
python3 virtui_dev.py
```

### openSUSE / SLE (Zypper)

To install dependencies manually from official repositories (this is done automatically when installing the package):

```bash
sudo zypper in libvirt-python python3-textual python3-PyYAML python3-markdown-it-py
```
