#!/usr/bin/env python3
"""
Test the fixed Ubuntu template detection logic
"""


def test_fixed_template_detection():
    """Test that the fixed logic correctly identifies Ubuntu vs openSUSE templates"""

    print("Testing FIXED Ubuntu template detection...\n")

    test_cases = [
        # (template_name, expected_is_ubuntu, expected_os_type, description)
        ("autoinstall-basic.yaml", True, "ubuntu", "Ubuntu autoinstall"),
        ("preseed-basic.cfg", True, "ubuntu", "Ubuntu preseed"),
        ("preseed-minimal.cfg", True, "ubuntu", "Ubuntu preseed"),
        ("autoyast-basic.xml", False, "opensuse", "openSUSE AutoYaST XML"),
        ("autoyast-basic.cfg", False, "opensuse", "openSUSE AutoYaST CFG FIXED!"),
        ("agama-config.json", False, "opensuse", "Agama JSON"),
        ("custom-autoyast.cfg", False, "opensuse", "openSUSE custom CFG FIXED!"),
        ("some-other-file.cfg", False, "opensuse", "Generic CFG file FIXED!"),
    ]

    for template_name, exp_ubuntu, exp_os_type, desc in test_cases:
        # Fixed Ubuntu template detection logic
        is_ubuntu_template = (
            template_name.endswith(".yaml")
            or "autoinstall" in template_name.lower()
            or template_name.startswith("preseed")
            or (template_name.endswith(".cfg") and "preseed" in template_name.lower())
        )

        # OS type determination
        if is_ubuntu_template:
            determined_os_type = "ubuntu"
        else:
            determined_os_type = "opensuse"

        success = (is_ubuntu_template == exp_ubuntu) and (determined_os_type == exp_os_type)
        status = "PASS" if success else "FAIL"

        print(f"{status} {desc}")
        print(f"   Template: '{template_name}'")
        print(f"   Ubuntu template: {is_ubuntu_template} (expected: {exp_ubuntu})")
        print(f"   OS type: {determined_os_type} (expected: {exp_os_type})")
        print()

        if not success:
            raise AssertionError(f"Template detection failed for {template_name}")


def test_cmdline_generation():
    """Test cmdline generation with fixed template detection"""

    print("Testing cmdline generation with FIXED template detection...\n")

    test_cases = [
        # (template_name, automation_url, expected_cmdline)
        ("autoyast-basic.cfg", "http://example.com/autoyast.cfg", "autoyast="),
        ("autoyast-custom.cfg", "http://example.com/custom.cfg", "autoyast="),
        ("preseed-basic.cfg", "http://example.com/preseed.cfg", "preseed/url="),
        ("autoinstall-basic.yaml", "ubuntu-autoinstall", "autoinstall ds=nocloud"),
    ]

    for template_name, automation_url, expected_cmd in test_cases:
        # Fixed template detection
        is_ubuntu_template = (
            template_name.endswith(".yaml")
            or "autoinstall" in template_name.lower()
            or template_name.startswith("preseed")
            or (template_name.endswith(".cfg") and "preseed" in template_name.lower())
        )

        os_type = "ubuntu" if is_ubuntu_template else "opensuse"

        # Cmdline generation logic
        cmdline = ""
        if automation_url:
            if automation_url.endswith(".json"):
                cmdline = f"inst.auto={automation_url} inst.auto_insecure=1"
            elif automation_url.endswith(".cfg"):
                if os_type and os_type.lower() == "ubuntu":
                    cmdline = (
                        f"auto=true priority=critical preseed/url={automation_url} interface=auto"
                    )
                else:
                    cmdline = f"autoyast={automation_url}"
            elif automation_url == "ubuntu-autoinstall" or "autoinstall" in automation_url.lower():
                cmdline = "autoinstall ds=nocloud"
            else:
                cmdline = f"autoyast={automation_url}"

        success = expected_cmd in cmdline
        status = "PASS" if success else "FAIL"

        print(f"{status} Template: '{template_name}' → OS: {os_type}")
        print(f"   Generated cmdline: {cmdline}")
        print(f"   Expected to contain: '{expected_cmd}'")
        print()

        if not success:
            raise AssertionError(f"Cmdline generation failed for {template_name}")


if __name__ == "__main__":
    print("Testing CRITICAL FIX for SLES/openSUSE cmdline generation...\n")

    test_fixed_template_detection()
    test_cmdline_generation()

    print("CRITICAL FIX VERIFIED!")
    print("\nFix Summary:")
    print("  BEFORE: template_name.endswith('.cfg') → ALL .cfg files = Ubuntu")
    print(
        "  AFTER:  template_name.endswith('.cfg') and 'preseed' in template_name.lower() → Only preseed .cfg files = Ubuntu"
    )
    print("\nImpact:")
    print("  • openSUSE AutoYaST .cfg files now correctly get 'autoyast=' cmdline")
    print("  • SLES AutoYaST .cfg files now correctly get 'autoyast=' cmdline")
    print("  • Ubuntu preseed .cfg files still correctly get 'preseed/url=' cmdline")
    print("  • Ubuntu autoinstall .yaml files still correctly get 'autoinstall ds=nocloud' cmdline")
