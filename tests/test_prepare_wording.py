"""The words `prepare_json` asks with are constants, and a check keeps them so.

`prepare_json` walks a person through a deployment by asking questions, then writes the
answer to a file and tells them how to run it. Until now every one of those questions was
typed where it was asked, so a reword was a search-and-replace through the command file and
nothing noticed when one of the two `_prepare_iso` copies of a label disagreed with the other.

Two things are guarded here, the same way `test_deploy_wording.py` guards the deploy
failures:

*Nothing writes a prepare prompt in place.* The prepare path asks through five helpers —
`print`, `_get_input`, `_get_password`, `_select_choice`, `_get_positive_int` and
`_get_positive_float` — so a sentence in the first argument of one of those calls, inside
one of the prepare functions, is wording written at the call site. The scan below is scoped
to those functions rather than to the whole file: the file holds hundreds of strings no
ticket has looked at, and a check that reports all of them is a backlog, not a check.

*The wording itself has not moved.* AC3 promises a person sees the same thing, so every
sentence is pinned here in its finished form. The answer file is pinned by running the
writer and reading back what the terminal got, rather than by reading the constant, because
the sentence a person sees is the formatted one.

These sentences are translated, which is the opposite of `DeployText`. ADR 0001 records why:
"The interactive tool's words are passed through `_()` and are translated, which is right",
and only the words a *program* compares are kept in one language. `prepare_json` is not a
machine command — there is no `--machine` route to it, and machine mode never writes a file
— so nothing here is a contract field, and they live in `StaticText` beside the rest of the
translated prose.

Passwords are guarded separately, because AC2 is about how they are asked and not only about
what the prompt says: a password prompt moved to the wrong helper would start echoing.
"""

import ast
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.constants import StaticText
from vmanager.vmanager_cmd import VManagerCMD

REPO = Path(__file__).resolve().parents[1]
VMANAGER_CMD = REPO / "src" / "vmanager" / "vmanager_cmd.py"
CONSTANTS = REPO / "src" / "vmanager" / "constants.py"

# The functions that ask a person for something on the prepare path. The shared input
# helpers themselves (`_get_input`, `_select_choice`) are used by other commands and hold
# no wording of their own, so their bodies are not scanned — only the prompts handed to them.
PREPARE_FUNCTIONS = {
    "do_prepare_json",
    "_prepare_iso",
    "_prepare_autoinstall",
    "_prepare_autoinstall_settings",
    "_ask_scc",
    "_prepare_post_install",
    "_get_positive_int",
    "_get_positive_float",
    "_write_json",
}

# Calls whose first argument is spoken to the person.
PROMPT_CALLS = {
    "print",
    "_get_input",
    "_get_password",
    "_select_choice",
    "_get_positive_int",
    "_get_positive_float",
}

# Prompts handed to `_get_password`. AC2: these must stay behind getpass.
PASSWORD_PROMPTS = {
    "PREPARE_USER_PASSWORD",
    "PREPARE_ROOT_PASSWORD",
    "PREPARE_SCC_REG_CODE",
}


def _is_wording(text):
    """Whitespace is not wording. The post-install continuation prompt is two spaces."""
    return any(character.isalpha() for character in text)


def _prompt_literals(source):
    """Every prompt spelled out as a literal on the prepare path: ``(line, text)``."""
    findings = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in PREPARE_FUNCTIONS:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in PROMPT_CALLS:
                continue
            first = call.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if _is_wording(first.value):
                    findings.append((call.lineno, first.value))
    return findings


class TestPrepareWordingIsNotWrittenInPlace(unittest.TestCase):
    def test_the_real_file_asks_from_constants(self):
        """No prepare prompt spells itself out."""
        findings = _prompt_literals(VMANAGER_CMD.read_text(encoding="utf-8"))
        self.assertEqual(
            findings,
            [],
            "prepare wording must come from StaticText: "
            + "; ".join(f"line {line}: {text!r}" for line, text in findings),
        )

    def test_the_scan_fires_on_a_prompt_written_in_place(self):
        """The guard above is not green because it matches nothing."""
        source = (
            "class C:\n"
            "    def _prepare_iso(self):\n"
            '        print("Made up words.")\n'
            '        self._get_input("Enter made up thing: ")\n'
            '        self._select_choice("Select Made Up Thing", ["a", "b"])\n'
        )
        self.assertEqual([line for line, _ in _prompt_literals(source)], [3, 4, 5])

    def test_the_scan_leaves_a_constant_and_an_option_list_alone(self):
        """A constant, a formatted constant, and the options beside it are allowed."""
        source = (
            "class C:\n"
            "    def _prepare_iso(self):\n"
            "        print(StaticText.PREPARE_NO_CACHED_ISOS)\n"
            "        print(StaticText.PREPARE_FETCHING_ISOS_TEMPLATE.format(version=v))\n"
            '        self._select_choice(StaticText.PREPARE_SELECT_BOOT_MODE, ["UEFI", "BIOS"])\n'
            '        self._get_input("  ")\n'
        )
        self.assertEqual(_prompt_literals(source), [])

    def test_the_scan_ignores_prompts_outside_the_prepare_path(self):
        """The guard is scoped, so another command's wording is not this ticket's backlog."""
        source = (
            'class C:\n    def do_installvm(self):\n        print("Another ticket\'s wording.")\n'
        )
        self.assertEqual(_prompt_literals(source), [])


class TestTheWordingAPersonSees(unittest.TestCase):
    """The sentences, in their finished form, exactly as they read before the move.

    Constant name -> (placeholder values, the whole sentence). A prompt that ends in a
    space or a colon keeps it: it is concatenated with what the person types, not printed
    on its own.
    """

    SENTENCES = {
        # Writing the answer back.
        "PREPARE_CONFIG_WRITTEN_TEMPLATE": (
            {"path": "/home/me/.config/virtui-manager/deploy/web01.json"},
            "\nDeployment config written to: /home/me/.config/virtui-manager/deploy/web01.json",
        ),
        "PREPARE_CONFIG_RUN_TEMPLATE": (
            {"path": "/tmp/web01.json"},
            "Run it with: vmc --json /tmp/web01.json",
        ),
        "PREPARE_CONFIG_DEPLOY_VM_TEMPLATE": (
            {"command": "web01.json"},
            "or, interactively: deploy_vm web01.json",
        ),
        "PREPARE_CONFIG_WRITE_FAILED_TEMPLATE": (
            {"path": "/readonly/web01.json", "error": "[Errno 13] Permission denied"},
            "Could not write the deployment config to /readonly/web01.json: "
            "[Errno 13] Permission denied",
        ),
        # The questions, in the order they are asked.
        "PREPARE_NOT_CONNECTED": (
            {},
            "Not connected to any server. Use 'connect <server_name>' first.",
        ),
        "PREPARE_SELECT_SERVER": ({}, "Select Server"),
        "PREPARE_ENTER_VM_NAME": ({}, "Enter VM name: "),
        "PREPARE_VM_NAME_REQUIRED": ({}, "VM name is required."),
        "PREPARE_SELECT_VM_TYPE": ({}, "Select VM Type"),
        "PREPARE_MEMORY_GB": ({}, "Memory (GB)"),
        "PREPARE_VCPU": ({}, "vCPU"),
        "PREPARE_DISK_SIZE_GB": ({}, "Disk size (GB)"),
        "PREPARE_NO_ISO": ({}, "Could not select an ISO."),
        "PREPARE_SELECT_NETWORK": ({}, "Select Network"),
        "PREPARE_SELECT_BOOT_MODE": ({}, "Select Boot Mode"),
        "PREPARE_SELECT_GRAPHICS": ({}, "Select Graphics"),
        "PREPARE_START_AFTER_INSTALL": ({}, "Start VM after installation?"),
        "PREPARE_ENTER_A_NUMBER": ({}, "Please enter a number."),
        "PREPARE_NON_NEGATIVE_INTEGER": ({}, "Must be a non-negative integer."),
        "PREPARE_POSITIVE_NUMBER": ({}, "Must be a positive number."),
        # The ISO choice. The four sources are also what `_prepare_iso` branches on.
        "PREPARE_ISO_SOURCE_REPOSITORY": ({}, "Distribution repository"),
        "PREPARE_ISO_SOURCE_POOL": ({}, "Pool volumes"),
        "PREPARE_ISO_SOURCE_CUSTOM": ({}, "Custom URL/Path"),
        "PREPARE_SELECT_ISO_SOURCE": ({}, "Select ISO Source"),
        "PREPARE_NO_VERSIONS": ({}, "No openSUSE/SLES versions found."),
        "PREPARE_SELECT_VERSION": ({}, "Select Version"),
        "PREPARE_FETCHING_ISOS_TEMPLATE": (
            {"version": "Leap 15.6"},
            "Fetching available ISOs for Leap 15.6...",
        ),
        "PREPARE_NO_ISOS_FOR_VERSION": ({}, "No ISOs found for this version."),
        "PREPARE_SELECT_ISO_IMAGE": ({}, "Select ISO Image"),
        "PREPARE_NO_CACHED_ISOS": ({}, "No cached ISOs found."),
        "PREPARE_SELECT_CACHED_ISO": ({}, "Select Cached ISO"),
        "PREPARE_NO_ISO_VOLUMES": ({}, "No ISO volumes found in active pools."),
        "PREPARE_SELECT_ISO_VOLUME": ({}, "Select ISO Volume"),
        "PREPARE_ENTER_CUSTOM_ISO": ({}, "Enter custom ISO URL or absolute path: "),
        # The auto-install settings.
        "PREPARE_USE_AUTOINSTALL": ({}, "Use automated installation?"),
        "PREPARE_NO_TEMPLATES": ({}, "No openSUSE/SLES auto-install templates found."),
        "PREPARE_SELECT_TEMPLATE": ({}, "Select Template"),
        "PREPARE_EXTRA_PACKAGES": ({}, "Extra packages (comma-separated, optional): "),
        "PREPARE_USE_PREFILL": ({}, "Use pre-filled auto-install settings from config?"),
        "PREPARE_USERNAME": ({}, "Username: "),
        "PREPARE_USER_PASSWORD": ({}, "User password: "),
        "PREPARE_ROOT_PASSWORD": ({}, "Root password: "),
        "PREPARE_TIMEZONE": ({}, "Timezone (e.g. Europe/Zurich): "),
        "PREPARE_KEYBOARD": ({}, "Keyboard layout (e.g. ch, us): "),
        "PREPARE_LANGUAGE": ({}, "Language (e.g. German (Germany)): "),
        "PREPARE_SCC_REGISTER": ({}, "Register with SUSE Customer Center (SCC)?"),
        "PREPARE_SCC_EMAIL": ({}, "SCC email: "),
        "PREPARE_SCC_REG_CODE": ({}, "SCC registration code: "),
        # Post-install.
        "PREPARE_POST_INSTALL_ASK": ({}, "Add post-install commands (run during auto-install)?"),
        "PREPARE_POST_INSTALL_HELP": (
            {},
            "Enter one post-install command per line (blank line to finish):",
        ),
    }

    def test_every_prompt_is_word_for_word_what_it_was(self):
        for name, (values, expected) in self.SENTENCES.items():
            with self.subTest(constant=name):
                value = getattr(StaticText, name)
                self.assertEqual(value.format(**values) if values else value, expected)

    def test_two_questions_reuse_the_constants_that_already_existed(self):
        """`Select Storage Pool` and `Cached ISOs` were constants before this ticket.

        A second constant holding the same sentence would be a sentence with two
        owners, which is the thing #31 exists to remove. `CACHED_ISOS` is also one of
        the four sources `_prepare_iso` branches on, so the comparison names it too.
        """
        self.assertEqual(StaticText.STORAGE_POOL_PROMPT, "Select Storage Pool")
        self.assertEqual(StaticText.CACHED_ISOS, "Cached ISOs")
        used = {
            node.attr
            for node in ast.walk(ast.parse(VMANAGER_CMD.read_text(encoding="utf-8")))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "StaticText"
            and node.attr in {"STORAGE_POOL_PROMPT", "CACHED_ISOS"}
        }
        self.assertEqual(used, {"STORAGE_POOL_PROMPT", "CACHED_ISOS"})

    def test_the_wording_is_marked_for_translation(self):
        """These are asked of a person, so they go through `_()` — ADR 0001.

        `_()` is the identity until a catalogue loads, so a value cannot show which
        family it was written in; the definition is read instead. This is the mirror
        of the `DeployText` test: a machine answer must not be wrapped, and a
        question asked of a person must be.
        """
        tree = ast.parse(CONSTANTS.read_text(encoding="utf-8"))
        body = next(
            node.body
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "StaticText"
        )
        wrapped = {}
        for item in body:
            if not isinstance(item, ast.Assign) or not isinstance(item.targets[0], ast.Name):
                continue
            name = item.targets[0].id
            if name.startswith("PREPARE_"):
                wrapped[name] = (
                    isinstance(item.value, ast.Call) and getattr(item.value.func, "id", None) == "_"
                )
        for name in self.SENTENCES:
            if name.startswith("PREPARE_"):
                with self.subTest(constant=name):
                    self.assertTrue(wrapped.get(name), f"{name} must be wrapped in _()")


class TestTheAnswerFile(unittest.TestCase):
    """What the writer prints, read back off the terminal rather than off the constant."""

    @patch("vmanager.vmanager_cmd.load_config")
    @patch("vmanager.vmanager_cmd.get_log_path")
    @patch("vmanager.vmanager_cmd.CLILogger")
    def setUp(self, mock_logger, mock_log_path, mock_load_config):
        mock_load_config.return_value = {"servers": []}
        mock_log_path.return_value = "/tmp/vmanager_test.log"
        with patch("sys.stdout", MagicMock()):
            self.cmd = VManagerCMD(vm_service=MagicMock())

    def test_writing_the_answer_says_what_it_said_before_the_move(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "web01.json"
            with redirect_stdout(io.StringIO()) as out:
                self.cmd._write_json(str(target), {"vm_name": "web01"})
            self.assertEqual(
                out.getvalue(),
                f"\nDeployment config written to: {target}\n"
                f"Run it with: vmc --json {target}\n"
                f"or, interactively: deploy_vm {target}\n",
            )
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {"vm_name": "web01"})

    def test_an_answer_that_cannot_be_written_is_reported_not_raised(self):
        """A person gets a sentence, not a traceback, and the caller knows to stop."""
        with tempfile.TemporaryDirectory() as directory:
            locked = Path(directory) / "locked"
            locked.mkdir()
            os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
            try:
                target = locked / "web01.json"
                with redirect_stdout(io.StringIO()) as out:
                    written = self.cmd._write_json(str(target), {"vm_name": "web01"})
            finally:
                os.chmod(locked, stat.S_IRWXU)
            self.assertFalse(written)
            self.assertFalse(target.exists())
            self.assertIn("Could not write the deployment config to ", out.getvalue())
            self.assertIn("[Errno 13]", out.getvalue())

    def test_a_config_file_is_still_written_when_the_directory_exists(self):
        """The guard does not swallow the success path it sits around."""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "deep" / "web01.json"
            with redirect_stdout(io.StringIO()):
                self.assertTrue(self.cmd._write_json(str(target), {"vm_name": "web01"}))
            self.assertTrue(target.exists())


class TestSecretsStayHidden(unittest.TestCase):
    """AC2: password fields stay hidden, as they are today."""

    def test_getpass_still_asks_and_still_does_not_echo(self):
        tree = ast.parse(VMANAGER_CMD.read_text(encoding="utf-8"))
        helper = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_get_password"
        )
        calls = {
            (node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id)
            for node in ast.walk(helper)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        self.assertIn("getpass", calls)

    def test_password_prompts_are_only_ever_asked_by_the_hidden_helper(self):
        """A prompt that names a password never reaches the echoing `_get_input`."""
        tree = ast.parse(VMANAGER_CMD.read_text(encoding="utf-8"))
        hidden, echoed = set(), set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            first = node.args[0]
            if (
                name not in {"_get_password", "_get_input"}
                or not isinstance(first, ast.Attribute)
                or not isinstance(first.value, ast.Name)
                or first.value.id != "StaticText"
            ):
                continue
            (hidden if name == "_get_password" else echoed).add(first.attr)
        self.assertEqual(hidden, PASSWORD_PROMPTS)
        self.assertEqual(echoed & PASSWORD_PROMPTS, set())


if __name__ == "__main__":
    unittest.main()
