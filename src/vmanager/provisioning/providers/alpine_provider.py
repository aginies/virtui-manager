"""Alpine Linux OS Provider for VirtUI Manager.

This module provides Alpine Linux-specific functionality for VM provisioning,
including ISO management and answers file generation.
"""

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import tarfile
from io import BytesIO

from ..os_provider import OSProvider, OSType, OSVersion


class AlpineDistro(Enum):
    """Alpine Linux distribution types."""

    V3_23 = "3.23"
    V3_22 = "3.22"
    V3_21 = "3.21"
    CUSTOM = "Custom ISO"


class AlpineProvider(OSProvider):
    """Provider for Alpine Linux distributions."""

    # Alpine Linux mirror base URL
    BASE_URL = "https://dl-cdn.alpinelinux.org/alpine/"

    def __init__(self, host_arch: str = "x86_64"):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        # Alpine uses x86_64 but also others
        self.host_arch = host_arch

    @property
    def os_type(self) -> OSType:
        """Return the OS type for Alpine Linux."""
        return OSType.ALPINE

    @property
    def preferred_boot_uefi(self) -> bool:
        """Alpine Linux prefers BIOS for standard installs (syslinux)."""
        return False

    def get_supported_versions(self) -> List[OSVersion]:
        """Get list of supported Alpine Linux versions."""
        versions = []
        for ver in ["3.23", "3.22", "3.21"]:
            versions.append(
                OSVersion(
                    os_type=OSType.ALPINE,
                    version_id=ver,
                    display_name=f"Alpine Linux {ver}",
                    architecture=self.host_arch,
                )
            )
        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Get ISO download sources for Alpine Linux."""
        # Alpine ISOs are in vX.Y/releases/arch/
        return [f"{self.BASE_URL}v{version.version_id}/releases/{self.host_arch}/"]

    def get_iso_list(self, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available Alpine Linux ISOs."""
        if version is None:
            version = "3.23"
        
        # Handle if full display name is passed
        if " " in version:
            version = version.split(" ")[-1]

        url = f"{self.BASE_URL}v{version}/releases/{self.host_arch}/"
        return self.get_iso_list_from_url(url, arch=self.host_arch)

    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate Alpine Linux apkovl tarball for automated installation."""

        # Use default template if not provided
        if not template_name:
            template_name = "alpine-answers-basic.txt"

        self.logger.info(f"Generating Alpine Linux automation file with template: {template_name}")

        # Detect desktop from template name
        desktop_cmd = ""
        keyb_pkg = "apk add setxkbmap\n"
        if template_name:
            if "gnome" in template_name.lower():
                desktop_cmd = "setup-desktop gnome\n" + keyb_pkg
            elif "plasma" in template_name.lower():
                desktop_cmd = "setup-desktop plasma\n" + keyb_pkg
            elif "xfce" in template_name.lower():
                desktop_cmd = "setup-desktop xfce\n" + keyb_pkg
            elif "mate" in template_name.lower():
                desktop_cmd = "setup-desktop mate\n" + keyb_pkg
            elif "sway" in template_name.lower():
                desktop_cmd = "setup-desktop sway\n" + keyb_pkg
            elif "lxqt" in template_name.lower():
                desktop_cmd = "setup-desktop lxqt\n" + keyb_pkg

        # Merge defaults and user config
        config = user_config.copy()
        config["vm_name"] = vm_name

        # Ensure default values are present
        defaults = {
            "username": "alpine",
            "password": "password",
            "root_password": "password",
            "timezone": "UTC",
            "keyboard": "us us",
            "ntp_client": "chrony",
            "ssh_server": "openssh",
            "disk_mode": "sys",
            "disk_device": "/dev/vda",
        }
        for key, value in defaults.items():
            if key not in config:
                config[key] = value

        template_path = self._find_template_file(template_name)
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()
        answers_content = self._substitute_variables(template_content, config)

        # Default to apkovl tarball for full automation
        output_file = output_path / "localhost.apkovl.tar.gz"
        
        # We create the tarball in memory first then write to file
        with tarfile.open(output_file, "w:gz") as tar:
            # 1. Add answers.txt
            answers_data = answers_content.encode("utf-8")
            # We put it in root/ so it's easily found by the trigger script
            answers_info = tarfile.TarInfo(name="root/answers.txt")
            answers_info.size = len(answers_data)
            answers_info.mtime = int(datetime.now().timestamp())
            tar.addfile(answers_info, BytesIO(answers_data))

            # 2. Add trigger script
            trigger_script = f"""#!/bin/sh
# Unattended Alpine Linux Installation
# Triggered by VirtUI Manager

# Ensure we don't run multiple times
if [ -f /tmp/alpine-install-started ]; then
    exit 0
fi
touch /tmp/alpine-install-started

# Send output to console
for cons in /dev/console /dev/tty0 /dev/ttyS0; do
    if [ -c "$cons" ]; then
        exec > "$cons" 2>&1
        break
    fi
done

echo ""
echo "####################################################"
echo "# Starting unattended Alpine Linux installation... #"
echo "####################################################"
echo ""

# Give the system a moment to fully settle
sleep 2

#echo "Initializing hardware drivers..."
/etc/init.d/devfs restart
/etc/init.d/modloop start
#/etc/init.d/hwdrivers start
/etc/init.d/fsck start

# Use 'yes y' to catch prompts
#setup-alpine -q -a -f /root/answers.txt

# remove this script
rm -vf /etc/local.d/virtui-install.start


setup-timezone {config.get('timezone')}
setup-apkrepos -1 -c
apk add qemu-guest-agent
/etc/init.d/qemu-guest-agent start

setup-hostname {config.get('vm_name')}.home.net
/etc/init.d/hostname start
setup-sshd openssh
{desktop_cmd}
#setup-ntp chrony
setup-interfaces -a
rc-udpate add sshd boot
#rc-update add chronyd boot
#rc-update add networking boot

setup-user -a {config.get('username')}
cat >> /home/aginies/.xinitrc <<EOF
setxkbmap {config.get('keyboard')}
EOF

# Set root and user password
echo "root:{config.get('root_password', 'password')}" | chpasswd
echo "{config.get('username')}:{config.get('user_password', 'password')}" | chpasswd

setup-keymap {config.get('keyboard')} {config.get('keyboard')}
export ERASE_DISKS={config.get('disk_device')}
export DEFAULT_DISK={config.get('disk_device')}
export DISK_MODE=sys
setup-disk -m sys -v {config.get('disk_device')}

# Workaround missing user dir
modprobe ext4
mkdir vda3t
mount /dev/vda3 vda3t
cp -a /home/{config.get('username')} vda3t/home
umount vda3t

echo ""
echo "########################################################"
echo "# Installation complete. System Halted                 #"
echo "#                                                      #"
echo "# YOU MUST POWER OFF TO CONTINUE as Alpine doesnt      #"
echo "# support halt poweroff :/                             #"
echo "#                                                      #"
echo "# WHEN THE VM IS POWER OFF, POWER ON it again          #"
echo "########################################################"
echo ""
#halt -f
"""
            script_data = trigger_script.encode("utf-8")
            script_info = tarfile.TarInfo(name="etc/local.d/virtui-install.start")
            script_info.size = len(script_data)
            script_info.mtime = int(datetime.now().timestamp())
            script_info.mode = 0o755  # Executable
            tar.addfile(script_info, BytesIO(script_data))

            # 3. Add a symlink to ensure 'local' service is enabled
            symlink_info = tarfile.TarInfo(name="etc/runlevels/default/local")
            symlink_info.type = tarfile.SYMTYPE
            symlink_info.linkname = "/etc/init.d/local"
            tar.addfile(symlink_info)

        return output_file

    def _substitute_variables(self, content: str, config: Dict[str, Any]) -> str:
        """Substitute variables in template content."""
        substitutions = config.copy()
        
        # Note: Alpine setup-alpine answers file often expects plaintext passwords
        # that it will hash itself. If we want to support pre-hashed, we'd need
        # a more complex template.

        result = content
        for key, value in substitutions.items():
            placeholder = f"{{{key}}}"
            result = result.replace(placeholder, str(value))
            # Also support ${key} format
            placeholder = f"${{{key}}}"
            result = result.replace(placeholder, str(value))
        return result

    def _find_template_file(self, template_name: str) -> Optional[Path]:
        """Find template file in templates directory."""
        current_dir = Path(__file__).parent
        templates_dir = current_dir.parent / "templates"

        template_path = templates_dir / template_name
        if template_path.exists():
            return template_path

        # Try without extension and add common extensions
        base_name = Path(template_name).stem
        for ext in [".txt", ".cfg"]:
            template_path = templates_dir / f"{base_name}{ext}"
            if template_path.exists():
                return template_path

        return None

    def validate_template_content(self, content: str, template_name: str) -> bool:
        """Validate Alpine answers file content."""
        # Simple validation: should have some common Alpine setup variables
        required_keywords = ["HOSTNAMEOPTS", "DISKOPTS"]
        for kw in required_keywords:
            if kw not in content:
                return False
        return True
