# UEFI Firmware & Secure Boot

VirtUI Manager provides sophisticated UEFI firmware management, including automatic firmware discovery, intelligent selection with scoring, NVRAM template handling, Secure Boot configuration, and AMD SEV/SEV-ES support. This chapter covers the firmware architecture and configuration options.

## Firmware Discovery

VirtUI Manager discovers available UEFI firmware through two mechanisms:

### Libvirt Domain Capabilities

When connected to a libvirt hypervisor, firmware information is retrieved from the hypervisor's domain capabilities:

1.  **Query:** Calls `get_domain_capabilities_xml()` with architecture `x86_64`, machine `pc`, and flags `0`.
2.  **Parser:** Extracts `<loader>` values from the capabilities XML.
3.  **Metadata:** Attempts to read firmware JSON metadata files from `/usr/share/qemu/firmware/`.

### Local Filesystem Fallback

If libvirt connection is unavailable or firmware metadata cannot be read, the system falls back to reading firmware JSON files directly from the local filesystem:

*   **Directory:** `/usr/share/qemu/firmware/`
*   **Files:** `*.json` files containing firmware metadata
*   **Parsing:** Each JSON file is loaded and parsed into a `Firmware` object

### Caching

Firmware discovery results are cached to avoid repeated libvirt calls. Cache keys are:

*   `"local"`: For local filesystem reads (no connection)
*   `"remote"`: For remote libvirt connections

Cache can be cleared with `clear_firmware_cache()` (all keys) or `clear_firmware_cache("local")` (specific key).

## Firmware Metadata Structure

Each firmware entry is represented by the `Firmware` class with the following properties:

| Property | Description |
|----------|-------------|
| `executable` | Path to the firmware executable (e.g., `/usr/share/edk2/ovmf/OVMF_CODE.fd`) |
| `nvram_template` | Path to the NVRAM template (e.g., `/usr/share/edk2/ovmf/OVMF_VARS.fd`) |
| `architectures` | List of supported architectures (e.g., `["x86_64"]`, `["aarch64"]`) |
| `features` | List of firmware features (e.g., `["secure-boot", "amd-sev"]`) |
| `interfaces` | List of interface types (e.g., `["uefi"]`, `["bios"]`) |
| `machines` | List of supported machine types (e.g., `["pc-q35-*"]`) |
| `description` | Human-readable description |
| `tags` | Additional tags |
| `device` | Device type (e.g., `"flash"` for pflash devices) |

### JSON Metadata Format

Firmware metadata files in `/usr/share/qemu/firmware/` follow this structure:

```json
{
    "interface-types": ["uefi"],
    "mapping": {
        "device": "flash",
        "executable": {"filename": "/usr/share/edk2/ovmf/OVMF_CODE.fd"},
        "nvram-template": {"filename": "/usr/share/edk2/ovmf/OVMF_VARS.fd"}
    },
    "features": ["secure-boot", "acpi-s3", "acpi-s4"],
    "targets": [
        {
            "architecture": "x86_64",
            "machines": ["pc-q35-*", "pc-i440fx-*"]
        }
    ],
    "description": "OVMF for x86_64",
    "tags": ["ovmf", "edk2"]
}
```

## Firmware Selection Scoring

The `select_best_firmware()` function implements an intelligent scoring system (similar to `virt-install`) to choose the best firmware from available options:

### Selection Process

1.  **Filter by architecture:** Only firmware compatible with the target architecture is considered.
2.  **Filter by machine type:** If a specific machine type is requested, firmware must be compatible (wildcard matching with `fnmatch`).
3.  **Filter by secure boot:** If secure boot is required, only secure-boot-capable firmware is considered. Fallback to non-secure firmware if none available.
4.  **Score remaining firmware:** Each firmware is scored based on desired characteristics.

### Scoring System

| Feature | Score | Notes |
|---------|-------|-------|
| Has executable | +10 | Base score |
| Has NVRAM template | +20 | Base score |
| NVRAM preferred | +100 | If `prefer_nvram=True` |
| `secure-boot` feature | +50 | If secure boot required, -50 if not |
| `amd-sev` feature | +50 | SEV capability |
| `amd-sev-es` feature | +60 | SEV-ES is more advanced |
| `intel-tdx` feature | +60 | TDX capability |
| `acpi-s3` feature | +2 | Modern systems support S3 |
| `acpi-s4` feature | +2 | Modern systems support S4 |
| `requires-smm` feature | +10 | Secure boot requires SMM |
| `enrolled-keys` feature | +3 | Pre-enrolled keys |
| `ovmf` in name | +5 | OVMF heuristic |
| `code` in name | +3 | Indicates code/vars pair |
| `vars` in name | -20 | Should not be code loader |

### Selection Example

```python
from vmanager.firmware_manager import select_best_firmware, get_uefi_files

firmwares = get_uefi_files(conn, use_cache=True)
best = select_best_firmware(
    firmwares,
    architecture="x86_64",
    machine_type="pc-q35-6.0",
    secure_boot=True,
    prefer_nvram=True,
)
```

## NVRAM Management

### NVRAM Template Cloning

When provisioning a VM with UEFI, VirtUI Manager clones the NVRAM template to the target storage pool:

1.  **Template Discovery:** The NVRAM template path is extracted from the selected firmware's metadata.
2.  **Temporary Pool:** A temporary libvirt storage pool is defined for the firmware directory.
3.  **Target Pool Selection:** The system prefers the dedicated `nvram` storage pool if it exists and is active; otherwise, it uses the VM's target storage pool.
4.  **Format:** NVRAM is created in `raw` format to avoid conversion errors with libvirt nvram templates.
5.  **Naming:** The cloned NVRAM file is named `{vm_name}_VARS.fd`.
6.  **Cleanup:** The temporary pool is destroyed and undefined after cloning.

### NVRAM in VM XML

The cloned NVRAM path is embedded in the VM XML:

```xml
<os>
    <type arch='x86_64' machine='pc-q35-6.0'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash'>/usr/share/edk2/ovmf/OVMF_CODE.fd</loader>
    <nvram>/var/lib/libvirt/nvram/myvm_VARS.fd</nvram>
</os>
```

### Autoselection vs Manual Firmware

VirtUI Manager supports two firmware selection modes:

*   **Autoselection:** Uses `firmware='efi'` attribute in the `<os>` element. Libvirt handles NVRAM automatically with `<nvram/>` (empty element).
*   **Manual Firmware:** Specifies explicit `<loader>` and `<nvram>` paths. Provides full control over firmware selection.

## Secure Boot Configuration

### Secure Boot Per-VM-Type

Secure Boot is configured differently based on the VM type:

| VM Type | Secure Boot | TPM | SEV | Notes |
|---------|------------|-----|-----|-------|
| `SECURE` | Enabled | Enabled | Enabled | Full security stack (unless OS disables it) |
| `COMPUTATION` | Disabled | Disabled | Disabled | Performance-focused |
| `DESKTOP` | Disabled | Disabled | Disabled | General Linux desktop |
| `LOW_RESOURCE` | Disabled | Disabled | Disabled | Low resource Linux |
| `WDESKTOP` (Windows) | Enabled | Enabled | Disabled | Required for Windows 11 |
| `WLDESKTOP` (Windows Legacy) | Disabled | Disabled | Disabled | Older Windows versions |
| `SERVER` | Disabled | Disabled | Disabled | General server |

### OS-Secure Boot Overrides

Secure Boot is **automatically disabled** for certain OS types during installation, even if the VM type would normally enable it:

| OS Type | Secure Boot Override | Reason |
|---------|---------------------|--------|
| Arch Linux | Disabled | Installation media does not support Secure Boot |
| Debian | Disabled | Installation media does not support Secure Boot |
| Alpine | Disabled | Installation media does not support Secure Boot |

This override applies to both `SECURE` VM type and automated installation.

### Secure Boot in VM XML

When Secure Boot is enabled, the firmware loader includes `secure='yes'`:

```xml
<loader readonly='yes' secure='yes' type='pflash'>/usr/share/edk2/ovmf/OVMF_CODE.fd</loader>
```

When disabled:

```xml
<loader readonly='yes' secure='no' type='pflash'>/usr/share/edk2/ovmf/OVMF_CODE.fd</loader>
```

### Secure Boot Feature Detection

The firmware selection scoring system prioritizes firmware with the `secure-boot` feature:

*   **Required:** +50 points (highly preferred)
*   **Not required:** -50 points (strongly discouraged)

If no secure-boot-capable firmware is found, the system logs a warning and falls back to non-secure firmware.

## AMD SEV/SEV-ES Support

### SEV Capability Detection

VirtUI Manager detects AMD SEV (Secure Encrypted Virtualization) support through libvirt domain capabilities:

```python
from vmanager.firmware_manager import get_host_sev_capabilities

sev_caps = get_host_sev_capabilities(conn)
# Returns: {"sev": True/False, "sev-es": True/False}
```

The detection parses the host capabilities XML for:

*   `<host><cpu><sev/></cpu></host>`: Indicates SEV support
*   `<guest><arch name='x86_64'><features><sev-es/></features></guest>`: Indicates SEV-ES support

### SEV Configuration in VM XML

When SEV is enabled (for `SECURE` VM type on compatible hardware), the VM XML includes:

```xml
<launchSecurity type='sev'>
    <cbitpos>47</cbitpos>
    <reducedPhysBits>1</reducedPhysBits>
    <policy>0x0033</policy>
</launchSecurity>
```

### SEV Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `cbitpos` | 47 | Bit position for ciphertext identifier |
| `reducedPhysBits` | 1 | Number of reduced physical address bits |
| `policy` | 0x0033 | SEV policy (debug disabled, no ASM, no sharing) |

These values are returned by `_get_sev_capabilities()` and are typical for AMD EPYC processors.

### SEV Per-OS Overrides

Like Secure Boot, SEV is **automatically disabled** for Arch Linux, Debian, and Alpine during installation:

```python
is_arch_debian_or_alpine = os_type in [OSType.ARCHLINUX, OSType.DEBIAN, OSType.ALPINE]
settings["sev"] = True if not is_arch_debian_or_alpine else False
```

## OVMF Debug Mode

VirtUI Manager supports enabling OVMF debug output, which logs UEFI firmware events to `/tmp/debug.log` on the guest:

```python
from vmanager.vm_actions import set_ovmf_debug

# Enable OVMF debug
set_ovmf_debug(domain, enable=True)

# Disable OVMF debug
set_ovmf_debug(domain, enable=False)
```

This is useful for troubleshooting UEFI boot issues, Secure Boot failures, and NVRAM problems.

## Machine Type and Firmware Compatibility

### Machine Type Matching

Firmware metadata includes supported machine types with wildcard patterns (e.g., `pc-q35-*`, `pc-i440fx-*`). The `_match_machine_pattern()` function uses `fnmatch.fnmatch()` for pattern matching:

```python
fnmatch.fnmatch("pc-q35-6.0", "pc-q35-*")  # True
fnmatch.fnmatch("pc-i440fx-5.2", "pc-q35-*")  # False
```

### Default Machine Types

The default machine type is determined by the boot firmware setting:

| Boot Mode | Default Machine Type |
|-----------|---------------------|
| UEFI | `pc-q35-{latest}` (e.g., `pc-q35-8.1`) |
| BIOS | `pc-i440fx-{latest}` (e.g., `pc-i440fx-8.1`) |

The latest machine types are detected from the hypervisor via `get_latest_machine_types()`.

## Troubleshooting

### Firmware Debug Report

VirtUI Manager can generate a detailed firmware debug report:

```python
from vmanager.firmware_manager import generate_firmware_debug_report

report = generate_firmware_debug_report(conn)
print(report)
```

The report includes:

*   Total firmware options
*   Per-firmware details (executable, description, architectures, machines, features, NVRAM)
*   Summary by architecture (total, with NVRAM, with flash, secure-boot)

### Common Issues

**No firmware found:**
*   Check that `/usr/share/qemu/firmware/` exists and contains `.json` files.
*   Verify libvirt domain capabilities include `<os><loader>` entries.
*   Check that the firmware files are readable by the libvirt-qemu user.

**Secure Boot not available:**
*   The host firmware may not include the `secure-boot` feature.
*   Check the firmware debug report for available features.
*   Some QEMU builds exclude Secure Boot support.

**NVRAM template missing:**
*   The selected firmware may not have an `nvram-template` entry.
*   Check the firmware JSON metadata for the `nvram-template` field.
*   The system will log a warning and let libvirt decide if no NVRAM template is found.

**SEV not detected:**
*   The host CPU may not support SEV (requires AMD EPYC or newer).
*   SEV support must be enabled in the BIOS/UEFI settings.
*   The KVM kernel module must be loaded with SEV support.
