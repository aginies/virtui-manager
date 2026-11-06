#!/usr/bin/env python3
import ast
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSTANTS_FILE = os.path.join(BASE_DIR, "../src/vmanager/constants.py")
SEARCH_DIR = os.path.join(BASE_DIR, "../src")

# vmanager_cmd.py is deliberately not listed. It used to be, while none of its strings
# came from constants: the file has no tooltips, so the prose the exclusion was justified
# by was never looked at either way. Its deploy failures now name DeployText constants, and
# the constants census below cannot see a file it skips - with the entry in place it called
# all eighteen of them unused. Covering the file is what makes that census tell the truth.
dont_check_files = [
    "gui_wrapper.py",
    "i18n.py",
    "virtui_dev.py",
    "wrapper.py",
]


def get_class_constants(filename):
    """
    Parse the constants file and return a dictionary of {ClassName: {set_of_vars}}
    """
    if not os.path.exists(filename):
        print(f"Error: Constants file '{filename}' not found.")
        sys.exit(1)

    with open(filename, encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError as e:
            print(f"Error parsing {filename}: {e}")
            sys.exit(1)

    class_constants = {}

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            vars_set = set()
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            vars_set.add(target.id)
            class_constants[class_name] = vars_set

    return class_constants


# New AST visitor for checking tooltip assignments
class TooltipVisitor(ast.NodeVisitor):
    def __init__(self, filename, class_constants):
        self.filename = filename
        self.errors = []
        self.class_constants = class_constants  # {ClassName: {set_of_vars}}
        self._parents = []  # To keep track of parent nodes

    def visit(self, node):
        self._parents.append(node)
        super().visit(node)
        self._parents.pop()

    def _is_translation_func(self, node):
        return isinstance(node, ast.Name) and node.id == "_"

    def _check_tooltip_value(self, node_value, line_no):
        # 1. Check if the value is a call to _()
        if (
            isinstance(node_value, ast.Call)
            and isinstance(node_value.func, ast.Name)
            and node_value.func.id == "_"
        ):
            return  # Valid: _("some text")

        # 2. Check if the value is an attribute access to a constant (e.g., StaticText.TOOLTIP_MESSAGE)
        if isinstance(node_value, ast.Attribute):
            class_name = node_value.value.id if isinstance(node_value.value, ast.Name) else None
            constant_name = node_value.attr

            if class_name and constant_name:
                if (
                    class_name in self.class_constants
                    and constant_name in self.class_constants[class_name]
                ):
                    return  # Valid: ClassName.CONSTANT

        # 3. If it's a plain string, it's an error
        if isinstance(node_value, ast.Constant) and isinstance(node_value.value, str):
            self.errors.append((line_no, node_value.value))
            return

        # 4. If it's an f-string (JoinedStr), it's also an error if not wrapped in _()
        if isinstance(node_value, ast.JoinedStr):
            # Extract content from f-string for reporting
            fstring_content_parts = []
            for val in node_value.values:
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    fstring_content_parts.append(val.value)
                elif isinstance(val, ast.FormattedValue):
                    # We are interested in the string parts, not the formatted variables themselves
                    if isinstance(val.value, ast.Constant) and isinstance(val.value.value, str):
                        fstring_content_parts.append(val.value.value)
                    elif isinstance(val.value, ast.Name):
                        # For f-string like f"hello {name}", we just get "hello "
                        # The variable part isn't a hardcoded string we can translate
                        pass

            if fstring_content_parts:
                self.errors.append((line_no, "".join(fstring_content_parts).strip()))
            else:
                self.errors.append((line_no, "<f-string with only variables>"))
            return

    def visit_Call(self, node):
        for kw in node.keywords:
            if kw.arg == "tooltip":
                self._check_tooltip_value(kw.value, node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node):
        # Check for assignments like `widget.tooltip = "Some text"`
        # This assumes a single target for simplicity.
        if isinstance(node.targets[0], ast.Attribute):
            if node.targets[0].attr == "tooltip":
                self._check_tooltip_value(node.value, node.lineno)
        self.generic_visit(node)


def check_tooltips(search_dir, class_constants):
    print(f"Checking for untranslated tooltips in {search_dir}...")
    error_found = False

    abs_constants_file = os.path.abspath(CONSTANTS_FILE)

    for root, _, files in os.walk(search_dir):
        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

            # Skip the constants file itself
            if os.path.abspath(file_path) == abs_constants_file:
                continue

            if file in dont_check_files:
                continue

            try:
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                tree = ast.parse(content)
                visitor = TooltipVisitor(file_path, class_constants)
                visitor.visit(tree)

                if visitor.errors:
                    error_found = True
                    for lineno, text in visitor.errors:
                        display_text = (text[:40] + "...") if len(text) > 40 else text
                        print(
                            f"Error: Untranslated tooltip in '{file_path}:{lineno}': \"{display_text}\""
                        )

            except SyntaxError as e:
                print(f"Error parsing {file_path}: {e}")
            except Exception as e:
                print(f"Error processing {file_path}: {e}")

    return error_found


# The calls that hand a deploy failure to whoever ran the command. The sentence is printed,
# and under --machine it also ships as the `message` of the failure answer, so a sentence
# typed at the call site moves the machine contract without anyone renaming a constant.
#
# Deliberately only these calls, not the whole file: vmanager_cmd.py holds 606 string
# literals passed as the first argument to print, input and the prompt helpers, and a check
# that reports all of them is a backlog rather than a check.
DEPLOY_FAILURE_CALLS = ("_deploy_fail",)


def _written_prose(node):
    """The words spelled into an argument, or None when it names something instead.

    None covers both a constant reference and a bare `str(e)`: neither adds wording of its
    own, which is the distinction this pass cares about. An f-string made only of
    placeholders reads the caller's own text, so it has no prose either.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ]
        return "".join(parts) if parts else None
    return None


def find_hardcoded_deploy_wording(source, filename):
    """Find deploy failures whose wording is written at the call site.

    Returns [(line, text)] and reports nothing itself, so the suite can ask it about a
    snippet as well as about the real file.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Bound as a method or called plain: both reach a deploy failure.
        called = (
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        )
        if called not in DEPLOY_FAILURE_CALLS or not node.args:
            continue
        text = _written_prose(node.args[0])
        if text is not None:
            findings.append((node.lineno, text.strip()))
    return findings


def check_deploy_wording(search_dir):
    """Report deploy failures answered with a sentence written in place."""
    print(f"Checking deploy failure wording in {search_dir}...")
    error_found = False

    for root, _, files in os.walk(search_dir):
        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

            if file in dont_check_files:
                continue

            try:
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError as e:
                print(f"Warning: Could not read file {file_path}: {e}")
                continue

            for lineno, text in find_hardcoded_deploy_wording(content, file_path):
                display_text = (text[:40] + "...") if len(text) > 40 else text
                print(
                    f"Error: Hardcoded deploy wording in '{file_path}:{lineno}': \"{display_text}\""
                )
                error_found = True

    return error_found


def check_usages(search_dir, class_constants):
    error_found = False

    # Pre-compile regexes for each class
    # Matches ClassName.Attribute where Attribute is alphanumeric + underscore
    patterns = {cls: re.compile(rf"\b{cls}\.([a-zA-Z0-9_]+)") for cls in class_constants}

    print(f"Checking usage of constants in {search_dir}...")

    abs_constants_file = os.path.abspath(CONSTANTS_FILE)

    for root, _, files in os.walk(search_dir):
        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

            # Skip the constants file itself
            if os.path.abspath(file_path) == abs_constants_file:
                continue

            if file in dont_check_files:
                continue

            try:
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError as e:
                print(f"Warning: Could not read file {file_path}: {e}")
                continue

            for cls, valid_vars in class_constants.items():
                # Find all usages of Class.Attribute
                matches = patterns[cls].findall(content)

                for usage in matches:
                    if usage not in valid_vars:
                        # Report error
                        print(
                            f"Error: File '{file_path}' uses '{cls}.{usage}' which is NOT defined in {CONSTANTS_FILE}"
                        )
                        error_found = True

    return error_found


def check_unused_constants(search_dir, class_constants):
    """Check for constants defined in constants.py but never used in the codebase."""
    print(f"Checking for unused constants in {search_dir}...")

    # Track all constant usages
    used_constants = {cls: set() for cls in class_constants}

    # Pre-compile regex patterns for each class
    patterns = {cls: re.compile(rf"\b{cls}\.([a-zA-Z0-9_]+)") for cls in class_constants}

    abs_constants_file = os.path.abspath(CONSTANTS_FILE)

    # Scan all Python files to find which constants are used
    for root, _, files in os.walk(search_dir):
        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

            # Skip the constants file itself
            if os.path.abspath(file_path) == abs_constants_file:
                continue

            if file in dont_check_files:
                continue

            try:
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError as e:
                print(f"Warning: Could not read file {file_path}: {e}")
                continue

            # Find all constant usages in this file
            for cls, pattern in patterns.items():
                matches = pattern.findall(content)
                used_constants[cls].update(matches)

    # Find unused constants
    error_found = False
    unused_count = 0

    for cls, defined_vars in class_constants.items():
        unused_vars = defined_vars - used_constants[cls]

        if unused_vars:
            error_found = True
            for var in sorted(unused_vars):
                print(f"Warning: Constant '{cls}.{var}' is defined but never used in the codebase")
                unused_count += 1

    if unused_count > 0:
        print(f"\nTotal unused constants: {unused_count}")

    return error_found


def main():
    class_constants = get_class_constants(CONSTANTS_FILE)

    constants_error = check_usages(SEARCH_DIR, class_constants)
    tooltip_error = check_tooltips(SEARCH_DIR, class_constants)
    deploy_error = check_deploy_wording(SEARCH_DIR)
    unused_error = check_unused_constants(SEARCH_DIR, class_constants)

    print(f"Excluded: {dont_check_files}")
    if constants_error:
        print("Failure: in class_constants.")
        sys.exit(1)
    elif tooltip_error:
        print("Failure: found untranslated tooltips.")
        sys.exit(1)
    elif deploy_error:
        print("Failure: deploy failure wording must come from DeployText.")
        sys.exit(1)
    else:
        if unused_error:
            print(
                "Warning: Found unused constants (consider removing them to reduce translation work)."
            )
        print("Success: All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
