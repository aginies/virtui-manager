"""
Remote Viewer GTK3 Package

A modular VNC/SPICE viewer for virtual machines.

This package provides a clean, maintainable implementation of the remote viewer
functionality, organized into focused modules for better testability and reuse.
"""

from .viewer_app import RemoteViewer, main

__all__ = ['RemoteViewer', 'main']
