"""
Modals for VM Provisioning (Installation).
"""
import logging
import subprocess

import os
from pathlib import Path
from textual.widgets import Input, Select, Button, Label, ProgressBar, Checkbox, Collapsible
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual import on, work

import libvirt
from config import load_config
from constants import AppInfo
from vm_provisioner import VMProvisioner, VMType, OpenSUSEDistro
from storage_manager import list_storage_pools
from vm_service import VMService
from utils import remote_viewer_cmd
from modals.base_modals import BaseModal
from modals.utils_modals import FileSelectionModal
from modals.vm_type_info_modal import VMTypeInfoModal
from modals.input_modals import _sanitize_domain_name

class InstallVMModal(BaseModal[str | None]):
    """
    Modal for creating and provisioning a new OpenSUSE VM.
    """

    def __init__(self, vm_service: VMService, uri: str):
        super().__init__()
        self.vm_service = vm_service
        self.uri = uri
        self.conn = self.vm_service.connect(uri)
        self.provisioner = VMProvisioner(self.conn)
        self.iso_list = []

    def compose(self):
        # Get Pools
        pools = list_storage_pools(self.conn)
        active_pools = [(p['name'], p['name']) for p in pools if p['status'] == 'active']
        default_pool = 'default' if any(p[0] == 'default' for p in active_pools) else (active_pools[0][1] if active_pools else None)

        with Vertical(id="install-dialog"):
            yield Label(f"Install OpenSUSE VM on {self.uri}", classes="title")

            yield Label("VM Name:", classes="label")
            yield Input(placeholder="my-new-vm", id="vm-name")

            yield Label("VM Type:", classes="label")
            with Horizontal(classes="label-row"):
                yield Select([(t.value, t) for t in VMType], value=VMType.DESKTOP, id="vm-type", allow_blank=False)
                yield Button("Info", id="vm-type-info-btn", variant="primary")

            yield Label("Distribution:", classes="label")
            distro_options = [(d.value, d) for d in OpenSUSEDistro]
            distro_options.insert(0, ("Cached ISOs", "cached"))
            custom_repos = self.provisioner.get_custom_repos()
            for repo in custom_repos:
                # Use URI as value, Name as label
                name = repo.get('name', repo['uri'])
                uri = repo['uri']
                # Insert before CUSTOM option (last one usually)
                distro_options.insert(-1, (name, uri))

            yield Select(distro_options, value=OpenSUSEDistro.LEAP, id="distro", allow_blank=False)

            # Container for ISO selection (Repo)
            with Vertical(id="repo-iso-container"):
                yield Label("ISO Image (Repo):", classes="label")
                # Show download path
                config = load_config()
                iso_path = Path(config.get('ISO_DOWNLOAD_PATH', str(Path.home() / ".cache" / AppInfo.name / "isos")))
                yield Label(f"ISOs will be downloaded to: {iso_path}", classes="info-text", id="iso-path-label")
                yield Select([], prompt="Select ISO...", id="iso-select", disabled=True)

            # Container for Custom ISO
            with Vertical(id="custom-iso-container"):
                yield Label("Custom ISO (Local Path):", classes="label")
                with Horizontal(classes="input-row"):
                    yield Input(placeholder="/path/to/local.iso", id="custom-iso-path", classes="path-input")
                    yield Button("Browse", id="browse-iso-btn")

                with Vertical(id="checksum-container"):
                    yield Checkbox("Validate Checksum", id="validate-checksum", value=False)
                    yield Input(placeholder="SHA256 Checksum (Optional)", id="checksum-input", disabled=True)
                    yield Label("", id="checksum-status", classes="status-text")

            # Container for Autoinstall (Disabled for now)
            #with Vertical(id="autoinstall-container"):
            #    yield Label("Autoinstall File (Optional):", disabled=True)
            #    with Horizontal(classes="input-row"):
            #        yield Input(placeholder="/path/to/autoinstall.xml", id="autoinstall-path", classes="path-input", disabled=True)
            #        yield Button("Browse", id="browse-autoinstall-btn", disabled=True)

            with Collapsible(title="Expert Mode", id="expert-mode-collapsible"):
                with Horizontal(id="expert-mode"):
                    with Vertical(id="expert-mem"):
                        yield Label(" Memory (MB)", classes="label")
                        yield Input("4096", id="memory-input", type="integer")
                    with Vertical(id="expert-cpu"):
                        yield Label(" CPUs", classes="label")
                        yield Input("2", id="cpu-input", type="integer")
                    with Vertical(id="expert-disk-size"):
                        yield Label(" Disk Size(GB)", classes="label")
                        yield Input("8", id="disk-size-input", type="integer")
                    with Vertical(id="expert-disk-format"):
                        yield Label(" Disk Format", classes="label")
                        yield Select([("Qcow2", "qcow2"), ("Raw", "raw")], value="qcow2", id="disk-format")
                    with Vertical(id="expert-firmware"):
                        yield Label(" Firmware", classes="label")
                        yield Checkbox("UEFI", id="boot-uefi-checkbox", value=True, tooltip="Unchecked means legacy boot")

            yield Label("Storage Pool:", id="vminstall-storage-label")
            yield Select(active_pools, value=default_pool, id="pool", allow_blank=False)

            yield ProgressBar(total=100, show_eta=False, id="progress-bar")
            yield Label("", id="status-label")

            with Vertical():
                with Horizontal(classes="buttons"):
                    yield Button("Install", variant="primary", id="install-btn", disabled=True)
                    yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self):
        """Called when modal is mounted."""
        # Initial state
        self.query_one("#custom-iso-container").styles.display = "none"
        # Default to showing cached ISOs first
        self.query_one("#distro", Select).value = "cached"
        self.fetch_isos("cached")
        # Ensure expert defaults are set correctly based on initial selection
        self._update_expert_defaults(self.query_one("#vm-type", Select).value)

    def _update_expert_defaults(self, vm_type):
        mem = 4096
        vcpu = 2
        disk_size = 8
        disk_format = "qcow2"

        if vm_type == VMType.COMPUTATION:
            mem = 8192
            vcpu = 4
            disk_format = "raw"
        elif vm_type == VMType.SERVER:
            mem = 4096
            vcpu = 6
            disk_size = 18
        elif vm_type == VMType.DESKTOP:
            mem = 4096
            vcpu = 4
            disk_size = 18
        elif vm_type == VMType.SECURE:
            mem = 4096
            vcpu = 2

        self.query_one("#memory-input", Input).value = str(mem)
        self.query_one("#cpu-input", Input).value = str(vcpu)
        self.query_one("#disk-size-input", Input).value = str(disk_size)
        self.query_one("#disk-format", Select).value = disk_format

    @on(Select.Changed, "#vm-type")
    def on_vm_type_changed(self, event: Select.Changed):
        self._update_expert_defaults(event.value)

    @on(Select.Changed, "#distro")
    def on_distro_changed(self, event: Select.Changed):
        self.query_one("#expert-mode-collapsible", Collapsible).collapsed = True
        if event.value == OpenSUSEDistro.CUSTOM:
            self.query_one("#repo-iso-container").styles.display = "none"
            self.query_one("#custom-iso-container").styles.display = "block"
            self._check_form_validity()
        else:
            self.query_one("#repo-iso-container").styles.display = "block"
            self.query_one("#custom-iso-container").styles.display = "none"
            self.fetch_isos(event.value)

    @on(Checkbox.Changed, "#validate-checksum")
    def on_checksum_toggle(self, event: Checkbox.Changed):
        self.query_one("#checksum-input").disabled = not event.value

    @on(Input.Changed, "#custom-iso-path")
    def on_custom_path_changed(self):
        self._check_form_validity()

    @on(Input.Changed, "#checksum-input")
    def on_checksum_changed(self):
        self._check_form_validity()

    @work(exclusive=True, thread=True)
    def fetch_isos(self, distro: OpenSUSEDistro | str):
        self.app.call_from_thread(self._update_iso_status, "Fetching ISO list...", True)

        try:
            isos = []
            if distro == "cached":
                isos = self.provisioner.get_cached_isos()
            else:
                isos = self.provisioner.get_iso_list(distro)

            # Create Select options: (label, url)
            iso_options = []
            for iso in isos:
                name = iso['name']
                url = iso['url']
                date = iso.get('date', '')

                label = f"{name} ({date})" if date else name
                iso_options.append((label, url))

            def update_select():
                sel = self.query_one("#iso-select", Select)
                sel.set_options(iso_options)
                sel.disabled = False
                if iso_options:
                    sel.value = iso_options[0][1] # Select first by default
                else:
                    sel.clear() # No options, clear any previous value
                self._update_iso_status("", False)
                self._check_form_validity() # Re-check validity after options change

            self.app.call_from_thread(update_select)

        except Exception as e:
            self.app.call_from_thread(self.app.show_error_message, f"Failed to fetch ISOs: {e}")
            self.app.call_from_thread(self._update_iso_status, "Error fetching ISOs", False)

    def _update_iso_status(self, message, loading):
        lbl = self.query_one("#status-label", Label)
        if message:
            lbl.update(message)
            lbl.styles.display = "block"
        else:
            lbl.styles.display = "none"

        # Disable install while fetching
        self.query_one("#install-btn", Button).disabled = loading

    @on(Select.Changed, "#iso-select")
    def on_iso_selected(self, event: Select.Changed):
        self._check_form_validity()

    @on(Input.Changed, "#vm-name")
    def on_name_changed(self):
        self._check_form_validity()

    def _check_form_validity(self):
        name = self.query_one("#vm-name", Input).value.strip()
        distro = self.query_one("#distro", Select).value

        valid_iso = False
        if distro == OpenSUSEDistro.CUSTOM:
            path = self.query_one("#custom-iso-path", Input).value.strip()
            valid_iso = bool(path) # Basic check, validation happens on install
        else:
            iso = self.query_one("#iso-select", Select).value
            valid_iso = (iso and iso != Select.BLANK)

        btn = self.query_one("#install-btn", Button)
        if name and valid_iso:
            btn.disabled = False
        else:
            btn.disabled = True

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self):
        self.dismiss()

    @on(Button.Pressed, "#browse-iso-btn")
    def on_browse_iso(self):
        """Open file picker for Custom ISO."""
        def set_path(path: str | None) -> None:
            if path:
                self.query_one("#custom-iso-path", Input).value = path
                self._check_form_validity()

        self.app.push_screen(FileSelectionModal(), set_path)

    #@on(Button.Pressed, "#browse-autoinstall-btn")
    #def on_browse_autoinstall(self):
    #    """Open file picker for Autoinstall file."""
    #    def set_path(path: str | None) -> None:
    #        if path:
    #            self.query_one("#autoinstall-path", Input).value = path

    #    self.app.push_screen(FileSelectionModal(), set_path)

    @on(Button.Pressed, "#vm-type-info-btn")
    def on_vm_type_info(self):
        """Show VM Type info modal."""
        self.app.push_screen(VMTypeInfoModal())

    @on(Button.Pressed, "#install-btn")
    def on_install(self):
        vm_name_raw = self.query_one("#vm-name", Input).value

        # 1. Sanitize VM Name
        try:
            vm_name, was_modified = _sanitize_domain_name(vm_name_raw)
        except ValueError as e:
            self.app.show_error_message(str(e))
            return

        if was_modified:
            self.app.show_quick_message(f"VM name sanitized: '{vm_name_raw}' -> '{vm_name}'")
            self.query_one("#vm-name", Input).value = vm_name

        if not vm_name:
            self.app.show_error_message("VM name cannot be empty.")
            return

        # 2. Check if VM exists
        try:
            self.conn.lookupByName(vm_name)
            self.app.show_error_message(f"A VM with the name '{vm_name}' already exists. Please choose a different name.")
            return
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
                self.app.show_error_message(f"Error checking VM name: {e}")
                return
        except Exception as e:
            self.app.show_error_message(f"An unexpected error occurred: {e}")
            return

        vm_type = self.query_one("#vm-type", Select).value
        pool_name = self.query_one("#pool", Select).value
        distro = self.query_one("#distro", Select).value

        iso_url = None
        custom_path = None
        checksum = None
        validate = False

        if distro == OpenSUSEDistro.CUSTOM:
            custom_path = self.query_one("#custom-iso-path", Input).value.strip()
            validate = self.query_one("#validate-checksum", Checkbox).value
            if validate:
                checksum = self.query_one("#checksum-input", Input).value.strip()
        else:
            iso_url = self.query_one("#iso-select", Select).value

        # Expert Mode Settings
        expert_mode = not self.query_one("#expert-mode-collapsible", Collapsible).collapsed
        memory_mb = 4096
        vcpu = 2
        disk_size = 20
        disk_format = "qcow2"
        boot_uefi = True

        if expert_mode:
            try:
                memory_mb = int(self.query_one("#memory-input", Input).value)
                vcpu = int(self.query_one("#cpu-input", Input).value)
                disk_size = int(self.query_one("#disk-size-input", Input).value)
                disk_format = self.query_one("#disk-format", Select).value
                boot_uefi = self.query_one("#boot-uefi-checkbox", Checkbox).value
            except ValueError:
                self.app.show_error_message("Invalid input for expert settings. Using defaults.")

        # Disable inputs
        for widget in self.query("Input"): widget.disabled = True
        for widget in self.query("Select"): widget.disabled = True
        for widget in self.query("Button"): widget.disabled = True

        self.query_one("#progress-bar").styles.display = "block"
        self.query_one("#status-label").styles.display = "block"

        self.run_provisioning(vm_name, vm_type, iso_url, pool_name, custom_path, validate, checksum, memory_mb, vcpu, disk_size, disk_format, boot_uefi)

    @work(exclusive=True, thread=True)
    def run_provisioning(self, name, vm_type, iso_url, pool_name, custom_path, validate, checksum, memory_mb, vcpu, disk_size, disk_format, boot_uefi):
        p_bar = self.query_one("#progress-bar", ProgressBar)
        status_lbl = self.query_one("#status-label", Label)

        def progress_cb(stage, percent):
            self.app.call_from_thread(status_lbl.update, stage)
            self.app.call_from_thread(p_bar.update, progress=percent)

        try:
            final_iso_url = iso_url

            if custom_path:
                # 1. Validate Checksum
                if validate:
                    progress_cb("Validating Checksum...", 0)
                    if not self.provisioner.validate_iso(custom_path, checksum):
                        raise Exception("Checksum validation failed!")
                    progress_cb("Checksum Validated", 10)

                # 2. Upload
                progress_cb("Uploading ISO...", 10)
                def upload_progress(p):
                    progress_cb(f"Uploading: {p}%", 10 + int(p * 0.4))

                final_iso_url = self.provisioner.upload_iso(custom_path, pool_name, upload_progress)

            # 3. Provision
            # Suspend global updates to prevent UI freeze during heavy provisioning ops
            self.app.call_from_thread(self.app.vm_service.suspend_global_updates)
            try:
                dom = self.provisioner.provision_vm(
                    vm_name=name,
                    vm_type=vm_type,
                    iso_url=final_iso_url,
                    storage_pool_name=pool_name,
                    memory_mb=memory_mb,
                    vcpu=vcpu,
                    disk_size_gb=disk_size,
                    disk_format=disk_format,
                    boot_uefi=boot_uefi,
                    progress_callback=progress_cb
                )
            finally:
                self.app.call_from_thread(self.app.vm_service.resume_global_updates)
                # Manually trigger a refresh as we suppressed the events
                self.app.call_from_thread(self.app.on_vm_data_update)

            self.app.call_from_thread(self.app.show_success_message, f"VM '{name}' created successfully!")

            # 4. Auto-connect Remote Viewer
            def launch_viewer():
                domain_name = dom.name()
                cmd = remote_viewer_cmd(self.uri, domain_name, self.app.r_viewer)
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid,
                        #env=env
                    )
                    logging.info(f"{self.app.r_viewer} started with PID {proc.pid} for {domain_name}")
                    self.app.show_quick_message(f"Remote viewer {self.app.r_viewer} started for {domain_name}")
                except Exception as e:
                    logging.error(f"Failed to spawn {self.app.r_viewer} for {domain_name}: {e}")
                    self.app.call_from_thread(
                        self.app.show_error_message,
                        f"{self.app.r_viewer} failed to start for {domain_name}: {e}"
                    )
                    return


            self.app.call_from_thread(launch_viewer)
            self.app.call_from_thread(self.dismiss, True)

        except Exception as e:
            self.app.call_from_thread(self.app.show_error_message, f"Provisioning failed: {e}")
            self.app.call_from_thread(self.dismiss)
