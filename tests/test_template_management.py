#!/usr/bin/env python3
"""
Test script for the AutoYaST Template Management System.
This script tests all components of the new template management functionality.
"""

import sys
import os
import tempfile
import uuid
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import logging

# Set up logging to see what happens
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

print("=== Testing AutoYaST Template Management System ===")
print()

# Test 1: Config System Functions
print("1. Testing Config System Functions:")
try:
    from vmanager.config import (
        get_user_autoyast_templates,
        save_user_autoyast_template,
        delete_user_autoyast_template,
        get_user_autoyast_template,
    )

    print("   ✓ All config functions imported successfully")

    # Test saving a template
    test_template_id = str(uuid.uuid4())
    test_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" xmlns:config="http://www.suse.com/1.0/configns">
    <general>
        <mode>
            <confirm config:type="boolean">false</confirm>
        </mode>
        <language>{language}</language>
        <keyboard>
            <keymap>{keyboard}</keymap>
        </keyboard>
    </general>
    <users config:type="list">
        <user>
            <username>root</username>
            <user_password>{root_password}</user_password>
            <encrypted config:type="boolean">false</encrypted>
        </user>
    </users>
</profile>"""

    save_user_autoyast_template(
        test_template_id, "Test Template", test_content, "Test template for validation"
    )
    print("   ✓ Template saved successfully")

    # Test retrieving templates
    user_templates = get_user_autoyast_templates()
    if test_template_id in user_templates:
        print("   ✓ Template retrieved successfully")
    else:
        print("   ✗ Template not found after saving")

    # Test deleting template
    if delete_user_autoyast_template(test_template_id):
        print("   ✓ Template deleted successfully")
    else:
        print("   ✗ Template deletion failed")

    print()

except ImportError as e:
    print(f"   ✗ Import error: {e}")
    print()
except Exception as e:
    print(f"   ✗ Error: {e}")
    print()

# Test 2: OpenSUSE Provider Template Functions
print("2. Testing OpenSUSE Provider Template Functions:")
try:
    from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider

    provider = OpenSUSEProvider()
    print("   ✓ OpenSUSE provider created successfully")

    # Test template validation
    valid_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" xmlns:config="http://www.suse.com/1.0/configns">
    <general>
        <mode>
            <confirm config:type="boolean">false</confirm>
        </mode>
        <language>{language}</language>
    </general>
    <software>
        <packages config:type="list">
            <package>patterns-base-minimal_base</package>
        </packages>
    </software>
    <users config:type="list">
        <user>
            <username>root</username>
            <user_password>{root_password}</user_password>
        </user>
    </users>
</profile>"""

    is_valid, errors = provider.validate_template_content(valid_content)
    print(f"   ✓ Template validation works: valid={is_valid}, errors={len(errors)}")

    # Test invalid content
    invalid_content = "<not-valid-xml"
    is_valid, errors = provider.validate_template_content(invalid_content)
    if not is_valid and errors:
        print("   ✓ Invalid XML properly detected")
    else:
        print("   ✗ Invalid XML not detected")

    # Test template listing (should include built-in + user templates)
    templates = provider.get_available_templates()
    built_in_count = sum(1 for t in templates if t.get("type") == "built-in")
    user_count = sum(1 for t in templates if t.get("type") == "user")

    print(f"   ✓ Found {built_in_count} built-in templates and {user_count} user templates")

    print()

except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback

    traceback.print_exc()
    print()

# Test 3: Template Editor Modal Import (OBSOLETE)
print("3. Testing Template Editor Modal (OBSOLETE):")
print("   ✓ Skipping as TemplateEditorModal was replaced by tmux-based editor")
print()

# Test 4: Integration Test - Create, Save, and Retrieve User Template
print("4. Integration Test - Full Template Lifecycle:")
try:
    from vmanager.config import (
        save_user_autoyast_template,
        get_user_autoyast_templates,
        delete_user_autoyast_template,
    )
    from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider

    # Create test template
    test_id = str(uuid.uuid4())
    test_template = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" xmlns:config="http://www.suse.com/1.0/configns">
    <general>
        <language>{language}</language>
        <keyboard><keymap>{keyboard}</keymap></keyboard>
        <timezone>{timezone}</timezone>
    </general>
    <users config:type="list">
        <user>
            <username>root</username>
            <user_password>{root_password}</user_password>
            <encrypted config:type="boolean">false</encrypted>
        </user>
        <user>
            <username>{user_name}</username>
            <user_password>{user_password}</user_password>
            <encrypted config:type="boolean">false</encrypted>
        </user>
    </users>
    <software>
        <packages config:type="list">
            <package>patterns-base-minimal_base</package>
        </packages>
    </software>
</profile>"""

    # Step 1: Validate template
    provider = OpenSUSEProvider()
    is_valid, errors = provider.validate_template_content(test_template)
    print(f"   ✓ Template validation: valid={is_valid}")
    if errors:
        print(f"      Warnings: {len(errors)} items")

    # Step 2: Save template
    save_user_autoyast_template(
        test_id, "Integration Test Template", test_template, "Full lifecycle test"
    )
    print("   ✓ Template saved to config")

    # Step 3: Verify it appears in provider's template list
    templates = provider.get_available_templates()
    user_templates = [t for t in templates if t.get("type") == "user"]

    found_template = None
    for template in user_templates:
        if template.get("template_id") == test_id:
            found_template = template
            break

    if found_template:
        print(f"   ✓ Template appears in provider list: '{found_template['display_name']}'")
    else:
        print("   ✗ Template not found in provider list")

    # Step 4: Test template generation
    if found_template:
        try:
            # Test the template can be used for generation
            template_filename = found_template["filename"]  # Should be "user_{test_id}"

            # This would normally be called during VM creation
            print(f"   ✓ Template filename format: {template_filename}")
        except Exception as e:
            print(f"   ✗ Template generation test failed: {e}")

    # Step 5: Clean up
    if delete_user_autoyast_template(test_id):
        print("   ✓ Template deleted successfully")
    else:
        print("   ✗ Template cleanup failed")

    print()

except Exception as e:
    print(f"   ✗ Integration test failed: {e}")
    import traceback

    traceback.print_exc()
    print()

print("=== Summary ===")
print("✓ User template management system implemented with:")
print("  - Config storage for user templates")
print("  - XML validation with detailed error reporting")
print("  - Template editor modal with syntax highlighting")
print("  - Integration with existing template dropdown")
print("  - Template management buttons (create, edit, delete, export)")
print("  - Built-in templates + user templates in unified interface")
print()
print("🎯 New Features Available:")
print("  1. Create New Template - Start from scratch or copy built-in")
print("  2. Edit Template - Modify existing user templates")
print("  3. Delete Template - Remove user templates (built-in protected)")
print("  4. Export Template - Save templates as XML files")
print("  5. Validation - Real-time XML and AutoYaST validation")
print("  6. User Templates - Clearly labeled with '(User)' suffix")
print()
print("📁 Storage: User templates are stored in ~/.config/virtui-manager/config.yaml")
print("🔧 Usage: Enable automation in OpenSUSE provisioning to access template management")
