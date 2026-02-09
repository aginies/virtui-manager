#!/usr/bin/env python3
import ast
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSTANTS_FILE = os.path.join(BASE_DIR, "../src/vmanager/constants.py")
SEARCH_DIR = os.path.join(BASE_DIR, "../src")

dont_check_files = ["remote_viewer.py", 
                    "vmanager_cmd.py",
                    "gui_wrapper.py",
                    "i18n.py",
                    "virtui_dev.py",
                    "wrapper.py",
                    "remote_viewer_gtk4.py",
                   ]

def get_class_constants(filename):
    """
    Parse the constants file and return a dictionary of {ClassName: {set_of_vars}}
    """
    if not os.path.exists(filename):
        print(f"Error: Constants file '{filename}' not found.")
        sys.exit(1)

    with open(filename, encoding='utf-8') as f:
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

def check_usages(search_dir, class_constants):
    error_found = False

    # Pre-compile regexes for each class
    # Matches ClassName.Attribute where Attribute is alphanumeric + underscore
    patterns = {
        cls: re.compile(rf"\b{cls}\.([a-zA-Z0-9_]+)")
        for cls in class_constants
    }

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
                with open(file_path, encoding='utf-8', errors='ignore') as f:
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
                        print(f"Error: File '{file_path}' uses '{cls}.{usage}' which is NOT defined in {CONSTANTS_FILE}")
                        error_found = True

    return error_found

class I18NVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.errors = []
        self._parents = []

    def visit(self, node):
        self._parents.append(node)
        super().visit(node)
        self._parents.pop()

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            self._check_string(node, node.value)
        self.generic_visit(node)

    # For Python < 3.8 compatibility if needed, though CI uses 3.12
    def visit_Str(self, node):
        self._check_string(node, node.s)
        self.generic_visit(node)

    def visit_JoinedStr(self, node):
        # Heuristic for f-strings: concatenate text parts
        text_content = ""
        for value in node.values:
             if isinstance(value, ast.Constant) and isinstance(value.value, str):
                 text_content += " " + value.value
             # Keep ast.Str for very old python versions if running there, though unlikely
             elif sys.version_info < (3, 8) and isinstance(value, ast.Str):
                 text_content += " " + value.s

        if text_content:
            self._check_string(node, text_content)
        self.generic_visit(node)

    def _check_string(self, node, text_content):
        # Heuristic: Must have letters and spaces to be considered "text"
        if not (re.search(r'[a-zA-Z]', text_content) and re.search(r'\s', text_content)):
            return

        # Look up the tree to see if we are in a translation or logging call
        # We check a few levels up to handle f-strings and keyword arguments
        for i in range(2, min(5, len(self._parents) + 1)):
            p = self._parents[-i]
            if isinstance(p, ast.Call):
                if self._is_translation_func(p.func) or self._is_logging_func(p.func):
                    return
            if isinstance(p, ast.keyword):
                # If it's a keyword arg, check the call it belongs to
                # The keyword node's parent (grandparent of current) should be the Call
                pass

        parent = self._parents[-2] if len(self._parents) >= 2 else None
        if not parent:
            return

        # Ignore docstrings
        if isinstance(parent, ast.Expr):
             grandparent = self._parents[-3] if len(self._parents) >= 3 else None
             if isinstance(grandparent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                 if node == parent.value:
                     return

        # If we are a constant inside a JoinedStr, we already check the JoinedStr itself
        if isinstance(node, ast.Constant) and isinstance(parent, ast.JoinedStr):
            return
        # Backward compatibility for very old python if needed
        if sys.version_info < (3, 8) and isinstance(node, ast.Str) and isinstance(parent, ast.JoinedStr):
            return

        self.errors.append((node.lineno, text_content))

    def _is_translation_func(self, node):
        if isinstance(node, ast.Name) and node.id == '_':
            return True
        return False

    def _is_logging_func(self, node):
        # logging.info(...) -> Attribute
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == 'logging':
                if node.attr in ('debug', 'info', 'warning', 'warn', 'error', 'critical', 'exception'):
                    return True
        # also handle cases like self.log_message or logger.info if possible
        # but let's stick to the requested ones for now.
        return False

def check_i18n(search_dir):
    print(f"Checking for untranslated strings in {search_dir}...")
    error_found = False

    for root, _, files in os.walk(search_dir):
        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

            if file in dont_check_files:
                continue

            # Skip tests/ or checks/ if they are in search_dir (SEARCH_DIR is src, so fine)

            try:
                with open(file_path, encoding='utf-8') as f:
                    content = f.read()

                tree = ast.parse(content)
                visitor = I18NVisitor(file_path)
                visitor.visit(tree)

                if visitor.errors:
                    error_found = True
                    for lineno, text in visitor.errors:
                        # Limit text length for display
                        display_text = (text[:40] + '...') if len(text) > 40 else text
                        print(f"Warning: Untranslated text in '{file_path}:{lineno}': \"{display_text}\"")

            except SyntaxError as e:
                print(f"Error parsing {file_path}: {e}")
            except Exception as e:
                print(f"Error processing {file_path}: {e}")

    return error_found

def main():
    class_constants = get_class_constants(CONSTANTS_FILE)

    constants_error = check_usages(SEARCH_DIR, class_constants)
    i18n_error = check_i18n(SEARCH_DIR)

    print(f"Excluded: {dont_check_files}")
    if constants_error:
        print("Failure: in class_constants.")
        sys.exit(1)
    elif i18n_error:
        print("Failure: found, but its WIP (missing translation).")
        sys.exit(0)
    else:
        print("Success: All checks passed.")
        sys.exit(0)

if __name__ == "__main__":
    main()
