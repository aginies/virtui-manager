#!/usr/bin/env python3
import os
import re
import sys
import configparser

def get_version_from_pyproject(file_path):
    """Extracts version from pyproject.toml using regex to avoid external deps."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Look for version = "x.y.z" specifically under [project] section roughly
            # or just match the pattern as it's usually unique in top level or project table
            match = re.search(r'^version\s*=\s*["\']([^"\\]+)["\\]', content, re.MULTILINE)
            if match:
                return match.group(1)
    except FileNotFoundError:
        print(f"Error: {file_path} not found.")
    return None

def get_version_from_setup_cfg(file_path):
    """Extracts version from setup.cfg using configparser."""
    config = configparser.ConfigParser()
    try:
        if not os.path.exists(file_path):
             print(f"Error: {file_path} not found.")
             return None
        config.read(file_path)
        if 'metadata' in config and 'version' in config['metadata']:
            return config['metadata']['version']
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
    return None

def get_version_from_constants(file_path):
    """Extracts version from constants.py."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Look for version = "x.y.z"
            match = re.search(r'version\s*=\s*["\']([^"\\]+)["\\]', content)
            if match:
                return match.group(1)
    except FileNotFoundError:
        print(f"Error: {file_path} not found.")
    return None

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    files = {
        'pyproject.toml': os.path.join(base_dir, '../pyproject.toml'),
        'setup.cfg': os.path.join(base_dir, '../setup.cfg'),
        'src/vmanager/constants.py': os.path.join(base_dir, '..', 'src', 'vmanager', 'constants.py')
    }
    
    versions = {}
    has_error = False

    # Extract versions
    versions['pyproject.toml'] = get_version_from_pyproject(files['pyproject.toml'])
    versions['setup.cfg'] = get_version_from_setup_cfg(files['setup.cfg'])
    versions['src/vmanager/constants.py'] = get_version_from_constants(files['src/vmanager/constants.py'])

    # Print versions
    print("Checking versions in project files...")
    print("-" * 60)
    print(f"{'File':<30} | {'Version':<15}")
    print("-" * 60)
    
    for filename, version in versions.items():
        v_str = version if version else "MISSING/ERROR"
        print(f"{filename:<30} | {v_str:<15}")
        if version is None:
            has_error = True

    print("-" * 60)

    # Validate
    unique_versions = set(v for v in versions.values() if v is not None)
    
    if len(unique_versions) > 1:
        print("FAILURE: Version mismatch found.")
        sys.exit(1)
    elif len(unique_versions) == 0:
         print("FAILURE: No versions found.")
         sys.exit(1)
    elif has_error:
        print("FAILURE: Some files could not be read.")
        sys.exit(1)
    else:
        print(f"SUCCESS: All versions match ({unique_versions.pop()}).")
        sys.exit(0)

if __name__ == "__main__":
    main()
