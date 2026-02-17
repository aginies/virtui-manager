#!/usr/bin/env python3
"""
Comprehensive tests for the VirtUI Manager Pipeline System.

This test suite covers:
- Pipeline parsing and validation
- Command execution and data flow
- VM selection and context passing
- Error handling and edge cases
- Dry-run and interactive modes
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
import sys
import os

# Add the source directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vmanager.pipeline import (
    PipelineExecutor,
    PipelineContext,
    PipelineCommand,
    PipelineParser,
    PipelineMode,
    PipelineStage,
    SelectCommand,
    VMOperationCommand,
    SnapshotCommand,
    WaitCommand,
    ViewCommand,
    InfoCommand,
)


class TestPipelineContext(unittest.TestCase):
    """Test the PipelineContext class."""

    def setUp(self):
        self.context = PipelineContext()

    def test_initial_state(self):
        """Test initial context state."""
        self.assertEqual(self.context.selected_vms, {})
        self.assertEqual(self.context.errors, [])
        self.assertEqual(self.context.metadata, {})

    def test_add_selected_vms(self):
        """Test adding selected VMs to context."""
        self.context.add_selected_vms("server1", ["vm1", "vm2"])
        self.assertEqual(len(self.context.selected_vms), 1)
        self.assertIn("server1", self.context.selected_vms)
        self.assertEqual(self.context.selected_vms["server1"], ["vm1", "vm2"])

    def test_has_selected_vms(self):
        """Test has_selected_vms method."""
        self.assertFalse(self.context.has_selected_vms())

        self.context.add_selected_vms("server1", ["vm1"])
        self.assertTrue(self.context.has_selected_vms())

    def test_clear_selection(self):
        """Test clearing VM selection."""
        self.context.add_selected_vms("server1", ["vm1", "vm2"])
        self.assertTrue(self.context.has_selected_vms())

        self.context.clear_selection()
        self.assertFalse(self.context.has_selected_vms())
        self.assertEqual(self.context.selected_vms, {})

    def test_get_all_selected_vms(self):
        """Test getting all selected VMs as tuples."""
        self.context.add_selected_vms("server1", ["vm1", "vm2"])
        self.context.add_selected_vms("server2", ["vm3"])

        all_vms = self.context.get_all_selected_vms()
        expected = [("server1", "vm1"), ("server1", "vm2"), ("server2", "vm3")]
        self.assertEqual(sorted(all_vms), sorted(expected))


class TestPipelineCommands(unittest.TestCase):
    """Test individual pipeline commands."""

    def setUp(self):
        self.context = PipelineContext()
        self.mock_vm_service = Mock()
        self.mock_cli = Mock()
        self.mock_cli.active_connections = {"default": Mock()}

    def test_select_command_validation(self):
        """Test SelectCommand validation."""
        # Valid command
        cmd = SelectCommand("select", ["vm1", "vm2"])
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(errors, [])

        # Invalid command - no arguments
        cmd = SelectCommand("select", [])
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(len(errors), 1)
        self.assertIn("requires VM names", errors[0])

    def test_select_command_execution(self):
        """Test SelectCommand execution."""
        cmd = SelectCommand("select", ["vm1", "vm2"])

        # Mock connection and domains
        mock_conn = Mock()
        mock_dom1 = Mock()
        mock_dom1.name.return_value = "vm1"
        mock_dom1.UUIDString.return_value = "uuid1"
        mock_dom2 = Mock()
        mock_dom2.name.return_value = "vm2"
        mock_dom2.UUIDString.return_value = "uuid2"
        
        mock_conn.listAllDomains.return_value = [mock_dom1, mock_dom2]
        mock_conn.getURI.return_value = "test:///default"
        self.mock_cli.active_connections = {"default": mock_conn}

        result_context = cmd.execute(self.context, self.mock_vm_service, self.mock_cli)

        self.assertTrue(result_context.has_selected_vms())
        self.assertIn("default", result_context.selected_vms)
        self.assertEqual(result_context.selected_vms["default"], ["vm1", "vm2"])

    def test_vm_operation_command_validation(self):
        """Test VMOperationCommand validation."""
        # Test different operations
        operations = ["start", "stop", "pause", "resume", "hibernate", "force_off"]

        for operation in operations:
            cmd = VMOperationCommand(operation, [])

            # Should fail without selected VMs
            errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
            self.assertEqual(len(errors), 1)
            self.assertIn("requires VM selection", errors[0])

            # Should pass with selected VMs
            self.context.add_selected_vms("default", ["vm1"])
            errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
            self.assertEqual(errors, [])
            self.context.clear_selection()

    def test_vm_operation_with_args(self):
        """Test VMOperationCommand with VM names as arguments."""
        cmd = VMOperationCommand("start", ["vm1", "vm2"])

        # Should validate successfully even without selected VMs
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(errors, [])

    def test_snapshot_command_validation(self):
        """Test SnapshotCommand validation."""
        # Valid snapshot create command
        cmd = SnapshotCommand("snapshot", ["create", "backup-01"])
        self.context.add_selected_vms("default", ["vm1"])

        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(errors, [])

        # Invalid - no operation specified
        cmd = SnapshotCommand("snapshot", [])
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(len(errors), 1)
        self.assertIn("action", errors[0])

    def test_wait_command(self):
        """Test WaitCommand validation."""
        # Valid wait command
        cmd = WaitCommand("wait", ["5"])
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(errors, [])

        # Invalid wait command - no time specified
        cmd = WaitCommand("wait", [])
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(len(errors), 1)
        self.assertIn("duration", errors[0])

        # Invalid wait command - non-numeric time
        cmd = WaitCommand("wait", ["abc"])
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(len(errors), 1)
        self.assertIn("duration", errors[0])

    def test_info_command(self):
        """Test InfoCommand."""
        cmd = InfoCommand("info", [])
        self.context.add_selected_vms("default", ["vm1", "vm2"])

        # InfoCommand should validate successfully with selected VMs
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(errors, [])

        # Should support piping
        self.assertTrue(cmd.supports_piping)

        # Test with explicit VM arguments
        cmd_with_args = InfoCommand("info", ["vm1", "vm2"])
        errors = cmd_with_args.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(errors, [])

    def test_view_command(self):
        """Test ViewCommand."""
        cmd = ViewCommand("view", [])
        self.context.add_selected_vms("default", ["vm1", "vm2"])

        # ViewCommand should validate successfully with selected VMs
        errors = cmd.validate(self.context, self.mock_vm_service, self.mock_cli)
        self.assertEqual(errors, [])

        with patch("vmanager.pipeline.remote_viewer_cmd") as mock_rv_cmd, \
             patch("subprocess.Popen") as mock_popen:
            mock_rv_cmd.return_value = ["echo", "viewer"]
            cmd.execute(self.context, self.mock_vm_service, self.mock_cli)
            self.assertTrue(mock_popen.called)


class TestPipelineParser(unittest.TestCase):
    """Test the PipelineParser class."""

    def setUp(self):
        self.parser = PipelineParser()

    def test_single_command_parsing(self):
        """Test parsing a single command (no pipes)."""
        commands = self.parser.parse("select vm1 vm2")

        self.assertEqual(len(commands), 1)
        self.assertIsInstance(commands[0], SelectCommand)
        self.assertEqual(commands[0].args, ["vm1", "vm2"])

    def test_simple_pipeline_parsing(self):
        """Test parsing a simple pipeline."""
        commands = self.parser.parse("select vm1 vm2 | start")

        self.assertEqual(len(commands), 2)
        self.assertIsInstance(commands[0], SelectCommand)
        self.assertIsInstance(commands[1], VMOperationCommand)

        # Check command arguments
        self.assertEqual(commands[0].args, ["vm1", "vm2"])
        self.assertEqual(commands[1].args, [])
        self.assertEqual(commands[1].command_str, "start")

    def test_complex_pipeline_parsing(self):
        """Test parsing a complex pipeline."""
        commands = self.parser.parse("select re:web.* | stop | snapshot create backup-test | start")

        self.assertEqual(len(commands), 4)
        self.assertIsInstance(commands[0], SelectCommand)
        self.assertIsInstance(commands[1], VMOperationCommand)
        self.assertIsInstance(commands[2], SnapshotCommand)
        self.assertIsInstance(commands[3], VMOperationCommand)

        # Check specific command details
        self.assertEqual(commands[0].args, ["re:web.*"])
        self.assertEqual(commands[1].command_str, "stop")
        self.assertEqual(commands[2].args, ["create", "backup-test"])
        self.assertEqual(commands[3].command_str, "start")

    def test_pipeline_with_quoted_args(self):
        """Test parsing pipeline with quoted arguments."""
        commands = self.parser.parse('select "vm with spaces" | start')

        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0].args, ["vm with spaces"])

    def test_invalid_command_parsing(self):
        """Test parsing pipeline with invalid command."""
        with self.assertRaises(ValueError) as context:
            self.parser.parse("select vm1 | invalid_command | start")

        self.assertIn("Unknown command", str(context.exception))

    def test_empty_pipeline(self):
        """Test parsing empty pipeline."""
        with self.assertRaises(ValueError):
            self.parser.parse("")

    def test_pipeline_with_only_pipes(self):
        """Test parsing pipeline with only pipe characters."""
        with self.assertRaises(ValueError):
            self.parser.parse("|||")


class TestPipelineExecutor(unittest.TestCase):
    """Test the PipelineExecutor class."""

    def setUp(self):
        self.mock_vm_service = Mock()
        self.mock_cli = Mock()
        
        # Mock connection and domains
        mock_conn = Mock()
        mock_dom1 = Mock()
        mock_dom1.name.return_value = "vm1"
        mock_dom1.UUIDString.return_value = "uuid1"
        mock_dom2 = Mock()
        mock_dom2.name.return_value = "vm2"
        mock_dom2.UUIDString.return_value = "uuid2"
        
        mock_conn.listAllDomains.return_value = [mock_dom1, mock_dom2]
        mock_conn.getURI.return_value = "test:///default"
        self.mock_cli.active_connections = {"default": mock_conn}

        self.executor = PipelineExecutor(self.mock_vm_service, self.mock_cli)

    def test_simple_pipeline_execution(self):
        """Test executing a simple pipeline."""
        with patch("vmanager.pipeline.remote_viewer_cmd") as mock_rv_cmd, \
             patch("subprocess.Popen") as mock_popen:
            mock_rv_cmd.return_value = ["echo", "viewer"]
            context = self.executor.execute_pipeline("select vm1 vm2 | view")

            # Check execution succeeded
            self.assertEqual(context.stage, PipelineStage.COMPLETE)
            self.assertEqual(len(context.errors), 0)

            # Check VMs were selected
            self.assertTrue(context.has_selected_vms())
            self.assertTrue(mock_popen.called)

    def test_dry_run_mode(self):
        """Test dry-run mode execution."""
        context = self.executor.execute_pipeline("select vm1 | start", PipelineMode.DRY_RUN)

        # Should complete successfully in dry-run mode
        self.assertEqual(context.stage, PipelineStage.COMPLETE)
        self.assertEqual(context.mode, PipelineMode.DRY_RUN)

    def test_pipeline_validation_errors(self):
        """Test pipeline with validation errors."""
        # Pipeline with validation error (no VMs specified)
        context = self.executor.execute_pipeline("select | start")

        # Should fail during validation
        self.assertEqual(context.stage, PipelineStage.FAILED)
        self.assertGreater(len(context.errors), 0)

    def test_pipeline_execution_with_wait(self):
        """Test pipeline with wait command."""
        with patch("time.sleep") as mock_sleep, \
             patch("vmanager.pipeline.remote_viewer_cmd") as mock_rv_cmd, \
             patch("subprocess.Popen") as mock_popen:
            mock_rv_cmd.return_value = ["echo", "viewer"]
            context = self.executor.execute_pipeline("select vm1 | wait 1 | view")

            # Should complete successfully
            self.assertEqual(context.stage, PipelineStage.COMPLETE)

            # In normal mode, sleep should be called
            if context.mode == PipelineMode.NORMAL:
                mock_sleep.assert_called_once_with(1.0)
            self.assertTrue(mock_popen.called)


class TestIntegration(unittest.TestCase):
    """Integration tests for the pipeline system."""

    def setUp(self):
        self.mock_vm_service = Mock()
        self.mock_cli = Mock()
        
        # Mock connection and domains
        mock_conn = Mock()
        mock_dom1 = Mock()
        mock_dom1.name.return_value = "web1"
        mock_dom1.UUIDString.return_value = "uuid1"
        mock_dom2 = Mock()
        mock_dom2.name.return_value = "web2"
        mock_dom2.UUIDString.return_value = "uuid2"
        
        mock_conn.listAllDomains.return_value = [mock_dom1, mock_dom2]
        mock_conn.getURI.return_value = "test:///default"
        self.mock_cli.active_connections = {"default": mock_conn}

        self.executor = PipelineExecutor(self.mock_vm_service, self.mock_cli)

    def test_complete_vm_workflow(self):
        """Test a complete VM management workflow."""
        # Simulate a backup and restart workflow
        pipeline = "select web1 web2 | snapshot create backup-$(date) | stop | start"

        with patch("subprocess.run"):
            context = self.executor.execute_pipeline(pipeline, PipelineMode.DRY_RUN)

        # Should complete successfully in dry-run mode
        self.assertEqual(context.stage, PipelineStage.COMPLETE)
        self.assertTrue(context.has_selected_vms())

    def test_regex_selection_workflow(self):
        """Test workflow with regex VM selection."""
        pipeline = "select re:web.* | view"

        context = self.executor.execute_pipeline(pipeline, PipelineMode.DRY_RUN)

        # Should complete successfully
        self.assertEqual(context.stage, PipelineStage.COMPLETE)

    def test_error_handling_in_pipeline(self):
        """Test error handling during pipeline execution."""
        # Simulate a scenario where VM operations might fail
        self.mock_cli.active_connections = {}  # No connections

        context = self.executor.execute_pipeline("select vm1 | start")

        # Should handle the error gracefully
        self.assertGreater(len(context.errors), 0)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions."""

    def setUp(self):
        self.parser = PipelineParser()

    def test_malformed_pipelines(self):
        """Test various malformed pipeline inputs."""
        malformed_cases = [
            "| select vm1",  # Leading pipe
            "select vm1 |",  # Trailing pipe
            "select vm1 || start",  # Double pipe
            "select vm1 | | start",  # Empty command between pipes
        ]

        for case in malformed_cases:
            with self.assertRaises(ValueError, msg=f"Should reject: {case}"):
                self.parser.parse(case)

    def test_whitespace_handling(self):
        """Test pipeline parsing with various whitespace patterns."""
        test_cases = [
            "  select   vm1   vm2   |   start  ",
            "\tselect\tvm1\t|\tstart\t",
            "select vm1\n| start",
        ]

        for case in test_cases:
            try:
                commands = self.parser.parse(case)
                self.assertEqual(len(commands), 2)
                self.assertIsInstance(commands[0], SelectCommand)
                self.assertIsInstance(commands[1], VMOperationCommand)
            except Exception as e:
                self.fail(f"Should handle whitespace in: '{case}', got error: {e}")

    def test_command_case_sensitivity(self):
        """Test if commands are case sensitive."""
        # Should work with lowercase
        commands = self.parser.parse("select vm1 | start")
        self.assertEqual(len(commands), 2)

        # Should fail with different case (commands are case sensitive)
        with self.assertRaises(ValueError):
            self.parser.parse("SELECT vm1 | START")


if __name__ == "__main__":
    # Run the tests with increased verbosity
    unittest.main(verbosity=2)
