"""
Modal to show how to configure networks.
"""
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Button, Markdown
from textual import on
from modals.base_modals import BaseModal

HOW_TO_NETWORK_TEXT = """
# Understanding Network Configuration in libvirt

libvirt provides flexible networking capabilities for virtual machines, allowing them to communicate with each other, the host, and external networks.

### Types of Networks

1.  **NAT (Network Address Translation) Network:**
    *   **Purpose:** Allows VMs to access the external network (internet) but prevents external machines from directly initiating connections to the VMs.
    *   **Mechanism:** libvirt creates a virtual bridge (e.g., `virbr0`) on the host, and VMs connect to this bridge. The host acts as a router, performing NAT for outgoing connections from VMs. VMs get IP addresses from a DHCP server managed by libvirt.
    *   **Use Cases:** Most common setup for general VM usage where VMs just need internet access.

2.  **Routed Network:**
    *   **Purpose:** VMs can communicate with other machines on the host's physical network, and potentially external networks, with proper routing configured on the host and potentially external routers. VMs will have IP addresses on the same subnet as the host's physical network, or a dedicated routed subnet.
    *   **Mechanism:** Similar to NAT, a virtual bridge is used, but without NAT. The host needs to be configured to route traffic between the virtual bridge and the physical interface. VMs typically get IP addresses from a DHCP server on the physical network or static IPs.
    *   **Use Cases:** When VMs need to be directly accessible from the physical network, or participate as full members of an existing network.

3.  **Isolated Network:**
    *   **Purpose:** VMs on this network can only communicate with each other and the host, but not with external networks.
    *   **Mechanism:** A virtual bridge is created, but no routing or NAT is configured to connect it to physical interfaces.
    *   **Use Cases:** Testing environments, isolated services, or when you need a private network segment for VMs.

### Key Concepts

*   **Bridge:** A software-based network device that connects multiple network segments at the data link layer. VMs connect to a virtual bridge, which then connects to a physical interface (for NAT/routed) or remains isolated.
*   **DHCP:** Dynamic Host Configuration Protocol. Automatically assigns IP addresses to VMs within a network.
*   **Forward Device:** The physical network interface on the host through which the virtual network's traffic is forwarded (for NAT or routed modes).
*   **MAC Address:** Media Access Control address. A unique identifier assigned to network interfaces. For KVM/QEMU, MAC addresses often start with `52:54:00:`.

### Common Tasks

*   **Create/Edit Network:** Define network parameters like name, IP range, DHCP settings, and forward mode.
*   **Activate/Deactivate Network:** Start or stop a virtual network.
*   **Autostart Network:** Configure a network to start automatically when the libvirt daemon starts.
*   **View XML:** Examine the underlying libvirt XML definition of a network, which provides full details of its configuration.
"""

class HowToNetworkModal(BaseModal[None]):
    """A modal to display instructions for network configuration."""

    def compose(self) -> ComposeResult:
        with Vertical(id="howto-network-dialog"):
            with ScrollableContainer(id="howto-network-content"):
                yield Markdown(HOW_TO_NETWORK_TEXT, id="howto-network-markdown")
        with Horizontal(id="dialog-buttons"):
            yield Button("Close", id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
