#!/usr/bin/env python3
"""
Test script for OpenSUSE provider integration
Tests that the multi-OS architecture works correctly for OpenSUSE.
"""

import sys
import os

# Add the src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def test_opensuse_provider_import():
    """Test that OpenSUSE provider can be imported correctly."""
    try:
        from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider

        print("✅ OpenSUSE provider import successful")
        return True
    except Exception as e:
        print(f"❌ OpenSUSE provider import failed: {e}")
        return False


def test_provider_registry():
    """Test that provider registry works correctly."""
    try:
        from vmanager.provisioning.provider_registry import ProviderRegistry
        from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider
        from vmanager.provisioning.os_provider import OSType

        registry = ProviderRegistry()
        provider = OpenSUSEProvider(host_arch="x86_64")
        registry.register_provider(provider)

        # Test retrieval
        retrieved_provider = registry.get_provider(OSType.LINUX)
        if retrieved_provider is None:
            print("❌ Provider registry: Failed to retrieve OpenSUSE provider")
            return False

        print("✅ Provider registry works correctly")
        return True
    except Exception as e:
        print(f"❌ Provider registry test failed: {e}")
        return False


def test_opensuse_provider_functionality():
    """Test basic OpenSUSE provider functionality."""
    try:
        from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider
        from vmanager.provisioning.os_provider import OSType

        provider = OpenSUSEProvider(host_arch="x86_64")

        # Test os_type property
        if provider.os_type != OSType.LINUX:
            print(f"❌ OpenSUSE provider os_type is {provider.os_type}, expected {OSType.LINUX}")
            return False

        # Test supported versions
        versions = provider.get_supported_versions()
        if not versions:
            print("❌ OpenSUSE provider returned no supported versions")
            return False

        print(f"✅ OpenSUSE provider supports {len(versions)} versions")
        for version in versions[:3]:  # Show first 3
            print(f"   - {version.display_name}")

        return True
    except Exception as e:
        print(f"❌ OpenSUSE provider functionality test failed: {e}")
        return False


def test_vmprovisioner_integration():
    """Test that VMProvisioner can work with OpenSUSE provider."""
    try:
        # This will fail due to libvirt not being available, but we can test the import and basic setup
        from vmanager.provisioning.provider_registry import get_registry
        from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider
        from vmanager.provisioning.os_provider import OSType

        # Register provider manually (since VMProvisioner needs libvirt)
        registry = get_registry()
        provider = OpenSUSEProvider(host_arch="x86_64")
        registry.register_provider(provider)

        # Test provider lookup
        retrieved = registry.get_provider(OSType.LINUX)
        if retrieved is None:
            print("❌ VMProvisioner integration: Failed to retrieve provider from global registry")
            return False

        print("✅ VMProvisioner integration test passed (provider registry)")
        return True
    except Exception as e:
        print(f"❌ VMProvisioner integration test failed: {e}")
        return False


def test_ostype_enum():
    """Test that OSType enum has all expected values."""
    try:
        from vmanager.provisioning.os_provider import OSType

        expected_types = ["LINUX", "OPENSUSE", "WINDOWS", "UBUNTU", "DEBIAN"]

        for expected in expected_types:
            if not hasattr(OSType, expected):
                print(f"❌ OSType enum missing expected value: {expected}")
                return False

        print(f"✅ OSType enum has all expected values: {[t.name for t in OSType]}")
        return True
    except Exception as e:
        print(f"❌ OSType enum test failed: {e}")
        return False


def run_all_tests():
    """Run all integration tests."""
    print("🧪 Testing OpenSUSE Provider Integration")
    print("=" * 50)

    tests = [
        ("OSType Enum", test_ostype_enum),
        ("OpenSUSE Provider Import", test_opensuse_provider_import),
        ("OpenSUSE Provider Functionality", test_opensuse_provider_functionality),
        ("Provider Registry", test_provider_registry),
        ("VMProvisioner Integration", test_vmprovisioner_integration),
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
        print("🎉 All tests passed! OpenSUSE provider integration is working correctly.")
        return True
    else:
        print("❌ Some tests failed. Check the output above for details.")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
