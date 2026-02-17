{ pkgs ? import <nixpkgs> {} }:

let
  virtui-manager = pkgs.callPackage ./default.nix {};
in
pkgs.mkShell {
  inputsFrom = [ virtui-manager ];
  
  packages = with pkgs; [
    # Python development tools
    python3Packages.setuptools
    python3Packages.wheel
    python3Packages.pip

    # Testing tools
    python3Packages.pytest
    python3Packages.pytest-cov
    python3Packages.pytest-asyncio

    # Code quality tools
    python3Packages.black
    python3Packages.ruff
    python3Packages.mypy

    # Optional dependencies
    python3Packages.websockify

    # System dependencies
    libvirt
  ];

  shellHook = ''
    echo "VirtUI Manager development environment"
    echo "Python: $(python --version)"
    echo ""
    echo "Available commands:"
    echo "  pytest tests/              - Run tests"
    echo "  black src/                 - Format code"
    echo "  ruff check src/            - Lint code"
    echo "  mypy src/                  - Type check"
    echo "  python -m pip install -e . - Install in editable mode"
  '';
}