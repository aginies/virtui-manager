"""
Tests for OpenSUSE Agama Product Name Mapping

This test suite verifies that the OpenSUSE provider correctly maps
distribution versions to Agama product IDs and generates valid Agama
JSON templates with the correct product names.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider
from vmanager.provisioning.os_provider import OSType, OSVersion


class TestOpenSUSEAgamaProductMapping(unittest.TestCase):
    """Test the product name mapping for Agama templates"""

    def setUp(self):
        """Set up test fixtures"""
        self.provider = OpenSUSEProvider(host_arch="x86_64")

    def test_tumbleweed_product_name(self):
        """Test that Tumbleweed version maps to 'Tumbleweed' product"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="tumbleweed",
            display_name="openSUSE Tumbleweed",
            architecture="x86_64",
            is_evaluation=False,
        )
        product_name = self.provider._get_opensuse_product_name(version)
        self.assertEqual(product_name, "Tumbleweed")

    def test_slowroll_product_name(self):
        """Test that Slowroll version maps to 'Slowroll' product"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="slowroll",
            display_name="openSUSE Slowroll",
            architecture="x86_64",
            is_evaluation=False,
        )
        product_name = self.provider._get_opensuse_product_name(version)
        self.assertEqual(product_name, "Slowroll")

    def test_leap_16_product_name(self):
        """Test that Leap 16.x version maps to 'Leap 16.0' product"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="leap-16.0",
            display_name="openSUSE Leap 16.0",
            architecture="x86_64",
            is_evaluation=False,
        )
        product_name = self.provider._get_opensuse_product_name(version)
        self.assertEqual(product_name, "openSUSE_Leap")

    def test_microos_product_name(self):
        """Test that MicroOS version maps to 'openSUSE Micro OS' product"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="microos",
            display_name="openSUSE MicroOS",
            architecture="x86_64",
            is_evaluation=False,
        )
        product_name = self.provider._get_opensuse_product_name(version)
        self.assertEqual(product_name, "openSUSE Micro OS")

    def test_leap_15_fallback_to_tumbleweed(self):
        """Test that Leap 15.x maps to openSUSE_Leap"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="leap-15.6",
            display_name="openSUSE Leap 15.6",
            architecture="x86_64",
            is_evaluation=False,
        )
        product_name = self.provider._get_opensuse_product_name(version)
        self.assertEqual(product_name, "openSUSE_Leap")

    def test_unknown_version_fallback(self):
        """Test that unknown versions fall back to Tumbleweed with warning"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="unknown-distro",
            display_name="Unknown Distribution",
            architecture="x86_64",
            is_evaluation=False,
        )
        with self.assertLogs(level="WARNING") as log:
            product_name = self.provider._get_opensuse_product_name(version)
            self.assertEqual(product_name, "Tumbleweed")
            self.assertTrue(
                any(
                    "Unknown version" in message and "defaulting to Tumbleweed" in message
                    for message in log.output
                )
            )

    def test_case_insensitive_matching(self):
        """Test that version matching is case-insensitive"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="TUMBLEWEED",
            display_name="openSUSE Tumbleweed",
            architecture="x86_64",
            is_evaluation=False,
        )
        product_name = self.provider._get_opensuse_product_name(version)
        self.assertEqual(product_name, "Tumbleweed")


class TestOpenSUSEAgamaTemplateGeneration(unittest.TestCase):
    """Test Agama template generation with product names"""

    def setUp(self):
        """Set up test fixtures"""
        self.provider = OpenSUSEProvider(host_arch="x86_64")
        self.user_config = {
            "username": "testuser",
            "root_password": "testroot123",
            "user_password": "testpass123",
            "timezone": "UTC",
            "language": "en_US",
            "keyboard": "us",
        }

    def test_agama_basic_template_tumbleweed(self):
        """Test generating agama-basic.json with Tumbleweed product"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="tumbleweed",
            display_name="openSUSE Tumbleweed",
            architecture="x86_64",
            is_evaluation=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            result = self.provider.generate_automation_file(
                version=version,
                vm_name="test-vm",
                user_config=self.user_config,
                output_path=output_path,
                template_name="agama-basic.json",
            )

            # Verify file was created
            self.assertTrue(result.exists())

            # Read and parse the JSON
            with open(result, "r") as f:
                content = json.load(f)

            # Verify product ID is set correctly
            self.assertIn("product", content)
            self.assertIn("id", content["product"])
            self.assertEqual(content["product"]["id"], "Tumbleweed")

            # Verify no placeholder remains
            with open(result, "r") as f:
                raw_content = f.read()
            self.assertNotIn("OPENSUSE_PRODUCT_NAME", raw_content)

    def test_agama_template_slowroll(self):
        """Test generating Agama template with Slowroll product"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="slowroll",
            display_name="openSUSE Slowroll",
            architecture="x86_64",
            is_evaluation=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            result = self.provider.generate_automation_file(
                version=version,
                vm_name="test-slowroll",
                user_config=self.user_config,
                output_path=output_path,
                template_name="agama-server.json",
            )

            with open(result, "r") as f:
                content = json.load(f)

            self.assertEqual(content["product"]["id"], "Slowroll")

    def test_agama_template_leap_16(self):
        """Test generating Agama template with Leap 16.0 product"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="leap-16.0",
            display_name="openSUSE Leap 16.0",
            architecture="x86_64",
            is_evaluation=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            result = self.provider.generate_automation_file(
                version=version,
                vm_name="test-leap16",
                user_config=self.user_config,
                output_path=output_path,
                template_name="agama-desktop.json",
            )

            with open(result, "r") as f:
                content = json.load(f)

            self.assertEqual(content["product"]["id"], "openSUSE_Leap")

    def test_all_agama_templates_have_product(self):
        """Test that all Agama templates get the product ID set"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="tumbleweed",
            display_name="openSUSE Tumbleweed",
            architecture="x86_64",
            is_evaluation=False,
        )

        agama_templates = [
            "agama-basic.json",
            "agama-minimal.json",
            "agama-desktop.json",
            "agama-server.json",
            "agama-development.json",
        ]

        for template_name in agama_templates:
            with self.subTest(template=template_name):
                with tempfile.TemporaryDirectory() as tmpdir:
                    output_path = Path(tmpdir)
                    result = self.provider.generate_automation_file(
                        version=version,
                        vm_name="test-vm",
                        user_config=self.user_config,
                        output_path=output_path,
                        template_name=template_name,
                    )

                    with open(result, "r") as f:
                        content = json.load(f)

                    # Verify product is set
                    self.assertIn("product", content, f"No product in {template_name}")
                    self.assertIn("id", content["product"], f"No product.id in {template_name}")
                    self.assertEqual(
                        content["product"]["id"],
                        "Tumbleweed",
                        f"Wrong product ID in {template_name}",
                    )

                    # Verify no placeholder remains
                    with open(result, "r") as f:
                        raw_content = f.read()
                    self.assertNotIn(
                        "OPENSUSE_PRODUCT_NAME",
                        raw_content,
                        f"Placeholder not replaced in {template_name}",
                    )

    def test_agama_json_is_valid(self):
        """Test that generated Agama JSON is valid and parseable"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="tumbleweed",
            display_name="openSUSE Tumbleweed",
            architecture="x86_64",
            is_evaluation=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            result = self.provider.generate_automation_file(
                version=version,
                vm_name="test-vm",
                user_config=self.user_config,
                output_path=output_path,
                template_name="agama-basic.json",
            )

            # This should not raise any JSON parsing errors
            with open(result, "r") as f:
                content = json.load(f)

            # Verify it has expected structure
            self.assertIsInstance(content, dict)
            self.assertIn("hostname", content)
            self.assertIn("localization", content)
            self.assertIn("product", content)
            self.assertIn("software", content)
            self.assertIn("storage", content)

    def test_product_name_logging(self):
        """Test that product name setting is logged"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="slowroll",
            display_name="openSUSE Slowroll",
            architecture="x86_64",
            is_evaluation=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            with self.assertLogs(level="INFO") as log:
                self.provider.generate_automation_file(
                    version=version,
                    vm_name="test-vm",
                    user_config=self.user_config,
                    output_path=output_path,
                    template_name="agama-basic.json",
                )
                # Verify logging mentions the product ID
                self.assertTrue(
                    any("Set Agama product ID to: Slowroll" in message for message in log.output),
                    "Product ID setting was not logged",
                )

    def test_autoyast_template_unaffected(self):
        """Test that AutoYaST templates are not affected by product name logic"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="leap-15.6",
            display_name="openSUSE Leap 15.6",
            architecture="x86_64",
            is_evaluation=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            result = self.provider.generate_automation_file(
                version=version,
                vm_name="test-vm",
                user_config=self.user_config,
                output_path=output_path,
                template_name="autoyast-basic.xml",
            )

            # Verify it's XML, not JSON
            self.assertTrue(result.name.endswith(".xml"))

            # Read content and verify it's XML
            with open(result, "r") as f:
                content = f.read()

            self.assertTrue(content.strip().startswith("<?xml"))
            self.assertIn("<profile", content)
            # AutoYaST shouldn't have OPENSUSE_PRODUCT_NAME
            self.assertNotIn("OPENSUSE_PRODUCT_NAME", content)

    def test_manual_product_override(self):
        """Test that manually provided product name is not overridden"""
        version = OSVersion(
            os_type=OSType.LINUX,
            version_id="tumbleweed",
            display_name="openSUSE Tumbleweed",
            architecture="x86_64",
            is_evaluation=False,
        )

        # Provide manual product name in user_config
        config_with_product = self.user_config.copy()
        config_with_product["OPENSUSE_PRODUCT_NAME"] = "CustomProduct"

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            result = self.provider.generate_automation_file(
                version=version,
                vm_name="test-vm",
                user_config=config_with_product,
                output_path=output_path,
                template_name="agama-basic.json",
            )

            with open(result, "r") as f:
                content = json.load(f)

            # Manual override should be respected
            self.assertEqual(content["product"]["id"], "CustomProduct")


class TestOpenSUSEAgamaTemplateContent(unittest.TestCase):
    """Test that Agama templates have correct structure"""

    def setUp(self):
        """Set up test fixtures"""
        self.templates_dir = (
            Path(__file__).parent.parent / "src" / "vmanager" / "provisioning" / "templates"
        )

    def test_all_agama_templates_have_placeholder(self):
        """Verify all OpenSUSE Agama templates have the product placeholder"""
        agama_templates = [
            "agama-basic.json",
            "agama-minimal.json",
            "agama-desktop.json",
            "agama-server.json",
            "agama-development.json",
        ]

        for template_name in agama_templates:
            with self.subTest(template=template_name):
                template_path = self.templates_dir / template_name
                self.assertTrue(template_path.exists(), f"{template_name} not found")

                with open(template_path, "r") as f:
                    content = f.read()

                # Verify placeholder is present
                self.assertIn(
                    "{OPENSUSE_PRODUCT_NAME}",
                    content,
                    f"{template_name} missing product placeholder",
                )

                # Verify it's in the product section
                json_content = json.loads(content.replace("{OPENSUSE_PRODUCT_NAME}", "Tumbleweed"))
                self.assertIn("product", json_content)
                self.assertIn("id", json_content["product"])

    def test_sles_template_has_hardcoded_product(self):
        """Verify SLES template has hardcoded 'SLES' product, not placeholder"""
        sles_template = self.templates_dir / "agama-server-sles.json"

        if sles_template.exists():
            with open(sles_template, "r") as f:
                content = json.load(f)

            # SLES should have hardcoded "SLES" product
            self.assertIn("product", content)
            self.assertEqual(content["product"]["id"], "SLES")

            # And should not have the placeholder
            with open(sles_template, "r") as f:
                raw_content = f.read()
            self.assertNotIn("OPENSUSE_PRODUCT_NAME", raw_content)


if __name__ == "__main__":
    unittest.main()
