"""
Tests for the libvirt error handler: what a run writes to the log.
"""

import contextlib
import logging
import os
import sys
import unittest

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import libvirt  # noqa: E402

from vmanager.libvirt_error_handler import libvirt_error_handler  # noqa: E402

# The codes libvirt answers with when the thing asked for is simply not there.
ABSENCES = [
    ("VIR_ERR_NO_DOMAIN", libvirt.VIR_ERR_NO_DOMAIN),
    ("VIR_ERR_NO_STORAGE_VOL", libvirt.VIR_ERR_NO_STORAGE_VOL),
    ("VIR_ERR_NO_STORAGE_POOL", libvirt.VIR_ERR_NO_STORAGE_POOL),
]


class _Collect(logging.Handler):
    """Keeps the root logger's records for one test instead of writing them out."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def collected_logs():
    """Capture what the run would have logged, at every level."""
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    collector = _Collect()
    root.setLevel(logging.DEBUG)
    root.handlers = [collector]
    try:
        yield collector.records
    finally:
        root.setLevel(saved_level)
        root.handlers = saved_handlers


class TestLibvirtErrorHandler(unittest.TestCase):
    """A run that works writes nothing that reads like a failure."""

    def _error(self, code, message="not found"):
        """An error tuple as libvirt hands one to the handler, at its own error level."""
        return (code, 10, message, libvirt.VIR_ERR_ERROR, "qemu:///system")

    @staticmethod
    def _at(records, level):
        return [r for r in records if r.levelno == level]

    def test_an_absence_the_caller_expected_is_not_an_error(self):
        """Probing for a thing on purpose is not a failure when it is missing."""
        for name, code in ABSENCES:
            with self.subTest(code=name), collected_logs() as records:
                libvirt_error_handler(None, self._error(code, f"{name} message"))

            self.assertEqual(self._at(records, logging.ERROR), [])
            noted = self._at(records, logging.DEBUG)
            self.assertEqual(len(noted), 1)
            self.assertIn(f"{name} message", noted[0].getMessage())

    def test_an_unexpected_failure_is_still_an_error(self):
        """A failure that can stop something keeps the detail it has now."""
        with collected_logs() as records:
            libvirt_error_handler(None, self._error(libvirt.VIR_ERR_INTERNAL_ERROR, "disk full"))

        failed = self._at(records, logging.ERROR)
        self.assertEqual(len(failed), 1)
        self.assertIn("disk full", failed[0].getMessage())

    def test_a_warning_stays_a_warning(self):
        """Only the expected absences change level; everything else is as libvirt says."""
        with collected_logs() as records:
            libvirt_error_handler(
                None,
                (libvirt.VIR_ERR_INTERNAL_ERROR, 10, "harmless", libvirt.VIR_ERR_WARNING, "u"),
            )

        self.assertEqual(self._at(records, logging.ERROR), [])
        warned = self._at(records, logging.WARNING)
        self.assertEqual(len(warned), 1)
        self.assertIn("harmless", warned[0].getMessage())


if __name__ == "__main__":
    unittest.main()
