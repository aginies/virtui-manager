# Installation

## Prerequisites

VirtUI Manager is a Python-based application that leverages `libvirt` for virtualization management and `textual` for its terminal user interface.

### System Requirements

*   **Operating System:** Linux (tested on openSUSE, Fedora, Ubuntu, Arch)
*   **Python:** 3.8+
*   **Virtualization:** KVM/QEMU and Libvirt installed and running.
*   **Access:** Your user must have permissions to manage libvirt (usually part of the `libvirt` group).

## Installation Steps

### 1. Clone the Repository

Get the latest source code from GitHub:

```bash
git clone https://github.com/aginies/virtui-manager.git
cd virtui-manager
```

### 2. Install Dependencies

You can install dependencies using your system's package manager (recommended for stability) or via Python's package manager (`pip`).

#### openSUSE / SLE (Zypper)

```bash
sudo zypper in libvirt-python python3-textual python3-PyYAML python3-markdown-it-py
```

#### Generic / Virtual Environment (Pip)

If your distribution doesn't package these libraries or you prefer a virtual environment:

```bash
pip install libvirt-python textual pyyaml markdown-it-py
```

### 3. Run the Application

Navigate to the source directory and launch the manager:

```bash
cd src/vmanager
python3 vmanager.py
```

!!! tip "Add to PATH"
    For easier access, you can create a shell alias or symlink:
    `alias vimgr='python3 /path/to/virtui-manager/src/vmanager/vmanager.py'`
