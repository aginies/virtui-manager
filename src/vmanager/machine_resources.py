"""Machine commands over a hypervisor's resources: pools, volumes, domains, networks, host.

Documented with the rest of ``--machine`` in
``documentation/manual/cli.md``. A scripted caller needs the facts these commands
report and the operations they perform, with an exit status that says what
happened. These commands share the helpers the interactive commands call, and
depart from that tool in three deliberate places, each recorded in ``docs/adr/``
and listed in ``docs/adr/README.md``. They are thin: they read the words of the
command, call the helper, and return the ``data`` dict.

One connection serves a run. ``vmc -u <uri>`` connects to that hypervisor before
the command runs, which is what ``_connection`` returns; a command never picks a
server of its own, since the caller named the one it means.
"""

import os
import xml.etree.ElementTree as ET

import libvirt

from .constants import MachineProgressStage, MachineResourceText
from .libvirt_utils import get_host_resources, network_facts
from .machine_mode import (
    EXIT_CONFIG,
    EXIT_CONNECTION,
    EXIT_EXISTS,
    EXIT_NOT_FOUND,
    EXIT_OPERATION,
    EXIT_UNSUPPORTED,
    EXIT_USAGE,
    MachineError,
    machine_command,
)
from .storage_manager import (
    _ensure_pool_active,
    _safe_get_volume_path,
    create_overlay_volume,
    create_storage_pool,
    delete_volume,
    list_storage_pools,
    list_storage_volumes,
    pool_facts,
    pool_target_path,
    volume_facts,
)
from .vm_actions import force_off_vm, guest_reboot, start_vm, stop_vm
from .vm_provisioner import METADATA_NS
from .vm_queries import get_vm_network_ip

# Where the target of a pool created by name goes, as the images of a pool the
# caller did not describe should not land in another pool's directory.
POOL_PARENT = "/var/lib/libvirt"

# The words of a domain's state, as a caller switches on them. libvirt numbers
# them; the names are what a program compares, and what `vmc` prints.
#
# This is the one reading the machine surface does not share with the interactive
# one: the interactive tool answers from the caches of `vm_service`, these commands
# ask the hypervisor. Why, and what would make it wrong, is recorded in
# docs/adr/0002-machine-commands-read-the-hypervisor-directly.md. The names come
# from here rather than from `vm_queries.get_status`, which answers the localized
# words a person reads, for the reason in decision 0001: a program compares
# `running`, and a word it compares must not move. What is read is the same call
# either way — `domain.info()`.
STATE_NAMES = {
    libvirt.VIR_DOMAIN_NOSTATE: "nostate",
    libvirt.VIR_DOMAIN_RUNNING: "running",
    libvirt.VIR_DOMAIN_BLOCKED: "blocked",
    libvirt.VIR_DOMAIN_PAUSED: "paused",
    libvirt.VIR_DOMAIN_SHUTDOWN: "shutting down",
    libvirt.VIR_DOMAIN_SHUTOFF: "stopped",
    libvirt.VIR_DOMAIN_CRASHED: "crashed",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
}


def _words(args, path=""):
    """Split a command's words into its positionals and its options.

    `path` is the command these words belong to (`"volume upload"`), which decides
    what a flag may be: one the command has never heard of, one given a value it
    does not take, or one missing the value it needs are each answered at the word
    that is wrong. Ignoring any of them would report success as though the flag
    had been heeded.
    """
    allowed = OPTIONS.get(path)
    positional, options = [], {}
    index = 0
    while index < len(args):
        word = args[index]
        if word.startswith("--"):
            name = word[2:]
            if allowed is not None and name not in allowed:
                raise MachineError(
                    MachineResourceText.NO_SUCH_FLAG_TEMPLATE.format(command=path, flag=name),
                    EXIT_USAGE,
                    "usage",
                )
            if name in FLAGS_WITHOUT_VALUE:
                if index + 1 < len(args) and not args[index + 1].startswith("--"):
                    raise MachineError(
                        MachineResourceText.FLAG_TAKES_NO_VALUE_TEMPLATE.format(flag=name),
                        EXIT_USAGE,
                        "usage",
                    )
                options[name] = True
                index += 1
                continue
            if index + 1 >= len(args):
                raise MachineError(
                    MachineResourceText.FLAG_NEEDS_VALUE_TEMPLATE.format(flag=name),
                    EXIT_USAGE,
                    "usage",
                )
            options[name] = args[index + 1]
            index += 2
        else:
            positional.append(word)
            index += 1
    return positional, options


# The flags each subcommand takes. A flag outside this table is answered as the
# mistake it is: ignoring one would report success as though it had been heeded.
OPTIONS = {
    "pool list": (),
    "pool info": (),
    "pool create": ("path",),
    "volume list": ("pool",),
    "volume info": ("pool", "name"),
    "volume upload": ("pool", "name", "file", "format", "force"),
    "volume create-overlay": ("pool", "name", "backing", "size", "force"),
    "volume delete": ("pool", "name", "force"),
    "volume rename": ("pool", "name", "new-name", "force"),
    "domain list": ("prefix",),
    "domain state": (),
    "domain address": (),
    "domain metadata": (),
    "domain start": (),
    "domain shutdown": (),
    "domain reboot": (),
    "domain destroy": (),
    "domain undefine": (),
    "network list": (),
    "network info": (),
    "host capabilities": (),
    "host resources": (),
}

# The flags that are their own value: written means yes. `--force yes` is a caller
# misled about the flag, and is answered as a bad argument rather than believed.
FLAGS_WITHOUT_VALUE = {"force"}

# The subcommands that act on one named thing, written after the command rather
# than flagged: `pool info default`, `domain state web01`. `OPTIONS` says what a
# subcommand may be given, this says what it must be given, and the manual writes
# the same thing as `<name>`; tests/test_cli_docs.py holds the three together.
SUBJECT_WRITTEN_AFTER = {
    "pool info",
    "pool create",
    "domain state",
    "domain address",
    "domain metadata",
    "domain start",
    "domain shutdown",
    "domain reboot",
    "domain destroy",
    "domain undefine",
    "network info",
}


def _read_subcommand(command, args):
    """The subcommand asked for, with the positionals and options after it.

    `OPTIONS` is the vocabulary these commands dispatch on, so a word outside it
    is refused before any flag is read: a caller is told the subcommand is the
    thing it does not know, rather than that a flag it happened to add is not
    real. Reading the subject here too means a command cannot forget to.
    """
    subcommand = args[0] if args else None
    if subcommand is None:
        # A command spoken alone: the vocabulary it could have gone on to is the
        # answer that helps, and it is the same list the guard below reads.
        known = ", ".join(
            sorted(word[len(command) + 1 :] for word in OPTIONS if word.startswith(f"{command} "))
        )
        raise MachineError(
            MachineResourceText.NEEDS_SUBCOMMAND_TEMPLATE.format(
                command=command, subcommands=known
            ),
            EXIT_USAGE,
            "usage",
        )
    path = f"{command} {subcommand}"
    if path not in OPTIONS:
        raise MachineError(
            MachineResourceText.UNKNOWN_SUBCOMMAND_TEMPLATE.format(
                command=command, subcommand=subcommand
            ),
            EXIT_USAGE,
            "usage",
        )
    positional, options = _words(args[1:], path)
    if path in SUBJECT_WRITTEN_AFTER and not positional:
        raise MachineError(
            MachineResourceText.NEEDS_NAME_TEMPLATE.format(command=path), EXIT_USAGE, "usage"
        )
    return subcommand, positional, options


def _required(options, name, command):
    """The value of a mandatory option, or a usage error naming it."""
    value = options.get(name)
    if not value:
        raise MachineError(
            MachineResourceText.NEEDS_FLAG_TEMPLATE.format(command=command, flag=name),
            EXIT_USAGE,
            "usage",
        )
    return value


def _connection(app):
    """The connection of this run, which ``vmc -u`` opened before the command."""
    if not app.active_connections:
        raise MachineError(
            MachineResourceText.NO_HYPERVISOR_CONNECTED,
            EXIT_CONNECTION,
            "connection_failed",
        )
    if len(app.active_connections) > 1:
        raise MachineError(
            MachineResourceText.TOO_MANY_HYPERVISORS_TEMPLATE.format(
                count=len(app.active_connections)
            ),
            EXIT_CONFIG,
            "ambiguous_connection",
        )
    return next(iter(app.active_connections.values()))


def _not_found(error, what, name):
    """Say whether a libvirt error means "there is no such thing"."""
    codes = getattr(error, "get_error_code", lambda: None)()
    missing = (
        libvirt.VIR_ERR_NO_DOMAIN,
        libvirt.VIR_ERR_NO_STORAGE_POOL,
        libvirt.VIR_ERR_NO_STORAGE_VOL,
        libvirt.VIR_ERR_NO_NETWORK,
    )
    if codes in missing:
        return MachineError(
            MachineResourceText.NOT_FOUND_TEMPLATE.format(kind=what, name=name),
            EXIT_NOT_FOUND,
            "not_found",
        )
    return MachineError(
        MachineResourceText.COULD_NOT_READ_TEMPLATE.format(kind=what, name=name, error=error),
        EXIT_OPERATION,
        "operation_failed",
    )


def _pool(conn, name):
    try:
        return conn.storagePoolLookupByName(name)
    except libvirt.libvirtError as e:
        raise _not_found(e, "storage pool", name) from e


def _volume(pool, name):
    try:
        return pool.storageVolLookupByName(name)
    except libvirt.libvirtError as e:
        raise _not_found(e, "volume", name) from e


def _domain(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError as e:
        raise _not_found(e, "domain", name) from e


def _domain_state(domain):
    try:
        return STATE_NAMES.get(domain.info()[0], "unknown")
    except libvirt.libvirtError:
        # A domain can vanish between the lookup and the read. A caller that
        # asked for its state is told "missing", which is what it has.
        return "missing"


def _volume_entry(vol):
    """A volume as this protocol answers it: the facts, in megabytes."""
    facts = volume_facts(vol)
    return {
        "name": facts["name"],
        "path": facts["path"],
        "capacity_mb": _megabytes(facts["capacity"]),
        "allocation_mb": _megabytes(facts["allocation"]),
        "format": facts["format"],
    }


def _state(active):
    """Whether something runs, in the one word a caller switches on."""
    return "active" if active else "inactive"


def _megabytes(byte_count):
    """Bytes as a caller counts them. A number a script compares is worth the
    rounding; a fraction of a megabyte decides nothing."""
    return round((byte_count or 0) / 1024 / 1024)


# --------------------------------------------------------------------------------------
# Storage pools
# --------------------------------------------------------------------------------------


@machine_command("pool")
def _pool_command(app, args, _session):
    """``pool list``, ``pool info <name>``, ``pool create <name> [--path <dir>]``."""
    conn = _connection(app)
    subcommand, positional, options = _read_subcommand("pool", args)

    if subcommand == "list":
        entries = []
        for pool in conn.listAllStoragePools(0):
            try:
                # The reading behind the interactive `list_pool` table, so the
                # two cannot tell different stories about one pool.
                facts = pool_facts(pool)
            except libvirt.libvirtError:
                # A pool that cannot say its own name is not one a caller could
                # act on by it, so it is left out rather than named "unknown".
                continue
            entries.append(
                {
                    "name": facts["name"],
                    "state": facts["status"],
                    "capacity_mb": _megabytes(facts["capacity"]),
                    "available_mb": _megabytes(facts["available"]),
                }
            )
        return {"pools": entries}

    if subcommand == "info":
        facts = pool_facts(_pool(conn, positional[0]))
        return {
            "name": facts["name"],
            "state": facts["status"],
            "capacity_mb": _megabytes(facts["capacity"]),
            "allocation_mb": _megabytes(facts["allocation"]),
            "available_mb": _megabytes(facts["available"]),
            "path": facts["path"],
        }

    if subcommand == "create":
        name = positional[0]
        # A scripted caller that only says a name means a directory pool of its
        # own, not somebody else's images directory.
        target = options.get("path") or os.path.join(POOL_PARENT, name)
        try:
            conn.storagePoolLookupByName(name)
            raise MachineError(
                MachineResourceText.POOL_ALREADY_DEFINED_TEMPLATE.format(name=name),
                EXIT_EXISTS,
                "exists",
            )
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_POOL:
                raise _not_found(e, "storage pool", name) from e
        try:
            pool = create_storage_pool(conn, name, "dir", target)
        except Exception as e:
            raise MachineError(
                MachineResourceText.COULD_NOT_CREATE_POOL_TEMPLATE.format(name=name, error=e),
                EXIT_OPERATION,
                "operation_failed",
            ) from e
        _pools_changed()
        return {"name": pool.name(), "path": pool_target_path(pool)}


# --------------------------------------------------------------------------------------
# Storage volumes
# --------------------------------------------------------------------------------------


@machine_command("volume")
def _volume_command(app, args, session):
    """``volume list|info|upload|create-overlay|delete|rename --pool <pool> --name <name>``."""
    conn = _connection(app)
    subcommand, positional, options = _read_subcommand("volume", args)
    if positional:
        raise MachineError(
            MachineResourceText.UNEXPECTED_ARGUMENT_PREFIX + positional[0], EXIT_USAGE, "usage"
        )

    if subcommand == "list":
        pool = _pool(conn, _required(options, "pool", "volume list"))
        _ensure_pool_active(pool)
        try:
            names = pool.listVolumes()
        except libvirt.libvirtError as e:
            raise MachineError(
                MachineResourceText.COULD_NOT_LIST_POOL_TEMPLATE.format(error=e),
                EXIT_OPERATION,
                "operation_failed",
            ) from e
        entries = []
        for name in names:
            try:
                entries.append(_volume_entry(pool.storageVolLookupByName(name)))
            except libvirt.libvirtError:
                continue
        return {"pool": pool.name(), "volumes": entries}

    if subcommand == "info":
        pool = _pool(conn, _required(options, "pool", "volume info"))
        vol = _volume(pool, _required(options, "name", "volume info"))
        return _volume_entry(vol)

    if subcommand == "upload":
        return _volume_upload(conn, options, session)

    if subcommand == "create-overlay":
        pool = _pool(conn, _required(options, "pool", "volume create-overlay"))
        name = _required(options, "name", "volume create-overlay")
        backing_name = _required(options, "backing", "volume create-overlay")
        backing = _volume(pool, backing_name)
        backing_path = _safe_get_volume_path(backing)
        if not backing_path:
            raise MachineError(
                MachineResourceText.BACKING_HAS_NO_PATH_TEMPLATE.format(name=backing_name),
                EXIT_OPERATION,
                "operation_failed",
            )
        size = options.get("size")
        if size is not None:
            try:
                size_bytes = int(float(size) * 1024**3)
            except ValueError:
                raise MachineError(
                    MachineResourceText.SIZE_NOT_A_NUMBER_TEMPLATE.format(value=size),
                    EXIT_USAGE,
                    "usage",
                ) from None
        else:
            size_bytes = None
        _refuse_existing(pool, name, options.get("force"))
        try:
            vol = create_overlay_volume(pool, name, backing_path, size_bytes=size_bytes)
        except Exception as e:
            raise MachineError(
                MachineResourceText.COULD_NOT_CREATE_OVERLAY_TEMPLATE.format(name=name, error=e),
                EXIT_OPERATION,
                "operation_failed",
            ) from e
        _volumes_changed()
        return {"name": vol.name(), "path": _safe_get_volume_path(vol), "backing": backing_path}

    if subcommand == "delete":
        pool = _pool(conn, _required(options, "pool", "volume delete"))
        name = _required(options, "name", "volume delete")
        vol = _volume(pool, name)
        if options.get("force"):
            try:
                vol.delete(libvirt.VIR_STORAGE_VOL_DELETE_WITH_SNAPSHOTS)
                _volumes_changed()
                return {"name": name}
            except libvirt.libvirtError:
                pass  # Fall through to a plain delete, which says what went wrong.
        try:
            delete_volume(vol)
        except Exception as e:
            raise MachineError(
                MachineResourceText.COULD_NOT_DELETE_VOLUME_TEMPLATE.format(name=name, error=e),
                EXIT_OPERATION,
                "operation_failed",
            ) from e
        _volumes_changed()
        return {"name": name}

    if subcommand == "rename":
        # There is nothing to call: the Python binding does not wrap
        # `virStorageVolRename`, so this refuses and names the tool that can do it.
        # Why status 9, and why not a copy or a `virsh` call, is recorded in
        # docs/adr/0003-renaming-a-volume-answers-status-9.md.
        raise MachineError(
            MachineResourceText.VOLUME_RENAME_IS_NOT_POSSIBLE, EXIT_UNSUPPORTED, "unsupported"
        )


def _refuse_existing(pool, name, force):
    """Stop a create or a rename that would take a name the pool already has.

    `--force` is the caller saying the name is its own to overwrite, so the volume
    standing on it is deleted first — which is what makes room for the new one, and
    what a caller that names the name twice means by it.
    """
    try:
        existing = pool.storageVolLookupByName(name)
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_STORAGE_VOL:
            return
        raise _not_found(e, "volume", name) from e
    if not force:
        raise MachineError(
            MachineResourceText.VOLUME_ALREADY_IN_POOL_TEMPLATE.format(name=name),
            EXIT_EXISTS,
            "exists",
        )
    try:
        existing.delete(0)
    except libvirt.libvirtError as e:
        raise MachineError(
            MachineResourceText.COULD_NOT_REPLACE_VOLUME_TEMPLATE.format(name=name, error=e),
            EXIT_OPERATION,
            "operation_failed",
        ) from e
    # The name is free now, whether or not the new volume ends up on it.
    _volumes_changed()


def _volume_upload(conn, options, session):
    """``volume upload --pool P --name N --file F [--format raw|qcow2] [--force]``.

    The volume is made the size of the file and filled in one stream, so a
    caller with an image on its own disk does not have to reach the hypervisor's
    filesystem. This is the same stream a restore uses.
    """
    pool = _pool(conn, _required(options, "pool", "volume upload"))
    name = _required(options, "name", "volume upload")
    source = _required(options, "file", "volume upload")
    vol_format = options.get("format", "raw")
    if vol_format not in ("raw", "qcow2"):
        raise MachineError(
            MachineResourceText.FORMAT_MUST_BE_RAW_OR_QCOW2_TEMPLATE.format(value=vol_format),
            EXIT_USAGE,
            "usage",
        )
    if not os.path.isfile(source):
        raise MachineError(
            MachineResourceText.NO_SUCH_FILE_TEMPLATE.format(path=source), EXIT_CONFIG, "config"
        )

    _refuse_existing(pool, name, options.get("force"))
    size = os.path.getsize(source)
    vol_xml = (
        f"<volume><name>{name}</name><capacity unit='bytes'>{size}</capacity>"
        f"<target><format type='{vol_format}'/></target></volume>"
    )
    _ensure_pool_active(pool)
    try:
        vol = pool.createXML(vol_xml, 0)
    except libvirt.libvirtError as e:
        raise MachineError(
            MachineResourceText.COULD_NOT_CREATE_VOLUME_TEMPLATE.format(name=name, error=e),
            EXIT_OPERATION,
            "operation_failed",
        ) from e

    stream = None
    try:
        stream = conn.newStream(0)
        vol.upload(stream, 0, size)
        with open(source, "rb") as handle:
            sent = 0
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                stream.send(chunk)
                sent += len(chunk)
                if size:
                    session.progress(
                        MachineProgressStage.UPLOAD,
                        f"{name}: {sent // 1024 // 1024} MB",
                        int(sent * 100 / size),
                    )
        stream.finish()
    except Exception as e:
        if stream is not None:
            try:
                stream.abort()
            except libvirt.libvirtError:
                pass
        # The half-written volume is not something a retry could use.
        try:
            vol.delete(0)
            _volumes_changed()
        except libvirt.libvirtError:
            pass
        raise MachineError(
            MachineResourceText.COULD_NOT_UPLOAD_TEMPLATE.format(source=source, error=e),
            EXIT_OPERATION,
            "upload_failed",
        ) from e

    _volumes_changed()
    return {"name": name, "path": _safe_get_volume_path(vol), "bytes": size}


# --------------------------------------------------------------------------------------
# Domains
# --------------------------------------------------------------------------------------


@machine_command("domain")
def _domain_command(app, args, session):
    """``domain list|state|address|metadata|start|shutdown|reboot|destroy|undefine <name>``."""
    conn = _connection(app)
    subcommand, positional, options = _read_subcommand("domain", args)

    if subcommand == "list":
        try:
            domains = conn.listAllDomains(0)
        except libvirt.libvirtError as e:
            raise MachineError(
                MachineResourceText.COULD_NOT_LIST_DOMAINS_TEMPLATE.format(error=e),
                EXIT_OPERATION,
                "operation_failed",
            ) from e
        prefix = options.get("prefix") or ""
        return {
            "domains": [
                {"name": domain.name(), "state": _domain_state(domain)}
                for domain in domains
                if domain.name().startswith(prefix)
            ]
        }

    name = positional[0]
    domain = _domain(conn, name)

    if subcommand == "state":
        return {"name": name, "state": _domain_state(domain)}

    if subcommand == "address":
        return _domain_address(domain, options)

    if subcommand == "metadata":
        return {"name": name, "metadata": _domain_metadata(domain)}

    action = DOMAIN_LIFECYCLE.get(subcommand)
    if action is None:
        raise MachineError(
            MachineResourceText.UNKNOWN_SUBCOMMAND_TEMPLATE.format(
                command="domain", subcommand=subcommand
            ),
            EXIT_USAGE,
            "usage",
        )
    if subcommand == "undefine":
        _refuse_if_running(domain, "undefine")
    try:
        action(domain)
    except libvirt.libvirtError as e:
        raise MachineError(
            MachineResourceText.COULD_NOT_ACT_ON_DOMAIN_TEMPLATE.format(
                action=subcommand, name=name, error=e
            ),
        ) from e
    except Exception as e:
        # `start_vm` says a disk is missing with a plain Exception, and a
        # caller of this command is told the same either way: the operation
        # did not happen, and the words say what was in the way.
        raise MachineError(
            MachineResourceText.COULD_NOT_ACT_ON_DOMAIN_TEMPLATE.format(
                action=subcommand, name=name, error=e
            ),
            EXIT_OPERATION,
            "operation_failed",
        ) from e
    stage = DOMAIN_ACTION_STAGES.get(subcommand)
    if session is not None and stage is not None:
        session.progress(stage, f"{subcommand} {name}")
    _machine_changed(app, domain, gone=subcommand == "undefine")
    return {
        "name": name,
        "state": _domain_state(domain) if subcommand != "undefine" else "undefined",
    }


# What a machine is told to do, taken from the module the interactive commands
# use rather than from a second reading of the same words. The difference is not
# pedantry: `shutdown` is the ACPI event the guest sees and can refuse, and the
# only thing resembling it in the binding that is not that event is `destroy`.
# `start` arrives with the check the interactive command makes, that every disk
# of the machine is where it said it would be — a machine started onto a missing
# volume fails in a way the caller cannot read off the answer.
DOMAIN_LIFECYCLE = {
    "start": start_vm,
    "shutdown": stop_vm,
    "reboot": guest_reboot,
    "destroy": force_off_vm,
    "undefine": lambda domain: domain.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE),
}

# Which of those actions report a progress event, and the stage each reports it as:
# the action's own word, so a caller that branches on `start` keeps working. An
# action with no entry here says nothing while it works — `undefine` is the one, and
# it has no stage because the machine it removes has no state left to report. The
# words are the set in `constants.py` and the manual lists them, so a stage cannot
# join one side of that without the other.
DOMAIN_ACTION_STAGES = {
    "start": MachineProgressStage.START,
    "shutdown": MachineProgressStage.SHUTDOWN,
    "reboot": MachineProgressStage.REBOOT,
    "destroy": MachineProgressStage.DESTROY,
}


def _domain_metadata(domain):
    """The facts the caller stored on a domain, in the namespace of ours it uses.

    libvirt answers with the element as text. A domain carrying none of them is
    an answer, not a failure: the caller asked what it left there, and nothing
    is what is there.
    """
    try:
        stored = domain.metadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT, METADATA_NS)
    except libvirt.libvirtError:
        return {}
    if not stored:
        return {}
    try:
        properties = ET.fromstring(stored)
    except ET.ParseError as e:
        raise MachineError(
            MachineResourceText.COULD_NOT_READ_METADATA_TEMPLATE.format(
                name=domain.name(), error=e
            ),
            EXIT_OPERATION,
            "operation_failed",
        ) from e
    prefix = f"{{{METADATA_NS}}}"
    kept = {}
    for child in properties:
        key = child.tag[len(prefix) :] if child.tag.startswith(prefix) else child.tag
        kept[key] = child.text or ""
    return kept


def _machine_changed(app, domain, gone=False):
    """Forget what was remembered about this machine, as the interactive tool does.

    The caches of a session hold the state of each machine and the list of them,
    and a caller can ask for another thing here after changing one. Only a
    command that got through says anything: a refusal changed no hypervisor, and
    emptying the caches for it would cost the next command a round trip for
    nothing.
    """
    service = getattr(app, "vm_service", None)
    if service is None:
        return
    service.invalidate_vm_state_cache(service.get_vm_identity(domain)[0])
    if gone:
        # A machine that is not there is a smaller list of them.
        service.invalidate_domain_cache()


def machine_created(app, domain):
    """Forget the list of machines, as well as what was remembered about this one.

    A deploy adds a machine, so it is the list that answers stale afterwards: a
    caller asking what the hypervisor has is answered from before the machine it
    made. The state of the machine itself has nothing remembered yet, and is
    dropped anyway, so an action taken next reads the hypervisor and not the
    moment the deploy saw on its way out.
    """
    _machine_changed(app, domain)
    service = getattr(app, "vm_service", None)
    if service is not None:
        # A machine there was not before is a bigger list of them.
        service.invalidate_domain_cache()


def _volumes_changed():
    """Forget the volumes of a pool, and the pools themselves.

    The space a pool reports is the space its volumes leave, so a volume added or
    removed makes the pool list as stale as the volume list.
    """
    list_storage_volumes.cache_clear()
    list_storage_pools.cache_clear()


def _pools_changed():
    """Forget the pools, which a pool added or removed has changed."""
    list_storage_pools.cache_clear()


def _refuse_if_running(domain, verb):
    if domain.isActive():
        raise MachineError(
            MachineResourceText.DOMAIN_RUNNING_REFUSAL_TEMPLATE.format(
                name=domain.name(), verb=verb
            ),
            EXIT_OPERATION,
            "operation_failed",
        )


def _domain_address(domain, options):
    """``domain address <name>``: what the hypervisor leased to this machine.

    libvirt answers from the network's leases, which is where the address of a
    machine that has not reported one is. A domain with no lease yet is not an
    error: `ipv4` is simply null, and the caller decides what to wait for.
    """
    try:
        interfaces = get_vm_network_ip(domain)
    except libvirt.libvirtError as e:
        raise MachineError(
            MachineResourceText.COULD_NOT_READ_ADDRESSES_TEMPLATE.format(
                name=domain.name(), error=e
            ),
            EXIT_OPERATION,
            "operation_failed",
        ) from e
    leases = [
        {
            "mac": iface.get("mac"),
            "ipv4": list(iface.get("ipv4") or []),
            "ipv6": list(iface.get("ipv6") or []),
        }
        for iface in interfaces
    ]
    first = next((lease["ipv4"][0] for lease in leases if lease["ipv4"]), None)
    return {
        "name": domain.name(),
        "state": _domain_state(domain),
        # The address without its prefix length, which is what a caller connects to.
        "ipv4": first.split("/")[0] if first else None,
        "interfaces": leases,
    }


# --------------------------------------------------------------------------------------
# Networks and the host
# --------------------------------------------------------------------------------------


@machine_command("network")
def _network_command(app, args, _session):
    """``network list``, ``network info <name>``."""
    conn = _connection(app)
    subcommand, positional, options = _read_subcommand("network", args)

    if subcommand == "list":
        try:
            networks = conn.listAllNetworks(0)
        except libvirt.libvirtError as e:
            raise MachineError(
                MachineResourceText.COULD_NOT_LIST_NETWORKS_TEMPLATE.format(error=e),
                EXIT_OPERATION,
                "operation_failed",
            ) from e
        return {
            "networks": [
                {"name": facts["name"], "state": _state(facts["active"])}
                for facts in (network_facts(network) for network in networks)
            ]
        }

    if subcommand == "info":
        try:
            network = conn.networkLookupByName(positional[0])
        except libvirt.libvirtError as e:
            raise _not_found(e, "network", positional[0]) from e
        facts = network_facts(network)
        return {
            "name": facts["name"],
            "state": _state(facts["active"]),
            "bridge": facts["bridge_name"],
            "autostart": facts["autostart"],
        }


@machine_command("host")
def _host_command(app, args, _session):
    """``host capabilities``, ``host resources``."""
    conn = _connection(app)
    subcommand, _, _ = _read_subcommand("host", args)

    if subcommand == "capabilities":
        resources = _node_information(conn)
        architectures, uefi = _capabilities(conn)
        return {
            "hostname": _hostname(conn),
            # What can run here: every architecture the hypervisor has guests for.
            "architectures": architectures,
            "host_cpus": resources["host_cpus"],
            "memory_mb": resources["memory_mb"],
            # UEFI is not assumed anywhere: a caller asks for it and reads this.
            "uefi": uefi,
        }

    if subcommand == "resources":
        return _node_information(conn)


def _hostname(conn):
    try:
        return conn.getHostname()
    except libvirt.libvirtError:
        return None


def _node_information(conn):
    """What the hypervisor has: its CPUs and its memory, in MB."""
    resources = get_host_resources(conn)
    return {
        "host_cpus": resources.get("total_cpus"),
        "memory_mb": (resources.get("total_memory") or 0) * 1024 or None,
        # What a new machine could be given, which is what a caller decides with.
        "available_mb": (resources.get("available_memory") or 0) * 1024 or None,
    }


def _capabilities(conn):
    """What the hypervisor's capabilities document says about the machines it can run.

    One read answers the two questions `host capabilities` asks: every
    architecture it has guests for, and whether a UEFI guest can boot here. They
    were asked separately, each reading and parsing the same document for itself,
    so the command paid for it twice and kept two ideas of what a document it
    cannot read means. A hypervisor that will not answer, or answers with
    something that is not the document, is answered as each question answered on
    its own: no architecture, no UEFI.

    The two answers stay different from each other: a document naming
    architectures with no UEFI loader in it names the architectures and says
    `false` for UEFI.
    """
    try:
        root = ET.fromstring(conn.getCapabilities())
    except (libvirt.libvirtError, ET.ParseError, TypeError):
        return [], False

    architectures = []
    uefi = False
    for guest in root.findall("guest"):
        architecture = guest.find("arch")
        if architecture is None:
            continue
        name = architecture.get("name")
        if name and name not in architectures:
            architectures.append(name)
        for loader in architecture.findall("loader"):
            if (loader.get("type") or "pflash") == "pflash":
                uefi = True
    return architectures, uefi
