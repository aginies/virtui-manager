#!/usr/bin/env python3
"""
Script to remove unused constants from constants.py

This script:
1. Identifies constants that are defined but never used in the codebase
2. Creates a backup of constants.py
3. Removes the unused constant definitions
4. Provides a dry-run mode to preview changes before applying them

Usage:
    python3 checks/remove_unused_constants.py --dry-run  # Preview changes
    python3 checks/remove_unused_constants.py            # Actually remove constants
    python3 checks/remove_unused_constants.py --backup   # Remove and keep backup
"""

import ast
import os
import re
import sys
import argparse
import shutil
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSTANTS_FILE = os.path.join(BASE_DIR, "../src/vmanager/constants.py")
SEARCH_DIR = os.path.join(BASE_DIR, "../src")

dont_check_files = [
    "remote_viewer.py",
    "vmanager_cmd.py",
    "gui_wrapper.py",
    "i18n.py",
    "virtui_dev.py",
    "wrapper.py",
    "remote_viewer_gtk4.py",
]


def get_class_constants(filename):
    """
    Parse the constants file and return a dictionary of {ClassName: {var: line_numbers}}
    """
    if not os.path.exists(filename):
        print(f"Error: Constants file '{filename}' not found.")
        sys.exit(1)

    with open(filename, encoding="utf-8") as f:
        content = f.read()
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            print(f"Error parsing {filename}: {e}")
            sys.exit(1)

    class_constants = {}

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            vars_dict = {}
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            # Store the line number where this constant is defined
                            vars_dict[target.id] = item.lineno
            class_constants[class_name] = vars_dict

    return class_constants, content.split("\n")


def find_unused_constants(search_dir, class_constants):
    """Find constants that are defined but never used in the codebase."""
    print(f"Scanning codebase in {search_dir}...")

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

    # Find unused constants with their line numbers
    unused_constants = {}

    for cls, defined_vars in class_constants.items():
        unused_vars = {}
        for var, lineno in defined_vars.items():
            if var not in used_constants[cls]:
                unused_vars[var] = lineno

        if unused_vars:
            unused_constants[cls] = unused_vars

    return unused_constants


def create_backup(filename):
    """Create a backup of the constants file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{filename}.backup_{timestamp}"
    shutil.copy2(filename, backup_file)
    print(f"Backup created: {backup_file}")
    return backup_file


def remove_constants_from_file(filename, unused_constants, lines):
    """
    Remove unused constants from the file.

    This function identifies multi-line assignments and removes them completely.
    """
    # Collect all line numbers to remove (including multi-line assignments)
    lines_to_remove = set()

    for cls, vars_dict in unused_constants.items():
        for var, start_line in vars_dict.items():
            # Line numbers in AST are 1-based, but list indices are 0-based
            line_idx = start_line - 1

            # Mark this line for removal
            lines_to_remove.add(line_idx)

            # Check if this is a multi-line assignment (ends with open parenthesis, bracket, or continuation)
            if line_idx < len(lines):
                line_content = lines[line_idx].rstrip()

                # If the line doesn't end with a clear terminator, it's multi-line
                if line_content and not line_content.endswith((";", ")", "]", "}")):
                    if "(" in line_content or "[" in line_content or "{" in line_content:
                        # Count open brackets/parens to find where the assignment ends
                        open_count = (
                            line_content.count("(")
                            + line_content.count("[")
                            + line_content.count("{")
                        )
                        close_count = (
                            line_content.count(")")
                            + line_content.count("]")
                            + line_content.count("}")
                        )

                        # Keep looking at next lines until balanced
                        next_idx = line_idx + 1
                        while next_idx < len(lines) and open_count > close_count:
                            lines_to_remove.add(next_idx)
                            next_line = lines[next_idx]
                            open_count += (
                                next_line.count("(") + next_line.count("[") + next_line.count("{")
                            )
                            close_count += (
                                next_line.count(")") + next_line.count("]") + next_line.count("}")
                            )
                            next_idx += 1

    # Create new content excluding the lines to remove
    new_lines = []
    for idx, line in enumerate(lines):
        if idx not in lines_to_remove:
            new_lines.append(line)

    # Write the new content
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))

    return len(lines_to_remove)


def main():
    parser = argparse.ArgumentParser(
        description="Remove unused constants from constants.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what would be removed (safe, no changes made)
  python3 checks/remove_unused_constants.py --dry-run
  
  # Remove unused constants and keep backup
  python3 checks/remove_unused_constants.py --backup
  
  # Remove unused constants without keeping backup
  python3 checks/remove_unused_constants.py
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without actually removing anything",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Keep the backup file after removal (default: delete backup if successful)",
    )

    args = parser.parse_args()

    # Get constants with line numbers
    class_constants, lines = get_class_constants(CONSTANTS_FILE)

    # Find unused constants
    unused_constants = find_unused_constants(SEARCH_DIR, class_constants)

    if not unused_constants:
        print("No unused constants found. Nothing to remove.")
        sys.exit(0)

    # Display what will be removed
    total_count = 0
    print("\n" + "=" * 70)
    print("UNUSED CONSTANTS FOUND:")
    print("=" * 70)

    for cls in sorted(unused_constants.keys()):
        vars_dict = unused_constants[cls]
        if vars_dict:
            print(f"\n{cls}: ({len(vars_dict)} unused)")
            for var in sorted(vars_dict.keys()):
                lineno = vars_dict[var]
                print(f"  - {var:50} (line {lineno})")
                total_count += 1

    print("\n" + "=" * 70)
    print(f"Total unused constants: {total_count}")
    print("=" * 70)

    if args.dry_run:
        print("\n[DRY RUN] No changes made. Remove --dry-run flag to actually remove constants.")
        sys.exit(0)

    # Confirm before proceeding
    print("\nThis will remove the above constants from constants.py")
    response = input("Continue? [y/N]: ").strip().lower()

    if response != "y":
        print("Aborted.")
        sys.exit(0)

    # Create backup
    backup_file = create_backup(CONSTANTS_FILE)

    try:
        # Remove the constants
        lines_removed = remove_constants_from_file(CONSTANTS_FILE, unused_constants, lines)
        print(f"\n✓ Successfully removed {lines_removed} lines from {CONSTANTS_FILE}")

        # Delete backup unless --backup flag was used
        if not args.backup:
            os.remove(backup_file)
            print(f"✓ Backup file removed")
        else:
            print(f"✓ Backup preserved: {backup_file}")

        print("\n" + "=" * 70)
        print("NEXT STEPS:")
        print("=" * 70)
        print("1. Run the check script to verify:")
        print("   python3 checks/check_constants.py")
        print("2. Test your application to ensure nothing broke")
        print("3. Review and commit the changes:")
        print("   git diff src/vmanager/constants.py")

    except Exception as e:
        print(f"\n✗ Error removing constants: {e}")
        print(f"Backup preserved at: {backup_file}")
        sys.exit(1)


if __name__ == "__main__":
    main()
