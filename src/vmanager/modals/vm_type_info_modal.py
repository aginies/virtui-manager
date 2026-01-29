"""
Modal to show VM Type differences from DEFAULT_SETTINGS.md.
"""
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Button, Markdown
from textual import on
from .base_modals import BaseModal
from ..constants import ButtonLabels

VM_TYPE_INFO_TEXT = """
| [Storage Settings](https://www.qemu.org/docs/master/system/qemu-block-drivers.html) | Secure VM | Computation | Desktop (Linux) | Windows | Win Legacy | Server |
| :--------------- | :---: | :---: | :---: | :---: | :---: | :---: |
| preallocation | metadata | off | metadata | metadata | metadata | off |
| encryption| on | off | off | off | off | off |
| disk_cache | writethrough | unsafe | none | none | none | none |
| lazy_refcounts| on | on | off | off | off | off |
| format | qcow2 | raw | qcow2 | qcow2 | qcow2 | qcow2 |
| disk bus | virtio | virtio | virtio | sata | sata | virtio |
| capacity | 8G | 8G | 30G | 40G | 30G | 18G |
| cluster_size | 1024k | NA | 1024k | 1024k | 1024k | NA

| Guest Settings | Secure VM | Computation | Desktop (Linux) | Windows | Win Legacy | Server |
| :------------- | :---: | :---: | :---: | :---: | :---: | :---: |
| [CPU migratable](https://libvirt.org/kbase/launch_security_sev.html) | off | off | on | on | on | on |
| machine | pc-q35 | pc-q35 | pc-q35 | pc-q35 | pc-i440fx | pc-q35 |
| [watchdog](https://libvirt.org/formatdomain.html#watchdog-devices) | none | i6300esb poweroff | none | none | none | none |
| [boot UEFI](https://libvirt.org/formatdomain.html#bios-bootloader) | auto | auto | auto | auto | auto | auto |
| [vTPM](https://libvirt.org/formatdomain.html#tpm-device) | tpm-crb 2.0 | none | none | tpm-crb 2.0 | none | none |
| [iothreads](https://libvirt.org/formatdomain.html#iothreads-allocation) | disable | 4 | disable | disable | disable | disable |
| [video](https://libvirt.org/formatdomain.html#video-devices) | qxl | qxl | virtio | virtio | virtio | virtio |
| [sound](https://libvirt.org/formatdomain.html#sound-devices) | none | none | ich9 | ich9 | ich9 | none |
| [network](https://libvirt.org/formatdomain.html#network-interfaces) | e1000 | virtio | e1000 | e1000 | e1000 | virtio |
| [keyboard](https://libvirt.org/formatdomain.html#input-devices) | ps2 | virtio | virtio | virtio | usb | virtio |
| [memory backing](https://libvirt.org/formatdomain.html#memory-backing) | off | memfd/shared | memfd/shared | memfd/shared | memfd/shared | off |
| mouse | ps2 | virtio | virtio | virtio | usb | virtio |
| [on_poweroff](https://libvirt.org/formatdomain.html#events-configuration) | destroy | restart | destroy | destroy | destroy | destroy |
| on_reboot | destroy | restart | restart | restart | restart | restart |
| on_crash | destroy | restart | destroy | destroy | destroy | restart |
| [suspend_to_mem](https://libvirt.org/formatdomain.html#power-management) | off | off | on | on | on | on |
| suspend_to_disk | off | off | on | on | on | on |
| [features](https://libvirt.org/formatdomain.html#hypervisor-features) | acpi apic pae | acpi apic pae | acpi apic pae | acpi apic pae | acpi apic pae | acpi apic pae |
| [host fs](https://libvirt.org/formatdomain.html#filesystems) fmode, dmode, source_dir, target_dir | NA | NA | 644 755 /tmp/ /tmp/host | NA | NA | NA |

| SEV | Secure VM | Computation | Desktop (Linux) | Windows | Win Legacy | Server |
| :------------ | :---: | :---: | :---: | :---: | :---: | :---: |
| [kvm SEV](https://libvirt.org/kbase/launch_security_sev.html) | mem_encrypt=on kvm_amd sev=1 sev_es=1 | NA | NA | NA | NA | NA |
| sec cbitpos | auto | NA | NA | NA | NA | auto |
| sec reducedPhysBits | auto | NA | NA | NA | NA | auto |
| sec policy | auto | NA | NA | NA | NA | auto |
"""

class VMTypeInfoModal(BaseModal[None]):
    """A modal to display instructions for VM Types."""

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="howto-vmtype-dialog", classes="howto-dialog"):
            yield Markdown(VM_TYPE_INFO_TEXT, id="howto-vmtype-markdown")
            yield Button(ButtonLabels.CLOSE, id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
