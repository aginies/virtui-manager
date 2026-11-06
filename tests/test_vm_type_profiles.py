"""One profile means one machine, whichever surface is asked (issue #25).

The hardware behind a profile used to be written down twice: the config-driven
deploy read ``VM_TYPE_DEFAULTS`` and the interactive provisioning screen
pre-filled its own numbers in ``InstallVMModal._update_expert_defaults``. The
two had drifted apart - a ``server`` deploy started 16384 MB while the screen
offered 4096, a ``wldesktop`` deploy 8192 against 4096 - so the same profile
meant two different machines.

The expected hardware below is deliberately not read from the code: it is the
maintainer's decision table on the issue, transcribed by hand (the interactive
values define a profile). Each surface is then asked the way a caller asks it -
a deploy file through ``resolve_vm_resources``, a profile choice through the
modal's pre-fill handler - so an edit that makes either surface disagree with
this table, or with the other one, fails here.
"""

import json
import os
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# The analyser has no src on its path (pyproject sets no mypy_path), as in every
# test module here; the sys.path.insert above is what makes these resolve.
from vmanager.deploy_config import load_deploy_config, resolve_vm_resources  # type: ignore[import-not-found]
from vmanager.modals.provisioning_modals import InstallVMModal  # type: ignore[import-not-found]
from vmanager.vm_provisioner import VM_TYPE_DEFAULTS, VMType  # type: ignore[import-not-found]

# profile name in a deploy file -> (VMType, memory in MB, vCPU, text the
# interactive memory field shows). The last column is pinned as text because a
# pre-fill that changes "4" to "4.0" is a visible change on screen, and the
# decision was that the interactive surface keeps showing exactly what it showed.
PROFILE_HARDWARE = {
    "secure": (VMType.SECURE, 4096, 2, "4"),
    "computation": (VMType.COMPUTATION, 8192, 4, "8"),
    "desktop": (VMType.DESKTOP, 4096, 4, "4"),
    "low_resource": (VMType.LOW_RESOURCE, 512, 1, "0.5"),
    "wdesktop": (VMType.WDESKTOP, 16384, 8, "16"),
    "wldesktop": (VMType.WLDESKTOP, 4096, 4, "4"),
    # The screen's Server pre-fill went back up to 16 GB in 3.5.3, and the rule
    # above is that the screen defines the profile - so a server deploy starts
    # 16384 MB again. The screen still shows "16", as it did before #25.
    "server": (VMType.SERVER, 16384, 6, "16"),
}


class _Field:
    """Stand-in for an Input / Select / Checkbox: only ``value`` and ``disabled``."""

    def __init__(self):
        self.value: Any = None
        self.disabled = False


def expert_defaults_for(vm_type, distro=None):
    """Run the screen's pre-fill for one profile and answer the fields it filled.

    The modal is built without ``__init__`` (which opens a libvirt connection)
    and with ``query_one`` handed fake widgets, so what is observed is the
    handler's own answer: the values a person would see in the expert inputs.
    """
    fields = {
        "#memory-input": _Field(),
        "#cpu-input": _Field(),
        "#disk-size-input": _Field(),
        "#disk-format": _Field(),
        "#boot-uefi-checkbox": _Field(),
        # A Secure VM has no video output, so the screen also defaults its serial
        # console checkbox on for that profile; the handler queries it for every
        # profile and this fake has to answer for all of them.
        "#automation-serial-console": _Field(),
    }
    modal = InstallVMModal.__new__(InstallVMModal)
    modal.query_one = lambda selector, *args, **kwargs: fields[selector]
    InstallVMModal._update_expert_defaults(modal, vm_type, distro)
    return fields


class TestProfileMeansOneMachine(unittest.TestCase):
    def _config_path(self, profile_name):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(
                {
                    "server": "s",
                    "vm_name": "v",
                    "vm_type": profile_name,
                    "iso": {"source": "url", "url": "u"},
                },
                handle,
            )
            path = handle.name
        self.addCleanup(os.unlink, path)
        return path

    def _deploy_hardware(self, profile_name):
        """(VMType, memory_mb, vcpu) the config-driven deploy gives a profile."""
        vm_type, memory_mb, vcpu, _disk = resolve_vm_resources(
            load_deploy_config(self._config_path(profile_name))
        )
        return vm_type, memory_mb, vcpu

    def test_every_profile_is_declared_once_on_both_sides(self):
        """The shared table and this table cover the same seven profiles."""
        self.assertEqual(
            {_vm_type for _name, (_vm_type, *_rest) in PROFILE_HARDWARE.items()},
            set(VMType),
        )
        self.assertEqual(set(VM_TYPE_DEFAULTS), set(VMType))
        # The name a deploy file says is the profile's own enum name, lowercase.
        self.assertEqual(
            sorted(PROFILE_HARDWARE), sorted(vm_type.name.lower() for vm_type in VMType)
        )

    def test_deploy_and_screen_answer_the_same_hardware(self):
        for profile_name, (vm_type, memory_mb, vcpu, prefill_text) in PROFILE_HARDWARE.items():
            with self.subTest(profile=profile_name):
                self.assertEqual(self._deploy_hardware(profile_name), (vm_type, memory_mb, vcpu))
                fields = expert_defaults_for(vm_type)
                self.assertEqual(fields["#memory-input"].value, prefill_text)
                self.assertEqual(int(float(fields["#memory-input"].value) * 1024), memory_mb)
                self.assertEqual(fields["#cpu-input"].value, str(vcpu))

    def test_screen_prefill_comes_from_the_shared_table(self):
        """The screen has no copy of its own: change the table, it follows.

        The numbers above would also pass with two hand-maintained tables that
        happen to agree today. This one cannot: it moves a single entry of the
        shared table and requires the pre-fill to move with it.
        """
        moved = {"memory_mb": 12288, "vcpu": 9}
        with patch.dict(VM_TYPE_DEFAULTS, {VMType.SERVER: moved}):
            fields = expert_defaults_for(VMType.SERVER)
        self.assertEqual(fields["#memory-input"].value, "12")
        self.assertEqual(fields["#cpu-input"].value, "9")

    def test_unchanged_profile_facts_stay_with_the_screen(self):
        """Disk format and firmware are the screen's, not the profile table's.

        A deploy takes its boot mode from its own file and never asked about a
        disk format, so these two facts live where they are used. They are
        asserted here so that moving them into the shared table - which would
        silently change what a config deploy creates - is a decision, not an
        accident.
        """
        self.assertEqual(expert_defaults_for(VMType.COMPUTATION)["#disk-format"].value, "raw")
        self.assertFalse(expert_defaults_for(VMType.WLDESKTOP)["#boot-uefi-checkbox"].value)
        self.assertTrue(expert_defaults_for(VMType.DESKTOP)["#boot-uefi-checkbox"].value)


if __name__ == "__main__":
    unittest.main()
