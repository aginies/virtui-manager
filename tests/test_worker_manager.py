import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from textual.worker import WorkerState

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vmanager import WorkerManager


class TestWorkerManager(unittest.TestCase):
    def setUp(self):
        # Create a mock app
        self.mock_app = MagicMock()
        self.worker_manager = WorkerManager(self.mock_app)

    def test_init(self):
        """Test WorkerManager initialization."""
        self.assertIsInstance(self.worker_manager, WorkerManager)
        self.assertEqual(self.worker_manager.app, self.mock_app)
        self.assertEqual(self.worker_manager.workers, {})

    def test_run_with_exclusive_true(self):
        """Test running a worker with exclusive=True."""
        mock_callable = MagicMock()

        # First run - should succeed
        worker = self.worker_manager.run(mock_callable, name="test_worker", exclusive=True)
        self.assertIsNotNone(worker)

        # Second run with same name - should return None
        worker2 = self.worker_manager.run(mock_callable, name="test_worker", exclusive=True)
        self.assertIsNone(worker2)

    def test_run_with_exclusive_false(self):
        """Test running a worker with exclusive=False."""
        mock_callable = MagicMock()

        # First run - should succeed
        worker1 = self.worker_manager.run(mock_callable, name="test_worker", exclusive=False)
        self.assertIsNotNone(worker1)

        # Second run with same name - should succeed (not exclusive)
        worker2 = self.worker_manager.run(mock_callable, name="test_worker", exclusive=False)
        self.assertIsNotNone(worker2)

    def test_is_running(self):
        """Test checking if worker is running."""
        mock_callable = MagicMock()

        # No workers running yet
        self.assertFalse(self.worker_manager.is_running("test_worker"))

        # Start a worker
        worker = self.worker_manager.run(mock_callable, name="test_worker", exclusive=True)
        self.assertTrue(self.worker_manager.is_running("test_worker"))

    def test_cancel(self):
        """Test canceling a worker."""
        mock_callable = MagicMock()

        # Start a worker
        worker = self.worker_manager.run(mock_callable, name="test_worker", exclusive=True)
        self.assertIsNotNone(worker)

        # Cancel the worker
        cancelled_worker = self.worker_manager.cancel("test_worker")
        self.assertEqual(cancelled_worker, worker)

        # Try to cancel non-existent worker
        cancelled_worker = self.worker_manager.cancel("non_existent")
        self.assertIsNone(cancelled_worker)

    def test_cancel_all(self):
        """Test canceling all workers."""
        mock_callable = MagicMock()

        # Start a few workers
        self.worker_manager.run(mock_callable, name="worker1", exclusive=True)
        self.worker_manager.run(mock_callable, name="worker2", exclusive=True)
        self.worker_manager.run(mock_callable, name="worker3", exclusive=True)

        # Verify they are running
        self.assertTrue(self.worker_manager.is_running("worker1"))
        self.assertTrue(self.worker_manager.is_running("worker2"))
        self.assertTrue(self.worker_manager.is_running("worker3"))

        # Cancel all
        self.worker_manager.cancel_all()

        # Verify they are all cancelled
        self.assertFalse(self.worker_manager.is_running("worker1"))
        self.assertFalse(self.worker_manager.is_running("worker2"))
        self.assertFalse(self.worker_manager.is_running("worker3"))

    def test_cleanup_finished_workers(self):
        """Test cleanup of finished workers."""
        mock_callable = MagicMock()

        # Start a worker
        worker = self.worker_manager.run(mock_callable, name="test_worker", exclusive=True)
        self.assertIsNotNone(worker)

        # Manually set the worker to finished state
        worker.state = WorkerState.SUCCESS

        # Check that worker is initially in dict
        self.assertIn("test_worker", self.worker_manager.workers)

        # Cleanup finished workers (should remove it)
        self.worker_manager._cleanup_finished_workers()

        # Check that worker is now removed
        self.assertNotIn("test_worker", self.worker_manager.workers)


if __name__ == "__main__":
    unittest.main()