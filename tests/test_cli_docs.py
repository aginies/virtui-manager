"""The manual must describe the command surface `vmc` actually has.

A caller reads `documentation/manual/cli.md` to write a script against `vmc`, so
a command documented that the tool does not implement, or a flag documented for a
command that takes its subject as a positional, is a script that fails for a
reason the manual denies. Both directions are checked: what is documented exists,
and what is registered is documented.

The registry side is read from the code rather than restated here: the manual is
compared with `MACHINE_COMMANDS`, `OPTIONS` and `SUBJECT_WRITTEN_AFTER`, so this
fails when either side moves alone.

The stage a progress event carries is held to the same rule, for the same reason: a
program branches on the word the way it branches on an exit status. The set in
`MachineProgressStage` is what the commands emit, the manual lists that set, and no
command may emit a stage the manual has never heard of.

The table of what a `deploy` answers is held to the answer the code builds the same way,
and for the same reason: a field a route answers that the table does not list is one a
script cannot discover, and a field the table describes as something the code never
answers is a branch that never fires. So the fields are read out of `_deploy_result` and
the two deploy routes rather than restated here. What a command shows as its usage is
held to the flags it reads for the same reason again, and no page may offer a caller a
`--config` flag that `vmc` never took.
"""

import ast
import re
import unittest
from pathlib import Path

from vmanager import machine_mode
from vmanager.constants import MachineProgressStage
from vmanager.machine_mode import MACHINE_COMMANDS
from vmanager.machine_resources import OPTIONS, SUBJECT_WRITTEN_AFTER

REPO = Path(__file__).resolve().parents[1]
CLI_MANUAL = REPO / "documentation" / "manual" / "cli.md"
VMANAGER_CMD = REPO / "src" / "vmanager" / "vmanager_cmd.py"
MACHINE_RESOURCES = REPO / "src" / "vmanager" / "machine_resources.py"
SOURCE = REPO / "src"

# The one machine command not in OPTIONS: it answers what this vmc is without a
# connection, so it has no flags to validate and no subject to act on.
COMMAND_WITHOUT_OPTIONS = {"machine info"}


def _cells(line):
    """The cells of a markdown row, leaving an escaped `\\|` inside a cell alone."""
    parts = re.split(r"(?<!\\)\|", line.strip())
    return [part for part in parts[1:-1]]


def _machine_command_table():
    """The documented resource commands, as `{form: (flags, needs_name)}`.

    A row's first cell holds one or two backticked forms (`pool list` /
    `pool info <name>`), a form is a command, its subcommand — which may be
    several written once (`domain start|shutdown|destroy|reboot`) — then its
    arguments. A flag in square brackets is optional; a name in angle brackets is
    the subject written after the command.
    """
    text = CLI_MANUAL.read_text()
    section = text.split("### Resource commands")[1].split("### Choosing the hypervisor")[0]
    documented = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cell = _cells(line)[0]
        for form in re.findall(r"`([^`]+)`", cell):
            form = form.replace("\\|", "|")
            words = form.split()
            command = words[0]
            subcommands = [None]
            arguments = words[1:]
            if arguments and not arguments[0].startswith(("-", "<", "[")):
                subcommands = arguments[0].split("|")
                arguments = arguments[1:]
            flags = set()
            needs_name = False
            for argument in arguments:
                token = argument.strip("[]<>")
                if token.startswith("--"):
                    flags.add(token[2:])
                elif argument.startswith("<"):
                    # The subject written after the command, as `domain state <name>`.
                    needs_name = True
            for subcommand in subcommands:
                path = command if subcommand is None else f"{command} {subcommand}"
                documented[path] = (flags, needs_name)
    return documented


def _interactive_commands():
    """The commands named in the manual's Command Reference Summary."""
    text = CLI_MANUAL.read_text()
    summary = text.split("## Command Reference Summary")[1]
    names = set()
    for row in summary.splitlines():
        if not row.startswith("|"):
            continue
        parts = [part.strip() for part in re.split(r"(?<!\\)\|", row.strip())[1:-1]]
        if not parts:
            continue
        category = parts[0]
        commands = "|".join(parts[1:])
        if category in ("Category", "", "---", "----------"):
            continue
        # A row's commands cell may hold a backticked pipe — `pipeline` is described
        # with one — so the cell is everything after the category, not one field.
        names.update(
            token
            for token in re.findall(r"`([^`]+)`", commands)
            if token not in ("|",) and not token.startswith("!")
        )
    return names


def _implemented_commands():
    """Every `do_*` command the interactive CLI defines."""
    return set(re.findall(r"^    def do_([a-z_]+)\(", VMANAGER_CMD.read_text(), re.M))


def _documented_stages():
    """The stage names the manual says a progress event can carry.

    The manual keeps them in one place, the table in its Progress events section: a
    row per stage, the word in the first cell. The section is read up to the next
    one, so the prose the manual writes about the same word elsewhere is not taken
    for a second list.
    """
    manual = CLI_MANUAL.relative_to(REPO)
    text = CLI_MANUAL.read_text()
    if "### Progress events" not in text:
        raise AssertionError(f"{manual} has no Progress events section to read the stages from.")
    section = text.split("### Progress events")[1].split("### Resource commands")[0]
    stages = {
        word
        for line in section.splitlines()
        if line.startswith("| `")
        for word in re.findall(r"`([^`]+)`", _cells(line)[0])
    }
    if not stages:
        raise AssertionError(f"the Progress events section of {manual} lists no stage at all.")
    return stages


# The class in the constants module that holds the stage words, named in the
# messages so a failure says where a stage has to come from.
STAGE_CLASS = "MachineProgressStage"
CONSTANTS_SOURCE = "src/vmanager/constants.py"

# The walk over `src` the stage guard reads, built once: every call that writes a
# progress event, every call in the source under the name it calls, and every
# module-level table a stage can be looked up from.
_SOURCE_SCAN = None


def _source_scan():
    global _SOURCE_SCAN
    if _SOURCE_SCAN is None:
        events = []
        calls = {}
        tables = {}

        def walk(node, inside, path):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inside = inside + (node,)
            for child in ast.iter_child_nodes(node):
                name = _name_called(child.func) if isinstance(child, ast.Call) else None
                if name:
                    calls.setdefault(name, []).append((path, child, inside))
                    if name == "progress":
                        events.append((path, child, inside))
                walk(child, inside, path)

        for path in sorted(SOURCE.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            tables[path] = _module_tables(tree)
            walk(tree, (), path)
        _SOURCE_SCAN = (events, calls, tables)
    return _SOURCE_SCAN


def _name_called(expression):
    """The word a call names: `session.progress(...)` and `progress(...)` are both progress."""
    if isinstance(expression, ast.Attribute):
        return expression.attr
    if isinstance(expression, ast.Name):
        return expression.id
    return None


def _module_tables(tree):
    """The dicts a module defines at its top level, by name."""
    tables = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    tables[target.id] = node.value
    return tables


def _emitted_stages():
    """What the commands emit, as `{stage: [where it is emitted]}`.

    A call site this cannot follow fails the run instead of being skipped: skipping
    one is the way a stage escapes the guard, so the guard reads the source rather
    than trusting that a test reaches every command.
    """
    emitted = {}
    for path, call, inside in _source_scan()[0]:
        site = f"{path.relative_to(REPO)}:{call.lineno}"
        if not call.args:
            raise AssertionError(f"{site} writes a progress event without naming a stage.")
        for stage in _stage_words(call.args[0], inside, path):
            emitted.setdefault(stage, []).append(site)
    return emitted


def _stage_words(expression, inside, path, followed=frozenset()):
    """The words the stage argument of a progress event can hold.

    A stage is written as the constant holding it, `MachineProgressStage.UPLOAD`. It
    may arrive as a name, but only one this can tie back to the set, and the tie is
    read from the code rather than assumed: a local looked up from a table whose
    values are stages, or a parameter of an enclosing function every caller passes a
    stage into. Anything else — the word spelled out at the call, a name with no
    source this can read — fails, because the manual cannot be held to a stage the
    code keeps to itself.
    """
    site = f"{path.relative_to(REPO)}:{expression.lineno}"
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        if expression.value.id == STAGE_CLASS:
            stage = getattr(MachineProgressStage, expression.attr, None)
            if not isinstance(stage, str):
                raise AssertionError(
                    f"{site}: {STAGE_CLASS}.{expression.attr} is not a stage in {CONSTANTS_SOURCE}."
                )
            return {stage}
    elif isinstance(expression, ast.Name):
        words = _stages_tied_to_a_name(expression.id, inside, path, followed)
        if words:
            return words
    raise AssertionError(
        f"{site}: the stage of a progress event has to come from {STAGE_CLASS} in "
        f"{CONSTANTS_SOURCE}, and this is neither one of them nor a name that can be "
        "followed to them."
    )


def _stages_tied_to_a_name(name, inside, path, followed):
    """The stages a variable can hold, read from where the code binds it.

    The functions the call is written in are searched innermost first for a local
    assigned from a table of stages (`stage = STAGES.get(word)`), then for a
    parameter of that name. A table imported from another module is not followed:
    the tables that hand out a stage are module-level dicts of the file reading
    them, and one that needs more than that is worth a look here.
    """
    for function in reversed(inside):
        table = _table_looked_up_in(function, name, path)
        if table is not None:
            return {
                stage for value in table.values for stage in _stage_words(value, (), path, followed)
            }
        parameters = [
            argument.arg for argument in function.args.args if argument.arg not in ("self", "cls")
        ]
        if name in parameters:
            return _stages_passed_to(function.name, name, parameters, followed)
    return set()


def _table_looked_up_in(function, name, path):
    """The table a local of `function` was looked up from, or None."""
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "get"
            and isinstance(node.value.func.value, ast.Name)
        ):
            continue
        return _source_scan()[2][path].get(node.value.func.value.id)
    return None


def _stages_passed_to(function, name, parameters, followed):
    """The stages every call of `function` passes where its `name` parameter is."""
    key = (function, name)
    if key in followed:
        return set()
    words = set()
    for path, call, inside in _source_scan()[1].get(function, []):
        argument = _argument_for(call, parameters, name)
        if argument is not None:
            words |= _stage_words(argument, inside, path, followed | {key})
    return words


def _argument_for(call, parameters, name):
    """What a call passes where `parameters` holds `name`.

    `self` and `cls` are left out of `parameters`, as a call leaves them out.
    """
    position = parameters.index(name)
    if position < len(call.args):
        return call.args[position]
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


class ResourceCommandTable(unittest.TestCase):
    def test_every_documented_command_exists(self):
        for path in _machine_command_table():
            with self.subTest(command=path):
                self.assertIn(
                    path.split()[0],
                    MACHINE_COMMANDS,
                    f"'{path}' is documented but vmc registers no '{path.split()[0]}' command.",
                )

    def test_every_documented_subcommand_is_registered(self):
        for path in _machine_command_table():
            if path in COMMAND_WITHOUT_OPTIONS:
                continue
            with self.subTest(command=path):
                self.assertIn(
                    path,
                    OPTIONS,
                    f"'{path}' is documented but is not in the vocabulary of subcommands "
                    "the resource commands dispatch on.",
                )

    def test_every_registered_subcommand_is_documented(self):
        for path in set(OPTIONS) | COMMAND_WITHOUT_OPTIONS:
            with self.subTest(command=path):
                self.assertIn(path, _machine_command_table())

    def test_documented_flags_match_the_code(self):
        for path, (flags, _needs_name) in _machine_command_table().items():
            if path in COMMAND_WITHOUT_OPTIONS:
                continue
            with self.subTest(command=path):
                self.assertEqual(
                    flags,
                    set(OPTIONS[path]),
                    f"the flags documented for '{path}' are not the flags the code reads.",
                )

    def test_documented_subjects_match_the_code(self):
        for path, (_flags, needs_name) in _machine_command_table().items():
            with self.subTest(command=path):
                self.assertEqual(
                    needs_name,
                    path in SUBJECT_WRITTEN_AFTER,
                    f"'{path}' is documented {'with a name written after it' if needs_name else 'with no name'}, "
                    "which is not how the code takes it.",
                )


class ProgressStageList(unittest.TestCase):
    """The stages a progress event carries are the stages the manual lists.

    A caller branches on the stage as it branches on an exit status, so the words
    are a contract in both directions: the manual lists what the commands emit, and
    what the commands emit is what the manual lists. The direction a coder is likely
    to break is the first one — a command given a stage of its own leaves a script
    branching on words it was never told about — so the emitters are read out of the
    source, not counted from a list this file keeps of them.
    """

    def test_the_manual_lists_every_stage_a_command_emits(self):
        documented = _documented_stages()
        for stage, sites in sorted(_emitted_stages().items()):
            with self.subTest(stage=stage):
                self.assertIn(
                    stage,
                    documented,
                    f"'{stage}' is emitted at {', '.join(sorted(set(sites)))} and the stage list in "
                    f"the Progress events section of {CLI_MANUAL.relative_to(REPO)} does not have it.",
                )

    def test_the_manual_lists_no_stage_no_command_emits(self):
        emitted = _emitted_stages()
        for stage in sorted(_documented_stages()):
            with self.subTest(stage=stage):
                self.assertIn(
                    stage,
                    emitted,
                    f"the manual lists the stage '{stage}', but no command emits it.",
                )

    def test_the_set_in_the_constants_module_is_what_the_commands_emit(self):
        emitted = _emitted_stages()
        for stage, sites in sorted(emitted.items()):
            with self.subTest(stage=stage):
                self.assertIn(
                    stage,
                    MachineProgressStage.ALL,
                    f"'{stage}' is emitted at {', '.join(sorted(set(sites)))} but is not in the set "
                    f"in {CONSTANTS_SOURCE}, so there is no set for the manual to list.",
                )
        for stage in sorted(MachineProgressStage.ALL):
            with self.subTest(stage=stage):
                self.assertIn(
                    stage,
                    emitted,
                    f"the set in {CONSTANTS_SOURCE} holds '{stage}', but no command emits it: the "
                    "set is what the commands emit, not what they might.",
                )


def _documented_exit_statuses():
    """The exit statuses the manual promises a caller, as written."""
    section = CLI_MANUAL.read_text().split("### Exit statuses")[1].split("\n## ")[0]
    statuses = set()
    for line in section.splitlines():
        cells = _cells(line)
        if cells and cells[0].strip().isdigit():
            statuses.add(int(cells[0].strip()))
    return statuses


def _statuses_a_command_answers():
    """The statuses the command surface names as its answer, read out of the code.

    A status is named where a command decides it, which is how it reaches a
    caller; the constants an import merely carries along are not decisions, so
    the code is walked for the names rather than searched as text.
    """
    answers = {
        name: value for name, value in vars(machine_mode).items() if name.startswith("EXIT_")
    }
    decided = set()
    for module in (VMANAGER_CMD, MACHINE_RESOURCES):
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node, ast.Name) and node.id in answers:
                decided.add(answers[node.id])
    return decided


class ExitStatusTable(unittest.TestCase):
    """A status row is a promise that some command can actually answer it."""

    def test_every_documented_status_is_answered_by_a_command(self):
        decided = _statuses_a_command_answers()
        for status in sorted(_documented_exit_statuses()):
            with self.subTest(status=status):
                self.assertIn(
                    status,
                    decided,
                    f"status {status} is documented but no command answers with it: "
                    "either a command has to reach it, or the row has to go",
                )

    def test_every_status_a_command_answers_is_documented(self):
        documented = _documented_exit_statuses()
        for status in sorted(_statuses_a_command_answers()):
            with self.subTest(status=status):
                self.assertIn(
                    status,
                    documented,
                    f"a command answers status {status}, which the table does not list",
                )


# What a `deploy` answers is the contract a caller writes a script against, and the
# answer is built by one function and added to by each of the two routes. The fields are
# read out of those three rather than listed here: a list of them would be a second
# thing to keep in step, and this guard exists because the manual's table had stopped
# describing the code — it was missing a field one route answers, and described another
# as a string when the route that answers it answers a word no deploy file may hold.
DEPLOY_ANSWER_TABLE = "What a `deploy` answers"
DEPLOY_ANSWER_BUILDER = "_deploy_result"
DEPLOY_ROUTES = ("deploy_from_config", "_deploy_existing_volume")


def _named_functions(source, names, where):
    """The definitions of `names`, read out of a module's source."""
    found = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name in names
    }
    missing = sorted(set(names) - set(found))
    if missing:
        raise AssertionError(
            f"{where.relative_to(REPO)} no longer defines {', '.join(missing)}, so the guard "
            "written against it reads nothing. Update the guard."
        )
    return found


def _fields_written_by(function):
    """The answer fields a function writes.

    A field is written where it is put in the answer: a key of the dict literal the
    answer is built from, or an `answer["field"] = ...` added on the way out.
    """
    fields = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            fields |= {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            fields.add(node.slice.value)
    return fields


def _deploy_answers():
    """{field: the routes that answer it}, read out of the command surface."""
    functions = _named_functions(
        VMANAGER_CMD.read_text(), {DEPLOY_ANSWER_BUILDER, *DEPLOY_ROUTES}, VMANAGER_CMD
    )
    shared = _fields_written_by(functions[DEPLOY_ANSWER_BUILDER])
    answers = {field: set(DEPLOY_ROUTES) for field in shared}
    for route in DEPLOY_ROUTES:
        for field in _fields_written_by(functions[route]) - shared:
            answers.setdefault(field, set()).add(route)
    return answers


def _documented_deploy_answers():
    """{field: its row} from the manual's table of what a `deploy` answers."""
    manual = CLI_MANUAL.read_text()
    if DEPLOY_ANSWER_TABLE not in manual:
        raise AssertionError(
            f"{CLI_MANUAL.relative_to(REPO)} has no {DEPLOY_ANSWER_TABLE!r} section, so the "
            "answer table this guard reads is gone. Put it back or update the guard."
        )
    section = manual.split(DEPLOY_ANSWER_TABLE, 1)[1].split("### Progress events")[0]
    rows = {
        _cells(line)[0].strip().strip("`").strip(): " ".join(_cells(line))
        for line in section.splitlines()
        if line.startswith("| `")
    }
    if not rows:
        raise AssertionError(
            f"the answer table in {CLI_MANUAL.relative_to(REPO)} has no rows to read."
        )
    return rows


class DeployAnswerTable(unittest.TestCase):
    """A row in the answer table is a field a deploy answers, and the other way round."""

    def test_every_field_a_deploy_answers_has_a_row(self):
        documented = _documented_deploy_answers()
        for field in sorted(_deploy_answers()):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    documented,
                    f"a deploy answers `{field}` and the table has no row for it, so a caller "
                    f"reading {CLI_MANUAL.relative_to(REPO)} cannot know it is there",
                )

    def test_every_documented_field_is_answered(self):
        answered = _deploy_answers()
        for field in sorted(_documented_deploy_answers()):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    answered,
                    f"the table promises `{field}`, which no deploy route answers: either a "
                    "route has to answer it, or the row has to go",
                )

    def test_the_screen_row_says_the_answer_can_be_no_screen(self):
        """A field whose answer may be `null` cannot be documented as a plain string.

        `null` is how a deploy file asks for no screen, and it is what both routes
        answer with. A row that promises a string sends a caller to branch on words, and
        the word the summary uses for the same machine — `none` — is refused as a bad
        `graphics` when it is written back into a file.
        """
        row = _documented_deploy_answers()["graphics"]
        self.assertIn(
            "null",
            row,
            "a deploy answers `null` for a machine with no screen, and the row for `graphics` "
            "does not say so",
        )


# A flag a command reads is a flag its caller has to be told about, and a usage line is
# where the CLI itself says what a command takes.
FLAG_NAME = re.compile(r"^--[a-z][a-z-]*$")
FLAG_IN_TEXT = re.compile(r"--[a-z][a-z-]*")


def _handlers_reading_flags():
    """{handler: (the flags it reads, the usage lines it shows)}, from the code."""
    source = VMANAGER_CMD.read_text()
    reading_flags = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("do_")
        and any(
            isinstance(constant, ast.Constant)
            and isinstance(constant.value, str)
            and FLAG_NAME.match(constant.value)
            for constant in ast.walk(node)
        )
    }
    handlers = {}
    for handler, function in sorted(_named_functions(source, reading_flags, VMANAGER_CMD).items()):
        shown = [ast.get_docstring(function) or ""] + [
            constant.value
            for constant in ast.walk(function)
            if isinstance(constant, ast.Constant)
            and isinstance(constant.value, str)
            and constant.value.lstrip().startswith("Usage:")
        ]
        handlers[handler] = (
            {
                constant.value
                for constant in ast.walk(function)
                if isinstance(constant, ast.Constant)
                and isinstance(constant.value, str)
                and FLAG_NAME.match(constant.value)
            },
            "\n".join(shown),
        )
    return handlers


def _documented_signatures(command):
    """The usage lines the manual writes for `command`, as it words them."""
    forms = [
        form
        for line in CLI_MANUAL.read_text().splitlines()
        for form in re.findall(r"`([^`]*)`", line)
        if form.split()[:1] == [command] and len(form.split()) > 1
    ]
    if not forms:
        raise AssertionError(
            f"{CLI_MANUAL.relative_to(REPO)} documents no usage line for `{command}`."
        )
    return forms


class CommandUsageLines(unittest.TestCase):
    """The usage a command shows covers the flags the command reads."""

    def test_every_flag_a_command_reads_is_in_the_usage_line_it_shows(self):
        """Measured for every command that reads a flag, not the ones someone remembered.

        `deploy_vm` read `--dryrun` and its alias `--show`, and both its own usage line
        and the manual named one of the two.
        """
        for handler, (flags, shown) in sorted(_handlers_reading_flags().items()):
            with self.subTest(command=handler):
                self.assertFalse(
                    flags - set(FLAG_IN_TEXT.findall(shown)),
                    f"{handler} reads {', '.join(sorted(flags - set(FLAG_IN_TEXT.findall(shown))))} "
                    "and neither its docstring nor the usage line it prints names them",
                )

    def test_the_documented_deploy_vm_usage_holds_both_spellings_of_a_dry_run(self):
        """The manual's usage for `deploy_vm` says what the command takes, alias included."""
        for form in _documented_signatures("deploy_vm"):
            with self.subTest(usage=form):
                self.assertEqual(
                    set(FLAG_IN_TEXT.findall(form)),
                    {"--dryrun", "--show"},
                    "deploy_vm takes --show as another way of asking for --dryrun, so its "
                    "usage line has to name both",
                )


class StaleFlagNames(unittest.TestCase):
    """A flag `vmc` does not take must not survive where a caller would read it."""

    def test_nothing_promises_a_vmc_config_flag(self):
        """`-C/--json` is the flag that names a deployment file; `--config` never was.

        Only a `--config` offered as `vmc`'s own is meant here: archinstall takes a
        `--config` of its own, and that command line is quoted as it is run.
        """
        promised = re.compile(r"vmc[^\n]*--config(?![-\w])")
        for path in sorted(SOURCE.rglob("*.py")) + [CLI_MANUAL]:
            text = path.read_text()
            with self.subTest(path=path.relative_to(REPO)):
                self.assertNotRegex(
                    text,
                    promised,
                    f"{path.relative_to(REPO)} offers a `--config` flag to a caller of vmc, "
                    "and vmc has never taken one",
                )


class InteractiveCommandSummary(unittest.TestCase):
    def test_every_summary_command_exists(self):
        implemented = _implemented_commands()
        for name in _interactive_commands():
            with self.subTest(command=name):
                self.assertIn(name, implemented)

    def test_every_command_is_in_the_summary(self):
        documented = _interactive_commands()
        for name in _implemented_commands():
            with self.subTest(command=name):
                self.assertIn(name, documented)


if __name__ == "__main__":
    unittest.main()
