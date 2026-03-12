"""
Provider implementations for different operating systems.
"""

from .alpine_provider import AlpineProvider, AlpineDistro
from .archlinux_provider import ArchLinuxProvider, ArchLinuxDistro
from .debian_provider import DebianProvider, DebianDistro
from .fedora_provider import FedoraProvider, FedoraDistro
from .opensuse_provider import OpenSUSEProvider, OpenSUSEDistro
from .ubuntu_provider import UbuntuProvider, UbuntuDistro

__all__ = [
    "AlpineProvider",
    "AlpineDistro",
    "ArchLinuxProvider",
    "ArchLinuxDistro",
    "DebianProvider",
    "DebianDistro",
    "FedoraProvider",
    "FedoraDistro",
    "OpenSUSEProvider",
    "OpenSUSEDistro",
    "UbuntuProvider",
    "UbuntuDistro",
]
