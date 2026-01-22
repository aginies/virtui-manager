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
python3 virtui-dev.py
```

### Generic / Virtual Environment (Pip)

If your distribution doesn't package these libraries or you prefer a virtual environment:

```bash
pip3 install virtui-manager
```

Now **virtui-manager**, **virtui-manager-cmd**, **virtui-remote-cmd** will be available from Command line.


### openSUSE / SLE (Zypper)

To install dependencies manually from official repositories (this is done automatically when installing the packages):

```bash
sudo zypper in libvirt-python python3-textual python3-PyYAML python3-markdown-it-py
```
