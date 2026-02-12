{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    python3Packages.setuptools
    python3Packages.wheel
    python3Packages.pip
    libvirt
    textual
    pyyaml
    markdown-it-py
    websockify
    python3Packages.pytest
    python3Packages.pytest-cov
    python3Packages.black
    python3Packages.ruff
    python3Packages.mypy
  ];
}