# Simple test to verify the Nix package can be built
{ pkgs ? import <nixpkgs> {} }:

let
  virtui-manager = import ./default.nix { pkgs = pkgs; };
in
virtui-manager