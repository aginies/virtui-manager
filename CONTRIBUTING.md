# Contributing to Virtui Manager

Thank you for your interest in improving Virtui Manager! This project provides a TUI for libvirt, and maintaining stability is top priority.

## General Rules
* **Code Review:** All submissions must be via Pull Request and require manual review by a maintainer.
* **Testing:** You must test your code against a live libvirt environment.
* **AI Assistance:** Use of AI asssit is permitted but **must be carefully reviewed**. You are responsible for every line of code submitted. This project is human driven, so we don't do change because an AI assist says that the code could be improved on this area.

## Technical Constraints (Workers, Caching, Text)
Virtui Manager uses an asynchronous worker system and metadata caching to prevent the UI from freezing.
* **Do Not Block:** Never use `time.sleep()` or blocking I/O in the main thread. This will freeze the TUI.
* **Use Workers:** Long-running tasks (cloning, migration) must be wrapped in Textual workers.
* **Cache Awareness:** Ensure that actions modifying a VM's state invalidate the corresponding cache.
* **Text:** All text must be put into `constants.py` file for translation, don't hard code text into the phyton code

## Nix Package Support
This project includes Nix package definitions for easy installation and development. When contributing:
* Ensure that changes to dependencies are reflected in `default.nix` and `flake.nix`
* Test that the package builds correctly with `nix build`
* Test that the development shell works with `nix develop`

## Impact Analysis
When submitting a PR, you must explain:
1.  **Use Case:** What scenario does this fix/add?
2.  **Benefits:** How does this help the user (e.g., lower bandwidth, faster UI)?
3.  **Component Changes:** Which other parts of the app are affected?
4.  **Translation update:** Any translation needs to be updated?