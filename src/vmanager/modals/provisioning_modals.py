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
    Modal for creating and provisioning a new VM supporting multiple OS types.
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
        active_pools = [(p["name"], p["name"]) for p in pools if p["status"] == "active"]
        default_pool = (
            "default"
            if any(p[0] == "default" for p in active_pools)
            else (active_pools[0][1] if active_pools else None)
        )

        with ScrollableContainer(id="install-dialog"):
            yield Label(StaticText.INSTALL_VM.format(uri=self.uri), classes="title")
            yield Label(StaticText.VM_NAME, classes="label")
            yield Input(placeholder="my-new-vm", id="vm-name")

            yield Label(StaticText.VM_TYPE, classes="label")
            with Horizontal(classes="label-row"):
                yield Select(
                    [(t.value, t) for t in VMType],
                    value=VMType.DESKTOP,
                    id="vm-type",
                    allow_blank=False,
                )
                yield Button(ButtonLabels.INFO, id="vm-type-info-btn")

            with Vertical(classes="label-row"):
                # yield Label("Operating System", classes="label")
                with Horizontal():
                    with Vertical(id="os-type-container"):
                        os_options = [
                            ("OpenSUSE Linux", "opensuse"),
                            ("Windows", "windows"),
                            ("Custom", "custom"),
                        ]
                        yield Select(os_options, value="opensuse", id="os-type", allow_blank=False)
                    with Vertical(id="os-version-containers"):
                        # Container for Windows version selection
                        with Vertical(id="windows-version-container"):
                            # yield Label("Windows Version", classes="label")
                            yield Select(
                                [
                                    ("Windows 10 Enterprise", "10"),
                                    ("Windows 11 Enterprise", "11"),
                                    ("Windows Server 2019", "2019"),
                                    ("Windows Server 2022", "2022"),
                                ],
                                value="10",
                                id="windows-version",
                                allow_blank=False,
                            )
                        # Container for OpenSUSE version selection
                        with Vertical(id="opensuse-version-container"):
                            # yield Label("OpenSUSE Version", classes="label")
                            yield Select(
                                [
                                    ("Leap 15.6", "leap-15.6"),
                                    ("Leap 16.0", "leap-16.0"),
                                    (OpenSUSEDistro.TUMBLEWEED.value, OpenSUSEDistro.TUMBLEWEED),
                                    (OpenSUSEDistro.SLOWROLL.value, OpenSUSEDistro.SLOWROLL),
                                    (OpenSUSEDistro.STABLE.value, OpenSUSEDistro.STABLE),
                                    (OpenSUSEDistro.CURRENT.value, OpenSUSEDistro.CURRENT),
                                ],
                                value="leap-15.6",
                                id="opensuse-version",
                                allow_blank=False,
                            )
                        # Container for Custom repositories (custom repos from config + cached ISOs)
                        with Vertical(id="custom-repos-container"):
                            # yield Label("Custom Repository", classes="label", id="custom-repos-label")
                            custom_repos = self.provisioner.get_custom_repos()
                            custom_repo_options = [
                                (StaticText.CACHED_ISOS, "cached")
                            ]  # Start with cached ISOs
                            for repo in custom_repos:
                                # Use URI as value, Name as label
                                name = repo.get("name", repo["uri"])
                                uri = repo["uri"]
                                custom_repo_options.append((name, uri))
                            yield Select(custom_repo_options, id="custom-repos", allow_blank=False)

            yield Label(StaticText.DISTRIBUTION, classes="label", id="distribution-label")
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

            yield Select(distro_options, id="distro", allow_blank=True)

            # Container for ISO selection (Repo)
            with Vertical(id="repo-iso-container"):
                # yield Label(StaticText.ISO_IMAGE_REPO, classes="label")
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
                yield Label(StaticText.SELECT_STORAGE_POOL)
                yield Select(
                    active_pools,
                    prompt=StaticText.SELECT_POOL_PROMPT,
                    id="storage-pool-select",
                    allow_blank=False,
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
                            tooltip=StaticText.DISK_FORMAT_TOOLTIP,
                        )
                    with Vertical(id="expert-firmware"):
                        yield Label(StaticText.FIRMWARE_LABEL, classes="label")
                        yield Checkbox(
                            "UEFI",
                            id="boot-uefi-checkbox",
                            value=True,
                            tooltip=StaticText.LEGACY_BOOT_TOOLTIP,
                        )

            # Automated Installation (AutoYaST) Configuration
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
                            [("None", None)],  # Default: no automation
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
        # Initial state - show OpenSUSE containers, hide Windows and Custom
        self.query_one("#windows-version-container").styles.display = "none"
        self.query_one(
            "#opensuse-version-container"
        ).styles.display = "block"  # Show for default OpenSUSE selection
        self.query_one("#custom-repos-container").styles.display = "none"
        self.query_one("#custom-iso-container").styles.display = "none"
        self.query_one(
            "#repo-iso-container"
        ).styles.display = "block"  # Show for OpenSUSE by default
        self.query_one("#pool-iso-container").styles.display = "none"

        # Show automation template container and user config (always visible now)
        self.query_one("#automation-template-container").styles.display = "block"
        self.query_one(
            "#automation-user-config-wrapper"
        ).styles.display = "none"  # Hidden until template selected

        # Hide the old Distribution field (we'll keep it for backwards compatibility but hide it)
        self.query_one("#distribution-label").styles.display = "none"
        self.query_one("#distro", Select).styles.display = "none"

        # Ensure expert defaults are set correctly based on initial selection
        self._update_expert_defaults(self.query_one("#vm-type", Select).value)

        # Populate initial storage pool volumes if "From Storage Pool" is default
        storage_pool_select = self.query_one("#storage-pool-select", Select)
        if storage_pool_select.value:
            self.fetch_pool_isos(storage_pool_select.value)

        # Trigger initial OpenSUSE version selection to load ISOs
        # Use call_later to ensure all widgets are fully initialized
        self.call_later(self._load_initial_opensuse_isos)

        # Load AutoYaST templates on mount
        self.call_later(self._load_automation_templates)

    def _load_initial_opensuse_isos(self):
        """Load ISOs for the initial OpenSUSE version selection."""
        try:
            logging.info("_load_initial_opensuse_isos() called")
            opensuse_version_select = self.query_one("#opensuse-version", Select)
            logging.info(f"OpenSUSE version select widget: {opensuse_version_select}")
            logging.info(f"OpenSUSE version select value: {opensuse_version_select.value}")

            if opensuse_version_select.value:
                logging.info(
                    f"Loading initial ISOs for OpenSUSE version: {opensuse_version_select.value}"
                )
                self.fetch_isos(opensuse_version_select.value)
            else:
                logging.warning("No OpenSUSE version selected for initial ISO loading")
                # Try to set a default value
                if hasattr(opensuse_version_select, "options") and opensuse_version_select.options:
                    first_option = opensuse_version_select.options[0][
                        1
                    ]  # Get the value of first option
                    logging.info(f"Setting default OpenSUSE version to: {first_option}")
                    opensuse_version_select.value = first_option
                    self.fetch_isos(first_option)
        except Exception as e:
            logging.error(f"Error in _load_initial_opensuse_isos: {e}")
            import traceback

            logging.error(f"Traceback: {traceback.format_exc()}")

    def _load_automation_templates(self):
        """Load available AutoYaST templates from the OpenSUSE provider."""
        try:
            logging.info("Loading AutoYaST templates...")

            # Try to get the template select widget first - if it doesn't exist, skip loading
            try:
                template_select = self.query_one("#automation-template-select", Select)
                logging.info("Found automation template select widget")
            except Exception as widget_error:
                logging.warning(f"Automation template select widget not found: {widget_error}")
                return  # Exit early if widget doesn't exist

            # Start with "None" option (no automation)
            template_options = [("None", None)]

            # Get the OpenSUSE provider
            provider = self.provisioner.get_provider("opensuse")
            if provider:
                logging.info("OpenSUSE provider found")

                # Try to get available templates (using getattr to avoid type checking issues)
                get_templates_method = getattr(provider, "get_available_templates", None)
                if get_templates_method:
                    logging.info("get_available_templates method found")
                    templates = get_templates_method()
                    logging.info(f"Found {len(templates)} templates")

                    # Convert to Select options format: (display_name, identifier)
                    # Use template_id for user templates (full path), filename for built-in
                    for template in templates:
                        identifier = template.get("template_id", template["filename"])
                        template_options.append((template["display_name"], identifier))
                        logging.info(f"  - {template['display_name']}: {identifier}")

                else:
                    logging.warning(
                        "OpenSUSE provider doesn't support template scanning (no get_available_templates method)"
                    )

            else:
                logging.warning("OpenSUSE provider not available")

            # Always set options (at minimum, "None" will be available)
            logging.info(f"Setting template options with {len(template_options)} options...")
            template_select.set_options(template_options)
            # Set default to "None" (first option)
            template_select.value = None
            logging.info(
                f"Template select updated with {len(template_options)} options, default: None"
            )

        except Exception as e:
            logging.error(f"Error loading automation templates: {e}")
            import traceback

            logging.error(f"Traceback: {traceback.format_exc()}")
            # Set fallback options (just "None")
            try:
                template_select = self.query_one("#automation-template-select", Select)
                fallback_options = [("None", None)]
                template_select.set_options(fallback_options)
                template_select.value = None
                logging.info("Set fallback options after error")
            except Exception as widget_error:
                logging.error(f"Failed to set fallback options: {widget_error}")
                # If we can't even set fallback options, the widget doesn't exist yet

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
        self.query_one("#boot-uefi-checkbox", Checkbox).value = boot_uefi

    @on(Select.Changed, "#vm-type")
    def on_vm_type_changed(self, event: Select.Changed):
        self._update_expert_defaults(event.value)

    @on(Select.Changed, "#os-type")
    def on_os_type_changed(self, event: Select.Changed):
        """Handle OS type selection (opensuse vs windows vs custom)."""
        os_type = event.value

        # Hide all containers first
        self.query_one("#windows-version-container").styles.display = "none"
        self.query_one("#opensuse-version-container").styles.display = "none"
        self.query_one("#custom-repos-container").styles.display = "none"
        self.query_one("#repo-iso-container").styles.display = "none"
        self.query_one("#custom-iso-container").styles.display = "none"
        self.query_one("#pool-iso-container").styles.display = "none"

        # Keep Distribution field hidden (it's legacy)
        self.query_one("#distribution-label").styles.display = "none"
        self.query_one("#distro", Select).styles.display = "none"

        # Show/hide automation collapsible - only for OpenSUSE
        automation_collapsible = self.query_one("#automation-collapsible", Collapsible)
        if os_type == "opensuse":
            automation_collapsible.styles.display = "block"
        else:
            automation_collapsible.styles.display = "none"
            # Reset automation template to "None" when switching away from OpenSUSE
            self.query_one("#automation-template-select", Select).value = None
            self.query_one("#automation-user-config-wrapper").styles.display = "none"

        if os_type == "windows":
            # Show Windows version selector
            self.query_one("#windows-version-container").styles.display = "block"
            # Update VM type defaults for Windows
            vm_type = self.query_one("#vm-type", Select)
            if vm_type.value in [VMType.DESKTOP]:
                vm_type.value = VMType.WDESKTOP
            self._update_expert_defaults(vm_type.value)
        elif os_type == "opensuse":
            # Show OpenSUSE version selector
            self.query_one("#opensuse-version-container").styles.display = "block"
            # Trigger OpenSUSE version selection to load ISOs
            opensuse_version_select = self.query_one("#opensuse-version", Select)
            if opensuse_version_select.value:
                self.on_opensuse_version_changed(
                    Select.Changed(opensuse_version_select, opensuse_version_select.value)
                )
            # Update VM type defaults for Linux
            vm_type = self.query_one("#vm-type", Select)
            if vm_type.value in [VMType.WDESKTOP, VMType.WLDESKTOP]:
                vm_type.value = VMType.DESKTOP
            self._update_expert_defaults(vm_type.value)
        elif os_type == "custom":
            # Show Custom repositories selector (includes Cached ISOs option)
            self.query_one("#custom-repos-container").styles.display = "block"
            # Show repo ISO container to display ISOs
            self.query_one("#repo-iso-container").styles.display = "block"

            # Trigger custom repo selection (will be "cached" by default)
            custom_repos_select = self.query_one("#custom-repos", Select)
            if custom_repos_select.value:
                self.fetch_isos(custom_repos_select.value)

            # Update VM type defaults for Linux
            vm_type = self.query_one("#vm-type", Select)
            if vm_type.value in [VMType.WDESKTOP, VMType.WLDESKTOP]:
                vm_type.value = VMType.DESKTOP
            self._update_expert_defaults(vm_type.value)

        self._check_form_validity()

    @on(Select.Changed, "#opensuse-version")
    def on_opensuse_version_changed(self, event: Select.Changed):
        """Handle OpenSUSE version selection."""
        opensuse_version = event.value

        # Hide all ISO source containers first
        self.query_one("#repo-iso-container").styles.display = "none"
        self.query_one("#custom-iso-container").styles.display = "none"
        self.query_one("#pool-iso-container").styles.display = "none"

        if opensuse_version:
            # Show repo ISO container and fetch ISOs for this OpenSUSE version
            self.query_one("#repo-iso-container").styles.display = "block"
            self.fetch_isos(opensuse_version)

        self._check_form_validity()

    @on(Select.Changed, "#custom-repos")
    def on_custom_repo_changed(self, event: Select.Changed):
        """Handle custom repository selection (includes cached ISOs)."""
        custom_repo_uri = event.value

        # Hide all ISO source containers first
        self.query_one("#repo-iso-container").styles.display = "none"
        self.query_one("#custom-iso-container").styles.display = "none"
        self.query_one("#pool-iso-container").styles.display = "none"

        if custom_repo_uri:
            # Show repo ISO container and fetch ISOs (from custom repo or cached)
            self.query_one("#repo-iso-container").styles.display = "block"
            self.fetch_isos(custom_repo_uri)

        self._check_form_validity()

    @on(Select.Changed, "#windows-version")
    def on_windows_version_changed(self, event: Select.Changed):
        """Handle Windows version selection."""
        self._check_form_validity()

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
            if pool_select.value:
                self.fetch_pool_isos(pool_select.value)
            else:
                self.query_one(
                    "#iso-volume-select", Select
                ).clear()  # No pool selected, clear volumes
        else:  # Repo or Cached
            self.query_one("#repo-iso-container").styles.display = "block"
            self.fetch_isos(event.value)
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
        if event.value:
            self.fetch_pool_isos(event.value)
        else:
            self.query_one("#iso-volume-select", Select).clear()  # No pool selected, clear volumes
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
            # Debug logging
            logging.info(f"Fetching ISOs for distro: {distro}, type: {type(distro)}")

            # Check if provisioner is available
            if not self.provisioner:
                raise Exception("Provisioner is not initialized")

            if distro == "cached":
                logging.info("Fetching cached ISOs")
                isos = self.provisioner.get_cached_isos()
                logging.info(f"get_cached_isos() returned: {isos}")
            elif isinstance(distro, str) and distro.startswith("leap-"):
                # Handle specific Leap versions
                leap_version = distro.replace("leap-", "")
                # Leap 16.x uses /offline/, older versions use /iso/
                if leap_version.startswith("16."):
                    leap_path = "offline"
                else:
                    leap_path = "iso"
                leap_url = (
                    f"https://download.opensuse.org/distribution/leap/{leap_version}/{leap_path}/"
                )
                logging.info(f"Fetching ISOs for Leap {leap_version} from URL: {leap_url}")
                isos = self.provisioner.get_iso_list_from_url(leap_url)
                logging.info(f"get_iso_list_from_url({leap_url}) returned: {isos}")
            else:
                logging.info(f"Calling provisioner.get_iso_list({distro})")
                isos = self.provisioner.get_iso_list(distro)
                logging.info(f"get_iso_list({distro}) returned: {isos}")

            # Debug logging
            logging.info(f"Retrieved {len(isos)} ISOs for {distro}")

            # Remove test ISOs since real fetching is working
            # TEMPORARY: Add test ISOs if none found (for debugging UI)
            # if not isos and isinstance(distro, OpenSUSEDistro):
            #     logging.warning(f"No ISOs found for {distro}, adding test ISOs for debugging")
            #     isos = [
            #         {
            #             "name": f"Test-{distro.value}-DVD-x86_64-Current.iso",
            #             "url": f"https://example.com/test-{distro.value.lower()}.iso",
            #             "date": "2024-01-01"
            #         },
            #         {
            #             "name": f"Test-{distro.value}-NET-x86_64-Current.iso",
            #             "url": f"https://example.com/test-{distro.value.lower()}-net.iso",
            #             "date": "2024-01-01"
            #         }
            #     ]
            #     logging.info(f"Added test ISOs: {isos}")

            # Create Select options: (label, url)
            iso_options = []
            for i, iso in enumerate(isos):
                try:
                    logging.info(f"Processing ISO {i}: {iso}")
                    name = iso.get("name", "Unknown")
                    url = iso.get("url", "")
                    date = iso.get("date", "")

                    if not url:
                        logging.warning(f"Skipping ISO with empty URL: {iso}")
                        continue

                    label = f"{name} ({date})" if date else name
                    iso_options.append((label, url))
                    logging.info(f"Added ISO option: {label} -> {url}")
                except Exception as e:
                    logging.error(f"Error processing ISO {i}: {iso}, error: {e}")
                    continue

            logging.info(f"Created {len(iso_options)} ISO options")

            def update_select():
                sel = self.query_one("#iso-select", Select)
                logging.info(f"Updating ISO select with {len(iso_options)} options")
                sel.set_options(iso_options)
                sel.disabled = False
                if iso_options:
                    sel.value = iso_options[0][1]  # Select first by default
                    logging.info(f"Set default ISO to: {iso_options[0][1]}")
                else:
                    sel.clear()  # No options, clear any previous value
                    logging.info("No ISO options available, cleared select")
                self._update_iso_status("", False)
                self._check_form_validity()  # Re-check validity after options change

            self.app.call_from_thread(update_select)

        except Exception as e:
            logging.error(f"Error fetching ISOs for {distro}: {e}")
            import traceback

            logging.error(f"Traceback: {traceback.format_exc()}")

            self.app.call_from_thread(
                self.app.show_error_message,
                ErrorMessages.FAILED_TO_FETCH_ISOS_TEMPLATE.format(error=e),
            )
            self.app.call_from_thread(
                self._update_iso_status, StaticText.ERROR_FETCHING_ISOS, False
            )
            self.app.call_from_thread(
                self._update_iso_status, StaticText.ERROR_FETCHING_ISOS, False
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

    @on(Select.Changed, "#iso-select")
    def on_iso_selected(self, event: Select.Changed):
        self._check_form_validity()

    @on(Input.Changed, "#vm-name")
    def on_name_changed(self):
        self._check_form_validity()

    @on(Select.Changed, "#automation-template-select")
    def on_automation_template_changed(self, event: Select.Changed):
        """Handle automation template selection changes."""
        template_value = event.value

        # If "None" is selected, disable and hide user config
        # Otherwise, enable and show user config
        automation_enabled = template_value is not None

        # Enable/disable automation controls
        self.query_one("#automation-root-password", Input).disabled = not automation_enabled
        self.query_one("#automation-username", Input).disabled = not automation_enabled
        self.query_one("#automation-user-password", Input).disabled = not automation_enabled
        self.query_one("#automation-hostname", Input).disabled = not automation_enabled
        self.query_one("#automation-language", Select).disabled = not automation_enabled
        self.query_one("#automation-keyboard", Select).disabled = not automation_enabled

        # Show/hide automation user config
        if automation_enabled:
            self.query_one("#automation-user-config-wrapper").styles.display = "block"

            # Set default hostname from VM name if not already set
            vm_name = self.query_one("#vm-name", Input).value.strip()
            if vm_name and not self.query_one("#automation-hostname", Input).value:
                self.query_one("#automation-hostname", Input).value = vm_name
        else:
            self.query_one("#automation-user-config-wrapper").styles.display = "none"

        self._check_form_validity()

    def _edit_template_with_editor(self, selected_template_filename, is_new=False):
        """Edit a template using the system editor via TemplateManager."""
        template_manager = self.app.autoyast_template_manager
        template_manager.provisioner = self.provisioner  # Set provisioner for validation

        # Prepare template data
        template_content = ""
        template_name = ""
        template_description = ""
        template_id = None
        is_user_template = False

        if not is_new and selected_template_filename:
            # Get template from TemplateManager
            templates = template_manager.get_all_templates()
            selected_template = None

            for template in templates:
                if template["filename"] == selected_template_filename:
                    selected_template = template
                    break

            if not selected_template:
                self.notify("Selected template not found", severity="error")
                return

            template_name = selected_template["display_name"]
            template_description = selected_template.get("description", "")

            if selected_template.get("type") == "user":
                # User template - edit existing
                template_content = selected_template.get("content", "")
                template_id = selected_template.get("template_id")
                is_user_template = True
                # Remove "(User)" suffix from name for editing
                template_name = template_name.replace(" (User)", "")
            else:
                # Built-in template - create new based on this one
                template_content = (
                    template_manager.get_template_content(selected_template_filename) or ""
                )
                template_name = f"Custom {template_name}"
                template_description = f"Based on {selected_template['display_name']}"
                is_user_template = False
        else:
            # Creating new template from scratch
            template_name = "My Custom Template"
            template_description = "Custom AutoYaST template"
            template_content = template_manager.get_skeleton_template()

        # Define callbacks for the editor
        def on_save(edited_content: str):
            """Handle saved template content."""
            # Validate XML first
            is_valid, error = template_manager.validate_xml(edited_content)
            if not is_valid:
                self.notify(f"Invalid XML: {error}", severity="error")
                return

            # Save the template
            if is_user_template and template_id:
                success, _ = template_manager.save_template(
                    template_name, edited_content, template_description, template_id
                )
                action = "updated"
            else:
                success, new_id = template_manager.save_template(
                    template_name, edited_content, template_description
                )
                action = "created"

            if success:
                self.notify(f"Template '{template_name}' {action} successfully!")
                # Reload templates immediately
                self._load_automation_templates()
                # Do comprehensive validation in background
                self._validate_template_async(edited_content, template_name)
            else:
                self.notify("Error saving template", severity="error")

        def on_cancel():
            """Handle cancelled edit."""
            self.notify("Template unchanged", severity="information")

        def on_error(error_msg: str):
            """Handle editor error."""
            self.notify(error_msg, severity="error")

        # Launch editor via TemplateManager
        template_manager.edit_template_in_tmux(
            template_content,
            on_save=on_save,
            on_cancel=on_cancel,
            on_error=on_error,
        )

    @work(exclusive=False, thread=True)
    def _validate_template_async(self, template_content: str, template_name: str):
        """Validate template content asynchronously in background."""
        try:
            template_manager = self.app.autoyast_template_manager
            template_manager.provisioner = self.provisioner

            validation_result = template_manager.validate_template(template_content)

            def show_validation_results():
                if not validation_result["valid"]:
                    self.notify(
                        f"Template '{template_name}' has validation issues: {validation_result['error']}",
                        severity="warning",
                    )
                elif validation_result.get("warnings"):
                    warning_count = len(validation_result["warnings"])
                    self.notify(
                        f"Template '{template_name}' saved with {warning_count} warning(s)",
                        severity="information",
                    )
                    if validation_result["warnings"]:
                        self.notify(
                            f"Warning: {validation_result['warnings'][0]}",
                            severity="information",
                        )
                else:
                    self.notify(
                        f"Template '{template_name}' validation complete",
                        severity="information",
                    )

            self.app.call_from_thread(show_validation_results)

        except Exception as e:
            logging.error(f"Error in async template validation: {e}")

            def show_validation_error():
                self.notify(
                    f"Template '{template_name}' saved but validation failed",
                    severity="warning",
                )

            self.app.call_from_thread(show_validation_error)

    @on(Button.Pressed, "#manage-templates-btn")
    def manage_templates(self, event: Button.Pressed):
        """Open the template management modal."""
        try:
            template_manager = self.app.autoyast_template_manager

            # Open template management modal
            def on_dismiss(result: bool | None):
                """Handle modal dismissal - reload templates if needed."""
                if result:
                    # Reload templates in the select widget
                    self._load_automation_templates()

            self.app.push_screen(TemplateManagementModal(template_manager), on_dismiss)

        except Exception as e:
            logging.error(f"Error opening template management: {e}")
            self.notify("Error opening template management", severity="error")

    @on(Input.Changed, "#automation-root-password")
    def on_automation_root_password_changed(self):
        self._check_form_validity()

    @on(Input.Changed, "#automation-username")
    def on_automation_username_changed(self):
        self._check_form_validity()

    @on(Input.Changed, "#automation-user-password")
    def on_automation_user_password_changed(self):
        self._check_form_validity()

    @on(Input.Changed, "#automation-hostname")
    def on_automation_hostname_changed(self):
        self._check_form_validity()

    def _check_form_validity(self):
        name = self.query_one("#vm-name", Input).value.strip()
        os_type = self.query_one("#os-type", Select).value

        valid_config = False

        if os_type == "windows":
            # For Windows, just need a version selected
            windows_version = self.query_one("#windows-version", Select).value
            valid_config = bool(windows_version and windows_version != Select.BLANK)
        elif os_type == "opensuse":
            # For OpenSUSE, need version selected and ISO selected
            opensuse_version = self.query_one("#opensuse-version", Select).value
            if opensuse_version and opensuse_version != Select.BLANK:
                iso = self.query_one("#iso-select", Select).value
                valid_config = iso and iso != Select.BLANK
        elif os_type == "custom":
            # For Custom, need custom repo selected and ISO selected
            custom_repo = self.query_one("#custom-repos", Select).value
            if custom_repo and custom_repo != Select.BLANK:
                iso = self.query_one("#iso-select", Select).value
                valid_config = iso and iso != Select.BLANK

        # Check automation requirements if automation is enabled and OS is OpenSUSE
        automation_valid = True
        if os_type == "opensuse":
            # Check if a template is selected (not None)
            template = self.query_one("#automation-template-select", Select).value
            automation_enabled = template is not None

            if automation_enabled:
                # Require all automation fields to be filled
                root_password = self.query_one("#automation-root-password", Input).value.strip()
                username = self.query_one("#automation-username", Input).value.strip()
                user_password = self.query_one("#automation-user-password", Input).value.strip()
                hostname = self.query_one("#automation-hostname", Input).value.strip()

                automation_valid = bool(
                    root_password and username and user_password and hostname and template
                )

        btn = self.query_one("#install-btn", Button)
        if name and valid_config and automation_valid:
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
        os_type = self.query_one("#os-type", Select).value
        configure_before_install = self.query_one(
            "#configure-before-install-checkbox", Checkbox
        ).value

        # Validate storage pool
        if not pool_name or pool_name == Select.BLANK:
            self.app.show_error_message(ErrorMessages.PLEASE_SELECT_VALID_STORAGE_POOL)
            return

        # Handle OS-specific configuration
        iso_url = None
        custom_path = None
        checksum = None
        validate = False
        windows_version = None
        automation_config = None

        if os_type == "windows":
            # Windows VM configuration
            windows_version = self.query_one("#windows-version", Select).value
            if not windows_version or windows_version == Select.BLANK:
                self.app.show_error_message("Please select a Windows version")
                return
        elif os_type == "opensuse":
            # OpenSUSE configuration - get selected ISO from the opensuse version selection
            opensuse_version = self.query_one("#opensuse-version", Select).value
            iso_url = self.query_one("#iso-select", Select).value
            if not opensuse_version or opensuse_version == Select.BLANK:
                self.app.show_error_message("Please select an OpenSUSE version")
                return
            if not iso_url or iso_url == Select.BLANK:
                self.app.show_error_message("Please select an OpenSUSE ISO")
                return

            # Check if automation is enabled (template selected)
            template_name = self.query_one("#automation-template-select", Select).value
            automation_enabled = template_name is not None

            if automation_enabled:
                # Collect automation configuration
                root_password = self.query_one("#automation-root-password", Input).value.strip()
                username = self.query_one("#automation-username", Input).value.strip()
                user_password = self.query_one("#automation-user-password", Input).value.strip()
                hostname = self.query_one("#automation-hostname", Input).value.strip()

                # Validate automation fields (should already be validated by form validation)
                if not all([template_name, root_password, username, user_password, hostname]):
                    self.app.show_error_message(
                        "All automation fields are required when automation is enabled"
                    )
                    return

                # Get language and keyboard selections
                language = self.query_one("#automation-language", Select).value
                keyboard = self.query_one("#automation-keyboard", Select).value

                automation_config = {
                    "template_name": template_name,
                    "root_password": root_password,
                    "user_name": username,
                    "user_password": user_password,
                    "hostname": hostname,
                    "language": language,
                    "keyboard": keyboard,
                    "timezone": "UTC",
                }

        elif os_type == "custom":
            # Custom repository configuration
            custom_repo = self.query_one("#custom-repos", Select).value
            iso_url = self.query_one("#iso-select", Select).value
            if not custom_repo or custom_repo == Select.BLANK:
                self.app.show_error_message("Please select a custom repository")
                return
            if not iso_url or iso_url == Select.BLANK:
                self.app.show_error_message("Please select an ISO from the custom repository")
                return

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
            os_type,
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
            configure_before_install,
            windows_version,
            automation_config,
        )

    @work(exclusive=True, thread=True)
    def run_provisioning(
        self,
        name,
        vm_type,
        os_type,
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
        configure_before_install,
        windows_version=None,
        automation_config=None,
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

                # Call appropriate provisioning method based on OS type
                if os_type == "windows":
                    dom = self.provisioner.provision_windows_vm(
                        vm_name=name,
                        vm_type=vm_type,
                        windows_version=windows_version,
                        storage_pool_name=pool_name,
                        memory_mb=memory_mb,
                        vcpu=vcpu,
                        disk_size_gb=disk_size,
                        disk_format=disk_format,
                        boot_uefi=boot_uefi,
                        configure_before_install=configure_before_install,
                        show_config_modal_callback=(
                            show_config_modal if configure_before_install else None
                        ),
                        progress_callback=progress_cb,
                    )
                else:
                    # Both OpenSUSE and Custom use the same provisioning method with ISO selection
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
