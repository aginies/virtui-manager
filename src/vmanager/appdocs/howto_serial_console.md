# Serial Console

The serial console is the text-based interface to a VM's serial port (`ttyS0`). It is the primary way to see boot logs, installer output, and kernel messages on VMs without a video display (e.g. Secure VMs), and it works for both local and remote hypervisors.

## How it works

There are two halves to a working serial console:

1. **Host side (always present).** Every VM managed by VirtUI Manager has a serial port backed by a libvirt pty (`<serial type='pty'>` + `<console type='pty'>` in the domain XML). libvirt exposes it as the console named **`serial0`** (this is the name used by `openConsole` / the viewer's Console tab). Nothing needs to be configured on the host.

2. **Guest side (must be configured).** The guest kernel only writes to the serial port if it is told to, via the kernel command line:

   ```
   console=tty0 console=ttyS0,115200
   ```

   Without this, the serial pty exists but stays silent.

## Enabling the serial console at install time

When installing a VM with automated installation, check the **"Redirect console to serial (ttyS0)"** option in the automation section. It does two things:

- Adds the `console=` parameters to the **installer's** kernel command line, so you can follow the installation on the serial console.
- For **openSUSE/SLES** installs, bakes the same parameters into the **installed system's bootloader** (AutoYaST `<bootloader><append>` or Agama `bootloader.extraKernelParams`). This matters because the installer's kernel command line is discarded when the installer reboots into the installed system — without the bootloader entry, the console would go silent after the first reboot.

The option is **on by default for Secure VMs**, which have no video output, but can be unchecked.

For existing VMs, the serial port can be added or removed from the VM details (Serial & Channels section); the guest still needs the `console=` kernel parameter, which for an installed system means editing its bootloader configuration (e.g. `/etc/default/grub` + `grub2-mkconfig` on SUSE).
