"""Tests for what a deployment reports as it runs: a percent, and a word.

A percent is for a progress bar, so a caller is entitled to two things: the bar
never rewinds, and one event carries one number. Both are the job of the reporter
both deploy routes report through, and both were broken — the install route
looked the ISO up twice, at 55 and again at 55 after having reported 82, and
uploaded files with a percent of their own in the message.

The word beside the percent has one rule of its own: there are two deploy routes
and one activity, so the same event is reported with the same words whichever route
performed it. The volume-boot route used to write its strings where it used them
("Defining the machine" beside an install's `Defining VM`), which is both a second
vocabulary for a caller watching a machine come into being and a set of words no
translation pass can see. `WordingTest` reads the module to keep that from coming
back, in the way `tests/test_deploy_wording.py` does for the words a failed deploy
answers with — progress words and answer words are guarded apart on purpose, since
one streams beside a percentage and the other is the last thing a caller reads.
"""

import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from unittest.mock import MagicMock

from vmanager.constants import StaticText
from vmanager.vm_provisioner import VMProvisioner, progress_reporter

PROVISIONER = Path(__file__).resolve().parents[1] / "src" / "vmanager" / "vm_provisioner.py"

# A machine booted from a volume reports two words an install has no event for, and
# they are the only two it may add: an install defines and starts in one step, and it
# is never told to leave the machine stopped. A third word of its own for an event the
# other route already names is what this file exists to stop.
BOOT_ONLY = frozenset({"PROVISIONING_STARTING_VM", "PROVISIONING_COMPLETE_NOT_STARTED"})


def _reporter_called(node):
    """Whether this call is a deploy saying how far it has got.

    Deploy progress takes two shapes in this module: `report(message, percent)`, the
    reporter every route is handed, and the `progress_callback` a helper is passed and
    forwards straight on. Both carry a message a caller reads.
    """
    func = node.func
    name = (
        func.attr
        if isinstance(func, ast.Attribute)
        else (func.id if isinstance(func, ast.Name) else None)
    )
    return name in ("report", "progress_callback")


def _literals_in(expression):
    """The words spelled out inside a message argument, if any.

    A `StaticText` member holds none of them. A string does, and so does a piece of one
    left inside an f-string, which is wording written where it is used.
    """
    return [
        node.value
        for node in ast.walk(expression)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _progress_reports(source):
    """Every deploy progress report in `source`, as `(where, message argument, line)`.

    `where` is the chain of functions the call sits in, so a report can be tied to the
    route that makes it — including a helper defined inside a route, which reports for
    it.
    """
    reports = []

    def walk(node, inside):
        if isinstance(node, ast.Call) and _reporter_called(node) and node.args:
            reports.append((inside, node.args[0], node.lineno))
        for child in ast.iter_child_nodes(node):
            deeper = inside + (child.name,) if isinstance(child, ast.FunctionDef) else inside
            walk(child, deeper)

    walk(ast.parse(source), ())
    return reports


def _constants_named(expression):
    """The `StaticText` members a message argument can hold.

    A `.format(...)` names the same constant it formats, and a choice between two
    words names both — the report that ends a boot says one thing when the machine is
    started and another when it is left defined.
    """
    while isinstance(expression, ast.Call) and isinstance(expression.func, ast.Attribute):
        expression = expression.func.value
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        return {expression.attr} if expression.value.id == "StaticText" else set()
    if isinstance(expression, ast.IfExp):
        return _constants_named(expression.body) | _constants_named(expression.orelse)
    return set()


REPORTS = _progress_reports(PROVISIONER.read_text(encoding="utf-8"))


def _words(expressions):
    named = set()
    for expression in expressions:
        named |= _constants_named(expression)
    return named


def words_reported_by(*route):
    """The words reported inside one route: a function and the helpers defined in it."""
    return _words(
        expression for inside, expression, _line in REPORTS if inside[: len(route)] == route
    )


def words_reported_outside(*route):
    """The words reported by everything else in the module, which is the other route."""
    return _words(
        expression for inside, expression, _line in REPORTS if inside[: len(route)] != route
    )


class PercentTest(unittest.TestCase):
    def events(self, *reports, watched=True):
        seen = []
        report = progress_reporter(
            (lambda message, percent: seen.append((message, percent))) if watched else None
        )
        for message, percent in reports:
            report(message, percent)
        return seen

    def percents(self, *reports, **kwargs):
        return [percent for _message, percent in self.events(*reports, **kwargs)]

    def test_a_stage_reports_the_percent_it_was_given(self):
        self.assertEqual(
            self.percents(("defining", 40), ("starting", 60), ("done", 100)), [40, 60, 100]
        )

    def test_a_percent_never_goes_backwards(self):
        """The transcript that started this: 82, then 55, then 81."""
        self.assertEqual(
            self.percents(("automation", 82), ("iso lookup", 55), ("kernel", 81)),
            [82, 82, 82],
        )

    def test_a_lookup_that_changed_nothing_reports_nothing(self):
        """The second finding of an ISO that was there all along is not a stage."""
        self.assertEqual(
            [
                message
                for message, _percent in self.events(
                    ("Using existing ISO volume", 55),
                    ("automation", 82),
                    ("Using existing ISO volume", 55),
                )
            ],
            ["Using existing ISO volume", "automation"],
        )

    def test_a_stage_that_has_gone_further_is_reported_again(self):
        """A transfer repeated with more bytes done is progress, not a rewind."""
        self.assertEqual(
            self.percents(("uploading", 55), ("uploading", 65), ("uploading", 75)), [55, 65, 75]
        )

    def test_a_run_nobody_watches_is_still_written_down(self):
        report = progress_reporter(None)
        with self.assertLogs(level="INFO") as logs:
            report(StaticText.PROVISIONING_DEFINING_VM, 40)
        self.assertIn("40%", "\n".join(logs.output))


class DeployProgressTest(unittest.TestCase):
    """A real route run, rather than the rules about it."""

    def setUp(self):
        conn = MagicMock()
        conn.getCapabilities.return_value = (
            "<capabilities><guest><arch name='x86_64'>"
            "<machine>pc-q35-8.2</machine>"
            "</arch></guest></capabilities>"
        )
        self.provisioner = VMProvisioner(conn)
        self.events = []

    def test_the_percents_of_a_boot_are_not_in_the_past(self):
        self.provisioner.provision_existing_volume(
            vm_name="sidekick-worker01",
            disk_path="/var/lib/libvirt/images/sidekick-worker01.qcow2",
            disk_format="qcow2",
            memory_mb=2048,
            vcpu_count=2,
            network_name="default",
            progress_callback=lambda message, percent: self.events.append((message, percent)),
        )
        percents = [percent for _message, percent in self.events]
        self.assertTrue(percents, "a deploy that reports nothing reports nothing monotonic either")
        self.assertEqual(
            percents,
            sorted(percents),
            f"a progress bar that rewinds: {[(m, p) for m, p in self.events]}",
        )


class WordingTest(unittest.TestCase):
    """The words a deployment reports, which both routes must say the same way.

    A progress event carries two words for what is happening: `stage`, a name out of
    `MachineProgressStage` that a program branches on, and `message`, the operation's
    own text for whoever reads the log. Only the first is a contract — decision 0001 in
    `docs/adr/` says so, and leaves `message` translated — but both routes are doing the
    same job, so the same event must not arrive in two vocabularies. These are checked
    against the constants rather than against the English, because a reword of a message
    is a copy edit and a second word for one event is a defect.
    """

    def setUp(self):
        conn = MagicMock()
        conn.getCapabilities.return_value = (
            "<capabilities><guest><arch name='x86_64'>"
            "<machine>pc-q35-8.2</machine>"
            "</arch></guest></capabilities>"
        )
        self.provisioner = VMProvisioner(conn)
        # The firmware is a file pair cloned out of a template, which is a job for
        # libvirt and not for this test: the route only needs to know it happened.
        self.provisioner._setup_uefi_nvram = MagicMock(return_value=("/p/loader.fd", "/p/nvram.fd"))
        domain = MagicMock()
        domain.isActive.return_value = 1
        conn.defineXML.return_value = domain

    def boot(self, **kwargs):
        events = []
        self.provisioner.provision_existing_volume(
            vm_name="sidekick-worker01",
            disk_path="/var/lib/libvirt/images/sidekick-worker01.qcow2",
            disk_format="qcow2",
            memory_mb=2048,
            vcpu_count=2,
            network_name="default",
            boot_uefi=True,
            progress_callback=lambda message, percent: events.append((message, percent)),
            **kwargs,
        )
        return events

    def test_a_boot_reports_the_shared_events_in_the_words_of_an_install(self):
        """One event, one word, and the percentages a boot has always reported.

        The firmware, the definition and the ending are events both routes perform, so
        they are the install route's constants: `Setting up UEFI Firmware`, `Defining
        VM`, `Provisioning Complete`. Starting is the boot route's own (an install
        defines and starts in one step), and 20, 40, 60 and 100 are unchanged.
        """
        self.assertEqual(
            self.boot(),
            [
                (StaticText.PROVISIONING_SETTING_UP_UEFI_FIRMWARE, 20),
                (StaticText.PROVISIONING_DEFINING_VM, 40),
                (StaticText.PROVISIONING_STARTING_VM, 60),
                (StaticText.PROVISIONING_COMPLETE, 100),
            ],
        )

    def test_a_machine_left_defined_says_so_instead_of_claiming_a_boot(self):
        """The one ending a boot can be told to give, and its own word for it.

        `start: false` leaves a defined machine that was never started. An install has
        no word for that because it cannot end that way, so this is a constant of the
        boot route's own — not the shared word with the machine's name stuck on it,
        which the answer object already carries under `domain` and `running`.
        """
        events = self.boot(start=False)
        self.assertEqual(events[-1], (StaticText.PROVISIONING_COMPLETE_NOT_STARTED, 100))
        self.assertNotIn(StaticText.PROVISIONING_STARTING_VM, [message for message, _ in events])

    def test_the_boot_route_adds_only_the_words_that_are_genuinely_its_own(self):
        """Read from the code, so a second word for one event cannot be added quietly.

        Everything the volume-boot route reports is a word the install route reports
        too, except `BOOT_ONLY`. A boot that invents its own name for defining a domain
        would put two vocabularies back into one activity without touching a test a
        reader would think to change.
        """
        boot = words_reported_by("provision_existing_volume")
        its_own = boot - words_reported_outside("provision_existing_volume")
        self.assertTrue(
            boot,
            "the volume-boot route reports no words at all, so this guard has nothing to read",
        )
        self.assertEqual(
            its_own,
            BOOT_ONLY,
            f"the boot route words no route shares: {sorted(its_own)}",
        )

    def test_no_deploy_progress_word_is_written_in_place(self):
        """Every word a deploy reports is a constant, so a translation pass can see it.

        The install route already read its words from `StaticText`; the volume-boot
        route did not, and the install route had three literals of its own besides. A
        string written where it is used is invisible to the catalogue and free to
        disagree with the other route about the name of the same step.
        """
        spelled_out = [
            f"line {line}: {literal!r}"
            for _inside, expression, line in REPORTS
            for literal in _literals_in(expression)
        ]
        self.assertEqual(
            spelled_out,
            [],
            "deploy progress wording must come from StaticText: " + "; ".join(spelled_out),
        )

    def test_the_guard_above_is_not_green_because_it_reads_nothing(self):
        """The negative control: a word spelled out at the call is what it reports."""
        source = (
            "class P:\n"
            "    def provision_existing_volume(self, report):\n"
            '        report("Making the thing", 40)\n'
            "        report(StaticText.PROVISIONING_DEFINING_VM, 60)\n"
            '        progress_callback(f"Also made: {name}", 80)\n'
        )
        reports = _progress_reports(source)
        self.assertEqual(
            [len(_literals_in(expression)) for _inside, expression, _line in reports],
            [1, 0, 1],
            "a constant passes; a word written in place passes for nothing, whether it is "
            "quoted outright or left inside an f-string",
        )


if __name__ == "__main__":
    unittest.main()
