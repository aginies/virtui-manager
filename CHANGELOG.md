# Changelog

All notable changes to Virtui Manager are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `vmc --machine`, a machine-readable mode for `-c` and `--json`: one JSON
  object on stdout, command output and progress as log lines on stderr, and an
  exit status per failure (see the "Machine-Readable Output" section of the CLI
  manual). Written so that a program can drive `vmc` without parsing prose.
- `vmc -u/--uri`, which connects to the uri it is given and to nothing else,
  so a scripted caller says which hypervisor it means instead of relying on the
  local `autoconnect` configuration. With `--json` it replaces the `server`
  field of the deployment file.
- Resource commands for a scripted caller, machine-readable like `deploy`:
  `host capabilities`, `host resources`, `pool list|info|create`,
  `volume list|info|upload|create-overlay|delete`,
  `domain list|state|address|metadata|start|shutdown|reboot|destroy|undefine`,
  `network list|info`. Uploading an image streams it into the pool, and an
  overlay records the base it was made from, so a machine can start from a
  shared image and write nowhere else. What the words mean is what the
  interactive commands mean by them: `shutdown` is the ACPI event a guest can
  refuse, `destroy` is pulling the plug, and `start` checks that every disk is
  where the domain says it is before it puts power on.
- Config-driven deployment can boot a disk instead of installing one: `disk.volume`
  names a volume in the pool (with `disk.backing_volume` to make it as an
  overlay first), `seed.volume` attaches a read-only cloud-init cdrom, and
  `metadata` records what the machine is for in its domain XML. See the
  "Booting a disk instead of installing" section of the CLI manual.
- `machine info` command, reporting the protocol version, the version of
  virtui-manager and the commands it answers to in machine mode.
- `vmc --version`.

### Changed
- `vmc --json` exits non-zero when the deployment did not happen, instead of
  printing the reason and exiting 0. With `--machine`, every failure leaves one
  of the documented statuses (see the CLI manual).
- `deploy_from_config` returns what it deployed (or, with `--dryrun`, what it
  would deploy).
- The machine answers about a pool, a volume and a network come from the same
  reading the interactive tables make (`pool_facts`, `volume_facts`,
  `network_facts`), so the two cannot tell different stories about one resource.
- `volume rename` is answered with status 9 and a reason instead of an unknown
  error: libvirt-python exposes no way to rename a volume, so the message names
  `virsh vol-rename` as the way that does work.

### Fixed
- `domain shutdown`, `volume rename` and `network info` were calling libvirt
  methods that do not exist (`poweroff`, `rename`, `getAutostart`), so each left
  its caller with an unknown error. A test now reads every libvirt name the
  resource commands call out of the source and looks it up on the binding.
- Listing storage pools named a second unreachable pool after the first one, in
  the recoveries around pools that will not answer, because the previous pool's
  name was still in scope.

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
