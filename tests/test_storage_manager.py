"""Tests for storage_manager: the capacity an uploaded image keeps.

The upload defines the volume at the size of the local file, which is the
capacity of a raw image but only the compressed size of a qcow2 one. libvirt
probes the qcow2 header on a refresh and reports the virtual size the image was
built with, and that is what the volume must end up at: create_overlay_volume()
takes an overlay's capacity from its backing volume, so a base left at its
download size gives every clone a disk of that size.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

import libvirt

from vmanager.storage_manager import upload_file_to_volume

VOLUME_XML = (
    "<volume><name>base</name><capacity unit='bytes'>1024</capacity>"
    "<target><format type='qcow2'/></target></volume>"
)


class TestUploadCapacity(unittest.TestCase):
    """upload_file_to_volume adopts the capacity the uploaded image records."""

    def _local_file(self, size=1024):
        handle = tempfile.NamedTemporaryFile("wb", suffix="-image.qcow2", delete=False)
        handle.write(b"x" * size)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def _pool(self, probed_capacity):
        """A pool whose refresh reports the given capacity for the new volume."""
        pool = MagicMock()
        pool.isActive.return_value = True
        created = MagicMock()
        created.name.return_value = "base"
        pool.createXML.return_value = created

        refreshed = MagicMock()
        refreshed.name.return_value = "base"
        refreshed.info.return_value = (0, probed_capacity, 0)
        refreshed.XMLDesc.return_value = VOLUME_XML
        pool.storageVolLookupByName.return_value = refreshed
        return pool, created, refreshed

    def _raised_capacity(self, refreshed):
        """The capacity the volume was resized to, or None when it was not."""
        if not refreshed.resize.called:
            return None
        return refreshed.resize.call_args[0][0]

    def test_a_qcow2_takes_the_virtual_size_of_the_image(self):
        """A 20 GB image downloaded as 1 KiB leaves a 20 GB volume behind it."""
        path = self._local_file()
        pool, _created, refreshed = self._pool(probed_capacity=20 * 1024**3)

        upload_file_to_volume(pool, "base", path)

        self.assertEqual(self._raised_capacity(refreshed), 20 * 1024**3)
        self.assertEqual(refreshed.resize.call_args[0][1], 0)
        pool.refresh.assert_called_once_with(0)
        pool.createXML.assert_called_once()

    def test_a_raw_image_keeps_the_size_of_the_file(self):
        """Nothing is resized when libvirt reports the size already defined."""
        path = self._local_file(size=1024)
        pool, _created, refreshed = self._pool(probed_capacity=1024)

        vol = upload_file_to_volume(pool, "base", path)

        refreshed.resize.assert_not_called()
        self.assertIs(vol, refreshed)

    def test_a_pool_that_cannot_probe_keeps_the_uploaded_volume(self):
        """A failed refresh is not a failed upload: the volume is returned."""
        path = self._local_file()
        pool, created, _refreshed = self._pool(probed_capacity=0)
        pool.refresh.side_effect = libvirt.libvirtError("no pool")

        vol = upload_file_to_volume(pool, "base", path)

        self.assertIs(vol, created)
        pool.connect.return_value.storageVolDefineXML.assert_not_called()

    def test_a_pool_that_cannot_redefine_keeps_the_uploaded_volume(self):
        """A backend that refuses the redefinition still has a usable volume."""
        path = self._local_file()
        pool, _created, refreshed = self._pool(probed_capacity=20 * 1024**3)
        pool.connect.return_value.storageVolDefineXML.side_effect = libvirt.libvirtError("no")

        vol = upload_file_to_volume(pool, "base", path)

        self.assertIs(vol, refreshed)


class TestUploadFailure(unittest.TestCase):
    """A half-written volume is removed rather than left behind."""

    def test_a_failed_stream_deletes_the_partial_volume(self):
        pool = MagicMock()
        pool.isActive.return_value = True
        created = MagicMock()
        created.name.return_value = "base"
        pool.createXML.return_value = created
        pool.connect.return_value.newStream.return_value.sendAll.side_effect = Exception("broken")

        handle = tempfile.NamedTemporaryFile("wb", suffix="-image.qcow2", delete=False)
        handle.write(b"x" * 1024)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        with self.assertRaises(Exception):
            upload_file_to_volume(pool, "base", handle.name)

        created.delete.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
