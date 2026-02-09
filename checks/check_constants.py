#!/usr/bin/env python3
import os
import sys
import ast
import re

CONSTANTS_FILE = "../src/vmanager/constants.py"
SEARCH_DIR = "src"

def get_class_constants(filename):
    """
    Parse the constants file and return a dictionary of {ClassName: {set_of_vars}}
    """
    if not os.path.exists(filename):
        print(f"Error: Constants file '{filename}' not found.")
        sys.exit(1)

    with open(filename, 'r', encoding='utf-8') as f:
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

            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except IOError as e:
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

def main():
    class_constants = get_class_constants(CONSTANTS_FILE)
    if check_usages(SEARCH_DIR, class_constants):
        print("Failure: Invalid constant usages found.")
        sys.exit(1)
    else:
        print("Success: All constant usages are valid.")
        sys.exit(0)

if __name__ == "__main__":
    main()
