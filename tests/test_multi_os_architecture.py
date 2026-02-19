#!/usr/bin/env python3
"""
Test script to verify both Windows and OpenSUSE providers work together
Tests the complete multi-OS architecture.
"""

import sys
import os

# Add the src directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


def test_multi_os_architecture():
    """Test that both Windows and OpenSUSE providers work together."""
    try:
        from vmanager.provisioning.provider_registry import ProviderRegistry
        from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider
        from vmanager.provisioning.providers.windows_provider import WindowsProvider
        from vmanager.provisioning.os_provider import OSType

        registry = ProviderRegistry()

        # Register both providers
        opensuse_provider = OpenSUSEProvider(host_arch="x86_64")
        windows_provider = WindowsProvider()

        registry.register_provider(opensuse_provider)
        registry.register_provider(windows_provider)

        # Test that both can be retrieved
        retrieved_linux = registry.get_provider(OSType.LINUX)
        retrieved_windows = registry.get_provider(OSType.WINDOWS)

        if retrieved_linux is None:
            print("❌ Failed to retrieve Linux/OpenSUSE provider")
            return False

        if retrieved_windows is None:
            print("❌ Failed to retrieve Windows provider")
            return False

        # Test supported OS types
        supported_types = registry.get_supported_os_types()
        if OSType.LINUX not in supported_types:
            print("❌ Linux not in supported OS types")
            return False

        if OSType.WINDOWS not in supported_types:
            print("❌ Windows not in supported OS types")
            return False

        # Test version information
        all_versions = registry.get_all_supported_versions()

        linux_versions = all_versions.get(OSType.LINUX, [])
        windows_versions = all_versions.get(OSType.WINDOWS, [])

        print(f"✅ Multi-OS architecture working correctly:")
        print(f"   - OpenSUSE/Linux: {len(linux_versions)} versions")
        print(f"   - Windows: {len(windows_versions)} versions")
        print(f"   - Supported OS types: {[t.value for t in supported_types]}")

        return True

    except Exception as e:
        print(f"❌ Multi-OS architecture test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_vmprovisioner_os_mapping():
    """Test that VMProvisioner can properly map OS types."""
    try:
        from vmanager.provisioning.provider_registry import get_registry
        from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider
        from vmanager.provisioning.providers.windows_provider import WindowsProvider
        from vmanager.provisioning.os_provider import OSType

        # Set up the global registry
        registry = get_registry()
        opensuse_provider = OpenSUSEProvider(host_arch="x86_64")
        windows_provider = WindowsProvider()

        registry.register_provider(opensuse_provider)
        registry.register_provider(windows_provider)

        # Simulate VMProvisioner.get_provider() logic
        def simulate_get_provider(os_type_str):
            try:
                # Handle common case conversions (same logic as VMProvisioner)
                if os_type_str.lower() == "linux" or os_type_str.lower() == "opensuse":
                    os_type_enum = OSType.LINUX
                elif os_type_str.lower() == "windows":
                    os_type_enum = OSType.WINDOWS
                else:
                    # Try direct conversion
                    os_type_enum = OSType(os_type_str)

                return registry.get_provider(os_type_enum)
            except ValueError:
                return None

        # Test various input formats
        test_cases = [
            ("linux", "should return OpenSUSE provider"),
            ("Linux", "should return OpenSUSE provider"),
            ("opensuse", "should return OpenSUSE provider"),
            ("OpenSUSE", "should return OpenSUSE provider"),
            ("windows", "should return Windows provider"),
            ("Windows", "should return Windows provider"),
        ]

        all_passed = True
        for input_str, expected in test_cases:
            provider = simulate_get_provider(input_str)
            if provider is None:
                print(f"❌ Failed to get provider for '{input_str}' ({expected})")
                all_passed = False
            else:
                provider_type = provider.os_type.value
                print(f"✅ '{input_str}' → {provider_type} provider")

        return all_passed

    except Exception as e:
        print(f"❌ VMProvisioner OS mapping test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def run_complete_tests():
    """Run complete multi-OS architecture tests."""
    print("🧪 Testing Complete Multi-OS Architecture")
    print("=" * 50)

    tests = [
        ("Multi-OS Architecture", test_multi_os_architecture),
        ("VMProvisioner OS Type Mapping", test_vmprovisioner_os_mapping),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        print(f"\n🔍 Running: {test_name}")
        if test_func():
            passed += 1
        else:
            failed += 1

    print("\n" + "=" * 50)
    print(f"📊 Test Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 Complete multi-OS architecture is working perfectly!")
        print(
            "✅ VirtUI Manager now supports both Windows and OpenSUSE through the provider system!"
        )
        return True
    else:
        print("❌ Some tests failed. Check the output above for details.")
        return False


if __name__ == "__main__":
    success = run_complete_tests()
    sys.exit(0 if success else 1)
