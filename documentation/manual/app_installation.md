# Installation

## Prerequisites

VirtUI Manager is a Python-based application that leverages `libvirt` for virtualization management and `textual` for its terminal user interface.

### System Requirements

*   **Operating System:** Linux (tested on openSUSE, Fedora, Ubuntu, Arch)
*   **Python:** 3.8+
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

### Generic / Virtual Environment (Pip)

If your distribution doesn't package these libraries or you prefer a virtual environment:

```bash
pip3 install virtui-manager
```

Now **virtui-manager**, **virtui-manager-cmd**, **virtui-remote-cmd** will be available from Command line.


### openSUSE / SLE (Zypper)

To install dependencies manually from official repositories (this is done automatically when installing the package):

```bash
sudo zypper in libvirt-python python3-textual python3-PyYAML python3-markdown-it-py
```
