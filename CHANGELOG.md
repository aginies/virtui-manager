# Changelog

All notable changes to Virtui Manager are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.3.9]

### Changed
- Use plain ACPI shutdown/reboot instead of the QEMU guest agent for reliable power
  actions.

### Fixed
- Refresh the VM list after a rename so the updated name is displayed.
- Remove all video devices (not just the first) and use correct model defaults when
  changing the video model.
- Fix Python 3.9 compatibility (PEP 604 union annotations).
- Pin the CI lint/test jobs to ubuntu-24.04 so the source-built libvirt-python
  10.0.0 matches the libvirt version shipped by that distro.

### Removed
- Cleaned up 4 unused constants.

## [3.3.6]

### Added
- Prefer the QEMU guest agent for graceful shutdown and reboot with automatic
  fallback to plain ACPI.
- Helper to detect whether the QEMU guest agent is available in the guest.

### Changed
- Snapshot quiesce now requires the guest agent to be both configured and running.

### Fixed
- Make libvirt event registration idempotent (no duplicate/leaked callbacks on
  connection re-registration).
- Fix a stale refresh re-adding a removed VM card in the main view.

### Refactored
- Consolidated the base/components/modals CSS rules into a single vmanager.css
  and merged remaining duplicate CSS rules.

## [3.3.5]

### Added
- Pre-flight check of required install binaries (7z, mkfs.vfat, mcopy, qemu-img)
  before starting a VM installation.
- Auto-create the target directory when creating a `dir` storage pool.
- VM card changes prepared alongside the install-tools pre-flight feature.

## [3.3.4]

### Removed
- Removed commented-out code and verified dead code (cleanup batches).
- Bumped release to 3.3.4 and cleaned up lint/format.

## [3.3.3]

### Added
- `vmc` alias for `virtui-manager-cmd` to simplify CLI usage.
- New `-c` non-interactive flag and a connect-pipeline command for scripting.
- Input validation for IP addresses, storage paths, MAC addresses, and input ranges
  (also MAC address, kernel parameters and markdown sanitization on user input).
- Documentation chapters on backup, pipelines, auto-install, UEFI firmware and
  viewer architecture.

### Changed
- Default timezone on new VMs set to UTC.
- Expert Mode and automation sections now collapse on install.
- Install modal keeps the install button visible when the form overflows.
- Notification bar is now color-coded by VM state.
- Clipboard menu labels corrected and auto-push UX improved.

### Fixed
- Remote provisioning: several bugs in `vm_provisioner` and `vmc` alignment with the
  TUI provisioning flow.
- Remote servers: skip local ISO existence checks for remote pool volumes, disable
  Browse buttons in `AddPoolModal`, skip path-existence checks for remote attach volume,
  and return `None` pool volumes when a pool lookup fails.
- Remote VMs: disable USB/PCI host tabs and "Add Disk" in VM details.
- `RemoteAutoHTTPServer`: SSH call quoting with `shlex`, path validation on `stop()`,
  `rm -rf` guard, SSH-call optimization and test updates.
- VM actions: clone NVRAM per VM to avoid SELinux label conflicts, skip CD-ROM disks when
  deleting VM storage, and fix `strip_installation_assets` reading live XML on a running VM.
- Route libvirt errors to logging instead of crashing, and replace bare `except:` with
  `except Exception:` across the codebase.
- Kill tmux sessions when the GUI dies without cleanup.
- Connect to newly added autoconnect servers when reloading the server list.
- Check for an existing ISO in the pool before downloading.

### Refactored
- Centralized backup manager, pipeline and CLI command refactoring.
- Refactored `vm_actions` XML edits into a shared helper.

### Performance
- Optimized `RemoteAutoHTTPServer` SSH calls.

### Security
- Added path validation to prevent dangerous `rm -rf` and to quote remote command args.

### Documentation / CI
- Removed marketing language from docs.
- Fixed CI to install the correct `libgirepository` and cairo dev packages for PyGObject.
