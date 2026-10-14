# Changelog

All notable changes to Virtui Manager are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
