"""Alpine Linux OS Provider for VirtUI Manager.

This module provides Alpine Linux-specific functionality for VM provisioning,
including ISO management and answers file generation.
"""

import logging
import os
import re
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..os_provider import OSProvider, OSType, OSVersion, hash_password


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
        return self.get_iso_list_from_url(url)

    def get_iso_list_from_url(self, url: str) -> List[Dict[str, Any]]:
        """Get ISO list from a specific URL."""
        self.logger.info(f"Fetching Alpine Linux ISO list from {url}")
        
        iso_list = []
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                content = response.read().decode("utf-8")
                
            # Parse HTML to find ISO files
            # Alpine ISOs look like alpine-virt-3.20.3-x86_64.iso
            links = re.findall(r'href="([^"]*\.iso)"', content)
            
            for link in links:
                # Clean the link
                clean_link = link.lstrip("./")
                
                # We prefer 'virt' ISO for VMs, then 'standard'
                full_url = url + clean_link if not link.startswith("http") else link
                
                # Fetch metadata
                date_str = ""
                size_str = "Unknown"
                try:
                    req = urllib.request.Request(full_url, method="HEAD")
                    with urllib.request.urlopen(req, timeout=5) as head_res:
                        # Size
                        content_length = head_res.getheader("Content-Length")
                        if content_length:
                            size_mb = int(content_length) // (1024 * 1024)
                            size_str = f"{size_mb} MB"
                        
                        # Date
                        last_modified = head_res.getheader("Last-Modified")
                        if last_modified:
                            dt = parsedate_to_datetime(last_modified)
                            date_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception as e:
                    self.logger.debug(f"Could not fetch metadata for {full_url}: {e}")

                iso_list.append({
                    "name": clean_link,
                    "url": full_url,
                    "size": size_str,
                    "date": date_str,
                    "arch": self.host_arch
                })
                
            # Sort by name descending
            iso_list.sort(key=lambda x: x["name"], reverse=True)
            return iso_list

        except Exception as e:
            self.logger.error(f"Error fetching Alpine Linux ISO list from {url}: {e}")
            return []

    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate Alpine Linux apkovl tarball for automated installation."""
        import shutil
        import tarfile
        import tempfile
        from io import BytesIO

        # Use default template if not provided
        if not template_name:
            template_name = "alpine-answers-basic.txt"

        self.logger.info(f"Generating Alpine Linux automation file with template: {template_name}")

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
        if not template_path or not template_path.exists():
            self.logger.warning(f"Alpine template not found: {template_name}, using basic answers")
            answers_content = self._generate_basic_answers(config)
        else:
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()
            answers_content = self._substitute_variables(template_content, config)

        # Create the apkovl tarball
        # Standard Alpine search pattern for apkovl over HTTP is hostname.apkovl.tar.gz
        # but we serve it with a unique name from VMProvisioner.
        output_file = output_path / "alpine.apkovl.tar.gz"
        
        # We create the tarball in memory first then write to file
        with tarfile.open(output_file, "w:gz") as tar:
            answers_data = answers_content.encode("utf-8")
            answers_info = tarfile.TarInfo(name="root/answers.txt")
            answers_info.size = len(answers_data)
            answers_info.mtime = int(datetime.now().timestamp())
            tar.addfile(answers_info, BytesIO(answers_data))

            trigger_script = f"""#!/bin/sh
# Unattended Alpine Linux Installation
# Triggered by VirtUI Manager

# Ensure we don't run multiple times if rebooted into ISO
if [ -f /tmp/alpine-install-started ]; then
    exit 0
fi
touch /tmp/alpine-install-started

# Send output to console so it's visible in the viewer
#exec > /dev/console 2>&1

echo ""
echo "####################################################"
echo "# Starting unattended Alpine Linux installation... #"
echo "####################################################"
echo ""

#setup-keymap {config.get('keyboard')} {config.get('keyboard')}

# Give the system a moment to fully settle
#sleep 2

# Run setup-alpine with the answers file. 
#yes y | setup-alpine -f /root/answers.txt
#setup-alpine -f /root/answers.txt

/etc/init.d/devfs restart
/etc/init.d/modloop start
/etc/init.d/hwdrivers start

# set root and username password
echo "root:{config.get('root_password', 'password')}" | chpasswd
echo "{config.get('username')}:{config.get('user_password', 'password')}" | chpasswd

echo ""
#echo "Installation complete. Rebooting in 5 seconds..."
#sleep 5
#/sbin/reboot
"""
            script_data = trigger_script.encode("utf-8")
            script_info = tarfile.TarInfo(name="etc/local.d/virtui-install.start")
            script_info.size = len(script_data)
            script_info.mtime = int(datetime.now().timestamp())
            script_info.mode = 0o755  # Executable
            tar.addfile(script_info, BytesIO(script_data))

            # 3. Add a symlink to ensure 'local' service is enabled in default runlevel
            # Standard Alpine ISO might not have it enabled in the live environment.
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

        # Try exact match first
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

    def _generate_basic_answers(self, config: Dict[str, Any]) -> str:
        """Generate a basic Alpine answers file."""
        # Alpine answers file format
        vm_name = config.get("vm_name", "alpine-vm")
        username = config.get("username", "alpine")
        password = config.get("password", "password")
        root_password = config.get("root_password", "password")
        
        # Keyboard layout
        keyboard = config.get("keyboard", "us us")
        
        answers = [
            f'KEYMAPOPTS="{keyboard} {keyboard}"',
            f'HOSTNAMEOPTS="-n {vm_name}"',
            'INTERFACESOPTS="auto lo',
            'iface lo inet loopback',
            '',
            'auto eth0',
            'iface eth0 inet dhcp"',
            'DNSOPTS="-d localdomain 8.8.8.8"',
            f'TIMEZONEOPTS="-z {config.get("timezone", "UTC")}"',
            'PROXYOPTS="none"',
            'APKREPOSOPTS="-f"',
            f'USEROPTS="-a -u {username} -g {username}"',
            f'SSHDOPTS="-c {config.get("ssh_server", "openssh")}"',
            f'NTPOPTS="-c {config.get("ntp_client", "chrony")}"',
            f'DISKOPTS="-m {config.get("disk_mode", "sys")} {config.get("disk_device", "/dev/vda")}"',
            'LBUOPTS="none"',
            'APKCACHEOPTS="none"',
        ]
        return "\n".join(answers)

    def validate_template_content(self, content: str, template_name: str) -> bool:
        """Validate Alpine answers file content."""
        # Simple validation: should have some common Alpine setup variables
        required_keywords = ["HOSTNAMEOPTS", "DISKOPTS"]
        for kw in required_keywords:
            if kw not in content:
                return False
        return True
