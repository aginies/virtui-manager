"""The words a bad config is answered with are constants, and a check keeps them so.

Two things are guarded here, because the ticket that moved the wording says the
file it lives in was invisible to every check until now:

*Nothing writes a deploy failure in place.* ``deploy_from_config`` answers a bad
config in two voices at once — it prints the sentence, and under ``--machine`` it
ships the same sentence as the ``message`` of the failure answer. A sentence typed
where it is used therefore moves the machine contract without anyone renaming a
constant. `checks/check_constants.py` is loaded and asked for exactly that, over
the real file, so a new hardcoded answer fails the suite. The pass is scoped to
the calls that carry a deploy failure rather than to the whole file: the file holds
hundreds of strings no ticket has looked at, and a check that reports all of them
is a backlog, not a check.

*The wording itself has not moved.* A caller branches on `code` and `status`, but
it also reads `message` back into a log, and the ticket promises that wording
unchanged. The sentences are pinned here in their finished form, with the names a
deploy file would supply, so a reword is a deliberate edit to this list rather than
a side effect of moving a string.

`vmanager_cmd.py` is also checked against the constants check's exclusion list,
which is the claim this ticket exists to make true: the file is covered.
"""

import ast
import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.constants import DeployText

REPO = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = REPO / "checks" / "check_constants.py"
VMANAGER_CMD = REPO / "src" / "vmanager" / "vmanager_cmd.py"
CONSTANTS = REPO / "src" / "vmanager" / "constants.py"


def _load_check():
    """The checks directory is a script folder, not a package, so load it by path."""
    spec = importlib.util.spec_from_file_location("check_constants", CHECK_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the constants check from {CHECK_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_constants = _load_check()


class TestConstantsCheckCoversTheCommandLine(unittest.TestCase):
    def test_vmanager_cmd_is_not_excluded(self):
        """The command-line file is inside the constants check, not outside it.

        It was excluded outright while its strings were constants-free. They are
        not now, and nothing here argues that the check reads prose: it watches
        constant naming, constant usage, tooltips, and the deploy failure calls
        below — which is worth saying, because the exclusion was justified by the
        prose it cannot see either way.
        """
        self.assertNotIn("vmanager_cmd.py", check_constants.dont_check_files)


class TestDeployFailureWordingIsNotWrittenInPlace(unittest.TestCase):
    def test_the_real_file_answers_from_constants(self):
        """No `_deploy_fail` call spells its answer out."""
        findings = check_constants.find_hardcoded_deploy_wording(
            VMANAGER_CMD.read_text(encoding="utf-8"), str(VMANAGER_CMD)
        )
        self.assertEqual(
            findings,
            [],
            "deploy failure wording must come from DeployText: "
            + "; ".join(f"line {line}: {text!r}" for line, text in findings),
        )

    def test_the_pass_fires_on_a_sentence_written_in_place(self):
        """The guard above is not green because it matches nothing."""
        source = (
            "class C:\n"
            "    def go(self):\n"
            '        self._deploy_fail("Made up words.", 1, "code")\n'
            '        self._deploy_fail(f"Made up: {e}", 1, "code")\n'
        )
        findings = check_constants.find_hardcoded_deploy_wording(source, "snippet.py")
        self.assertEqual([line for line, _ in findings], [3, 4])

    def test_the_pass_leaves_a_constant_and_a_bare_exception_alone(self):
        """A constant, a formatted constant, and `str(e)` are all allowed."""
        source = (
            "class C:\n"
            "    def go(self):\n"
            "        self._deploy_fail(DeployText.VM_NAME_EMPTY, 1, 'code')\n"
            "        self._deploy_fail(\n"
            "            DeployText.CONFIG_ERROR_TEMPLATE.format(error=e), 1, 'code'\n"
            "        )\n"
            "        self._deploy_fail(str(e), 1, 'code')\n"
        )
        self.assertEqual(check_constants.find_hardcoded_deploy_wording(source, "snippet.py"), [])


class TestTheWordingACallerSees(unittest.TestCase):
    """The sentences, in their finished form, exactly as they read before the move."""

    # constant name -> (placeholder values, the whole sentence)
    SENTENCES = {
        "CONFIG_ERROR_TEMPLATE": (
            {"error": "Configuration root must be an object"},
            "Config error: Configuration root must be an object",
        ),
        "INVALID_VM_NAME_TEMPLATE": (
            {"error": "must not contain '/'"},
            "Invalid VM name: must not contain '/'",
        ),
        "VM_NAME_EMPTY": ({}, "VM name cannot be empty."),
        "CANNOT_CONNECT_TEMPLATE": (
            {"server": "qemu+ssh://host/system"},
            "Could not connect to server 'qemu+ssh://host/system'.",
        ),
        "VM_ALREADY_EXISTS_TEMPLATE": (
            {"name": "web01"},
            "VM 'web01' already exists.",
        ),
        "VM_NAME_CHECK_FAILED_TEMPLATE": (
            {"error": "unable to connect"},
            "Error checking VM name: unable to connect",
        ),
        "NO_DEFAULT_POOL": (
            {},
            "No storage pool available and could not create default pool.",
        ),
        "NO_SUCH_POOL_TEMPLATE": (
            {"pool": "default", "server": "local"},
            "No storage pool 'default' on local.",
        ),
        "NO_SUCH_VOLUME_TEMPLATE": (
            {"volume": "sidekick-worker01", "pool": "default"},
            "No volume 'sidekick-worker01' in the pool 'default'.",
        ),
        "NO_SUCH_SEED_TEMPLATE": (
            {"seed": "seed.iso", "pool": "default"},
            "No seed volume 'seed.iso' in 'default'.",
        ),
        "NO_SUCH_BACKING_TEMPLATE": (
            {"backing": "base.qcow2", "pool": "default"},
            "No backing volume 'base.qcow2' in 'default'.",
        ),
        "OVERLAY_FAILED_TEMPLATE": (
            {"volume": "disk.qcow2", "backing": "base.qcow2", "error": "no space left"},
            "Could not make 'disk.qcow2' over 'base.qcow2': no space left",
        ),
        "ISO_UNRESOLVABLE": ({}, "Could not resolve ISO from config."),
        "NO_DOMAIN": ({}, "Provisioning did not return a domain."),
        "PROVISION_FAILED_TEMPLATE": (
            {"error": "domain creation failed"},
            "Failed to provision VM: domain creation failed",
        ),
        "BOOT_FAILED_TEMPLATE": (
            {"error": "no such volume"},
            "Failed to boot the machine: no such volume",
        ),
    }

    def test_every_sentence_is_word_for_word_what_it_was(self):
        for name, (values, expected) in self.SENTENCES.items():
            with self.subTest(constant=name):
                value = getattr(DeployText, name)
                self.assertEqual(value.format(**values) if values else value, expected)

    def test_the_class_covers_every_failure_the_deploy_path_answers(self):
        """Every `_deploy_fail` call names one of the pinned sentences.

        A new way to fail has to say so here too, rather than answer with a
        sentence nothing else has read. The one call that passes `str(e)` is left
        out on purpose: it quotes the hypervisor and adds no wording of its own.
        """
        used = set()
        tree = ast.parse(VMANAGER_CMD.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_deploy_fail = (isinstance(func, ast.Name) and func.id == "_deploy_fail") or (
                isinstance(func, ast.Attribute) and func.attr == "_deploy_fail"
            )
            if not is_deploy_fail or not node.args:
                continue
            # Unwrap `DeployText.X.format(...)`, which is how every templated sentence is
            # written, down to the constant it names.
            first = node.args[0]
            while isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute):
                first = first.func.value
            if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name):
                if first.value.id == "DeployText":
                    used.add(first.attr)
        self.assertEqual(used, set(self.SENTENCES))

    def test_the_wording_is_not_marked_for_translation(self):
        """These answers are read by a program, so they stay out of the catalogue.

        `_()` is the identity until a catalogue is loaded, so a value cannot show
        which family it was written in; the definition itself is read instead. The
        reason is the one recorded on `MachineModeText`: the exit status would stay
        the same while the answer changed language.
        """
        tree = ast.parse(CONSTANTS.read_text(encoding="utf-8"))
        body = next(
            node.body
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DeployText"
        )
        for item in body:
            if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                self.fail(f"DeployText must not wrap a string in _(): {ast.dump(item.value)}")


if __name__ == "__main__":
    unittest.main()
