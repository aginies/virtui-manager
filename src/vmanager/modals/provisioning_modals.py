"""
Modals for VM Provisioning (Installation).
"""
import logging
import subprocess

import os
from textual.widgets import Input, Select, Button, Label, ProgressBar, Checkbox
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual import on, work

from vm_provisioner import VMProvisioner, VMType, OpenSUSEDistro
from storage_manager import list_storage_pools
from vm_service import VMService
from utils import remote_viewer_cmd
from modals.base_modals import BaseModal

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

        with ScrollableContainer(id="install-dialog"):
            yield Label(f"Install OpenSUSE VM on {self.uri}", classes="title")

            yield Label("VM Name:", classes="label")
            yield Input(placeholder="my-new-vm", id="vm-name")

            yield Label("VM Type:", classes="label")
            yield Select([(t.value, t) for t in VMType], value=VMType.DESKTOP, id="vm-type", allow_blank=False)

            yield Label("Distribution:", classes="label")
            # Add Custom option manually if not in Enum iteration, but I added it to Enum in previous step
            yield Select([(d.value, d) for d in OpenSUSEDistro], value=OpenSUSEDistro.LEAP, id="distro", allow_blank=False)

            # Container for ISO selection (Repo)
            with Vertical(id="repo-iso-container"):
                yield Label("ISO Image (Repo):", classes="label")
                yield Select([], prompt="Select ISO...", id="iso-select", disabled=True)

            # Container for Custom ISO
            with Vertical(id="custom-iso-container"):
                yield Label("Custom ISO (Local Path):", classes="label")
                with Horizontal():
                    yield Input(placeholder="/path/to/local.iso", id="custom-iso-path", classes="path-input")
                    # Future: File Picker Button

                yield Checkbox("Validate Checksum", id="validate-checksum", value=False)
                yield Input(placeholder="SHA256 Checksum (Optional)", id="checksum-input", disabled=True)
                yield Label("", id="checksum-status", classes="status-text")

            yield Label("Storage Pool:", classes="label")
            yield Select(active_pools, value=default_pool, id="pool", allow_blank=False)

            yield ProgressBar(total=100, show_eta=False, id="progress-bar")
            yield Label("", id="status-label")

            with Horizontal(classes="buttons"):
                yield Button("Install", variant="primary", id="install-btn", disabled=True)
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self):
        """Called when modal is mounted."""
        # Initial state
        self.query_one("#custom-iso-container").styles.display = "none"
        self.fetch_isos(OpenSUSEDistro.LEAP)

    @on(Select.Changed, "#distro")
    def on_distro_changed(self, event: Select.Changed):
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
    def fetch_isos(self, distro: OpenSUSEDistro):
        self.app.call_from_thread(self._update_iso_status, "Fetching ISO list...", True)

        try:
            isos = self.provisioner.get_iso_list(distro)
            # Create Select options: (filename, url)
            iso_options = []
            for url in isos:
                name = url.split('/')[-1]
                iso_options.append((name, url))

            def update_select():
                sel = self.query_one("#iso-select", Select)
                sel.set_options(iso_options)
                sel.disabled = False
                if iso_options:
                    sel.value = iso_options[0][1] # Select first by default
                self._update_iso_status("", False)

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

    @on(Button.Pressed, "#install-btn")
    def on_install(self):
        vm_name = self.query_one("#vm-name", Input).value.strip()
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

        # Disable inputs
        for widget in self.query("Input"): widget.disabled = True
        for widget in self.query("Select"): widget.disabled = True
        for widget in self.query("Button"): widget.disabled = True

        self.query_one("#progress-bar").styles.display = "block"
        self.query_one("#status-label").styles.display = "block"

        self.run_provisioning(vm_name, vm_type, iso_url, pool_name, custom_path, validate, checksum)

    @work(exclusive=True, thread=True)
    def run_provisioning(self, name, vm_type, iso_url, pool_name, custom_path, validate, checksum):
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
            dom = self.provisioner.provision_vm(
                vm_name=name,
                vm_type=vm_type,
                iso_url=final_iso_url,
                storage_pool_name=pool_name,
                progress_callback=progress_cb
            )

            self.app.call_from_thread(self.app.show_success_message, f"VM '{name}' created successfully!")

            # 4. Auto-connect Remote Viewer
            def launch_viewer():
                domain_name = dom.name()
                cmd = remote_viewer_cmd(self.uri, domain_name)
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
