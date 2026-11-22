"""Web dashboard for VirtUI Manager.

A thin FastAPI layer exposing a read-only view of the VM fleet on top of
the existing service layer (VMService / ConnectionManager), plus a static
browser frontend. Requires the ``web`` optional dependencies::

    pip install "virtui-manager[web]"
"""
