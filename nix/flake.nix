{
  description = "VirtUI Manager - Terminal-based interface to manage virtual machines using libvirt";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        packages = {
          virtui-manager = pkgs.python3Packages.buildPythonApplication {
            pname = "virtui-manager";
            version = "1.9.3";

            src = ../.;

            # Use pyproject.toml for build configuration
            format = "pyproject";

            propagatedBuildInputs = with pkgs.python3Packages; [
              libvirt
              textual
              pyyaml
              markdown-it-py
            ];

            # Optional webconsole support
            passthru.optional-dependencies = {
              webconsole = with pkgs.python3Packages; [ websockify ];
            };

            nativeBuildInputs = with pkgs.python3Packages; [
              setuptools
              wheel
            ];

            # Test dependencies
            nativeCheckInputs = with pkgs.python3Packages; [
              pytest
              pytest-cov
              pytest-asyncio
            ];

            # Run tests during build
            checkPhase = ''
              runHook preCheck
              pytest tests/test_vmcard.py tests/test_vmanager.py -v
              runHook postCheck
            '';

            # Don't run tests by default (requires system libvirt)
            doCheck = false;

            postPatch = ''
              # Fix the shebang in wrapper.py to use the correct python path
              substituteInPlace src/vmanager/wrapper.py \
                --replace '#!/usr/bin/env python3' "#!${pkgs.python3.interpreter}"
            '';

            meta = with pkgs.lib; {
              description = "Terminal-based interface to manage virtual machines using libvirt";
              homepage = "https://aginies.github.io/virtui-manager/";
              changelog = "https://github.com/aginies/virtui-manager/releases";
              license = licenses.gpl3Plus;
              maintainers = with maintainers; [ ];
              platforms = platforms.linux;
              mainProgram = "virtui-manager";
            };
          };

          default = self.packages.${system}.virtui-manager;
        };

        devShells.default = pkgs.mkShell {
          inputsFrom = [ self.packages.${system}.virtui-manager ];
          
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
        };
      });
}