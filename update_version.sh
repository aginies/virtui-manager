#!/bin/bash
# Script to update the version of VirtUI Manager in all required files.

if [ -z "$1" ]; then
    echo "Usage: $0 <new_version>"
    echo "Example: $0 2.4.1"
    exit 1
fi

NEW_VERSION=$1

# Detect current version from src/vmanager/constants.py as the source of truth
CURRENT_VERSION=$(grep 'version =' src/vmanager/constants.py | sed -E 's/.*version = "([^"]+)".*/\1/')

if [ -z "$CURRENT_VERSION" ]; then
    echo "Error: Could not detect current version from src/vmanager/constants.py"
    exit 1
fi

if [ "$CURRENT_VERSION" == "$NEW_VERSION" ]; then
    echo "Version is already $NEW_VERSION. Nothing to do."
    exit 0
fi

echo "Updating version from $CURRENT_VERSION to $NEW_VERSION..."

# 1. setup.cfg
if [ -f setup.cfg ]; then
    sed -i "s/^version = .*/version = $NEW_VERSION/" setup.cfg
    echo "Updated setup.cfg"
fi

# 2. pyproject.toml
if [ -f pyproject.toml ]; then
    sed -i "s/^version = \".*\"/version = \"$NEW_VERSION\"/" pyproject.toml
    echo "Updated pyproject.toml"
fi

# 3. tests/test_constants.py
if [ -f tests/test_constants.py ]; then
    sed -i "s/AppInfo.version, \".*\"/AppInfo.version, \"$NEW_VERSION\"/" tests/test_constants.py
    echo "Updated tests/test_constants.py"
fi

# 4. src/vmanager/constants.py
if [ -f src/vmanager/constants.py ]; then
    sed -i "s/version = \".*\"/version = \"$NEW_VERSION\"/" src/vmanager/constants.py
    echo "Updated src/vmanager/constants.py"
fi

# 5. nix/flake.nix
if [ -f nix/flake.nix ]; then
    sed -i "s/version = \".*\";/version = \"$NEW_VERSION\";/" nix/flake.nix
    echo "Updated nix/flake.nix"
fi

# 6. nix/default.nix
if [ -f nix/default.nix ]; then
    sed -i "s/version = \".*\";/version = \"$NEW_VERSION\";/" nix/default.nix
    echo "Updated nix/default.nix"
fi

# 7. virtui-manager.spec (Extra, as it's often needed)
if [ -f virtui-manager.spec ]; then
    sed -i "s/^Version:        .*/Version:        $NEW_VERSION/" virtui-manager.spec
    echo "Updated virtui-manager.spec"
fi

echo "Successfully updated version to $NEW_VERSION"
