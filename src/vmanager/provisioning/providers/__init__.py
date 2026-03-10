"""
Provider implementations for different operating systems.
"""

from .debian_provider import DebianProvider, DebianDistro
from .fedora_provider import FedoraProvider, FedoraDistro
from .opensuse_provider import OpenSUSEProvider, OpenSUSEDistro
from .ubuntu_provider import UbuntuProvider, UbuntuDistro

__all__ = [
    "DebianProvider",
    "DebianDistro",
    "FedoraProvider",
    "FedoraDistro",
    "OpenSUSEProvider",
    "OpenSUSEDistro",
    "UbuntuProvider",
    "UbuntuDistro",
]
