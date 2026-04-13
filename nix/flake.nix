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
            version = "2.5.3";

            src = ../.;

            # Use pyproject.toml for build configuration
            format = "pyproject";

            # Build-time dependencies
            buildInputs = with pkgs; [
              gtk3
              vte
              cairo
              gdk-pixbuf
              gobject-introspection
              pango
              gtk-vnc
              spice-gtk
              # Runtime tools
              tmux
              p7zip
              qemu
            ];

            propagatedBuildInputs = with pkgs.python3Packages; [
              libvirt
              textual
              pyyaml
              markdown-it-py
              packaging
              requests
              netifaces
              # GUI dependencies (optional at runtime, but included for convenience)
              pygobject3
              pycairo
            ];

            # Optional webconsole support
            passthru.optional-dependencies = {
              webconsole = with pkgs.python3Packages; [ websockify ];
              gui = with pkgs.python3Packages; [ 
                pygobject3
                pycairo
              ] ++ (with pkgs; [
                gtk3
                vte
                gobject-introspection
                cairo
                spice-gtk
                gtk-vnc
              ]);
            };

            nativeBuildInputs = with pkgs.python3Packages; [
              setuptools
              wheel
            ] ++ (with pkgs; [ 
              makeWrapper
              wrapGAppsHook3
              gobject-introspection
            ]);

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

            # Wrap binaries to set GI_TYPELIB_PATH for GTK/GObject Introspection
            # and ensure runtime tools are in the PATH
            postFixup = ''
              for prog in $out/bin/virtui-manager $out/bin/virtui-manager-cmd $out/bin/virtui-gui $out/bin/virtui-remote-viewer; do
                if [ -f "$prog" ]; then
                  wrapProgram "$prog" \
                    --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.tmux pkgs.p7zip pkgs.qemu pkgs.libvirt ]}" \
                    --prefix GI_TYPELIB_PATH : "${pkgs.lib.makeSearchPath "lib/girepository-1.0" [ pkgs.gtk3 pkgs.vte pkgs.gdk-pixbuf pkgs.gobject-introspection pkgs.gtk-vnc pkgs.spice-gtk ]}" \
                    --prefix GI_TYPELIB_PATH : "${pkgs.pango.out}/lib/girepository-1.0" \
                    --prefix LD_LIBRARY_PATH : "${pkgs.lib.makeLibraryPath [ pkgs.cairo ]}"
                fi
              done
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
            tmux
            p7zip
            qemu
            spice-gtk
            gtk-vnc
            vte
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
