# Contributing to Virtui Manager

Thank you for your interest in improving Virtui Manager! This project provides a TUI for libvirt, and maintaining stability is top priority.

## General Rules
* **Code Review:** All submissions must be via Pull Request and require manual review by a maintainer.
* **Testing:** You must test your code against a live libvirt environment.

## AI Assistance

* Do not post output from Large Language Models or similar generative AI as comments on GitHub or our discourse server, as such comments tend to be formulaic and low content.
* If you use generative AI tools as an aid in developing code or documentation changes, ensure that you fully understand the proposed changes and can explain why they are the correct approach.
* Make sure you have added value based on your personal competency to your contributions. Just taking some input, feeding it to an AI and posting the result is not of value to the project. To preserve precious core developer capacity, we reserve the right to rigorously reject seemingly AI generated low-value contributions.
* It is also strictly forbidden to post AI generated content to issues or PRs via automated tooling such as bots or agents. We may ban such users and/or report them to GitHub.
* You are responsible for every line of code submitted. This project is human driven, so we don't do change because an AI assist says that the code could be improved in any area.

## Technical Constraints (Workers, Caching, Text)
Virtui Manager uses an asynchronous worker system and metadata caching to prevent the UI from freezing.
* **Do Not Block:** Never use `time.sleep()` or blocking I/O in the main thread. This will freeze the TUI.
* **Use Workers:** Long-running tasks (cloning, migration) must be wrapped in Textual workers.
* **Cache Awareness:** Ensure that actions modifying a VM's state invalidate the corresponding cache.
* **Text:** All text must be put into `constants.py` file for translation, don't hard code text into the phyton code

## Security Note
Always implements comprehensive sanitization of sensitive information to prevent 
accidental exposure of passwords, connection URIs, libvirt error details, and other
secrets in command-line output, logs, and error messages.

## Nix Package Support
This project includes Nix package definitions for easy installation and development. When contributing:
* Ensure that changes to dependencies are reflected in `nix/default.nix` and `nix/flake.nix`
* Test that the package builds correctly with `nix build`
* Test that the development shell works with `nix develop`

## Impact Analysis
When submitting a PR, you must explain:
1.  **Use Case:** What scenario does this fix/add?
2.  **Benefits:** How does this help the user (e.g., lower bandwidth, faster UI)?
3.  **Component Changes:** Which other parts of the app are affected?
4.  **Translation update:** Any translation needs to be updated?
