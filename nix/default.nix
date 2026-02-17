{ pkgs ? import <nixpkgs> {} }:

pkgs.python3Packages.buildPythonApplication {
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
}