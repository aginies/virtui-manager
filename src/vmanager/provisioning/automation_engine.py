"""
Automation Engine for VirtUI Manager.

This module centralizes the generation of unattended installation files
(Kickstart, Preseed, AutoYaST, Cloud-init, etc.) for all supported operating systems.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .os_provider import OSType, hash_password


class AutomationEngine:
    """Central engine for generating OS automation files."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.templates_dir = Path(__file__).parent / "templates"

    def generate(
        self,
        os_type: OSType,
        version_id: str,
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """
        Dispatches automation file generation based on OS type.
        """
        self.logger.info(f"Generating automation file for {os_type.value} ({version_id})")

        if os_type == OSType.FEDORA:
            return self._generate_fedora(version_id, vm_name, user_config, output_path, template_name)
        elif os_type == OSType.UBUNTU:
            return self._generate_ubuntu(version_id, vm_name, user_config, output_path, template_name)
        elif os_type == OSType.DEBIAN:
            return self._generate_debian(version_id, vm_name, user_config, output_path, template_name)
        elif os_type == OSType.OPENSUSE:
            return self._generate_opensuse(version_id, vm_name, user_config, output_path, template_name)
        elif os_type == OSType.ARCHLINUX:
            return self._generate_archlinux(version_id, vm_name, user_config, output_path, template_name)
        elif os_type == OSType.ALPINE:
            return self._generate_alpine(version_id, vm_name, user_config, output_path, template_name)
        else:
            raise NotImplementedError(f"Automation not implemented for {os_type.value}")

    # --- Fedora / RHEL (Kickstart) ---
    def _generate_fedora(self, version, vm_name, user_config, output_path, template_name):
        if not template_name:
            template_name = "kickstart-basic.cfg"

        config = user_config.copy()
        config["vm_name"] = vm_name
        defaults = {
            "username": "fedorauser",
            "user_password": "",
            "root_password": "",
            "timezone": "UTC",
            "locale": "en_US.UTF-8",
            "keyboard": "us",
            "network_interface": "link",
        }
        for key, value in defaults.items():
            if key not in config:
                config[key] = value

        template_path = self._find_template(template_name, [".cfg", ".ks"])
        if not template_path:
            content = self._generate_basic_kickstart(config)
        else:
            with open(template_path, "r", encoding="utf-8") as f:
                content = self._substitute_variables(f.read(), config, hash_fields=["user_password", "password", "root_password"])

        output_file = output_path / "ks.cfg"
        self._write_file(output_file, content)
        return output_file

    # --- Ubuntu (Autoinstall / Preseed) ---
    def _generate_ubuntu(self, version, vm_name, user_config, output_path, template_name):
        if not template_name:
            template_name = "autoinstall-basic.yaml"

        config = user_config.copy()
        config["vm_name"] = vm_name
        
        is_autoinstall = "autoinstall" in template_name.lower() or template_name.endswith((".yaml", ".yml"))
        
        if is_autoinstall:
            template_path = self._find_template(template_name, [".yaml", ".yml"])
            if not template_path:
                raise FileNotFoundError(f"Ubuntu autoinstall template not found: {template_name}")
            
            with open(template_path, "r", encoding="utf-8") as f:
                content = self._substitute_variables(f.read(), config, hash_fields=["password", "user_password", "root_password"])
            
            output_file = output_path / "user-data"
            self._write_file(output_file, content)
            
            # meta-data is required for cloud-init
            meta_data = f"instance-id: {vm_name}\nlocal-hostname: {config.get('hostname', vm_name)}\n"
            self._write_file(output_path / "meta-data", meta_data)
            return output_file
        else:
            # Preseed fallback
            return self._generate_debian(version, vm_name, user_config, output_path, template_name)

    # --- Debian (Preseed) ---
    def _generate_debian(self, version, vm_name, user_config, output_path, template_name):
        if not template_name:
            template_name = "preseed-basic.cfg"
        
        config = user_config.copy()
        config["vm_name"] = vm_name
        
        template_path = self._find_template(template_name, [".cfg"])
        if not template_path:
            raise FileNotFoundError(f"Preseed template not found: {template_name}")
            
        with open(template_path, "r", encoding="utf-8") as f:
            content = self._substitute_variables(f.read(), config, hash_fields=["password", "user_password"])
        
        output_file = output_path / "preseed.cfg"
        self._write_file(output_file, content)
        return output_file

    # --- OpenSUSE (AutoYaST / Agama) ---
    def _generate_opensuse(self, version, vm_name, user_config, output_path, template_name):
        if not template_name:
            template_name = "autoyast-basic.xml"
        
        config = user_config.copy()
        config["vm_name"] = vm_name
        
        # OpenSUSE often uses 'user_name' instead of 'username'
        if "username" in config and "user_name" not in config:
            config["user_name"] = config["username"]

        if template_name.endswith(".json") or "agama" in template_name.lower():
            # Agama JSON
            template_path = self._find_template(template_name, [".json"])
            output_filename = "agama.json"
        else:
            # AutoYaST XML
            template_path = self._find_template(template_name, [".xml"])
            output_filename = "autoyast.xml"

        if not template_path:
            raise FileNotFoundError(f"OpenSUSE template not found: {template_name}")

        with open(template_path, "r", encoding="utf-8") as f:
            content = self._substitute_variables(f.read(), config, hash_fields=["root_password", "user_password"])
            
        output_file = output_path / output_filename
        self._write_file(output_file, content)
        return output_file

    # --- Arch Linux (Archinstall) ---
    def _generate_archlinux(self, version, vm_name, user_config, output_path, template_name):
        if not template_name:
            template_name = "archinstall-basic.json"
        
        config = user_config.copy()
        config["vm_name"] = vm_name
        
        template_path = self._find_template(template_name, [".json"])
        if not template_path:
             raise FileNotFoundError(f"Arch Linux template not found: {template_name}")

        with open(template_path, "r", encoding="utf-8") as f:
            # Archinstall usually takes plaintext in JSON and handles hashing itself, 
            # but VirtUI templates might expect substitution.
            content = self._substitute_variables(f.read(), config)
            
        output_file = output_path / "user_configuration.json"
        self._write_file(output_file, content)
        
        # Also handle user_credentials if present in templates
        creds_template = self.templates_dir / "archinstall-user-credentials.json"
        if creds_template.exists():
            with open(creds_template, "r", encoding="utf-8") as f:
                creds_content = self._substitute_variables(f.read(), config)
            self._write_file(output_path / "user_credentials.json", creds_content)

        return output_file

    # --- Alpine Linux (Answers / apkovl) ---
    def _generate_alpine(self, version, vm_name, user_config, output_path, template_name):
        import tarfile
        import io
        from datetime import datetime

        if not template_name:
            template_name = "alpine-answers-basic.txt"
        
        config = user_config.copy()
        config["vm_name"] = vm_name
        
        # Detect desktop from template name and install needed stuff
        desktop_cmd = ""
        keyb_xorg_pkg = "apk add setxkbmap font-dejavu\nsetup-xorg-base\n"
        lightdm = "apk add lightdm-gtk-greeter xterm\n"
        start_lightdm = "rc-update add lightdm boot\n"
        if template_name:
            if "gnome" in template_name.lower():
                desktop_cmd = "setup-desktop gnome\n" + keyb_xorg_pkg
            elif "plasma" in template_name.lower():
                desktop_cmd = "setup-desktop plasma\n" + keyb_xorg_pkg
            elif "xfce" in template_name.lower():
                desktop_cmd = "setup-desktop xfce\n" + keyb_xorg_pkg
            elif "mate" in template_name.lower():
                desktop_cmd = "setup-desktop mate\n" + keyb_xorg_pkg
            elif "sway" in template_name.lower():
                desktop_cmd = "setup-desktop sway\n" + keyb_xorg_pkg + lightdm + start_lightdm
            elif "lxqt" in template_name.lower():
                desktop_cmd = "setup-desktop lxqt\n" + keyb_xorg_pkg + lightdm + start_lightdm

        template_path = self._find_template(template_name, [".txt"])
        if not template_path:
            raise FileNotFoundError(f"Alpine template not found: {template_name}")
            
        with open(template_path, "r", encoding="utf-8") as f:
            answers_content = self._substitute_variables(f.read(), config)

        # Default to apkovl tarball for full automation
        output_file = output_path / "localhost.apkovl.tar.gz"
        
        with tarfile.open(output_file, "w:gz") as tar:
            # 1. Add answers.txt
            answers_data = answers_content.encode("utf-8")
            answers_info = tarfile.TarInfo(name="root/answers.txt")
            answers_info.size = len(answers_data)
            answers_info.mtime = int(datetime.now().timestamp())
            tar.addfile(answers_info, io.BytesIO(answers_data))

            # 2. Add trigger script
            trigger_script = f"""#!/bin/sh
set -e
# Send output to console
exec > /dev/console 2>&1

echo "Starting unattended Alpine Linux installation..."
/etc/init.d/devfs restart
/etc/init.d/modloop start
/etc/init.d/fsck start

# Run setup-alpine with answers
setup-alpine -f /root/answers.txt

# Post-install (optional desktop)
{desktop_cmd}

echo "Installation complete! Rebooting..."
reboot
"""
            trigger_data = trigger_script.encode("utf-8")
            trigger_info = tarfile.TarInfo(name="etc/local.d/virtui-install.start")
            trigger_info.size = len(trigger_data)
            trigger_info.mode = 0o755
            trigger_info.mtime = int(datetime.now().timestamp())
            tar.addfile(trigger_info, io.BytesIO(trigger_data))

        return output_file

    # --- Helpers ---

    def _find_template(self, name: str, extensions: List[str]) -> Optional[Path]:
        """Finds a template file in the templates directory."""
        path = Path(name)
        if path.is_absolute() and path.exists():
            return path
        
        # Try in templates directory
        target = self.templates_dir / name
        if target.exists():
            return target
        
        # Try with extensions
        base = target.stem
        for ext in extensions:
            target = self.templates_dir / f"{base}{ext}"
            if target.exists():
                return target
        
        return None

    def _substitute_variables(self, content: str, config: Dict[str, Any], hash_fields: List[str] = None) -> str:
        """Generic variable substitution with optional password hashing."""
        substitutions = config.copy()
        
        if hash_fields:
            for field in hash_fields:
                if field in substitutions and substitutions[field]:
                    val = str(substitutions[field]).strip()
                    # Only hash if not already hashed (very basic check)
                    if not val.startswith("$6$"):
                        substitutions[field] = hash_password(val)

        result = content
        for key, value in substitutions.items():
            result = result.replace(f"{{{key}}}", str(value))
            result = result.replace(f"${{{key}}}", str(value))
        return result

    def _write_file(self, path: Path, content: str):
        """Write content to file with restrictive permissions."""
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(content)

    def _generate_basic_kickstart(self, config: Dict[str, Any]) -> str:
        """Fallback basic Kickstart if template is missing."""
        user_pwd = config.get("user_password", config.get("password", "linux"))
        hashed_password = hash_password(str(user_pwd).strip())
        hashed_root_password = hash_password(str(config.get("root_password", "linux")).strip())

        return f"""# Basic Fedora Kickstart
text
lang {config.get('locale', 'en_US.UTF-8')}
keyboard {config.get('keyboard', 'us')}
timezone {config.get('timezone', 'UTC')} --isUtc
rootpw --iscrypted {hashed_root_password}
user --name={config.get('username', 'user')} --password={hashed_password} --iscrypted --groups=wheel
network --bootproto=dhcp --device={config.get('network_interface', 'link')} --activate --hostname={config['vm_name']}
ignoredisk --only-use=vda
clearpart --all --initlabel
autopart
bootloader --location=mbr
url --mirrorlist=https://mirrors.fedoraproject.org/mirrorlist?repo=fedora-$releasever&arch=$basearch
%packages
@core
openssh-server
qemu-guest-agent
%end
reboot
"""

    def generate_arch_setup_script(self, json_url: str, creds_url: str) -> str:
        """Generate Arch Linux auto-installation setup script."""
        return f"""#!/bin/bash
# Arch Linux Auto-Install Setup Script
# Generated by VirtUI Manager

set -e  # Exit on error

echo "Starting Arch Linux auto-installation setup..."

# Ensure network is online
echo "Waiting for network to be online..."
systemctl start systemd-networkd-wait-online.service || true
systemctl start network-online.target || true

# Wait a bit more to ensure network is fully ready
sleep 3

# Download the archinstall configuration
echo "Downloading archinstall configuration from {json_url}..."
curl -f -o /root/auto.json "{json_url}"
echo "Downloading archinstall creds configuration from {creds_url}..."
curl -f -o /root/creds.json "{creds_url}"

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to download archinstall or creds configuration!"
    echo "Press any key to get a shell for debugging..."
    read -n 1
    exec /bin/bash
fi

echo "Configuration downloaded successfully"

# Run archinstall with the downloaded configuration
echo "Starting archinstall..."
archinstall --config /root/auto.json --creds /root/creds.json --silent

if [ $? -ne 0 ]; then
    echo "ERROR: archinstall failed!"
    echo "Press any key to get a shell for debugging..."
    read -n 1
    exec /bin/bash
fi

echo "Installation complete! Rebooting..."
reboot
"""
