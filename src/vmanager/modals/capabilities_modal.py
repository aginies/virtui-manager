"""
Modal for displaying Host Domain Capabilities in a Tree View with Search.
"""
import xml.etree.ElementTree as ET
from textual import on
from textual.app import ComposeResult
from textual.widgets import Tree, Button, Label, Input
from textual.containers import Vertical, Horizontal, Container
from textual.widgets.tree import TreeNode

from .base_modals import BaseModal
from ..libvirt_utils import get_host_domain_capabilities
from ..constants import ButtonLabels, StaticText

class CapabilitiesTreeModal(BaseModal[None]):
    """Modal to show host capabilities XML as a tree."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.xml_root = None

    def compose(self) -> ComposeResult:
        with Container(id="capabilities-dialog"):
            yield Label(StaticText.HOST_CAPABILITIES, id="dialog-title")
            yield Input(placeholder="Search...", id="search-input")
            yield Tree(StaticText.CAPABILITIES_TREE_LABEL, id="xml-tree")
            with Horizontal(id="dialog-buttons"):
                yield Button(ButtonLabels.CLOSE, id="close-btn")

    def on_mount(self) -> None:
        tree = self.query_one("#xml-tree", Tree)
        tree.show_root = False
        self.query_one("#search-input").focus()

        xml_content = get_host_domain_capabilities(self.conn)
        if not xml_content:
            tree.root.add("No capabilities found or error occurred.")
            return

        try:
            self.xml_root = ET.fromstring(xml_content)
            self.update_tree("")
        except ET.ParseError as e:
            tree.root.add(f"Error parsing XML: {e}")

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.update_tree(event.value)

    def update_tree(self, filter_text: str) -> None:
        tree = self.query_one("#xml-tree", Tree)
        tree.clear()

        if self.xml_root is None:
            return

        # Add root manually
        self._add_node_recursive(tree.root, self.xml_root, filter_text.strip().lower())

        if not filter_text:
            # Expand the first level if no filter
            for node in tree.root.children:
                node.expand()

    def _matches(self, text: str | None, filter_text: str) -> bool:
        if not filter_text:
            return True
        return filter_text in (text or "").lower()

    def _add_node_recursive(self, parent_node: TreeNode, element: ET.Element, filter_text: str) -> bool:
        """
        Recursively adds nodes. Returns True if the node (or any descendant) matches the filter 
        and should be kept.
        """
        # 1. Determine if this specific element matches (tag, text, or any attribute)
        tag_match = self._matches(element.tag, filter_text)
        text_match = self._matches(element.text, filter_text) if element.text and element.text.strip() else False

        matching_attrs = []
        for k, v in element.attrib.items():
            if self._matches(k, filter_text) or self._matches(v, filter_text):
                matching_attrs.append((k, v))

        self_match = tag_match or text_match or (len(matching_attrs) > 0)

        # 2. Create the node optimistically
        label = f"[b]{element.tag}[/b]"

        # Show text inline if short and no children, OR if it matches
        has_xml_children = len(element) > 0
        text = element.text.strip() if element.text else ""
        
        if text and (not has_xml_children or len(text) < 50):
            label += f": {text}"

        # Expand if filtering, or default closed
        should_expand = bool(filter_text)
        node = parent_node.add(label, expand=should_expand)

        has_content = False

        # 3. Add attributes
        attrs_to_check = element.attrib.items()

        for k, v in attrs_to_check:
            is_attr_match = self._matches(k, filter_text) or self._matches(v, filter_text)
            if not filter_text or is_attr_match or self_match:
                # Highlight match if filter exists
                k_display = k
                v_display = v
                node.add(f"[i]@{k_display}[/i]: {v_display}", allow_expand=False)
                if is_attr_match and filter_text:
                    has_content = True

        if self_match and filter_text:
            has_content = True

        # 4. Recurse children
        for child in element:
            if self._add_node_recursive(node, child, filter_text):
                has_content = True

        # 5. Cleanup if nothing matched
        if filter_text and not has_content:
            node.remove()
            return False

        return True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss()
