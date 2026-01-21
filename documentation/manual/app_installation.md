# Installation

## Prerequisites

VirtUI Manager is a Python-based application that leverages `libvirt` for virtualization management and `textual` for its terminal user interface.

### System Requirements

*   **Operating System:** Linux (tested on openSUSE, Fedora, Ubuntu, Arch)
*   **Python:** 3.8+
*   **Virtualization:** KVM/QEMU and Libvirt installed and running.
*   **Access:** Your user must have permissions to manage libvirt (usually part of the `libvirt` group).


## OpenSUSE / SLE Installation

VirtUI Manager is available as a package in the [Virtualization](https://build.opensuse.org/package/show/Virtualization/virtui-manager) repository. Choose the right repository, go to it and download packages and install them. IE for 16.0:

* go to [16 repo](https://download.opensuse.org/repositories/Virtualization/16.0/noarch/)
* Search for **virtui-manager**
* Download the rpm packages: **virtui-manager**, **virtui-manager-doc**, **virtui-remote-viewer**
* install the packages

```bash
sudo zypper in virtui-*.rpm
```


## Installation Steps from Source Code

### 1. Clone the Repository

Get the latest source code from GitHub:

```bash
git clone https://github.com/aginies/virtui-manager.git
cd virtui-manager
```

### 2. Install Dependencies

You can install dependencies using your system's package manager (recommended for stability) or via Python's package manager (`pip`).

#### openSUSE / SLE (Zypper)

To install dependencies manually from official repositories (this is done automatically when installing the packages):
```bash
sudo zypper in libvirt-python python3-textual python3-PyYAML python3-markdown-it-py
```

#### Generic / Virtual Environment (Pip)

If your distribution doesn't package these libraries or you prefer a virtual environment:

```bash
pipx install libvirt-python textual pyyaml markdown-it-py
```

### 3. Run the Application

Navigate to the source directory and launch the manager:

```bash
cd src/vmanager
python3 vmanager.py
```

!!! tip "Add to PATH"
    For easier access, you can create a shell alias or symlink:
    `alias virtui-manager='python3 /path/to/virtui-manager/src/vmanager/vmanager.py'`
