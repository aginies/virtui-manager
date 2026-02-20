import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import time

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_service import VMService


class TestVMService(unittest.TestCase):
    def setUp(self):
        # Create a mock VMService instance without starting monitoring
        self.vm_service = VMService()
        # Mock the connection manager to avoid actual connections
        self.vm_service.connection_manager = MagicMock()

    def test_init(self):
        """Test VMService initialization."""
        self.assertIsInstance(self.vm_service, VMService)
        self.assertIsNotNone(self.vm_service.connection_manager)
        self.assertFalse(self.vm_service._global_updates_suspended)

    def test_set_callbacks(self):
        """Test setting update callbacks."""
        callback = MagicMock()

        self.vm_service.set_data_update_callback(callback)
        self.assertEqual(self.vm_service._data_update_callback, callback)

        self.vm_service.set_vm_update_callback(callback)
        self.assertEqual(self.vm_service._vm_update_callback, callback)

        self.vm_service.set_message_callback(callback)
        self.assertEqual(self.vm_service._message_callback, callback)

    @patch("threading.Thread")
    def test_start_stop_monitoring(self, mock_thread_class):
        """Test starting and stopping monitoring."""
        # Test start
        self.vm_service.start_monitoring()
        self.assertTrue(self.vm_service._monitoring_active)

        # Test stop
        self.vm_service.stop_monitoring()
        self.assertFalse(self.vm_service._monitoring_active)

    def test_suppress_unsuppress_events(self):
        """Test suppressing and unsuspressing VM events."""
        uuid = "test-uuid"

        self.vm_service.suppress_vm_events(uuid)
        self.assertIn(uuid, self.vm_service._suppressed_uuids)

        self.vm_service.unsuppress_vm_events(uuid)
        self.assertNotIn(uuid, self.vm_service._suppressed_uuids)

    def test_suspend_resume_global_updates(self):
        """Test suspending and resuming global updates."""
        self.vm_service.suspend_global_updates()
        self.assertTrue(self.vm_service._global_updates_suspended)

        self.vm_service.resume_global_updates()
        self.assertFalse(self.vm_service._global_updates_suspended)

    def test_update_visible_uuids(self):
        """Test updating the set of visible UUIDs."""
        test_uuids = {"uuid-1", "uuid-2", "uuid-3"}
        self.vm_service.update_visible_uuids(test_uuids)
        self.assertEqual(self.vm_service._visible_uuids, test_uuids)

        # Update with new set
        new_uuids = {"uuid-4", "uuid-5"}
        self.vm_service.update_visible_uuids(new_uuids)
        self.assertEqual(self.vm_service._visible_uuids, new_uuids)

    def test_invalidate_domain_cache(self):
        """Test invalidating the domain cache."""
        # Pre-populate caches
        self.vm_service._domain_cache = {"uuid1@uri": MagicMock()}
        self.vm_service._uuid_to_conn_cache = {"uuid1@uri": MagicMock()}
        self.vm_service._name_to_uuid_cache = {"uri": {"vm1": "uuid1"}}

        self.vm_service.invalidate_domain_cache()

        self.assertEqual(len(self.vm_service._domain_cache), 0)
        self.assertEqual(len(self.vm_service._uuid_to_conn_cache), 0)
        self.assertEqual(len(self.vm_service._name_to_uuid_cache), 0)

    def test_invalidate_vm_cache(self):
        """Test invalidating a specific VM's cache."""
        # Pre-populate caches
        uuid = "test-uuid@qemu:///system"
        self.vm_service._vm_data_cache = {uuid: {"info": (1, 2, 3), "xml": "<domain/>"}}
        self.vm_service._cpu_time_cache = {uuid: (12345, 1.0)}
        self.vm_service._io_stats_cache = {uuid: {"ts": 1.0, "disk_read": 100}}
        self.vm_service._domain_cache = {uuid: MagicMock()}
        self.vm_service._uuid_to_conn_cache = {uuid: MagicMock()}

        self.vm_service.invalidate_vm_cache(uuid)

        self.assertNotIn(uuid, self.vm_service._vm_data_cache)
        self.assertNotIn(uuid, self.vm_service._cpu_time_cache)
        self.assertNotIn(uuid, self.vm_service._io_stats_cache)
        self.assertNotIn(uuid, self.vm_service._domain_cache)
        self.assertNotIn(uuid, self.vm_service._uuid_to_conn_cache)

    def test_invalidate_vm_state_cache(self):
        """Test invalidating only state/info/xml caches for a VM."""
        uuid = "test-uuid@qemu:///system"
        # Pre-populate caches
        self.vm_service._vm_data_cache = {uuid: {"info": (1, 2, 3), "xml": "<domain/>"}}
        self.vm_service._cpu_time_cache = {uuid: (12345, 1.0)}
        self.vm_service._io_stats_cache = {uuid: {"ts": 1.0, "disk_read": 100}}
        self.vm_service._domain_cache = {uuid: MagicMock()}

        self.vm_service.invalidate_vm_state_cache(uuid)

        # Data cache should be cleared
        self.assertNotIn(uuid, self.vm_service._vm_data_cache)
        self.assertNotIn(uuid, self.vm_service._cpu_time_cache)
        self.assertNotIn(uuid, self.vm_service._io_stats_cache)
        # Domain cache should remain
        self.assertIn(uuid, self.vm_service._domain_cache)

    def test_connect_delegates_to_connection_manager(self):
        """Test that connect delegates to connection manager."""
        mock_conn = MagicMock()
        self.vm_service.connection_manager.connect.return_value = mock_conn

        result = self.vm_service.connect("qemu:///system")

        self.vm_service.connection_manager.connect.assert_called_once_with(
            "qemu:///system", force_retry=False
        )
        self.assertEqual(result, mock_conn)

    def test_disconnect_delegates_to_connection_manager(self):
        """Test that disconnect delegates to connection manager."""
        uri = "qemu:///system"
        self.vm_service.connection_manager.get_connection.return_value = None

        self.vm_service.disconnect(uri)

        self.vm_service.connection_manager.disconnect.assert_called_once_with(uri)

    def test_get_connection(self):
        """Test getting an existing connection."""
        mock_conn = MagicMock()
        self.vm_service.connection_manager.get_connection.return_value = mock_conn

        result = self.vm_service.get_connection("qemu:///system")

        self.assertEqual(result, mock_conn)
        self.vm_service.connection_manager.get_connection.assert_called_once_with("qemu:///system")

    def test_get_uri_for_connection(self):
        """Test getting URI for a connection."""
        mock_conn = MagicMock()
        self.vm_service.connection_manager.get_uri_for_connection.return_value = "qemu:///system"

        result = self.vm_service.get_uri_for_connection(mock_conn)

        self.assertEqual(result, "qemu:///system")

    def test_get_all_uris(self):
        """Test getting all URIs."""
        expected_uris = ["qemu:///system", "qemu+ssh://host/system"]
        self.vm_service.connection_manager.get_all_uris.return_value = expected_uris

        result = self.vm_service.get_all_uris()

        self.assertEqual(result, expected_uris)

    def test_reset_connection_failures(self):
        """Test resetting connection failure count."""
        uri = "qemu:///system"
        self.vm_service.reset_connection_failures(uri)

        self.vm_service.connection_manager.reset_failure_count.assert_called_once_with(uri)

    def test_get_cached_vm_info_not_in_cache(self):
        """Test getting cached VM info when not present."""
        mock_domain = MagicMock()
        mock_domain.UUIDString.return_value = "test-uuid"
        mock_domain.name.return_value = "test-vm"
        mock_domain.connect.return_value = MagicMock()
        mock_domain.connect().getURI.return_value = "qemu:///system"

        result = self.vm_service.get_cached_vm_info(mock_domain)

        self.assertIsNone(result)

    def test_get_cached_vm_details_not_in_cache(self):
        """Test getting cached VM details when not present."""
        result = self.vm_service.get_cached_vm_details("nonexistent-uuid")
        self.assertIsNone(result)

    def test_parse_xml_devices(self):
        """Test parsing XML to extract devices."""
        xml_content = """
        <domain>
            <devices>
                <disk type='file' device='disk'>
                    <target dev='vda' bus='virtio'/>
                </disk>
                <disk type='file' device='disk'>
                    <target dev='vdb' bus='virtio'/>
                </disk>
                <interface type='network'>
                    <target dev='vnet0'/>
                </interface>
            </devices>
        </domain>
        """
        devices = self.vm_service._parse_xml_devices(xml_content)

        self.assertEqual(devices["disks"], ["vda", "vdb"])
        self.assertEqual(devices["interfaces"], ["vnet0"])

    def test_parse_xml_devices_empty(self):
        """Test parsing empty/None XML."""
        result = self.vm_service._parse_xml_devices(None)
        self.assertEqual(result, {"disks": [], "interfaces": []})

        result = self.vm_service._parse_xml_devices("")
        self.assertEqual(result, {"disks": [], "interfaces": []})

    def test_parse_xml_devices_invalid_xml(self):
        """Test parsing invalid XML gracefully."""
        result = self.vm_service._parse_xml_devices("<invalid>xml")
        self.assertEqual(result, {"disks": [], "interfaces": []})

    def test_suppress_multiple_vm_events(self):
        """Test suppressing events for multiple VMs."""
        uuids = ["uuid-1", "uuid-2", "uuid-3"]

        for uuid in uuids:
            self.vm_service.suppress_vm_events(uuid)

        for uuid in uuids:
            self.assertIn(uuid, self.vm_service._suppressed_uuids)

        # Unsuppress one
        self.vm_service.unsuppress_vm_events("uuid-2")
        self.assertNotIn("uuid-2", self.vm_service._suppressed_uuids)
        self.assertIn("uuid-1", self.vm_service._suppressed_uuids)
        self.assertIn("uuid-3", self.vm_service._suppressed_uuids)

    def test_force_update_event_set(self):
        """Test that force update event can be set and cleared."""
        self.vm_service._force_update_event.clear()
        self.assertFalse(self.vm_service._force_update_event.is_set())

        self.vm_service._force_update_event.set()
        self.assertTrue(self.vm_service._force_update_event.is_set())

        self.vm_service._force_update_event.clear()
        self.assertFalse(self.vm_service._force_update_event.is_set())

    def test_events_enabled_by_default(self):
        """Test that events are enabled by default."""
        self.assertTrue(self.vm_service._events_enabled)

    def test_cache_lock_exists(self):
        """Test that cache lock is properly initialized."""
        self.assertIsNotNone(self.vm_service._cache_lock)

    def test_active_uris_lock_exists(self):
        """Test that active URIs lock is properly initialized."""
        self.assertIsNotNone(self.vm_service._active_uris_lock)

    @patch("threading.Thread")
    def test_start_monitoring_only_once(self, mock_thread_class):
        """Test that monitoring can only be started once."""
        self.vm_service._monitoring_active = True
        initial_thread = self.vm_service._monitor_thread

        self.vm_service.start_monitoring()

        # Thread should not have been recreated
        self.assertEqual(self.vm_service._monitor_thread, initial_thread)

    def test_invalidate_cache_for_uri(self):
        """Test invalidating all caches for a specific URI."""
        uri = "qemu:///system"
        uuid1 = f"uuid1@{uri}"
        uuid2 = f"uuid2@{uri}"
        other_uuid = "uuid3@qemu+ssh://other/system"

        # Pre-populate caches
        self.vm_service._vm_data_cache = {
            uuid1: {"info": (1, 2, 3)},
            uuid2: {"info": (4, 5, 6)},
            other_uuid: {"info": (7, 8, 9)},
        }
        self.vm_service._domain_cache = {
            uuid1: MagicMock(),
            uuid2: MagicMock(),
            other_uuid: MagicMock(),
        }
        self.vm_service._name_to_uuid_cache = {
            uri: {"vm1": "uuid1", "vm2": "uuid2"},
            "qemu+ssh://other/system": {"vm3": "uuid3"},
        }

        self.vm_service._invalidate_cache_for_uri(uri)

        # VMs on target URI should be cleared
        self.assertNotIn(uuid1, self.vm_service._vm_data_cache)
        self.assertNotIn(uuid2, self.vm_service._vm_data_cache)
        self.assertNotIn(uuid1, self.vm_service._domain_cache)
        self.assertNotIn(uuid2, self.vm_service._domain_cache)

        # VMs on other URI should remain
        self.assertIn(other_uuid, self.vm_service._vm_data_cache)
        self.assertIn(other_uuid, self.vm_service._domain_cache)

    def test_cache_ttl_values(self):
        """Test that cache TTL values are properly set from constants."""
        from vmanager.constants import AppCacheTimeout

        self.assertEqual(self.vm_service._info_cache_ttl, AppCacheTimeout.INFO_CACHE_TTL)
        self.assertEqual(self.vm_service._xml_cache_ttl, AppCacheTimeout.XML_CACHE_TTL)


class TestVMServiceVMActions(unittest.TestCase):
    """Test VM action methods in VMService."""

    def setUp(self):
        self.vm_service = VMService()
        self.vm_service.connection_manager = MagicMock()
        self.mock_domain = MagicMock()
        self.mock_domain.UUIDString.return_value = "test-uuid"
        self.mock_domain.name.return_value = "test-vm"
        self.mock_domain.connect.return_value = MagicMock()
        self.mock_domain.connect().getURI.return_value = "qemu:///system"

    @patch("vmanager.vm_service.start_vm")
    @patch("vmanager.vm_service.check_domain_volumes_in_use")
    def test_start_vm_calls_action(self, mock_check_volumes, mock_start):
        """Test that start_vm calls the underlying action."""
        self.mock_domain.isActive.return_value = False

        self.vm_service.start_vm(self.mock_domain)

        mock_check_volumes.assert_called_once_with(self.mock_domain)
        mock_start.assert_called_once_with(self.mock_domain)

    @patch("vmanager.vm_service.start_vm")
    def test_start_vm_skips_if_already_active(self, mock_start):
        """Test that start_vm does nothing if VM is already running."""
        self.mock_domain.isActive.return_value = True

        self.vm_service.start_vm(self.mock_domain)

        mock_start.assert_not_called()

    @patch("vmanager.vm_service.stop_vm")
    def test_stop_vm_calls_action(self, mock_stop):
        """Test that stop_vm calls the underlying action."""
        self.vm_service.stop_vm(self.mock_domain)

        mock_stop.assert_called_once_with(self.mock_domain)

    @patch("vmanager.vm_service.pause_vm")
    def test_pause_vm_calls_action(self, mock_pause):
        """Test that pause_vm calls the underlying action."""
        self.vm_service.pause_vm(self.mock_domain)

        mock_pause.assert_called_once_with(self.mock_domain)

    @patch("vmanager.vm_service.force_off_vm")
    def test_force_off_vm_calls_action(self, mock_force_off):
        """Test that force_off_vm calls the underlying action."""
        self.vm_service.force_off_vm(self.mock_domain)

        mock_force_off.assert_called_once_with(self.mock_domain)

    @patch("vmanager.vm_service.delete_vm")
    def test_delete_vm_calls_action(self, mock_delete):
        """Test that delete_vm calls the underlying action."""
        log_callback = MagicMock()

        self.vm_service.delete_vm(
            self.mock_domain,
            delete_storage=True,
            delete_nvram=True,
            log_callback=log_callback,
        )

        mock_delete.assert_called_once_with(
            self.mock_domain,
            delete_storage=True,
            delete_nvram=True,
            log_callback=log_callback,
            conn=None,
        )

    def test_resume_vm_paused_state(self):
        """Test resuming a paused VM."""
        import libvirt

        self.mock_domain.state.return_value = (libvirt.VIR_DOMAIN_PAUSED, 0)

        self.vm_service.resume_vm(self.mock_domain)

        self.mock_domain.resume.assert_called_once()

    def test_resume_vm_pmsuspended_state(self):
        """Test waking up a PM suspended VM."""
        import libvirt

        self.mock_domain.state.return_value = (libvirt.VIR_DOMAIN_PMSUSPENDED, 0)

        self.vm_service.resume_vm(self.mock_domain)

        self.mock_domain.pMWakeup.assert_called_once_with(0)


class TestVMServiceIdentity(unittest.TestCase):
    """Test VM identity resolution methods."""

    def setUp(self):
        self.vm_service = VMService()
        self.vm_service.connection_manager = MagicMock()

    def test_get_vm_identity_with_known_uri(self):
        """Test getting VM identity with a known URI."""
        mock_domain = MagicMock()
        mock_domain.name.return_value = "test-vm"
        mock_domain.UUIDString.return_value = "test-uuid-1234"

        internal_id, name = self.vm_service.get_vm_identity(mock_domain, known_uri="qemu:///system")

        self.assertEqual(name, "test-vm")
        self.assertEqual(internal_id, "test-uuid-1234@qemu:///system")

    def test_get_vm_identity_caches_result(self):
        """Test that VM identity is cached."""
        mock_domain = MagicMock()
        mock_domain.name.return_value = "test-vm"
        mock_domain.UUIDString.return_value = "test-uuid-1234"

        # First call
        self.vm_service.get_vm_identity(mock_domain, known_uri="qemu:///system")

        # Verify cached
        self.assertIn("qemu:///system", self.vm_service._name_to_uuid_cache)
        self.assertIn("test-vm", self.vm_service._name_to_uuid_cache["qemu:///system"])

    def test_get_vm_identity_uses_cache(self):
        """Test that VM identity uses cache on second call."""
        mock_domain = MagicMock()
        mock_domain.name.return_value = "test-vm"

        # Pre-populate cache
        self.vm_service._name_to_uuid_cache["qemu:///system"] = {"test-vm": "cached-uuid"}

        internal_id, name = self.vm_service.get_vm_identity(mock_domain, known_uri="qemu:///system")

        # Should use cached UUID without calling UUIDString
        self.assertEqual(internal_id, "cached-uuid@qemu:///system")
        mock_domain.UUIDString.assert_not_called()

    def test_get_vm_identity_with_string_returns_unknown(self):
        """Test that passing a string instead of domain returns unknown."""
        internal_id, name = self.vm_service.get_vm_identity("not-a-domain")

        self.assertEqual(internal_id, "unknown")
        self.assertEqual(name, "unknown")

    def test_get_internal_id(self):
        """Test getting internal ID for a domain."""
        mock_domain = MagicMock()
        mock_domain.name.return_value = "test-vm"
        mock_domain.UUIDString.return_value = "test-uuid"

        internal_id = self.vm_service._get_internal_id(mock_domain, known_uri="qemu:///system")

        self.assertEqual(internal_id, "test-uuid@qemu:///system")

    def test_prefetch_vm_xml_local(self):
        """Test XML prefetch for local VMs (all should be fetched)."""
        # Mock domains
        mock_domain1 = MagicMock()
        mock_domain1.UUIDString.return_value = "uuid-1"
        mock_domain1.name.return_value = "vm-1"
        mock_domain1.connect.return_value = MagicMock()
        mock_domain1.connect().getURI.return_value = "qemu:///system"
        mock_domain1.XMLDesc.return_value = "<domain><name>vm-1</name></domain>"

        mock_domain2 = MagicMock()
        mock_domain2.UUIDString.return_value = "uuid-2"
        mock_domain2.name.return_value = "vm-2"
        mock_domain2.connect.return_value = MagicMock()
        mock_domain2.connect().getURI.return_value = "qemu:///system"
        mock_domain2.XMLDesc.return_value = "<domain><name>vm-2</name></domain>"
        mock_domain2.state.return_value = (5, 1)  # Shutoff state

        domains = [mock_domain1, mock_domain2]

        # Prefetch for local (is_remote=False)
        self.vm_service.prefetch_vm_xml(domains, is_remote=False)

        # Both domains should have XML cached (local fetches all)
        uuid1 = self.vm_service._get_internal_id(mock_domain1)
        uuid2 = self.vm_service._get_internal_id(mock_domain2)

        self.assertIn(uuid1, self.vm_service._vm_data_cache)
        self.assertIn(uuid2, self.vm_service._vm_data_cache)
        self.assertIsNotNone(self.vm_service._vm_data_cache[uuid1].get("xml"))
        self.assertIsNotNone(self.vm_service._vm_data_cache[uuid2].get("xml"))

    @patch("libvirt.VIR_DOMAIN_RUNNING", 1)
    def test_prefetch_vm_xml_remote(self):
        """Test XML prefetch for remote VMs (only running should be fetched)."""
        # Mock running domain
        mock_running = MagicMock()
        mock_running.UUIDString.return_value = "uuid-running"
        mock_running.name.return_value = "vm-running"
        mock_running.connect.return_value = MagicMock()
        mock_running.connect().getURI.return_value = "qemu+ssh://remote/system"
        mock_running.XMLDesc.return_value = "<domain><name>vm-running</name></domain>"
        mock_running.state.return_value = (1, 1)  # Running

        # Mock stopped domain
        mock_stopped = MagicMock()
        mock_stopped.UUIDString.return_value = "uuid-stopped"
        mock_stopped.name.return_value = "vm-stopped"
        mock_stopped.connect.return_value = MagicMock()
        mock_stopped.connect().getURI.return_value = "qemu+ssh://remote/system"
        mock_stopped.state.return_value = (5, 1)  # Shutoff

        domains = [mock_running, mock_stopped]

        # Prefetch for remote (is_remote=True)
        self.vm_service.prefetch_vm_xml(domains, is_remote=True)

        # Only running domain should have XML cached
        uuid_running = self.vm_service._get_internal_id(mock_running)
        uuid_stopped = self.vm_service._get_internal_id(mock_stopped)

        self.assertIn(uuid_running, self.vm_service._vm_data_cache)
        self.assertIsNotNone(self.vm_service._vm_data_cache[uuid_running].get("xml"))

        # Stopped domain should either not be in cache or have no XML
        if uuid_stopped in self.vm_service._vm_data_cache:
            self.assertIsNone(self.vm_service._vm_data_cache[uuid_stopped].get("xml"))


if __name__ == "__main__":
    unittest.main()
