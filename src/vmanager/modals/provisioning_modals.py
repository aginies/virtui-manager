"""
Modals for VM Provisioning (Installation).
"""

import logging
import os
import subprocess
from pathlib import Path

import libvirt
from textual import on, work
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Checkbox, Collapsible, Input, Label, ProgressBar, Select

from ..config import load_config
from ..constants import AppInfo, ButtonLabels, ErrorMessages, StaticText, SuccessMessages
from ..provisioning.templates.autoyast_template_manager import AutoYaSTTemplateManager
from ..storage_manager import list_storage_pools
from ..utils import remote_viewer_cmd
from ..vm_provisioner import OpenSUSEDistro, VMProvisioner, VMType
from ..vm_service import VMService
from .base_modals import BaseModal
from .input_modals import _sanitize_domain_name
from .template_modals import TemplateManagementModal
from .utils_modals import FileSelectionModal
from .vm_type_info_modal import VMTypeInfoModal
from .vmdetails_modals import VMDetailModal


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
        self.template_manager = AutoYaSTTemplateManager(self.provisioner)
        self.iso_list = []

    def compose(self):
        # Get Pools
        pools = list_storage_pools(self.conn)
        active_pools = [(p["name"], p["name"]) for p in pools if p["status"] == "active"]
        default_pool = (
            "default"
            if any(p[0] == "default" for p in active_pools)
            else (active_pools[0][1] if active_pools else None)
        )

        with ScrollableContainer(id="install-dialog"):
            yield Label(StaticText.INSTALL_VM.format(uri=self.uri), classes="title")
            yield Input(placeholder=StaticText.VM_NAME, id="vm-name")

            with Horizontal(classes="label-row"):
                yield Select(
                    [(t.value, t) for t in VMType],
                    value=VMType.DESKTOP,
                    id="vm-type",
                    allow_blank=False,
                )
                yield Button(ButtonLabels.INFO, id="vm-type-info-btn", variant="primary")

            distro_options = [(d.value, d) for d in OpenSUSEDistro]
            distro_options.insert(0, (StaticText.CACHED_ISOS, "cached"))
            custom_repos = self.provisioner.get_custom_repos()
            for repo in custom_repos:
                # Use URI as value, Name as label
                name = repo.get("name", repo["uri"])
                uri = repo["uri"]
                # Insert before CUSTOM option (last one usually)
                distro_options.insert(-1, (name, uri))

            # Add option to select from storage pool volumes
            distro_options.insert(-1, (StaticText.FROM_STORAGE_POOL, "pool_volumes"))

            yield Select(
                distro_options, id="distro", allow_blank=True, prompt=StaticText.DISTRIBUTION
            )

            # Container for ISO selection (Repo)
            with Vertical(id="repo-iso-container"):
                yield Label(StaticText.ISO_IMAGE_REPO, classes="label")
                config = load_config()
                iso_path = Path(
                    config.get(
                        "ISO_DOWNLOAD_PATH", str(Path.home() / ".cache" / AppInfo.name / "isos")
                    )
                )
                yield Label(
                    StaticText.ISOS_DOWNLOAD_PATH.format(iso_path=iso_path),
                    classes="info-text",
                    id="iso-path-label",
                )
                yield Select(
                    [], prompt=StaticText.SELECT_ISO_PROMPT, id="iso-select", disabled=True
                )

            # Container for Custom ISO
            with Vertical(id="custom-iso-container"):
                yield Label(StaticText.CUSTOM_ISO_LOCAL_PATH, classes="label")
                with Horizontal(classes="input-row"):
                    yield Input(
                        placeholder="/path/to/local.iso", id="custom-iso-path", classes="path-input"
                    )
                    yield Button(ButtonLabels.BROWSE, id="browse-iso-btn")

                with Vertical(id="checksum-container"):
                    yield Checkbox(
                        StaticText.VALIDATE_CHECKSUM, id="validate-checksum", value=False
                    )
                    yield Input(
                        placeholder=StaticText.SHA256_CHECKSUM_OPTIONAL_PLACEHOLDER,
                        id="checksum-input",
                        disabled=True,
                    )
                    yield Label(StaticText.EMPTY_LABEL, id="checksum-status", classes="status-text")

            # Container for ISO selection from Storage Pools
            with Vertical(id="pool-iso-container"):
                yield Label(StaticText.SELECT_STORAGE_POOL, classes="label")
                yield Select(
                    active_pools,
                    prompt=StaticText.SELECT_POOL_PROMPT,
                    id="storage-pool-select",
                    allow_blank=False,
                    value=active_pools[0][1] if active_pools else None,
                )
                yield Label(StaticText.SELECT_ISO_VOLUME, classes="label")
                yield Select(
                    [],
                    prompt=StaticText.SELECT_ISO_VOLUME_PROMPT,
                    id="iso-volume-select",
                    disabled=True,
                )

            with Vertical(id="pool-selection"):
                yield Label(StaticText.STORAGE_POOL, id="vminstall-storage-label")
                yield Select(active_pools, value=default_pool, id="pool", allow_blank=False)

            with Collapsible(title=StaticText.EXPERT_MODE, id="expert-mode-collapsible"):
                with Horizontal(id="expert-mode"):
                    with Vertical(id="expert-mem"):
                        yield Label(StaticText.MEMORY_GB_LABEL, classes="label")
                        yield Input("4", id="memory-input", type="integer")
                    with Vertical(id="expert-cpu"):
                        yield Label(StaticText.CPUS_LABEL, classes="label")
                        yield Input("2", id="cpu-input", type="integer")
                    with Vertical(id="expert-disk-size"):
                        yield Label(StaticText.DISK_SIZE_GB_LABEL, classes="label")
                        yield Input("8", id="disk-size-input", type="integer")
                    with Vertical(id="expert-disk-format"):
                        yield Label(StaticText.DISK_FORMAT_LABEL, classes="label")
                        yield Select(
                            [("Qcow2", "qcow2"), ("Raw", "raw")],
                            value="qcow2",
                            id="disk-format",
                        )
                    with Vertical(id="expert-firmware"):
                        yield Label(StaticText.FIRMWARE_LABEL, classes="label")
                        yield Checkbox(
                            "UEFI",
                            id="boot-uefi-checkbox",
                            value=True,
                            tooltip=StaticText.LEGACY_BOOT_TOOLTIP,
                        )

            # Automated Installation Configuration
            with Collapsible(
                title=StaticText.AUTOMATED_INSTALLATION_TITLE,
                id="automation-collapsible",
                collapsed=True,
            ):
                # Template selection - "None" means no automation
                with Vertical(id="automation-template-container"):
                    yield Label(StaticText.INSTALLATION_TEMPLATE_LABEL, classes="label")
                    with Horizontal(classes="template-management-buttons"):
                        yield Select(
                            [(StaticText.NONE_OPTION, None)],  # Default: no automation
                            value=None,
                            id="automation-template-select",
                            allow_blank=False,
                            tooltip=StaticText.AUTOMATION_TEMPLATE_TOOLTIP,
                        )
                        # Template management button
                        yield Button(
                            StaticText.MANAGE_TEMPLATES_BUTTON,
                            id="manage-templates-btn",
                            classes="small-button",
                        )
                # User configuration - only visible when a template is selected
                with Vertical(id="automation-user-config-wrapper"):
                    with Horizontal(id="automation-user-config"):
                        with Vertical(id="automation-user-left"):
                            yield Label(StaticText.ROOT_PASSWORD_LABEL, classes="label")
                            yield Input(
                                placeholder=StaticText.ROOT_PASSWORD_PLACEHOLDER,
                                id="automation-root-password",
                                password=True,
                                disabled=True,
                            )
                            yield Label(StaticText.USERNAME_LABEL, classes="label")
                            yield Input(
                                placeholder=StaticText.USERNAME_PLACEHOLDER,
                                id="automation-username",
                                disabled=True,
                            )
                        with Vertical(id="automation-user-right"):
                            yield Label(StaticText.HOSTNAME_LABEL, classes="label")
                            yield Input(
                                placeholder=StaticText.HOSTNAME_PLACEHOLDER,
                                id="automation-hostname",
                                disabled=True,
                            )
                            yield Label(StaticText.USER_PASSWORD_LABEL, classes="label")
                            yield Input(
                                placeholder=StaticText.USER_PASSWORD_PLACEHOLDER,
                                id="automation-user-password",
                                password=True,
                                disabled=True,
                            )
                        with Vertical():
                            yield Label(StaticText.LANGUAGE_LABEL, classes="label")
                            yield Select(
                                [
                                    ("English (US)", "en_US"),
                                    ("German", "de_DE"),
                                    ("French", "fr_FR"),
                                    ("Spanish", "es_ES"),
                                    ("Italian", "it_IT"),
                                    ("Portuguese (Brazil)", "pt_BR"),
                                    ("Russian", "ru_RU"),
                                    ("Japanese", "ja_JP"),
                                    ("Chinese (Simplified)", "zh_CN"),
                                ],
                                value="en_US",
                                id="automation-language",
                                disabled=True,
                                tooltip=StaticText.LANGUAGE_TOOLTIP,
                            )
                            yield Label(StaticText.KEYBOARD_LABEL, classes="label")
                            yield Select(
                                [
                                    ("US", "us"),
                                    ("German", "de"),
                                    ("French", "fr"),
                                    ("Spanish", "es"),
                                    ("Italian", "it"),
                                    ("Portuguese", "pt"),
                                    ("Russian", "ru"),
                                    ("Japanese", "jp"),
                                    ("UK", "uk"),
                                ],
                                value="us",
                                id="automation-keyboard",
                                disabled=True,
                                tooltip=StaticText.KEYBOARD_TOOLTIP,
                            )

            with Vertical():
                with Horizontal():
                    yield Checkbox(
                        StaticText.USE_VIRT_INSTALL_LABEL,
                        id="use-virt-install-checkbox",
                        value=False,
                        tooltip=StaticText.USE_VIRT_INSTALL_TOOLTIP,
                    )
                    yield Checkbox(
                        StaticText.REDIRECT_CONSOLE_SERIAL_LABEL,
                        id="automation-serial-console",
                        value=False,
                        tooltip=StaticText.SERIAL_CONSOLE_TOOLTIP,
                        disabled=True,
                    )

            yield Checkbox(
                StaticText.CONFIGURE_BEFORE_INSTALL,
                id="configure-before-install-checkbox",
                value=False,
                tooltip=StaticText.SHOW_VM_CONFIG_BEFORE_STARTING_TOOLTIP,
            )
            yield ProgressBar(total=100, show_eta=False, id="progress-bar")
            yield Label(StaticText.EMPTY_LABEL, id="status-label")

            with Horizontal(classes="buttons"):
                yield Button(
                    ButtonLabels.INSTALL, variant="primary", id="install-btn", disabled=True
                )
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_mount(self):
        """Called when modal is mounted."""
        # Initial state
        self.query_one("#custom-iso-container").styles.display = "none"
        self.query_one("#repo-iso-container").styles.display = "none"
        self.query_one("#pool-iso-container").styles.display = "none"  # Hide new container
        # Ensure expert defaults are set correctly based on initial selection
        self._update_expert_defaults(self.query_one("#vm-type", Select).value)

        # Populate initial storage pool volumes if "From Storage Pool" is default
        storage_pool_select = self.query_one("#storage-pool-select", Select)
        if storage_pool_select.value:
            self.fetch_pool_isos(storage_pool_select.value)

        # Hide virt-install checkbox if not available
        use_virt_install_checkbox = self.query_one("#use-virt-install-checkbox", Checkbox)
        if not self.provisioner.check_virt_install():
            use_virt_install_checkbox.value = False
            use_virt_install_checkbox.styles.display = "none"
        else:
            use_virt_install_checkbox.styles.display = "block"

    def _update_expert_defaults(self, vm_type):
        mem = 4
        vcpu = 2
        disk_size = 8
        disk_format = "qcow2"
        boot_uefi = True

        if vm_type == VMType.COMPUTATION:
            mem = 8
            vcpu = 4
            disk_format = "raw"
            boot_uefi = False
        elif vm_type == VMType.SERVER:
            mem = 4
            vcpu = 6
            disk_size = 18
        elif vm_type == VMType.DESKTOP:
            mem = 4
            vcpu = 4
            disk_size = 30
        elif vm_type == VMType.WDESKTOP:
            mem = 16
            vcpu = 8
            disk_size = 40
        elif vm_type == VMType.WLDESKTOP:
            mem = 4
            vcpu = 4
            disk_size = 30
            boot_uefi = False
        elif vm_type == VMType.SECURE:
            mem = 4
            vcpu = 2

        self.query_one("#memory-input", Input).value = str(mem)
        self.query_one("#cpu-input", Input).value = str(vcpu)
        self.query_one("#disk-size-input", Input).value = str(disk_size)
        self.query_one("#disk-format", Select).value = disk_format

        # Only update UEFI if it's not locked by automation
        uefi_checkbox = self.query_one("#boot-uefi-checkbox", Checkbox)
        if not uefi_checkbox.disabled:
            uefi_checkbox.value = boot_uefi

    @on(Select.Changed, "#vm-type")
    def on_vm_type_changed(self, event: Select.Changed):
        self._update_expert_defaults(event.value)

    @on(Select.Changed, "#distro")
    def on_distro_changed(self, event: Select.Changed):
        self.query_one("#expert-mode-collapsible", Collapsible).collapsed = True

        # Hide all ISO source containers first
        self.query_one("#repo-iso-container").styles.display = "none"
        self.query_one("#custom-iso-container").styles.display = "none"
        self.query_one("#pool-iso-container").styles.display = "none"

        if event.value == OpenSUSEDistro.CUSTOM:
            self.query_one("#custom-iso-container").styles.display = "block"
        elif event.value == "pool_volumes":
            self.query_one("#repo-iso-container").styles.display = "none"
            self.query_one("#custom-iso-container").styles.display = "none"
            self.query_one("#pool-iso-container").styles.display = "block"
            # Trigger fetching volumes for the currently selected storage pool
            pool_select = self.query_one("#storage-pool-select", Select)
            if pool_select.value and pool_select.value != Select.BLANK:
                self.fetch_pool_isos(pool_select.value)
            else:
                # If no pool is selected, clear the ISO volume select and keep it disabled
                iso_volume_select = self.query_one("#iso-volume-select", Select)
                iso_volume_select.clear()
                iso_volume_select.disabled = True
        else:  # Repo or Cached
            self.query_one("#repo-iso-container").styles.display = "block"
            self.fetch_isos(event.value)

        # Populate templates based on the selected distribution
        self._populate_templates_for_distribution(event.value)

        self._check_form_validity()

    @on(Checkbox.Changed, "#validate-checksum")
    def on_checksum_toggle(self, event: Checkbox.Changed):
        self.query_one("#checksum-input").disabled = not event.value

    @on(Input.Changed, "#custom-iso-path")
    def on_custom_path_changed(self):
        self._check_form_validity()

    @on(Input.Changed, "#checksum-input")
    def on_checksum_changed(self):
        self._check_form_validity()

    @on(Select.Changed, "#storage-pool-select")
    def on_storage_pool_selected(self, event: Select.Changed):
        """Handles when a storage pool is selected for ISO volumes."""
        if event.value and event.value != Select.BLANK:
            self.fetch_pool_isos(event.value)
        else:
            # No pool selected, clear volumes and disable the volume select
            iso_volume_select = self.query_one("#iso-volume-select", Select)
            iso_volume_select.clear()
            iso_volume_select.disabled = True
        self._check_form_validity()

    @on(Select.Changed, "#iso-volume-select")
    def on_iso_volume_selected(self, event: Select.Changed):
        """Handles when an ISO volume is selected."""
        self._check_form_validity()

    @work(exclusive=True, thread=True)
    def fetch_pool_isos(self, pool_name: str):
        """Fetches and populates the list of ISO volumes in a given storage pool."""
        self.app.call_from_thread(
            self._update_iso_status,
            StaticText.FETCHING_ISO_VOLUMES_FROM_TEMPLATE.format(pool_name=pool_name),
            True,
        )
        iso_volume_select = self.query_one("#iso-volume-select", Select)
        try:
            pool = self.conn.storagePoolLookupByName(pool_name)
            if not pool.isActive():
                raise Exception(
                    ErrorMessages.STORAGE_POOL_NOT_ACTIVE_TEMPLATE.format(pool_name=pool_name)
                )
            volumes = pool.listAllVolumes(0) if pool else []

            iso_volumes_options = []
            for vol in volumes:
                # Filter for ISO images - often ending in .iso or .img
                # This is a heuristic, actual content type is harder to determine without reading
                if vol.name().lower().endswith((".iso", ".img")):
                    iso_volumes_options.append(
                        (vol.name(), vol.path())
                    )  # Display name, store full path

            iso_volumes_options.sort(key=lambda x: x[0])  # Sort by name

            def update_iso_volume_select():
                if iso_volumes_options:
                    iso_volume_select.set_options(iso_volumes_options)
                    iso_volume_select.value = iso_volumes_options[0][1]  # Select first
                    iso_volume_select.disabled = False
                else:
                    iso_volume_select.clear()
                    iso_volume_select.disabled = True
                self._update_iso_status("", False)
                self._check_form_validity()

            self.app.call_from_thread(update_iso_volume_select)

        except Exception as e:
            self.app.call_from_thread(
                self.app.show_error_message,
                ErrorMessages.FAILED_TO_FETCH_ISO_VOLUMES_TEMPLATE.format(
                    pool_name=pool_name, error=e
                ),
            )
            self.app.call_from_thread(
                self._update_iso_status, StaticText.ERROR_FETCHING_VOLUMES, False
            )
            self.app.call_from_thread(iso_volume_select.clear)
            self.app.call_from_thread(lambda: setattr(iso_volume_select, "disabled", True))

    @work(exclusive=True, thread=True)
    def fetch_isos(self, distro: OpenSUSEDistro | str):
        self.app.call_from_thread(self._update_iso_status, StaticText.FETCHING_ISO_LIST, True)

        try:
            isos = []
            if distro == "cached":
                isos = self.provisioner.get_cached_isos()
            else:
                isos = self.provisioner.get_iso_list(distro)

            # Create Select options: (label, url)
            iso_options = []
            for iso in isos:
                name = iso["name"]
                url = iso["url"]
                date = iso.get("date", "")

                label = f"{name} ({date})" if date else name
                iso_options.append((label, url))

            def update_select():
                sel = self.query_one("#iso-select", Select)
                sel.set_options(iso_options)
                sel.disabled = False
                if iso_options:
                    sel.value = iso_options[0][1]  # Select first by default
                else:
                    sel.clear()  # No options, clear any previous value
                self._update_iso_status("", False)
                self._check_form_validity()  # Re-check validity after options change

            self.app.call_from_thread(update_select)

        except Exception as e:
            self.app.call_from_thread(
                self.app.show_error_message,
                ErrorMessages.FAILED_TO_FETCH_ISOS_TEMPLATE.format(error=e),
            )
            self.app.call_from_thread(
                self._update_iso_status, StaticText.ERROR_FETCHING_ISOS, False
            )

    def _update_iso_status(self, message, loading):
        lbl = self.query_one("#status-label", Label)
        if message:
            lbl.update(message)
            lbl.styles.display = "block"
        else:
            lbl.styles.display = "none"

        # Disable install while fetching
        self.query_one("#install-btn", Button).disabled = loading

    def _populate_templates_for_distribution(self, distro: OpenSUSEDistro | str):
        """
        Populate the template select widget based on the selected distribution.

        Template filtering rules:
        - "Cached ISOs" → Show ALL templates (AutoYaST + Agama)
        - "OpenSUSE Leap" → Show ONLY AutoYaST templates
        - "OpenSUSE Tumbleweed" → Show BOTH AutoYaST + Agama templates
        - "OpenSUSE Slowroll" → Show BOTH AutoYaST + Agama templates
        - "OpenSUSE Stable (Leap)" → Show ONLY Agama templates
        - "OpenSUSE Current (Tumbleweed)" → Show ONLY Agama templates
        - Custom repositories → Show ALL templates
        """
        # Get all templates
        all_templates = self.template_manager.get_all_templates()

        # Determine which templates to show based on distribution
        if isinstance(distro, OpenSUSEDistro):
            if distro == OpenSUSEDistro.LEAP:
                # OpenSUSE Leap → ONLY AutoYaST templates
                show_autoyast = True
                show_agama = False
            elif distro in [OpenSUSEDistro.TUMBLEWEED, OpenSUSEDistro.SLOWROLL]:
                # OpenSUSE Tumbleweed/Slowroll → BOTH AutoYaST + Agama templates
                show_autoyast = True
                show_agama = True
            elif distro in [OpenSUSEDistro.STABLE, OpenSUSEDistro.CURRENT]:
                # OpenSUSE Stable (Leap) / Current (Tumbleweed) → ONLY Agama templates
                show_autoyast = False
                show_agama = True
            else:
                # Other OpenSUSE distributions → show all (fallback)
                show_autoyast = True
                show_agama = True

            filtered_templates = []
            for template in all_templates:
                filename = template.get("filename", "")
                is_agama = filename.endswith(".json") or "(Agama)" in template["display_name"]
                is_autoyast = filename.endswith(".xml") or "(AutoYaST)" in template["display_name"]

                # Apply filtering based on distribution requirements
                should_include = False
                if show_autoyast and is_autoyast:
                    should_include = True
                elif show_agama and is_agama:
                    should_include = True

                if should_include:
                    # Include built-in templates that match the filter
                    if template["type"] == "built-in":
                        filtered_templates.append(template)
                    # For user templates, check if they're openSUSE/SLES related
                    elif template["type"] == "user":
                        if (
                            "(openSUSE)" in template["display_name"]
                            or "(SLES)" in template["display_name"]
                            or "(Agama)" in template["display_name"]
                            or "(AutoYaST)" in template["display_name"]
                        ):
                            filtered_templates.append(template)

        elif isinstance(distro, str):
            if distro in ["cached", "pool_volumes"]:
                # Cached ISOs or pool volumes → Show ALL templates
                filtered_templates = all_templates
            else:
                # Custom repository URL → Show ALL templates
                # This allows flexibility for users who may be using custom repos
                # with either AutoYaST or Agama support
                filtered_templates = []
                for template in all_templates:
                    # Check URL to determine if it's SUSE-related
                    distro_lower = distro.lower()
                    if (
                        "opensuse" in distro_lower
                        or "sle" in distro_lower
                        or "suse" in distro_lower
                    ):
                        # SUSE-based custom repo - include SUSE-related templates
                        if template["type"] == "built-in":
                            filtered_templates.append(template)
                        elif template["type"] == "user":
                            if (
                                "(openSUSE)" in template["display_name"]
                                or "(SLES)" in template["display_name"]
                                or "(Agama)" in template["display_name"]
                            ):
                                filtered_templates.append(template)
                    else:
                        # Non-SUSE custom repo - show all templates for flexibility
                        filtered_templates.append(template)
        else:
            # Fallback - show all templates
            filtered_templates = all_templates

        # Build select options
        template_options = [("None", None)]  # Default: no automation
        for template in filtered_templates:
            label = template["display_name"]
            # Use template_id for user templates, filename for built-in
            value = template.get("template_id") or template["filename"]
            template_options.append((label, value))

        # Update the select widget
        template_select = self.query_one("#automation-template-select", Select)
        template_select.set_options(template_options)
        template_select.value = None  # Reset to "None"

    @on(Select.Changed, "#iso-select")
    def on_iso_selected(self, event: Select.Changed):
        self._check_form_validity()

    @on(Select.Changed, "#automation-template-select")
    def on_template_selected(self, event: Select.Changed):
        """Handle template selection - enable/disable user config fields."""
        template_id = event.value

        # Enable/disable user configuration fields based on template selection
        should_enable = template_id and template_id != Select.BLANK

        # Update all automation user config fields
        try:
            self.query_one("#automation-root-password", Input).disabled = not should_enable
            self.query_one("#automation-hostname", Input).disabled = not should_enable
            self.query_one("#automation-username", Input).disabled = not should_enable
            self.query_one("#automation-user-password", Input).disabled = not should_enable
            self.query_one("#automation-language", Select).disabled = not should_enable
            self.query_one("#automation-keyboard", Select).disabled = not should_enable
            self.query_one("#automation-serial-console", Checkbox).disabled = not should_enable

            # If automation is enabled, prefill fields from config
            if should_enable:
                self._prefill_automation_fields()

            # Enforce UEFI for automated installations
            uefi_checkbox = self.query_one("#boot-uefi-checkbox", Checkbox)
            if should_enable:
                uefi_checkbox.value = True
                uefi_checkbox.disabled = True
            else:
                uefi_checkbox.disabled = False
        except Exception as e:
            # Widgets may not exist in all contexts
            logging.warning(f"Could not update automation config fields: {e}")

    @on(Input.Changed, "#vm-name")
    def on_name_changed(self, event: Input.Changed):
        # Synchronize with hostname in automated installation
        try:
            hostname_input = self.query_one("#automation-hostname", Input)
            hostname_input.value = event.value
        except Exception:
            pass
        self._check_form_validity()

    def _prefill_automation_fields(self):
        """Prefill automation fields from user configuration."""
        try:
            config = load_config()
            prefill_config = config.get("AUTO_INSTALL_PRE_FILL", {})

            # Prefill root password
            root_password = prefill_config.get("root_password", "")
            if root_password:
                self.query_one("#automation-root-password", Input).value = root_password

            # Prefill username
            username = prefill_config.get("username", "")
            if username:
                self.query_one("#automation-username", Input).value = username

            # Prefill user password
            user_password = prefill_config.get("user_password", "")
            if user_password:
                self.query_one("#automation-user-password", Input).value = user_password

            # Prefill keyboard layout
            keyboard = prefill_config.get("keyboard", "")
            if keyboard:
                keyboard_select = self.query_one("#automation-keyboard", Select)
                # Find matching keyboard option by value
                for option_text, option_value in keyboard_select._options:
                    if option_value == keyboard:
                        keyboard_select.value = option_value
                        break

            # Prefill language
            language = prefill_config.get("language", "")
            if language:
                language_select = self.query_one("#automation-language", Select)
                # Find matching language option by display text or value
                for option_text, option_value in language_select._options:
                    if option_text == language or option_value == language:
                        language_select.value = option_value
                        break

            # Prefill language
            language = prefill_config.get("language", "")
            if language:
                language_select = self.query_one("#automation-language", Select)
                # Find matching language option by display text or value
                for option in language_select._options:
                    if hasattr(option, "prompt") and hasattr(option, "value"):
                        if option.prompt == language or option.value == language:
                            language_select.value = option.value
                            break

        except Exception as e:
            # Log but don't fail - prefilling is optional functionality
            logging.warning(f"Could not prefill automation fields from config: {e}")

    def _check_form_validity(self):
        name = self.query_one("#vm-name", Input).value.strip()
        distro = self.query_one("#distro", Select).value

        valid_iso = False
        if distro == OpenSUSEDistro.CUSTOM:
            path = self.query_one("#custom-iso-path", Input).value.strip()
            valid_iso = bool(path)  # Basic check, validation happens on install
        elif distro == "pool_volumes":
            iso_volume = self.query_one("#iso-volume-select", Select).value
            valid_iso = iso_volume and iso_volume != Select.BLANK
        else:
            iso = self.query_one("#iso-select", Select).value
            valid_iso = iso and iso != Select.BLANK

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

    @on(Button.Pressed, "#vm-type-info-btn")
    def on_vm_type_info(self):
        """Show VM Type info modal."""
        self.app.push_screen(VMTypeInfoModal())

    @on(Button.Pressed, "#manage-templates-btn")
    def on_manage_templates(self):
        """Open the template management modal."""

        def on_template_modal_close(result: bool | None):
            # When template management modal closes, refresh the template list
            # in case templates were added, edited, or deleted
            distro = self.query_one("#distro", Select).value
            if distro:
                self._populate_templates_for_distribution(distro)

        self.app.push_screen(
            TemplateManagementModal(self.template_manager), on_template_modal_close
        )

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
            self.app.show_quick_message(
                SuccessMessages.VM_NAME_SANITIZED_TEMPLATE.format(
                    original=vm_name_raw, sanitized=vm_name
                )
            )
            self.query_one("#vm-name", Input).value = vm_name

        if not vm_name:
            self.app.show_error_message(ErrorMessages.VM_NAME_CANNOT_BE_EMPTY)
            return

        # 2. Check if VM exists
        try:
            self.conn.lookupByName(vm_name)
            self.app.show_error_message(
                ErrorMessages.VM_NAME_ALREADY_EXISTS_TEMPLATE.format(vm_name=vm_name)
            )
            return
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
                self.app.show_error_message(
                    ErrorMessages.ERROR_CHECKING_VM_NAME_TEMPLATE.format(error=e)
                )
                return
        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE.format(error=e)
            )
            return

        vm_type = self.query_one("#vm-type", Select).value
        pool_name = self.query_one("#pool", Select).value
        distro = self.query_one("#distro", Select).value
        use_virt_install = self.query_one("#use-virt-install-checkbox", Checkbox).value
        serial_console = self.query_one("#automation-serial-console", Checkbox).value
        configure_before_install = self.query_one(
            "#configure-before-install-checkbox", Checkbox
        ).value

        # Validate storage pool
        if not pool_name or pool_name == Select.BLANK:
            self.app.show_error_message(ErrorMessages.PLEASE_SELECT_VALID_STORAGE_POOL)
            return

        iso_url = None
        custom_path = None
        checksum = None
        validate = False

        if distro == OpenSUSEDistro.CUSTOM:
            custom_path = self.query_one("#custom-iso-path", Input).value.strip()
            validate = self.query_one("#validate-checksum", Checkbox).value
            if validate:
                checksum = self.query_one("#checksum-input", Input).value.strip()
        elif distro == "pool_volumes":
            iso_url = self.query_one("#iso-volume-select", Select).value
            if not iso_url or iso_url == Select.BLANK:
                self.app.show_error_message(ErrorMessages.SELECT_VALID_ISO_VOLUME)
                return
            # Validate that the volume path exists and is accessible
            if not os.path.exists(iso_url):
                self.app.show_error_message(
                    ErrorMessages.ISO_VOLUME_NOT_FOUND_TEMPLATE.format(iso_url=iso_url)
                )
                return
        else:
            iso_url = self.query_one("#iso-select", Select).value

        # Expert Mode Settings
        try:
            memory_gb = int(self.query_one("#memory-input", Input).value)
            memory_mb = memory_gb * 1024
            vcpu = int(self.query_one("#cpu-input", Input).value)
            disk_size = int(self.query_one("#disk-size-input", Input).value)
            disk_format = self.query_one("#disk-format", Select).value
            boot_uefi = self.query_one("#boot-uefi-checkbox", Checkbox).value
        except ValueError:
            self.app.show_error_message(ErrorMessages.INVALID_EXPERT_SETTINGS)
            return

        # Get automation template selection
        automation_template_id = None
        try:
            template_select = self.query_one("#automation-template-select", Select)
            if template_select.value and template_select.value != Select.BLANK:
                automation_template_id = template_select.value
        except Exception:
            # Template selection widget may not exist or may not be visible
            pass

        # Validate expert mode inputs
        if memory_gb < 1 or memory_gb > 8192:
            self.app.show_error_message(ErrorMessages.MEMORY_RANGE_ERROR)
            return
        if vcpu < 1 or vcpu > 768:
            self.app.show_error_message(ErrorMessages.CPU_RANGE_ERROR)
            return
        if disk_size < 1 or disk_size > 10000:
            self.app.show_error_message(ErrorMessages.DISK_SIZE_RANGE_ERROR)
            return

        try:
            pool = self.conn.storagePoolLookupByName(pool_name)
            if not pool.isActive():
                self.app.show_error_message(
                    ErrorMessages.STORAGE_POOL_NOT_ACTIVE_TEMPLATE.format(pool_name=pool_name)
                )
                return
        except Exception as e:
            self.app.show_error_message(
                ErrorMessages.ERROR_ACCESSING_STORAGE_POOL_TEMPLATE.format(
                    pool_name=pool_name, error=e
                )
            )
            return

        # Disable inputs
        for widget in self.query("Input"):
            widget.disabled = True
        for widget in self.query("Select"):
            widget.disabled = True
        for widget in self.query("Button"):
            widget.disabled = True
        self.query_one("#configure-before-install-checkbox", Checkbox).disabled = True
        self.query_one("#progress-bar").styles.display = "block"
        self.query_one("#status-label").styles.display = "block"

        self.run_provisioning(
            vm_name,
            vm_type,
            iso_url,
            pool_name,
            custom_path,
            validate,
            checksum,
            memory_mb,
            vcpu,
            disk_size,
            disk_format,
            boot_uefi,
            use_virt_install,
            serial_console,
            configure_before_install,
            automation_template_id,
        )

    @work(exclusive=True, thread=True)
    def run_provisioning(
        self,
        name,
        vm_type,
        iso_url,
        pool_name,
        custom_path,
        validate,
        checksum,
        memory_mb,
        vcpu,
        disk_size,
        disk_format,
        boot_uefi,
        use_virt_install,
        serial_console,
        configure_before_install,
        automation_template_id,
    ):
        p_bar = self.query_one("#progress-bar", ProgressBar)
        status_lbl = self.query_one("#status-label", Label)

        def progress_cb(stage, percent):
            self.app.call_from_thread(status_lbl.update, stage)
            self.app.call_from_thread(p_bar.update, progress=percent)

        try:
            final_iso_url = iso_url

            if custom_path:
                # Validate custom path exists
                if not os.path.exists(custom_path):
                    raise Exception(
                        ErrorMessages.CUSTOM_ISO_PATH_NOT_EXIST_TEMPLATE.format(path=custom_path)
                    )
                if not os.path.isfile(custom_path):
                    raise Exception(
                        ErrorMessages.CUSTOM_ISO_NOT_FILE_TEMPLATE.format(path=custom_path)
                    )

                # 1. Validate Checksum
                if validate:
                    if not checksum:
                        raise Exception(ErrorMessages.CHECKSUM_MISSING)
                    progress_cb(StaticText.VALIDATING_CHECKSUM, 0)
                    if not self.provisioner.validate_iso(custom_path, checksum):
                        raise Exception(ErrorMessages.CHECKSUM_VALIDATION_FAILED)
                    progress_cb(StaticText.CHECKSUM_VALIDATED, 10)

                # 2. Upload
                progress_cb(StaticText.UPLOADING_ISO, 10)

                def upload_progress(p):
                    progress_cb(
                        StaticText.UPLOADING_PROGRESS_TEMPLATE.format(progress=p), 10 + int(p * 0.4)
                    )

                final_iso_url = self.provisioner.upload_iso(custom_path, pool_name, upload_progress)
                if not final_iso_url:
                    raise Exception(ErrorMessages.NO_ISO_URL_SPECIFIED)

            # 3. Provision
            # Suspend global updates to prevent UI freeze during heavy provisioning ops
            self.app.call_from_thread(self.app.vm_service.suspend_global_updates)
            try:

                def show_config_modal(domain):
                    """Callback to show VM configuration in a modal."""
                    vm_name = domain.name()
                    uuid = domain.UUIDString()

                    def push_details():
                        app = self.app
                        # Close the install modal
                        self.dismiss()

                        result = app.vm_service.get_vm_details(
                            [self.uri], uuid, domain=domain, conn=self.conn
                        )
                        if result:
                            vm_info, domain_obj, conn_for_domain = result

                            def on_details_closed(res):
                                def start_and_view():
                                    try:
                                        if not domain_obj.isActive():
                                            domain_obj.create()
                                            app.call_from_thread(
                                                app.show_success_message,
                                                SuccessMessages.VM_STARTED_TEMPLATE.format(
                                                    vm_name=vm_name
                                                ),
                                            )
                                        # Launch viewer
                                        domain_name = domain_obj.name()
                                        cmd = remote_viewer_cmd(self.uri, domain_name, app.r_viewer)
                                        proc = subprocess.Popen(
                                            cmd,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL,
                                            preexec_fn=os.setsid,
                                        )
                                        logging.info(
                                            f"{app.r_viewer} started with PID {proc.pid} for {domain_name}"
                                        )
                                        app.call_from_thread(
                                            app.show_quick_message,
                                            SuccessMessages.REMOTE_VIEWER_STARTED_TEMPLATE.format(
                                                viewer=app.r_viewer, vm_name=domain_name
                                            ),
                                        )
                                    except Exception as e:
                                        logging.error(f"Failed to start VM or viewer: {e}")
                                        app.call_from_thread(
                                            app.show_error_message,
                                            ErrorMessages.FAILED_TO_START_VM_OR_VIEWER_TEMPLATE.format(
                                                error=e
                                            ),
                                        )

                                app.worker_manager.run(start_and_view, name=f"start_view_{vm_name}")

                            app.push_screen(
                                VMDetailModal(
                                    vm_name,
                                    vm_info,
                                    domain_obj,
                                    conn_for_domain,
                                    app.vm_service.invalidate_vm_state_cache,
                                ),
                                on_details_closed,
                            )
                        else:
                            app.show_error_message(
                                ErrorMessages.COULD_NOT_GET_VM_DETAILS_TEMPLATE.format(
                                    vm_name=vm_name
                                )
                            )

                    self.app.call_from_thread(push_details)

                # Build automation config if a template is selected
                automation_config = None
                if automation_template_id:
                    # Get user configuration values
                    try:
                        root_password = self.query_one("#automation-root-password", Input).value
                        hostname = self.query_one("#automation-hostname", Input).value
                        username = self.query_one("#automation-username", Input).value
                        user_password = self.query_one("#automation-user-password", Input).value
                        language = self.query_one("#automation-language", Select).value
                        keyboard = self.query_one("#automation-keyboard", Select).value

                        # Add SCC info from user config if present
                        config = load_config()
                        scc_config = config.get("SUSE_SCC", {})

                        automation_config = {
                            "template_name": automation_template_id,
                            "root_password": root_password,
                            "hostname": hostname or name,  # Default to VM name if empty
                            "username": username,
                            "user_password": user_password,
                            "language": language,
                            "keyboard": keyboard,
                            "serial_console": serial_console,  # Add serial console option
                            "scc_email": scc_config.get("scc_email", ""),
                            "scc_reg_code": scc_config.get("scc_reg_code", ""),
                            "scc_product_arch": scc_config.get("scc_product_arch", ""),
                        }
                    except Exception as e:
                        logging.warning(f"Could not retrieve automation config: {e}")
                        automation_config = {"template_name": automation_template_id}

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
                    use_virt_install=use_virt_install,
                    configure_before_install=configure_before_install,
                    show_config_modal_callback=(
                        show_config_modal if configure_before_install else None
                    ),
                    progress_callback=progress_cb,
                    automation_config=automation_config,
                )
            finally:
                self.app.call_from_thread(self.app.vm_service.resume_global_updates)
                self.app.call_from_thread(self.app.vm_service.invalidate_domain_cache)
                # Manually trigger a refresh as we suppressed the events
                self.app.call_from_thread(self.app.on_vm_data_update)

            if configure_before_install:
                self.app.call_from_thread(
                    self.app.show_success_message,
                    SuccessMessages.VM_DEFINED_CONFIGURE_TEMPLATE.format(vm_name=name),
                )
                return

            self.app.call_from_thread(
                self.app.show_success_message,
                SuccessMessages.VM_CREATED_SUCCESSFULLY_TEMPLATE.format(vm_name=name),
            )

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
                    )
                    logging.info(
                        f"{self.app.r_viewer} started with PID {proc.pid} for {domain_name}"
                    )
                    self.app.show_quick_message(
                        SuccessMessages.REMOTE_VIEWER_STARTED_TEMPLATE.format(
                            viewer=self.app.r_viewer, vm_name=domain_name
                        )
                    )
                except Exception as e:
                    logging.error(f"Failed to spawn {self.app.r_viewer} for {domain_name}: {e}")
                    self.app.call_from_thread(
                        self.app.show_error_message,
                        ErrorMessages.REMOTE_VIEWER_FAILED_TO_START_TEMPLATE.format(
                            viewer=self.app.r_viewer, domain_name=domain_name, error=e
                        ),
                    )
                    return

            self.app.call_from_thread(launch_viewer)
            self.app.call_from_thread(self.dismiss, True)

        except Exception as e:
            self.app.call_from_thread(
                self.app.show_error_message,
                ErrorMessages.PROVISIONING_FAILED_TEMPLATE.format(error=e),
            )
            self.app.call_from_thread(self.dismiss)
