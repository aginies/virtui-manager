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
            version = "1.8.1";

            src = ./.;

            propagatedBuildInputs = with pkgs.python3Packages; [
              libvirt
              textual
              pyyaml
              markdown-it-py
              websockify
            ];

            nativeBuildInputs = with pkgs; [
              python3Packages.setuptools
              python3Packages.wheel
            ];

            postPatch = ''
              # Fix the shebang in wrapper.py to use the correct python path
              substituteInPlace src/vmanager/wrapper.py \
                --replace '#!/usr/bin/env python3' '#!/usr/bin/env python3'
            '';

            # Set up the entry points
            entryPoints = {
              console_scripts = {
                "virtui-manager" = "vmanager.wrapper:main";
                "virtui-manager-cmd" = "vmanager.wrapper:cmd_main";
                "virtui-remote-viewer" = "vmanager.wrapper:remote_viewer_main";
                "virtui-gui" = "vmanager.wrapper:gui_main";
              };
            };

            meta = with pkgs.lib; {
              description = "Terminal-based interface to manage virtual machines using libvirt";
              homepage = "https://github.com/aginies/virtui-manager";
              license = licenses.gpl3;
              maintainers = [ maintainers.aginies ];
              platforms = platforms.linux;
            };
          };

          default = self.packages.${system}.virtui-manager;
        };

        devShells.default = pkgs.mkShell {
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
        };
      });
}