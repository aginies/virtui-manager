# 0003 — Renaming a volume answers status 9

Date: 2026-11-05
Status: Accepted

## Context

`vmc volume rename` appeared to exist. The handler called a method named `rename` on
the libvirt volume object, the Python binding raised `AttributeError`, and the
`AttributeError` was swallowed by the generic handler and reported as an unknown error.
A caller asking politely was told nothing useful, and the manual listed the command as
though it worked.

libvirt itself can rename a volume: there is `virStorageVolRename`. The Python binding
does not wrap it — `virStorageVol` answers to `upload`, `download`, `delete`, `resize`
and `wipe`, and not to `rename`.

## Decision

`volume rename` raises a machine error with **status 9** and a message naming the tool
that can do the job: `virsh vol-rename` on the hypervisor, or uploading the bytes under
the new name. It does not attempt the rename, and it does not pretend to have done it.

## Why

Status 9 says the command cannot be answered here, which is the truth, and the message
makes it actionable — the caller has a working path in the same breath as the refusal.
Any other status would lie: 7 would say the hypervisor refused something it was never
asked, 5 would say the volume does not exist, 6 would say the name is taken.

Three ways of making it work were considered and refused:

- **Shelling out to `virsh`.** This project talks to libvirt, not to a shell, and doing
  it for one command would mean owning a subprocess path, its quoting and its error
  parsing, to save the caller a command they can run themselves — and the caller is a
  script that is already on the hypervisor if it needs this.
- **Copy and delete.** Renaming by copying the volume under the new name and deleting
  the old one is not a rename: it is slow, it needs the free space, and it breaks the
  links that make a volume interesting — a snapshot chain or an overlay pointing at the
  old name now points at nothing. A destructive operation offered under the name
  `rename` is worse than a refusal.
- **Reporting success anyway.** This is where the command was, functionally.

If copy-and-delete is ever wanted, the honest shape of it is a different command —
`volume copy` — with the free space and the broken links stated as its cost, and rename
left refusing.

What it costs: the command surface now carries a command that always fails. Keeping it
rather than dropping it is deliberate — a caller who types it learns the refusal and the
alternative, where dropping it would teach only that the name was never there, and the
manual row and the `--new-name` option exist to carry that answer.

## What would make this wrong

- **The binding grows the wrapper.** Check `hasattr(libvirt.virStorageVol, "rename")`.
  The moment that is true, implement it and supersede this record. The guard test in
  `tests/test_machine_resources.py` proves every libvirt name this module calls exists on
  the binding; it cannot notice a name the module stopped calling, so the check above has
  to be made by hand. Note also that `documentation/manual/cli.md` currently says this
  status 9 "will not change", so the manual moves with the code.
- **Status 9 is carrying two meanings.** `cli.md` defines 9 as "the command has no
  machine-readable result in this version of vmc" — a statement about *this version*,
  which reads as temporary — while `volume rename` uses it for something that is not
  version-dependent. If callers start reading 9 as "try again with a newer vmc", this
  overload is the reason, and the fix is a status of its own rather than a reworded
  message.

## Where this shows in the code

`src/vmanager/machine_resources.py`, the `rename` branch of the volume handler;
`MachineResourceText.VOLUME_RENAME_IS_NOT_POSSIBLE` in `src/vmanager/constants.py`;
`documentation/manual/cli.md`.
