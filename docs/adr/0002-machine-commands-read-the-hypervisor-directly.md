# 0002 — Machine commands read the hypervisor, not the cache

Date: 2026-11-05
Status: Accepted

## Context

The interactive tool reads virtual machine state through the caches in `vm_service`.
That is not an optimization: a TUI that refreshes a list of machines over libvirt on
every paint is a TUI that freezes, so the cache exists to keep the interface responsive
and is invalidated whenever anything in the app changes a machine.

The `--machine` commands needed the same facts — is this domain running, what volumes
are in that pool — and the same helpers were sitting there.

## Decision

Machine commands ask the hypervisor directly. They never read through the session
caches, and one connection serves a whole run: `vmc -u <uri>` connects once, before the
command runs, and a command never picks a server of its own.

## Why

A scripted caller asks once and wants the hypervisor's `now`. The staleness a cache
introduces is invisible to the caller: the interactive tool can afford it because a
person looking at a slightly old list will look again, whereas a script that reads
`"running": true` and then ssh's into the machine has already acted on it. The cache is
there to stop a freezing UI, which is a cost a single command does not pay; what it
would buy such a caller is a wrong answer and a round trip it did not ask for.

What it costs, in its own words: every machine command pays for its own reading. There
is no warm answer to fall back on, so `domain list` is `listAllDomains` plus one
`domain.info()` per machine, and two commands run one after the other are two readings
that can disagree — which is the point, and also the price. When the hypervisor is
unreachable there is no cached answer either: the command fails, as it should, since an
answer that looks fresh and is not is the failure this decision exists to avoid.

The sharper cost is the one this decision does not pay but creates. A machine command
reads past the caches but changes the same hypervisor the interactive tool is displaying,
so every command that writes carries an obligation it would rather not have: to empty the
caches it did not read. That is `machine_resources._machine_changed`, `_volumes_changed`
and `_pools_changed`, and `machine_created` after a deploy. A command that changes
something and forgets leaves the interactive tool holding a machine that no longer
exists. The obligation is per-command, easy to leave out of a new command, and the place
this decision is most likely to be quietly undone.

The words are read separately for a related reason. `vm_queries.get_status` answers
`StatusText.RUNNING`, which is localized and rewordable; a program compares `running`,
so the number is turned into a name by `STATE_NAMES` in `machine_resources.py`. What is
read is the same call — `domain.info()` — which is what makes the two surfaces
comparable even though neither asks the other for anything.

## What would make this wrong

- If machine commands start polling — a `wait`, a `watch`, a loop in a script that runs
  every few seconds against one host — then reading the hypervisor every time becomes
  the expensive thing, and a short-lived cache with a stated TTL is the right answer.
  That is a decision about staleness, and it would have to be made out loud rather than
  inherited from the UI.
- If direct reads start costing the hypervisor something (a remote libvirtd behind a
  slow link, a host with hundreds of domains), the same revisit applies.
- If a machine command changes the hypervisor and the interactive tool goes on showing
  the old state, that is the invalidation obligation above going missing in that command.
  It is a bug in the command, not a reason to revisit this decision — the fix is to call
  the helper, not to start reading through the cache.

## Where this shows in the code

`src/vmanager/machine_resources.py`: the comment above `STATE_NAMES`, and `_connection`.
