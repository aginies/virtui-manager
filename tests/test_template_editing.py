#!/usr/bin/env python3
"""
Test template editing functionality for VirtUI Manager
"""

import unittest
import os
import sys
import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.provisioning.automation_engine import AutomationEngine  # noqa: E402
from vmanager.provisioning.os_provider import OSType  # noqa: E402

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "vmanager" / "provisioning" / "templates"
SERIAL_CONSOLE_PARAMS = "console=tty0 console=ttyS0,115200"


class TestSerialConsoleTemplateInjection(unittest.TestCase):
    """Tests for baking the serial console into the installed system's bootloader."""

    def setUp(self):
        self.engine = AutomationEngine()

    def _read_template(self, name):
        with open(TEMPLATES_DIR / name, encoding="utf-8") as f:
            return f.read()

    # --- AutoYaST ---

    def test_autoyast_with_existing_append(self):
        """Template with <append>: console params are added, existing kept."""
        content = self._read_template("autoyast-server.xml")
        self.assertIn("<append>", content)
        result = self.engine._apply_autoyast_serial_console(content)
        self.assertIn(SERIAL_CONSOLE_PARAMS, result)
        # Existing params must be preserved
        self.assertIn("quiet splash=silent showopts", result)
        # Params must sit inside the <append> element
        self.assertIn(f"<append>{SERIAL_CONSOLE_PARAMS} quiet splash=silent showopts</append>", result)
        # Still valid XML
        ET.fromstring(result)

    def test_autoyast_without_append(self):
        """Template without <append>: one is created inside <bootloader><global>."""
        content = self._read_template("autoyast-basic.xml")
        self.assertNotIn("<append>", content)
        result = self.engine._apply_autoyast_serial_console(content)
        self.assertIn(f"<append>{SERIAL_CONSOLE_PARAMS}</append>", result)
        # Must be placed after <bootloader><global>
        self.assertGreater(result.index("<append>"), result.index("<global>"))
        self.assertLess(result.index("<append>"), result.index("</global>"))
        # Still valid XML
        ET.fromstring(result)

    def test_autoyast_without_bootloader(self):
        """Template without <bootloader>: no-op with warning, content unchanged."""
        content = self._read_template("autoyast-skeleton.xml")
        self.assertNotIn("<bootloader>", content)
        result = self.engine._apply_autoyast_serial_console(content)
        self.assertEqual(result, content)

    def test_autoyast_idempotent(self):
        """Applying twice must not duplicate the console params."""
        for template in ("autoyast-server.xml", "autoyast-basic.xml"):
            content = self._read_template(template)
            once = self.engine._apply_autoyast_serial_console(content)
            twice = self.engine._apply_autoyast_serial_console(once)
            self.assertEqual(twice, once, f"not idempotent for {template}")
            self.assertEqual(twice.count("console=ttyS0"), 1)

    # --- Agama ---

    def test_agama_serial_console(self):
        """Agama JSON: extraKernelParams extended, existing params kept."""
        content = self._read_template("agama-basic.json")
        result = self.engine._apply_agama_serial_console(content)
        data = json.loads(result)
        extra = data["bootloader"]["extraKernelParams"]
        self.assertIn(SERIAL_CONSOLE_PARAMS, extra)
        self.assertIn("quiet splash=silent showopts", extra)

    def test_agama_serial_console_keeps_other_keys(self):
        """The JSON round-trip must not drop other bootloader keys."""
        content = self._read_template("agama-basic.json")
        before = json.loads(content)
        after = json.loads(self.engine._apply_agama_serial_console(content))
        self.assertEqual(after["bootloader"].get("timeout"), before["bootloader"]["timeout"])

    def test_agama_idempotent(self):
        """Applying twice must not duplicate the console params."""
        content = self._read_template("agama-basic.json")
        once = self.engine._apply_agama_serial_console(content)
        twice = self.engine._apply_agama_serial_console(once)
        self.assertEqual(twice, once)
        self.assertEqual(twice.count("console=ttyS0"), 1)

    # --- Full engine path (_generate_opensuse) ---

    def _generate(self, template_name, serial_console):
        user_config = {
            "hostname": "testvm",
            "language": "English (US)",
            "keyboard": "us",
            "timezone": "UTC",
            "root_password": "rootpw",
            "username": "testuser",
            "user_password": "userpw",
            "serial_console": serial_console,
        }
        with tempfile.TemporaryDirectory() as tmp:
            out_path = self.engine.generate(
                os_type=OSType.OPENSUSE,
                version_id="15.6",
                vm_name="testvm",
                user_config=user_config,
                output_path=Path(tmp),
                template_name=template_name,
            )
            with open(out_path, encoding="utf-8") as f:
                return f.read()

    def test_generate_autoyast_flag_on(self):
        content = self._generate("autoyast-basic.xml", True)
        self.assertIn(SERIAL_CONSOLE_PARAMS, content)
        ET.fromstring(content)

    def test_generate_autoyast_flag_off(self):
        content = self._generate("autoyast-basic.xml", False)
        self.assertNotIn("console=ttyS0", content)
        ET.fromstring(content)

    def test_generate_agama_flag_on(self):
        content = self._generate("agama-basic.json", True)
        data = json.loads(content)
        self.assertIn(SERIAL_CONSOLE_PARAMS, data["bootloader"]["extraKernelParams"])

    def test_generate_agama_flag_off(self):
        content = self._generate("agama-basic.json", False)
        self.assertNotIn("console=ttyS0", content)
        json.loads(content)

    def test_generate_agama_flag_absent(self):
        """No serial_console key at all: no console params injected."""
        user_config = {
            "hostname": "testvm",
            "language": "English (US)",
            "keyboard": "us",
            "timezone": "UTC",
            "root_password": "rootpw",
            "username": "testuser",
            "user_password": "userpw",
        }
        with tempfile.TemporaryDirectory() as tmp:
            out_path = self.engine.generate(
                os_type=OSType.OPENSUSE,
                version_id="15.6",
                vm_name="testvm",
                user_config=user_config,
                output_path=Path(tmp),
                template_name="agama-basic.json",
            )
            with open(out_path, encoding="utf-8") as f:
                content = f.read()
        self.assertNotIn("console=ttyS0", content)


class TestTemplateEditing(unittest.TestCase):
    """Test cases for template editing functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.template_content = """<?xml version="1.0"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" 
         xmlns:config="http://www.suse.com/1.0/configns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
    </mode>
  </general>

  <software>
    <packages config:type="list">
      <package>openssh</package>
    </packages>
    <patterns config:type="list">
      <pattern>base</pattern>
    </patterns>
  </software>

  <users config:type="list">
    <user>
      <username>root</username>
      <user_password>{{ROOT_PASSWORD}}</user_password>
      <encrypted config:type="boolean">false</encrypted>
    </user>
    <user>
      <username>{{USER_NAME}}</username>
      <user_password>{{USER_PASSWORD}}</user_password>
      <encrypted config:type="boolean">false</encrypted>
    </user>
  </users>
</profile>"""

    def test_template_editing_workflow(self):
        """Test the template editing workflow"""

        # Create temporary file (simulating what our editor function does)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        ) as tmp_file:
            tmp_file.write(self.template_content)
            tmp_file_path = tmp_file.name

        try:
            # Verify file was created correctly
            with open(tmp_file_path, "r", encoding="utf-8") as f:
                read_content = f.read()

            self.assertEqual(read_content, self.template_content, "Template content should match")

            # Get editor from environment (just like our real function)
            editor = os.environ.get("EDITOR", "vi")
            self.assertIsNotNone(editor, "Editor should be detected")

            # Simulate editor command that would be run
            editor_cmd = [editor, tmp_file_path]
            self.assertGreater(len(editor_cmd), 0, "Editor command should be formed")
            self.assertEqual(editor_cmd[0], editor, "First argument should be editor")
            self.assertEqual(editor_cmd[1], tmp_file_path, "Second argument should be file path")

        finally:
            # Clean up temporary file (just like our real function)
            os.unlink(tmp_file_path)

    def test_template_validation_valid(self):
        """Test template validation with valid XML"""
        valid_template = """<?xml version="1.0"?>
<profile xmlns="http://www.suse.com/1.0/yast2ns" xmlns:config="http://www.suse.com/1.0/configns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
    </mode>
  </general>
</profile>"""

        # Should not raise an exception
        try:
            ET.fromstring(valid_template)
        except ET.ParseError:
            self.fail("Valid template should parse successfully")

    def test_template_validation_invalid(self):
        """Test template validation with invalid XML"""
        invalid_template = """<?xml version="1.0"?>
<profile xmlns="http://www.suse.com/1.0/yast2ns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
    </mode>
  </general>
  <!-- Missing closing tag -->
"""

        # Should raise a ParseError
        with self.assertRaises(ET.ParseError, msg="Invalid template should raise ParseError"):
            ET.fromstring(invalid_template)

    def test_template_has_required_elements(self):
        """Test that template contains required AutoYaST elements"""
        root = ET.fromstring(self.template_content)

        # Check namespace
        self.assertIn(
            "{http://www.suse.com/1.0/yast2ns}", root.tag, "Should have AutoYaST namespace"
        )

        # Check for required sections
        general = root.find(".//{http://www.suse.com/1.0/yast2ns}general")
        self.assertIsNotNone(general, "Should have general section")

        software = root.find(".//{http://www.suse.com/1.0/yast2ns}software")
        self.assertIsNotNone(software, "Should have software section")

        users = root.find(".//{http://www.suse.com/1.0/yast2ns}users")
        self.assertIsNotNone(users, "Should have users section")

    def test_template_variable_substitution(self):
        """Test that template contains variable placeholders"""
        self.assertIn(
            "{{ROOT_PASSWORD}}", self.template_content, "Should contain root password variable"
        )
        self.assertIn("{{USER_NAME}}", self.template_content, "Should contain username variable")
        self.assertIn(
            "{{USER_PASSWORD}}", self.template_content, "Should contain user password variable"
        )


# Legacy functions for backward compatibility and direct script execution
def test_template_editing():
    """Legacy function - Test the template editing workflow"""

    # Simulate the template content
    template_content = """<?xml version="1.0"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" 
         xmlns:config="http://www.suse.com/1.0/configns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
    </mode>
  </general>

  <software>
    <packages config:type="list">
      <package>openssh</package>
    </packages>
    <patterns config:type="list">
      <pattern>base</pattern>
    </patterns>
  </software>

  <users config:type="list">
    <user>
      <username>root</username>
      <user_password>{{ROOT_PASSWORD}}</user_password>
      <encrypted config:type="boolean">false</encrypted>
    </user>
    <user>
      <username>{{USER_NAME}}</username>
      <user_password>{{USER_PASSWORD}}</user_password>
      <encrypted config:type="boolean">false</encrypted>
    </user>
  </users>
</profile>"""

    print("Testing template editing workflow...")

    # Create temporary file (simulating what our editor function does)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", delete=False, encoding="utf-8"
    ) as tmp_file:
        tmp_file.write(template_content)
        tmp_file_path = tmp_file.name

    try:
        print(f"Created temporary file: {tmp_file_path}")

        # Verify file was created correctly
        with open(tmp_file_path, "r", encoding="utf-8") as f:
            read_content = f.read()

        if read_content == template_content:
            print("✓ Template content written correctly")
        else:
            print("✗ Template content mismatch")
            return False

        # Get editor from environment (just like our real function)
        editor = os.environ.get("EDITOR", "vi")
        print(f"✓ Editor detected: {editor}")

        # Simulate editor command that would be run
        editor_cmd = [editor, tmp_file_path]
        print(f"✓ Editor command would be: {' '.join(editor_cmd)}")

        # In the real implementation, we would run:
        # subprocess.run(editor_cmd, check=True)
        # But for testing, we'll just verify the setup

        print("✓ Template editing workflow validated")
        return True

    finally:
        # Clean up temporary file (just like our real function)
        os.unlink(tmp_file_path)
        print("✓ Temporary file cleaned up")


def test_template_validation():
    """Legacy function - Test basic XML template validation"""
    valid_template = """<?xml version="1.0"?>
<profile xmlns="http://www.suse.com/1.0/yast2ns" xmlns:config="http://www.suse.com/1.0/configns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
    </mode>
  </general>
</profile>"""

    invalid_template = """<?xml version="1.0"?>
<profile xmlns="http://www.suse.com/1.0/yast2ns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
    </mode>
  </general>
  <!-- Missing closing tag -->
"""

    print("\nTesting template validation...")

    # Test basic XML parsing
    try:
        # Test valid template
        ET.fromstring(valid_template)
        print("✓ Valid template parsed successfully")

        # Test invalid template
        try:
            ET.fromstring(invalid_template)
            print("✗ Invalid template should have failed parsing")
            return False
        except ET.ParseError:
            print("✓ Invalid template correctly rejected")

        return True

    except ImportError:
        print("xml.etree.ElementTree not available - skipping validation test")
        return True


if __name__ == "__main__":
    import sys

    # Check if running with unittest
    if len(sys.argv) > 1 and "unittest" in sys.argv[0]:
        unittest.main(verbosity=2)
    else:
        # Legacy direct execution for backward compatibility
        print("VirtUI Manager Template Editing Test")
        print("====================================")

        success = True

        # Test template editing workflow
        success &= test_template_editing()

        # Test template validation
        success &= test_template_validation()

        if success:
            print("\n✓ All tests passed! Template editing refactoring is working.")
            print(
                "\nTo run with unittest framework: python -m unittest tests.test_template_editing -v"
            )
        else:
            print("\n✗ Some tests failed.")
            exit(1)
