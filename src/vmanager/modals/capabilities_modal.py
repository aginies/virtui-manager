"""
Modal for displaying Host Domain Capabilities in a Tree View.
"""
import xml.etree.ElementTree as ET
from textual.app import ComposeResult
from textual.widgets import Tree, Button, Label
from textual.containers import Vertical, Horizontal, Container
from textual.widgets.tree import TreeNode

from .base_modals import BaseModal
from ..libvirt_utils import get_host_domain_capabilities

class CapabilitiesTreeModal(BaseModal[None]):
    """Modal to show host capabilities XML as a tree."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    def compose(self) -> ComposeResult:
        with Container(id="capabilities-dialog"):
            yield Label("Host Capabilities", id="dialog-title")
            yield Tree("Capabilities", id="xml-tree")
            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="close-btn")

    def on_mount(self) -> None:
        tree = self.query_one("#xml-tree", Tree)
        tree.show_root = False
        
        xml_content = get_host_domain_capabilities(self.conn)
        if not xml_content:
            tree.root.add("No capabilities found or error occurred.")
            return

        try:
            root = ET.fromstring(xml_content)
            # Add root node explicitly since we hide the main root
            root_node = tree.root.add(f"[b]{root.tag}[/b]", expand=True)
            self._add_xml_node(root_node, root)
        except ET.ParseError as e:
            tree.root.add(f"Error parsing XML: {e}")

    def _add_xml_node(self, tree_node: TreeNode, element: ET.Element):
        # Process attributes
        if element.attrib:
            for key, value in element.attrib.items():
                 tree_node.add(f"[i]@{key}[/i]: {value}", allow_expand=False)

        # Process children and text
        for child in element:
            # Construct label for child
            label = f"[b]{child.tag}[/b]"
            
            # If child has text and NO children, show text inline if short
            has_children = len(child) > 0
            text = child.text.strip() if child.text else ""
            
            if text and not has_children and len(text) < 50:
                label += f": {text}"
                child_node = tree_node.add(label, expand=False)
                # If attributes exist, add them
                for key, value in child.attrib.items():
                    child_node.add(f"[i]@{key}[/i]: {value}", allow_expand=False)
            else:
                child_node = tree_node.add(label, expand=False)
                if text:
                     child_node.add(text, allow_expand=False)
                self._add_xml_node(child_node, child)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss()
