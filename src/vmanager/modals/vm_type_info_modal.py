"""
Modal to show VM Type differences from DEFAULT_SETTINGS.md.
"""
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Button, Markdown
from textual import on
from modals.base_modals import BaseModal

VM_TYPE_INFO_TEXT = """
| [Storage Settings](https://www.qemu.org/docs/master/system/qemu-block-drivers.html) | Secure VM | Computation | Desktop |
| :--------------- | :---: | :---: | :---: |
| preallocation | metadata | off | metadata |
| encryption| on | off | off |
| disk_cache | writethrough | unsafe | none |
| lazy_refcounts| on | on | off |
| format | qcow2 | raw | qcow2 |
| disk bus | virtio | virtio | virtio |
| capacity | 8G | 8G | 8G |
| cluster_size | 1024k | NA | 1024k

| Guest Settings | Secure VM | Computation | Desktop |
| :------------- | :---: | :---: | :---: |
| [CPU migratable](https://libvirt.org/kbase/launch_security_sev.html) | off | off | on |
| machine | pc-q35 | pc-q35 | pc-q35 |
| [watchdog](https://libvirt.org/formatdomain.html#watchdog-devices) | none | i6300esb poweroff | none |
| [boot UEFI](https://libvirt.org/formatdomain.html#bios-bootloader) | auto | auto | auto |
| [vTPM](https://libvirt.org/formatdomain.html#tpm-device) | tpm-crb 2.0 | none | none |
| [iothreads](https://libvirt.org/formatdomain.html#iothreads-allocation) | disable | 4 | 4 |
| [video](https://libvirt.org/formatdomain.html#video-devices) | qxl | qxl | virtio |
| [network](https://libvirt.org/formatdomain.html#network-interfaces) | e1000 | virtio | e1000 |
| [keyboard](https://libvirt.org/formatdomain.html#input-devices) | ps2 (will be disable in the futur) | virtio | virtio |
| [memory backing](https://libvirt.org/formatdomain.html#memory-backing) | off | memfd/shared | memfd/shared |
| mouse | disable | virtio | virtio |
| [on_poweroff](https://libvirt.org/formatdomain.html#events-configuration) | destroy | restart | destroy |
| on_reboot | destroy | restart | restart |
| on_crash | destroy | restart | destroy |
| [suspend_to_mem](https://libvirt.org/formatdomain.html#power-management) | off | off | on |
| suspend_to_disk | off | off | on |
| [features](https://libvirt.org/formatdomain.html#hypervisor-features) | acpi apic pae | acpi apic pae | acpi apic pae
| [host fs](https://libvirt.org/formatdomain.html#filesystems) fmode, dmode, source_dir, target_dir | NA | NA | 644 755 /tmp/ /tmp/host |

| SEV | Secure VM | Computation | Desktop |
| :------------ | :---: | :---: | :---: |
| [kvm SEV](https://libvirt.org/kbase/launch_security_sev.html) | mem_encrypt=on kvm_amd sev=1 sev_es=1 | NA | NA |
| sec cbitpos | auto | NA | NA |
| sec reducedPhysBits | auto | NA | NA |
| sec policy | auto | NA | NA |
"""

class VMTypeInfoModal(BaseModal[None]):
    """A modal to display instructions for VM Types."""

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="howto-vmtype-dialog", classes="howto-dialog"):
            yield Markdown(VM_TYPE_INFO_TEXT, id="howto-vmtype-markdown")
            yield Button("Close", id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
