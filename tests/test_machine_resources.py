# The fakes here are named as libvirt names its methods, which is what makes them useful
# fakes; the naming rules are about the code we own.
# ruff: noqa: N802, UP031
"""Tests for the resource commands of ``vmc --machine`` (pool, volume, domain, network, host).

The hypervisor is a fake: these commands are about the words they take, the
answer they give and the status they leave with, and all three can be checked
without libvirt answering. What the fake does answer is the shape the real
libvirt returns, so a change in one of them shows up here.
"""

import ast
import io
import json
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import libvirt

from vmanager import machine_resources
from vmanager.machine_mode import (
    EXIT_CONFIG,
    EXIT_CONNECTION,
    EXIT_EXISTS,
    EXIT_NOT_FOUND,
    EXIT_OPERATION,
    EXIT_USAGE,
    MachineError,
    MachineSession,
    run_command,
)
from vmanager.machine_resources import STATE_NAMES  # noqa: F401  (registers the commands)


class FakePool:
    """A storage pool with volumes in it, as ``machine_resources`` sees it."""

    def __init__(
        self, name, path="/var/lib/libvirt/images", capacity=100 * 1024**3, free=40 * 1024**3
    ):
        self._name = name
        self.path = path
        self.capacity = capacity
        self.free = free
        self.volumes = {}

    def name(self):
        return self._name

    def isActive(self):
        return 1

    def XMLDesc(self, _flags=0):
        return "<pool><target><path>%s</path></target></pool>" % self.path

    def listVolumes(self):
        return list(self.volumes)

    def storageVolLookupByName(self, name):
        if name not in self.volumes:
            raise libvirt_error(libvirt.VIR_ERR_NO_STORAGE_VOL, "No such volume")
        return self.volumes[name]

    def info(self):
        return (
            libvirt.VIR_STORAGE_POOL_RUNNING,
            self.capacity,
            self.capacity - self.free,
            self.free,
        )

    def refresh(self, _flags=0):
        return 0

    def createXML(self, xml, _flags=0):
        """Make a volume out of the XML it is given, as libvirt would."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml)
        name = root.find("name").text
        # A name in use is refused; a deleted volume's name is free.
        if name in self.volumes and self.volumes[name].deleted_with is None:
            raise libvirt_error(libvirt.VIR_ERR_OPERATION_FAILED, f"volume '{name}' already exists")
        format_element = root.find("target/format")
        volume = FakeVolume(
            name,
            self.path,
            fmt=format_element.get("type") if format_element is not None else "raw",
            capacity=int(
                root.find("capacity").get("unit") == "bytes" and root.find("capacity").text
            ),
        )
        self.volumes[name] = volume
        return volume

    # Used only by the helpers this module borrows from storage_manager.
    def XMLDescBytes(self):  # pragma: no cover - documentation of the shape
        return self.XMLDesc().encode()


class FakeVolume:
    """A volume: its bytes, its format, and what libvirt is asked to do with them."""

    def __init__(
        self, name, pool_path="/var/lib/libvirt/images", fmt="qcow2", capacity=8 * 1024**3
    ):
        self._name = name
        self.filepath = f"{pool_path}/{name}"
        self.fmt = fmt
        self.capacity = capacity
        self.deleted_with = None

    def name(self):
        return self._name

    def path(self):
        return self.filepath

    def info(self):
        return (libvirt.VIR_STORAGE_VOL_FILE, self.capacity, 1024)

    def XMLDesc(self, _flags=0):
        return (
            "<volume><capacity unit='bytes'>%d</capacity>"
            "<allocation unit='bytes'>1024</allocation>"
            "<target><path>%s</path><format type='%s'/></target></volume>"
            % (self.capacity, self.filepath, self.fmt)
        )

    def delete(self, flags=0):
        self.deleted_with = flags

    def upload(self, stream, _offset, _length):
        self.stream = stream


class FakeDomain:
    def __init__(self, name, state=None, active=True, disks=None):
        self._name = name
        self.state = libvirt.VIR_DOMAIN_RUNNING if state is None else state
        self.active = active
        self.actions = []
        # The disks `vm_actions.start_vm` checks before it powers on.
        self.disks = [] if disks is None else disks
        self.connection = None

    def name(self):
        return self._name

    def info(self):
        return (self.state, 1024**3, 1024**3, 1, 0)

    def isActive(self):
        return 1 if self.active else 0

    def metadata(self, _type, uri):
        # As libvirt answers a domain that carries nothing in that namespace.
        raise libvirt.libvirtError(f"no metadata in {uri}")

    def create(self, _flags=0):
        self.actions.append("start")
        self.active = True

    def shutdown(self):
        self.actions.append("shutdown")
        self.active = False

    def reboot(self, _flags=0):
        self.actions.append("reboot")

    def destroy(self):
        self.actions.append("destroy")
        self.active = False

    def undefineFlags(self, _flags=0):
        self.actions.append("undefine")

    def listAllSnapshots(self):
        return []

    def UUIDString(self):
        return f"uuid-{self._name}"

    def connect(self):
        return self.connection

    def XMLDesc(self, _flags=0):
        disks = "".join(
            f"<disk device='disk' type='file'><source pool='{pool}' volume='{volume}'/></disk>"
            for pool, volume in self.disks
        )
        return f"<domain><name>{self._name}</name><devices>{disks}</devices></domain>"


class FakeConnection:
    """The pieces of a libvirt connection these commands use."""

    def __init__(self):
        self.pools = {}
        self.domains = {}
        self.networks = {}
        self.hostname = "hypervisor.example.com"
        self.capabilities = (
            "<capabilities>"
            "<guest><arch name='x86_64'><machine>pc-q35-8.2</machine>"
            "<loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE.fd</loader>"
            "</arch></guest>"
            "</capabilities>"
        )
        self.streams = []

    def listAllStoragePools(self, _flags=0):
        return list(self.pools.values())

    def storagePoolLookupByName(self, name):
        if name not in self.pools:
            raise libvirt_error(libvirt.VIR_ERR_NO_STORAGE_POOL, "No such pool")
        return self.pools[name]

    def listAllDomains(self, _flags=0):
        return list(self.domains.values())

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt_error(libvirt.VIR_ERR_NO_DOMAIN, "No such domain")
        domain = self.domains[name]
        domain.connection = self
        return domain

    def getURI(self):
        return "qemu:///test"

    def listAllNetworks(self, _flags=0):
        return list(self.networks.values())

    def networkLookupByName(self, name):
        if name not in self.networks:
            raise libvirt_error(libvirt.VIR_ERR_NO_NETWORK, "No such network")
        return self.networks[name]

    def getHostname(self):
        return self.hostname

    def getCapabilities(self):
        return self.capabilities

    def getInfo(self):
        # [model, memory (MB), cpus, mhz, nodes, sockets, cores, threads]
        return ("x86_64", 32768, 8, 2400, 1, 1, 4, 2)

    def getMemoryStats(self, _cells=0):
        return {"total": 32 * 1024**2, "free": 8 * 1024**2}

    def newStream(self, _flags=0):
        stream = MagicMock()
        self.streams.append(stream)
        return stream


def make_app(conn=None):
    app = MagicMock()
    app.active_connections = {} if conn is None else {"hypervisor": conn}
    return app


def answer(conn, command):
    """Run a machine command and give back the `data` it answered with.

    The result is the last object written: a command may have reported progress
    before it.
    """
    out = io.StringIO()
    run_command(make_app(conn), command, MachineSession(command, stdout=out))
    return json.loads(out.getvalue().strip().splitlines()[-1])["data"]


def libvirt_error(code, message="not there"):
    """A libvirt error carrying the code libvirt would have set for it.

    The bindings take the code from the C layer, so a fake says which one it
    means the way upstream's tests do: by overriding the getter.
    """

    class _Error(libvirt.libvirtError):
        def get_error_code(self):
            return code

    return _Error(message)


class PoolCommandTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.conn.pools["default"] = FakePool("default")

    def test_list_reports_each_pool_with_its_room(self):
        result = answer(self.conn, "pool list")
        self.assertEqual(result["pools"][0]["name"], "default")
        self.assertEqual(result["pools"][0]["state"], "active")
        self.assertEqual(result["pools"][0]["capacity_mb"], 102400)
        self.assertEqual(result["pools"][0]["available_mb"], 40960)

    def test_info_names_the_pool_and_where_it_is(self):
        result = answer(self.conn, "pool info default")
        self.assertEqual(result["path"], "/var/lib/libvirt/images")

    def test_an_absent_pool_is_status_five(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "pool info nothing")
        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)

    def test_create_makes_a_pool_of_a_directory_of_its_own(self):
        created = FakePool("sidekick", path="/srv/sidekick")
        with patch("vmanager.machine_resources.create_storage_pool", return_value=created) as make:
            result = answer(self.conn, "pool create sidekick --path /srv/sidekick")
        self.assertEqual(make.call_args[0][1:3], ("sidekick", "dir"))
        self.assertEqual(make.call_args[0][3], "/srv/sidekick")
        self.assertEqual(result["name"], created.name())

    def test_create_without_a_path_uses_a_directory_named_after_the_pool(self):
        created = FakePool("sidekick", path="/var/lib/libvirt/sidekick")
        with patch("vmanager.machine_resources.create_storage_pool", return_value=created) as make:
            answer(self.conn, "pool create sidekick")
        self.assertEqual(make.call_args[0][3], "/var/lib/libvirt/sidekick")

    def test_a_pool_that_is_already_there_is_not_replaced(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "pool create default")
        self.assertEqual(raised.exception.status, EXIT_EXISTS)

    def test_no_connection_is_a_connection_failure(self):
        session = MachineSession("pool list")
        with self.assertRaises(MachineError) as raised:
            run_command(make_app(None), "pool list", session)
        self.assertEqual(raised.exception.status, EXIT_CONNECTION)


class SubcommandVocabularyTest(unittest.TestCase):
    """What a resource command answers when the word after it is not one it has.

    The vocabulary is the one the dispatch reads, so these are the answers a
    scripted caller gets — and the order they come in: the subcommand is refused
    before anything about its flags is looked at.
    """

    def setUp(self):
        self.conn = FakeConnection()
        self.conn.pools["default"] = FakePool("default")

    def assert_usage(self, command, message):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, command)
        self.assertEqual(raised.exception.status, EXIT_USAGE)
        self.assertEqual(str(raised.exception), message)

    def test_a_command_alone_says_what_is_missing(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "pool")
        self.assertEqual(raised.exception.status, EXIT_USAGE)
        # The word `None` is what a caller of an older vmc was answered with here.
        self.assertNotIn("None", str(raised.exception))
        self.assertIn("pool needs a subcommand", str(raised.exception))
        self.assertIn("list", str(raised.exception))

    def test_a_word_the_command_does_not_know_is_named(self):
        self.assert_usage("pool nosuch", "Unknown pool subcommand 'nosuch'.")

    def test_the_unknown_subcommand_is_answered_before_its_flags(self):
        self.assert_usage("pool nosuch --roomy", "Unknown pool subcommand 'nosuch'.")

    def test_a_command_acting_on_one_thing_asks_for_its_name(self):
        self.assert_usage("pool info", "pool info needs a name.")
        self.assert_usage("domain state", "domain state needs a name.")

    def test_a_command_that_takes_a_name_as_a_flag_asks_for_the_flag(self):
        self.assert_usage("volume info --pool default", "volume info needs --name.")


class VolumeCommandTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.pool = FakePool("default")
        self.conn.pools["default"] = self.pool
        self.pool.volumes["base.qcow2"] = FakeVolume("base.qcow2")

    def test_list_reports_the_volumes_of_one_pool(self):
        result = answer(self.conn, "volume list --pool default")
        self.assertEqual(result["pool"], "default")
        self.assertEqual(result["volumes"][0]["name"], "base.qcow2")
        self.assertEqual(result["volumes"][0]["format"], "qcow2")

    def test_a_pool_that_is_not_named_is_a_usage_error(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "volume list")
        self.assertEqual(raised.exception.status, EXIT_USAGE)

    def test_upload_streams_the_file_into_a_volume_of_its_size(self):
        import os
        from pathlib import Path

        image = Path("/tmp") / "an-image.qcow2"
        image.write_bytes(b"x" * 2048)
        self.addCleanup(lambda: os.remove(image))

        out = io.StringIO()
        run_command(
            make_app(self.conn),
            f"volume upload --pool default --name disk.qcow2 --file {image} --format qcow2",
            MachineSession("volume upload", stdout=out),
        )
        uploaded = self.pool.volumes["disk.qcow2"]
        result = json.loads(out.getvalue().strip().splitlines()[-1])["data"]
        self.assertEqual(result["bytes"], 2048)
        self.conn.streams[-1].send.assert_called_once_with(b"x" * 2048)
        self.conn.streams[-1].finish.assert_called_once()
        self.assertEqual(uploaded.name(), "disk.qcow2")

    def test_upload_with_force_replaces_the_volume_of_that_name(self):
        """`--force` is the caller saying the name is its own to overwrite."""
        import os
        from pathlib import Path

        image = Path("/tmp") / "a-replacement.img"
        image.write_bytes(b"y" * 1024)
        self.addCleanup(lambda: os.remove(image))
        old = self.pool.volumes["base.qcow2"]

        out = io.StringIO()
        run_command(
            make_app(self.conn),
            f"volume upload --pool default --name base.qcow2 --file {image} --force",
            MachineSession("volume upload", stdout=out),
        )
        self.assertEqual(old.deleted_with, 0)
        uploaded = self.pool.volumes["base.qcow2"]
        self.assertIsNot(uploaded, old)
        result = json.loads(out.getvalue().strip().splitlines()[-1])["data"]
        self.assertEqual(result["bytes"], 1024)

    def test_a_flag_that_takes_no_value_refuses_one(self):
        """`--force yes` is a caller misled about the flag, not a yes."""
        with self.assertRaises(MachineError) as raised:
            answer(
                self.conn,
                "volume upload --pool default --name d --file /etc/hostname --force yes",
            )
        self.assertEqual(raised.exception.status, EXIT_USAGE)
        self.assertIn("--force", str(raised.exception))

    def test_force_written_last_is_still_a_flag(self):
        run_command(
            make_app(self.conn),
            "volume delete --pool default --name base.qcow2 --force",
            MachineSession("volume delete", stdout=io.StringIO()),
        )
        self.assertEqual(
            self.pool.volumes["base.qcow2"].deleted_with,
            libvirt.VIR_STORAGE_VOL_DELETE_WITH_SNAPSHOTS,
        )

    def test_upload_of_a_file_that_is_not_there_is_a_config_error(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "volume upload --pool default --name d --file /nope/nope")
        self.assertEqual(raised.exception.status, EXIT_CONFIG)

    def test_upload_does_not_take_a_name_that_is_used(self):
        with self.assertRaises(MachineError) as raised:
            answer(
                self.conn,
                "volume upload --pool default --name base.qcow2 --file /etc/hostname",
            )
        self.assertEqual(raised.exception.status, EXIT_EXISTS)

    def test_create_overlay_records_the_volume_it_came_from(self):
        with patch("vmanager.machine_resources.create_overlay_volume") as overlay:
            made = FakeVolume("instance.qcow2")
            overlay.return_value = made
            result = answer(
                self.conn,
                "volume create-overlay --pool default --name instance.qcow2 --backing base.qcow2",
            )
        self.assertEqual(overlay.call_args[0][2], "/var/lib/libvirt/images/base.qcow2")
        self.assertEqual(result["backing"], "/var/lib/libvirt/images/base.qcow2")

    def test_create_overlay_asks_for_room_of_its_own(self):
        with patch("vmanager.machine_resources.create_overlay_volume") as overlay:
            overlay.return_value = FakeVolume("instance.qcow2")
            answer(
                self.conn,
                "volume create-overlay --pool default --name instance.qcow2 "
                "--backing base.qcow2 --size 40",
            )
        self.assertEqual(overlay.call_args[1]["size_bytes"], 40 * 1024**3)

    def test_a_size_that_is_not_a_number_is_a_usage_error(self):
        """A bad value in the words is the caller's mistake, not a bad config file."""
        with self.assertRaises(MachineError) as raised:
            answer(
                self.conn,
                "volume create-overlay --pool default --name instance.qcow2 "
                "--backing base.qcow2 --size abc",
            )
        self.assertEqual(raised.exception.status, EXIT_USAGE)
        self.assertEqual(raised.exception.code, "usage")

    def test_a_format_that_is_neither_raw_nor_qcow2_is_a_usage_error(self):
        with self.assertRaises(MachineError) as raised:
            answer(
                self.conn,
                "volume upload --pool default --name d --file /etc/hostname --format ext4",
            )
        self.assertEqual(raised.exception.status, EXIT_USAGE)
        self.assertEqual(raised.exception.code, "usage")

    def test_a_flag_the_command_does_not_take_is_a_usage_error(self):
        """A flag nobody offered is answered as the mistake it is, not ignored."""
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "volume info --pool default --name base.qcow2 --recursive yes")
        self.assertEqual(raised.exception.status, EXIT_USAGE)
        self.assertIn("--recursive", str(raised.exception))

    def test_a_flag_on_a_command_that_takes_none_is_a_usage_error(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "pool info default --force yes")
        self.assertEqual(raised.exception.status, EXIT_USAGE)
        self.assertIn("--force", str(raised.exception))

    def test_a_volume_that_is_not_there_is_status_five(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "volume info --pool default --name nope")
        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)

    def test_delete_removes_it(self):
        answer(self.conn, "volume delete --pool default --name base.qcow2")
        self.assertEqual(self.pool.volumes["base.qcow2"].deleted_with, 0)

    def test_rename_is_answered_as_impossible_rather_than_attempted(self):
        """libvirt-python has no way to rename a volume, so saying yes would be a
        lie and calling the method, a crash."""
        with self.assertRaises(MachineError) as raised:
            answer(
                self.conn, "volume rename --pool default --name base.qcow2 --new-name other.qcow2"
            )
        self.assertEqual(raised.exception.status, 9)
        self.assertEqual(raised.exception.code, "unsupported")
        self.assertIn("vol-rename", str(raised.exception))


class DomainCommandTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.conn.domains["sidekick-vm"] = FakeDomain("sidekick-vm")
        self.conn.domains["quiet"] = FakeDomain(
            "quiet", state=libvirt.VIR_DOMAIN_SHUTOFF, active=False
        )

    def test_list_reports_the_machines(self):
        result = answer(self.conn, "domain list")
        self.assertEqual(
            sorted(entry["name"] for entry in result["domains"]), ["quiet", "sidekick-vm"]
        )
        self.assertEqual(
            [entry["state"] for entry in result["domains"] if entry["name"] == "quiet"], ["stopped"]
        )

    def test_list_can_be_limited_to_what_one_caller_owns(self):
        result = answer(self.conn, "domain list --prefix sidekick-")
        self.assertEqual([entry["name"] for entry in result["domains"]], ["sidekick-vm"])

    def test_state_of_a_machine_that_is_not_there_is_status_five(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "domain state nope")
        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)

    def test_address_is_the_lease_without_its_prefix(self):
        leases = [{"mac": "52:54:00:1", "ipv4": ["192.168.122.11/24"], "ipv6": []}]
        with patch("vmanager.machine_resources.get_vm_network_ip", return_value=leases):
            result = answer(self.conn, "domain address sidekick-vm")
        self.assertEqual(result["ipv4"], "192.168.122.11")

    def test_a_machine_with_no_lease_is_not_an_error(self):
        with patch("vmanager.machine_resources.get_vm_network_ip", return_value=[]):
            result = answer(self.conn, "domain address sidekick-vm")
        self.assertIsNone(result["ipv4"])

    def test_the_facts_a_caller_stored_come_back(self):
        stored = (
            "<properties xmlns='https://virtui-manager/metadata/1'>"
            "<owner>mcp-sidekick</owner><instance>worker01</instance></properties>"
        )
        self.conn.domains["sidekick-vm"].metadata = lambda _type, _uri: stored
        result = answer(self.conn, "domain metadata sidekick-vm")
        self.assertEqual(result["name"], "sidekick-vm")
        self.assertEqual(result["metadata"], {"owner": "mcp-sidekick", "instance": "worker01"})

    def test_a_machine_with_nothing_stored_answers_nothing(self):
        """A caller asking what it left on a machine is not being told an error."""
        self.assertEqual(answer(self.conn, "domain metadata quiet")["metadata"], {})

    def test_what_the_provisioner_writes_the_command_reads(self):
        """The namespace the writer uses is the namespace the reader asks for."""
        import xml.etree.ElementTree as ET

        from vmanager.vm_provisioner import METADATA_NS, VMProvisioner

        written = ET.fromstring(
            f"<root>{VMProvisioner._metadata_xml({'owner': 'mcp-sidekick'})}</root>"
        )
        stored = ET.tostring(written.find(f"metadata/{{{METADATA_NS}}}properties")).decode()
        self.conn.domains["sidekick-vm"].metadata = lambda _type, _uri: stored
        self.assertEqual(
            answer(self.conn, "domain metadata sidekick-vm")["metadata"],
            {"owner": "mcp-sidekick"},
        )

    def test_powering_on_a_machine_starts_it(self):
        answer(self.conn, "domain start quiet")
        self.assertEqual(self.conn.domains["quiet"].actions, ["start"])

    def test_shutdown_is_the_gentle_one_and_destroy_the_plug(self):
        """The two words the caller distinguishes, told apart by the hypervisor.

        `poweroff` is not a method of the binding — it never was — so a machine
        calling `domain shutdown` was answered with a crash rather than a guest
        being asked politely.
        """
        self.conn.domains["plugged"] = FakeDomain("plugged")

        answer(self.conn, "domain shutdown sidekick-vm")
        self.assertEqual(self.conn.domains["sidekick-vm"].actions, ["shutdown"])
        self.assertEqual(self.conn.domains["plugged"].actions, [])

        answer(self.conn, "domain destroy plugged")
        self.assertEqual(self.conn.domains["plugged"].actions, ["destroy"])

    def test_starting_checks_the_disks_before_powering_on(self):
        """The check the interactive `start` makes is the same command's check."""
        self.conn.domains["missing"] = FakeDomain(
            "missing", active=False, disks=[("default", "gone.qcow2")]
        )
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "domain start missing")
        self.assertEqual(raised.exception.status, EXIT_OPERATION)
        self.assertEqual(self.conn.domains["missing"].actions, [])

    def test_a_machine_told_to_stop_when_stopped_is_a_failed_operation(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "domain shutdown quiet")
        self.assertEqual(raised.exception.status, EXIT_OPERATION)

    def test_a_running_machine_is_not_undone(self):
        with self.assertRaises(MachineError) as raised:
            answer(self.conn, "domain undefine sidekick-vm")
        self.assertEqual(raised.exception.status, EXIT_OPERATION)
        self.assertEqual(self.conn.domains["sidekick-vm"].actions, [])

    def test_a_stopped_machine_is_undone(self):
        answer(self.conn, "domain undefine quiet")
        self.assertEqual(self.conn.domains["quiet"].actions, ["undefine"])


class FakeNetwork:
    """A network, written out so it answers only what the binding answers."""

    def __init__(self, name, active=1, bridge="virbr0", autostart=1):
        self._name = name
        self._active = active
        self._bridge = bridge
        self._autostart = autostart

    def name(self):
        return self._name

    def isActive(self):  # pylint: disable=invalid-name
        return self._active

    def XMLDesc(self, _flags=0):  # pylint: disable=invalid-name
        return f"<network><bridge name='{self._bridge}'/></network>"

    def autostart(self):
        return self._autostart

    def UUIDString(self):  # pylint: disable=invalid-name
        return "0000-0000-" + self._name


class NetworkAndHostTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.conn.networks["default"] = FakeNetwork("default")

    def test_network_list_reports_what_is_there(self):
        result = answer(self.conn, "network list")
        self.assertEqual(result["networks"][0]["name"], "default")
        self.assertEqual(result["networks"][0]["state"], "active")

    def test_network_info_names_the_bridge_and_the_flags(self):
        result = answer(self.conn, "network info default")
        self.assertEqual(result["bridge"], "virbr0")
        self.assertEqual(result["state"], "active")
        self.assertTrue(result["autostart"])

    def test_capabilities_say_what_can_run_here(self):
        result = answer(self.conn, "host capabilities")
        self.assertEqual(result["architectures"], ["x86_64"])
        self.assertTrue(result["uefi"])
        self.assertEqual(result["host_cpus"], 8)

    def test_capabilities_of_a_hypervisor_without_uefi(self):
        self.conn.capabilities = "<capabilities><guest><arch name='x86_64'/></guest></capabilities>"
        result = answer(self.conn, "host capabilities")
        self.assertFalse(result["uefi"])

    def test_the_architectures_are_named_once_from_every_guest(self):
        """One guest per architecture is how a hypervisor describes them, so the list
        is the architectures, not the guests; a loader that does not say what kind it
        is, is the kind a UEFI guest boots from."""
        self.conn.capabilities = (
            "<capabilities>"
            "<guest><arch name='x86_64'>"
            "<loader>/usr/share/OVMF/OVMF_CODE.fd</loader>"
            "</arch></guest>"
            "<guest><arch name='x86_64'><machine>q35</machine></arch></guest>"
            "<guest><arch name='aarch64'/></guest>"
            "</capabilities>"
        )
        result = answer(self.conn, "host capabilities")
        self.assertEqual(result["architectures"], ["x86_64", "aarch64"])
        self.assertTrue(result["uefi"])

    def test_capabilities_a_hypervisor_cannot_answer_are_still_an_answer(self):
        """A hypervisor that refuses, answers with nothing, or answers with half a
        document is not a failed command: what can run here is nothing, and UEFI is not
        available. Both questions used to be read out of the document twice, and each
        read decided on its own what it could not read meant; they now decide once, and
        the answer is what each of them said alone.
        """

        def refuses():
            raise libvirt.libvirtError("capabilities not available")

        for unable in (refuses, lambda: None, lambda: "<capabilities><guest>"):
            with self.subTest(unable=unable):
                self.conn.getCapabilities = unable
                result = answer(self.conn, "host capabilities")
                self.assertEqual(result["architectures"], [])
                self.assertFalse(result["uefi"])

    def test_resources(self):
        result = answer(self.conn, "host resources")
        self.assertEqual(result["memory_mb"], 32768)


class OneReadingPerResourceTest(unittest.TestCase):
    """The table a person reads and the answer a program reads are drawn from the
    same reading of the hypervisor.

    They were not: the machine commands picked the fields out of libvirt a second
    time, and did it slightly differently — a pool's free space and a volume's
    format were read only for the answer, since the table has no column for them.
    Two readings of one thing is how a pool came to be `active` in one and
    `unavailable` in the other, which is the drift #12 was written about. So the
    agreement is checked here, on the same fake hypervisor, in one test per
    resource.
    """

    def setUp(self):
        self.conn = FakeConnection()
        self.pool = FakePool("default")
        self.conn.pools["default"] = self.pool
        self.pool.volumes["base.qcow2"] = FakeVolume("base.qcow2", self.pool.path)

    def test_a_pool_is_the_same_thing_in_both(self):
        from vmanager.storage_manager import list_storage_pools, pool_facts

        facts = pool_facts(self.pool)
        self.assertEqual(facts["available"], self.pool.free)
        self.assertEqual(facts["path"], self.pool.path)

        table = list_storage_pools(self.conn)[0]
        self.assertEqual(
            {key: table[key] for key in ("name", "status", "capacity", "available", "path")},
            {key: facts[key] for key in ("name", "status", "capacity", "available", "path")},
        )

        told = answer(self.conn, "pool list")["pools"][0]
        self.assertEqual(told["state"], facts["status"])
        self.assertEqual(told["capacity_mb"], round(facts["capacity"] / 1024 / 1024))
        self.assertEqual(told["available_mb"], round(facts["available"] / 1024 / 1024))

    def test_a_network_is_the_same_thing_in_both(self):
        from vmanager.libvirt_utils import network_facts
        from vmanager.network_manager import list_networks

        network = FakeNetwork("default", bridge="virbr7")
        self.conn.networks["default"] = network

        facts = network_facts(network)
        self.assertTrue(facts["active"])
        self.assertTrue(facts["autostart"])
        self.assertEqual(facts["bridge_name"], "virbr7")

        table = list_networks(self.conn)[0]
        self.assertEqual(table["name"], facts["name"])
        self.assertEqual(bool(table["active"]), facts["active"])
        self.assertEqual(bool(table["autostart"]), facts["autostart"])

        told = answer(self.conn, "network info default")
        self.assertEqual(told["state"], "active" if facts["active"] else "inactive")
        self.assertEqual(told["bridge"], facts["bridge_name"])
        self.assertEqual(told["autostart"], facts["autostart"])

    def test_a_volume_is_the_same_thing_in_both(self):
        from vmanager.storage_manager import list_storage_volumes, volume_facts

        facts = volume_facts(self.pool.volumes["base.qcow2"])
        self.assertEqual(facts["name"], "base.qcow2")
        self.assertEqual(facts["format"], "qcow2")

        table = list_storage_volumes(self.pool)[0]
        self.assertEqual(
            {key: table[key] for key in ("name", "capacity", "allocation", "path", "format")},
            {key: facts[key] for key in ("name", "capacity", "allocation", "path", "format")},
        )

        told = answer(self.conn, "volume info --pool default --name base.qcow2")
        self.assertEqual(told["capacity_mb"], round(facts["capacity"] / 1024 / 1024))
        self.assertEqual(told["format"], facts["format"])


class CacheInvalidationTest(unittest.TestCase):
    """A command that changed the hypervisor must not leave the tool reading the old one.

    What the interactive tool remembers about a hypervisor — the state of each
    machine, the volumes of a pool, the pools themselves — is held in caches that
    live as long as the process, and a caller can ask for more than one thing in
    a session: `domain start worker01` then `domain state worker01` has to answer
    with the machine as it is now, and a pool that was just created has to be in
    the list. Each mutating command is checked against the caches its interactive
    equivalent empties.
    """

    def setUp(self):
        self.conn = FakeConnection()
        self.pool = FakePool("default")
        self.conn.pools["default"] = self.pool
        self.pool.volumes["base.qcow2"] = FakeVolume("base.qcow2")

    def app_for(self, active=True):
        """A fresh session on this hypervisor, with a machine in it or out of it."""
        self.conn.domains["worker01"] = FakeDomain("worker01", active=active)
        return make_app(self.conn)

    def quiet_run(self, app, command):
        run_command(app, command, MachineSession(command, stdout=io.StringIO()))

    def test_a_domain_action_forgets_the_state_of_that_machine(self):
        for action in ("start", "shutdown", "reboot", "destroy"):
            with self.subTest(action=action):
                app = self.app_for(active=action != "start")
                self.quiet_run(app, f"domain {action} worker01")
                app.vm_service.invalidate_vm_state_cache.assert_called_once()

    def test_undefining_a_machine_forgets_it_and_its_state(self):
        app = self.app_for(active=False)
        self.quiet_run(app, "domain undefine worker01")
        app.vm_service.invalidate_domain_cache.assert_called_once()

    def test_a_volume_that_changed_forgets_the_volumes_of_its_pool(self):
        import os
        from pathlib import Path

        image = Path("/tmp") / "cache-check.qcow2"
        image.write_bytes(b"x" * 1024)
        self.addCleanup(lambda: os.remove(image))

        commands = {
            "upload": f"volume upload --pool default --name new.qcow2 --file {image}",
            "create-overlay": (
                "volume create-overlay --pool default --name child.qcow2 --backing base.qcow2"
            ),
            "delete": "volume delete --pool default --name base.qcow2",
            # `rename` is not here: it changes nothing, so it has nothing to forget.
        }
        for name, command in commands.items():
            with self.subTest(command=name):
                self.pool.volumes["base.qcow2"] = FakeVolume("base.qcow2")
                with (
                    patch.object(machine_resources.list_storage_volumes, "cache_clear") as volumes,
                    patch.object(machine_resources.list_storage_pools, "cache_clear") as pools,
                    patch("vmanager.machine_resources.create_overlay_volume") as overlay,
                ):
                    overlay.return_value = FakeVolume("child.qcow2")
                    self.quiet_run(make_app(self.conn), command)
                volumes.assert_called_once()
                # The space of the pool moved with the volume in it.
                pools.assert_called_once()

    def test_a_pool_that_changed_forgets_the_pools(self):
        with (
            patch.object(machine_resources.list_storage_pools, "cache_clear") as pools,
            patch("vmanager.machine_resources.create_storage_pool") as create,
        ):
            create.return_value = FakePool("fast")
            self.quiet_run(make_app(self.conn), "pool create fast --path /tmp/fast")
        pools.assert_called_once()

    def test_a_command_that_changed_nothing_forgets_nothing(self):
        """A refusal is not a change: the caches are as they were, not emptier."""
        app = self.app_for(active=True)
        with self.assertRaises(MachineError):
            self.quiet_run(app, "domain undefine worker01")
        app.vm_service.invalidate_vm_state_cache.assert_not_called()
        app.vm_service.invalidate_domain_cache.assert_not_called()

    def test_a_machine_that_is_not_there_forgets_nothing(self):
        app = make_app(self.conn)
        with self.assertRaises(MachineError):
            self.quiet_run(app, "domain start nosuch")
        app.vm_service.invalidate_vm_state_cache.assert_not_called()


class TestTheCommandsOnlyUseMethodsTheBindingHas(unittest.TestCase):
    """The names the resource commands call on libvirt objects, looked up on the
    binding itself.

    A fake is free to invent a method, so a test written against one can stay
    green while the command crashes on a real hypervisor: `poweroff`,
    `getAutostart` and `rename` were all called by commands whose tests passed.
    So the names are read out of this module's own source here and checked
    against the classes they belong to. Which class is decided by the name the
    code gives the variable — `pool`, `vol`, `network`, `domain`, `conn`,
    `stream` — a convention these commands keep.
    """

    CLASSES_BY_VARIABLE = {
        "pool": "virStoragePool",
        "vol": "virStorageVol",
        "volume": "virStorageVol",
        "backing": "virStorageVol",
        "network": "virNetwork",
        "net": "virNetwork",
        "domain": "virDomain",
        "conn": "virConnect",
        "stream": "virStream",
    }

    @unittest.skipUnless(
        isinstance(getattr(libvirt, "VIR_ERR_NO_DOMAIN", None), int),
        "needs the real libvirt binding, whose constants are numbers",
    )
    def test_every_libvirt_method_a_command_calls_exists(self):
        source = Path(__file__).resolve().parents[1] / "src" / "vmanager" / "machine_resources.py"
        called = defaultdict(set)
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            target = node.func if isinstance(node, ast.Call) else None
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                class_name = self.CLASSES_BY_VARIABLE.get(target.value.id)
                if class_name:
                    called[class_name].add(target.attr)
        absent = {
            class_name: sorted(
                name for name in names if not hasattr(getattr(libvirt, class_name), name)
            )
            for class_name, names in sorted(called.items())
        }
        self.assertEqual(
            {name: missing for name, missing in absent.items() if missing},
            {},
            "these commands call libvirt methods the binding does not have",
        )


if __name__ == "__main__":
    unittest.main()
