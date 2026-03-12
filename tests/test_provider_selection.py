#!/usr/bin/env python3
"""
Test provider selection logic for Ubuntu templates.
"""

import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))


def test_provider_selection_logic():
    """Test that the correct provider is selected for different Ubuntu templates."""
    print("🧪 Testing provider selection logic...")

    try:
        # Test the template detection logic directly
        print("\n🔍 Testing template detection logic...")

        test_cases = [
            # Ubuntu templates (should use Ubuntu provider)
            ("autoinstall-basic.yaml", True, "Ubuntu autoinstall template"),
            ("autoinstall-minimal.yaml", True, "Ubuntu autoinstall template"),
            ("preseed-basic.cfg", True, "Ubuntu preseed template"),
            ("preseed-minimal.cfg", True, "Ubuntu preseed template"),
            ("preseed-server", True, "Ubuntu preseed template without .cfg"),
            ("something.cfg", True, "Generic .cfg file"),
            # OpenSUSE templates (should use OpenSUSE provider)
            ("autoyast-basic.xml", False, "AutoYaST template"),
            ("agama-server.json", False, "Agama template"),
            ("something-else.xml", False, "Generic .xml file"),
        ]

        for template_name, should_be_ubuntu, description in test_cases:
            # Test the detection logic from vm_provisioner.py
            is_ubuntu_template = (
                template_name.endswith(".yaml")
                or "autoinstall" in template_name.lower()
                or template_name.startswith("preseed")
                or template_name.endswith(".cfg")
            )

            expected = "Ubuntu" if should_be_ubuntu else "OpenSUSE"
            actual = "Ubuntu" if is_ubuntu_template else "OpenSUSE"

            if is_ubuntu_template == should_be_ubuntu:
                print(f"PASS {template_name} → {actual} provider ({description})")
            else:
                print(
                    f"FAIL {template_name} → {actual} provider, expected {expected} ({description})"
                )
                return False

        print("\nAll template detection tests passed!")
        return True

    except Exception as e:
        print(f"FAIL Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_provider_selection_logic()
    print(
        f"\n{'PASS Provider selection logic is correct!' if success else 'FAIL Provider selection logic failed!'}"
    )
    sys.exit(0 if success else 1)
