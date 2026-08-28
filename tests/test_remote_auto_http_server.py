"""Tests for RemoteAutoHTTPServer."""
import errno
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add the src directory to the path to import vmanager modules
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)
from vmanager.auto_http_server import RemoteAutoHTTPServer


def _mock_completed(returncode=0, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _start_sequence(extra=None):
    """Build the standard subprocess call sequence for start()."""
    seq = [
        _mock_completed(stdout="/usr/bin/python3\n"),  # which python3
        _mock_completed(),  # kill existing servers
        _mock_completed(),  # mkdir
    ]
    if extra:
        seq.extend(extra)
    return seq


class TestRemoteAutoHTTPServer:
    def test_start_pushes_files_to_remote(self, tmp_path):
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text('{"test": true}')

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
                _mock_completed(),  # verify
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            port = server.start()
            assert port == 8000
            assert server.actual_port == 8000

    def test_start_kills_existing_servers(self, tmp_path):
        """start() must kill orphaned servers from previous runs."""
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
                _mock_completed(),  # verify
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            server.start()
            # The kill-existing call is the 2nd call (index 1)
            kill_call = mock_run.call_args_list[1]
            cmd = kill_call[0][0][8]
            assert "pkill" in cmd
            assert "virtui_auto_http" in cmd

    def test_start_cmd_detaches_process(self, tmp_path):
        """start_cmd must use a subshell + </dev/null so the SSH channel closes."""
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
                _mock_completed(),  # verify
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            server.start()
            # The start command is the 5th call (index 4)
            start_call = mock_run.call_args_list[4]
            cmd = start_call[0][0][8]
            # Subshell pattern: (nohup ... & echo $! > pid)
            assert "(nohup" in cmd
            assert "</dev/null" in cmd
            assert "& echo $! >" in cmd

    def test_start_raises_without_python3(self, tmp_path):
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.return_value = _mock_completed(returncode=1)
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            with pytest.raises(Exception, match="python3 not found"):
                server.start()

    def test_start_raises_port_in_use(self, tmp_path):
        """Port-in-use: our process dies, port held by orphan, log has the error."""
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
            ] + [
                _mock_completed(returncode=1),  # verify (x10): pid dead / port not ours
            ] * 10 + [
                _mock_completed(returncode=0),  # grep 'Address already in use' in log
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            with pytest.raises(OSError) as exc_info:
                server.start()
            assert exc_info.value.errno == errno.EADDRINUSE

    def test_start_ignores_orphan_on_same_port(self, tmp_path):
        """An orphan holding the port must not satisfy verification; our pid must be alive."""
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            # verify loop: first 9 attempts fail (orphan holds port, our pid dead),
            # 10th attempt our pid is alive and port is up
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
            ] + [
                _mock_completed(returncode=1),  # verify attempts 1-9
            ] * 9 + [
                _mock_completed(returncode=0),  # verify attempt 10: our pid alive + port up
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            port = server.start()
            assert port == 8000

    def test_start_raises_generic_failure(self, tmp_path):
        """Port never comes up and no 'Address already in use' in log -> generic error."""
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
            ] + [
                _mock_completed(returncode=1),  # verify (x10, port never up)
            ] * 10 + [
                _mock_completed(returncode=1),  # grep 'Address already in use' not found
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            with pytest.raises(Exception, match="failed to start"):
                server.start()

    def test_start_auto_port(self, tmp_path):
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")

        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
                _mock_completed(stdout="8080\n"),  # grep port from log
                _mock_completed(),  # verify
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=0, remote_host="user@host")
            port = server.start()
            assert port == 8080

    def test_stop_kills_remote_process(self, tmp_path):
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
                _mock_completed(),  # verify
                _mock_completed(),  # stop: kill
                _mock_completed(),  # stop: rm -rf
                _mock_completed(),  # stop: ssh ControlMaster exit
            ])
            server = RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host")
            server.start()
            server.stop()
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("kill" in c for c in calls)
            assert any("rm -rf" in c for c in calls)
            assert server.actual_port is None

    def test_get_url(self, tmp_path):
        server = RemoteAutoHTTPServer(tmp_path, port=8000, remote_host="user@host")
        server.actual_port = 8000
        url = server.get_url("autoinst.json", host="10.0.0.1")
        assert url == "http://10.0.0.1:8000/autoinst.json"

    def test_get_url_raises_when_not_started(self, tmp_path):
        server = RemoteAutoHTTPServer(tmp_path, port=8000, remote_host="user@host")
        with pytest.raises(RuntimeError, match="Server not started"):
            server.get_url("autoinst.json")

    def test_requires_remote_host(self, tmp_path):
        server = RemoteAutoHTTPServer(tmp_path, port=8000)
        with pytest.raises(ValueError, match="remote_host is required"):
            server.start()

    def test_context_manager(self, tmp_path):
        serve_dir = tmp_path / "auto"
        serve_dir.mkdir()
        (serve_dir / "autoinst.json").write_text("{}")
        with patch("vmanager.auto_http_server.subprocess.run") as mock_run:
            mock_run.side_effect = _start_sequence([
                _mock_completed(),  # base64 push
                _mock_completed(),  # start server
                _mock_completed(),  # verify
                _mock_completed(),  # stop: kill
                _mock_completed(),  # stop: rm -rf
                _mock_completed(),  # stop: ssh ControlMaster exit
            ])
            with RemoteAutoHTTPServer(serve_dir, port=8000, remote_host="user@host") as server:
                assert server.actual_port == 8000
            assert server.actual_port is None