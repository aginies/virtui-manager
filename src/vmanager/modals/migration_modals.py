"""
Modals for handling VM migration.
"""
import threading
from typing import Dict, List

import libvirt
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, ProgressBar, Select, Static

from ..constants import ButtonLabels, ErrorMessages, StaticText
from ..storage_manager import find_shared_storage_pools
from ..utils import extract_server_name_from_uri
from ..vm_actions import check_server_migration_compatibility, check_vm_migration_compatibility
from ..vm_migration import custom_migrate_vm, execute_custom_migration
from .custom_migration_modal import CustomMigrationModal

#from pprint import pprint # pprint(vars(object))

class MigrationModal(ModalScreen):
    """A modal to handle VM migration."""

    BINDINGS = [("escape", "app.pop_screen", "Cancel")]

    def __init__(self, vms: List[libvirt.virDomain], is_live: bool, connections: Dict[str, libvirt.virConnect], **kwargs):
        super().__init__(**kwargs)
        self.vms_to_migrate = vms
        self.is_live = is_live
        self.connections = connections
        self.source_conn = vms[0].connect()
        self.dest_conn = None
        self.dest_uri = None
        self.compatibility_checked = False
        self.checks_passed = False
        self.log_content = ""
        self.can_migrate_vms = []
        self.cannot_migrate_vms = []

    def compose(self) -> ComposeResult:
        vm_names = ", ".join([vm.name() for vm in self.vms_to_migrate])
        source_uri = self.source_conn.getURI()

        try:
            source_hostname = self.source_conn.getHostname()
        except libvirt.libvirtError:
            source_hostname = None # Can't get hostname, will have to rely on URI

        dest_servers = []
        for uri, conn in self.connections.items():
            if uri == source_uri:
                continue

            if source_hostname:
                try:
                    dest_hostname = conn.getHostname()
                    if dest_hostname == source_hostname:
                        continue # It's the same host, skip it
                except libvirt.libvirtError:
                    # Could not get hostname for destination, can't compare.
                    # We will let it be in the list and let libvirt fail if it's the same.
                    # This preserves existing behavior for problematic connections.
                    pass

            dest_servers.append((extract_server_name_from_uri(uri), uri))

        migration_type = "Live" if self.is_live else "Offline"

        default_dest_uri = None
        if dest_servers:
            default_dest_uri = dest_servers[0][1]
            self.dest_conn = self.connections[default_dest_uri]
            self.dest_uri = default_dest_uri

        with Vertical(id="migration-dialog",):
            with Vertical(id="migration-content-wrapper"):
                yield Label(StaticText.MIGRATE_VMS_TITLE.format(migration_type=migration_type, vm_names=vm_names))
                yield Static(StaticText.SELECT_DESTINATION_SERVER)
                yield Select(dest_servers, id="dest-server-select", prompt="Destination...", value=default_dest_uri, allow_blank=False)

                yield Static(StaticText.MIGRATION_OPTIONS)
                with Horizontal(classes="checkbox-container"):
                    yield Checkbox(StaticText.COPY_STORAGE_ALL, id="copy-storage-all", tooltip=StaticText.COPY_ALL_DISK_FILES_TOOLTIP, value=False)
                    yield Checkbox(StaticText.UNSAFE_MIGRATION, id="unsafe", tooltip=StaticText.PERFORM_UNSAFE_MIGRATION_TOOLTIP, disabled=not self.is_live)
                    yield Checkbox(StaticText.PERSISTENT_MIGRATION, id="persistent", tooltip=StaticText.KEEP_VM_PERSISTENT_ON_DESTINATION_TOOLTIP, value=True)
                with Horizontal(classes="checkbox-container"):
                    yield Checkbox(StaticText.COMPRESS_DATA, id="compress", tooltip=StaticText.COMPRESS_DATA_DURING_MIGRATION_TOOLTIP, disabled=not self.is_live)
                    yield Checkbox(StaticText.TUNNELLED_MIGRATION, id="tunnelled", tooltip=StaticText.TUNNEL_MIGRATION_DATA_TOOLTIP, disabled=not self.is_live)
                    yield Checkbox(StaticText.CUSTOM_MIGRATION, id="custom", tooltip=StaticText.USE_CUSTOM_MIGRATION_WORKFLOW_TOOLTIP, value=False)
                yield Static(StaticText.COMPATIBILITY_CHECK_RESULTS)
                yield ProgressBar(total=100, show_eta=False, id="migration-progress")
                yield Static(id="results-log")
                yield Grid(
                    ScrollableContainer(
                        Static(StaticText.VMS_READY_FOR_MIGRATION, classes="summary-title"),
                        Static(id="can-migrate-list"),
                    ),
                    ScrollableContainer(
                        Static(StaticText.VMS_NOT_READY_FOR_MIGRATION, classes="summary-title"),
                        Static(id="cannot-migrate-list"),
                    ),
                    id="migration-summary-grid"
                )

            with Horizontal(classes="modal-buttons"):
                yield Button(ButtonLabels.CHECK_COMPATIBILITY, variant="primary", id="check", classes="Buttonpage")
                yield Button(ButtonLabels.START_MIGRATION, variant="success", id="start", disabled=True, classes="Buttonpage")
                yield Button(ButtonLabels.CLOSE, variant="default", id="close", disabled=False, classes="close-button")


    def _lock_controls(self, lock: bool):
        self.query_one("#check").disabled = lock
        self.query_one("#start").disabled = True
        self.query_one("#dest-server-select").disabled = lock
        self.query_one("#close").disabled = lock

    def on_mount(self) -> None:
        """Called when the modal is mounted."""
        log = self.query_one("#results-log", Static)
        log.wrap = True
        self.query_one("#migration-progress").styles.display = "none"
        self.query_one("#migration-summary-grid").styles.display = "none"


    @on(Select.Changed, "#dest-server-select")
    def on_select_changed(self, event: Select.Changed):
        dest_uri = event.value
        if dest_uri:
            self.dest_conn = self.connections[dest_uri]
            self.dest_uri = dest_uri
        else:
            self.dest_conn = None
            self.dest_uri = None

        self.query_one("#start", Button).disabled = True
        self.compatibility_checked = False
        self.checks_passed = False
        self._clear_log()

    def _clear_log(self):
        self.log_content = ""
        self.query_one("#results-log", Static).update("")
        self.can_migrate_vms = []
        self.cannot_migrate_vms = []
        self.query_one("#can-migrate-list").update("")
        self.query_one("#cannot-migrate-list").update("")

    def _write_log_line(self, line: str):
        self.log_content += line + "\n"
        self.query_one("#results-log", Static).update(self.log_content)

    @work(exclusive=True, thread=True)
    def run_compatibility_checks(self):
        self.app.call_from_thread(self._lock_controls, True)

        def write_log(line):
            if threading.current_thread() is threading.main_thread():
                self._write_log_line(line)
            else:
                self.app.call_from_thread(self._write_log_line, line)

        all_checks_ok = True
        shared_pools = find_shared_storage_pools(self.source_conn, self.dest_conn)
        write_log(StaticText.SHARED_STORAGE_POOLS_HEADER)
        if not shared_pools:
            write_log(StaticText.NO_SHARED_STORAGE_INFO)
        else:
            for pool in shared_pools:
                write_log(StaticText.SHARED_POOL_INFO_TEMPLATE.format(name=pool['name'], pool_type=pool.get('type', 'N/A')))
                write_log(StaticText.POOL_TARGET_INFO.format(target=pool.get('target', 'N/A')))
                write_log(StaticText.POOL_STATUS_INFO.format(source_status=pool['source_status'], dest_status=pool['dest_status']))
                if pool['warning']:
                    write_log(StaticText.POOL_WARNING_TEMPLATE.format(warning=pool['warning']))

        for i, vm in enumerate(self.vms_to_migrate):
            try:
                write_log(StaticText.CHECKING_VM_HEADER_TEMPLATE.format(vm_name=vm.name()))

                # --- Server Compatibility ---
                write_log(StaticText.SERVER_COMPATIBILITY_HEADER)
                server_issues = check_server_migration_compatibility(self.source_conn, self.dest_conn, vm.name(), self.is_live)

                server_errors = [issue for issue in server_issues if issue['severity'] == 'ERROR']
                for issue in server_errors:
                    write_log(StaticText.SERVER_ERROR_TEMPLATE.format(message=issue['message']))

                server_warnings = [issue for issue in server_issues if issue['severity'] == 'WARNING']
                for issue in server_warnings:
                    write_log(StaticText.SERVER_WARNING_TEMPLATE.format(message=issue['message']))

                server_infos = [issue for issue in server_issues if issue['severity'] == 'INFO']
                # Filter out redundant server-wide informational messages for subsequent VMs
                if i > 0:
                    server_infos = [
                        issue for issue in server_infos
                        if StaticText.FILTER_MANUALLY_VERIFY not in issue['message'] and
                           StaticText.FILTER_FIREWALLS not in issue['message'] and
                           StaticText.FILTER_USER_AND not in issue['message']
                    ]
                for issue in server_infos:
                    write_log(StaticText.SERVER_INFO_TEMPLATE.format(message=issue['message']))


                # --- VM Compatibility ---
                write_log(StaticText.VM_COMPATIBILITY_HEADER)
                vm_issues = check_vm_migration_compatibility(vm, self.dest_conn, self.is_live)

                vm_errors = [issue for issue in vm_issues if issue['severity'] == 'ERROR']
                for issue in vm_errors:
                    write_log(StaticText.VM_ERROR_TEMPLATE.format(message=issue['message']))

                vm_warnings = [issue for issue in vm_issues if issue['severity'] == 'WARNING']
                for issue in vm_warnings:
                    write_log(StaticText.VM_WARNING_TEMPLATE.format(message=issue['message']))

                vm_infos = [issue for issue in vm_issues if issue['severity'] == 'INFO']
                for issue in vm_infos:
                    write_log(StaticText.VM_INFO_TEMPLATE.format(message=issue['message']))

                errors = server_errors + vm_errors
                if errors:
                    all_checks_ok = False
                    self.cannot_migrate_vms.append(vm.name())
                    write_log(StaticText.COMPATIBILITY_CHECK_FAILED_TEMPLATE.format(vm_name=vm.name()))
                else:
                    self.can_migrate_vms.append(vm.name())
                    write_log(StaticText.COMPATIBILITY_CHECK_PASSED_TEMPLATE.format(vm_name=vm.name()))
            except Exception as e:
                write_log(StaticText.FATAL_ERROR_CHECKING_TEMPLATE.format(vm_name=vm.name(), error=e))
                all_checks_ok = False
                self.cannot_migrate_vms.append(vm.name())
                continue # Continue to the next VM

        self.checks_passed = all_checks_ok
        self.compatibility_checked = True

        def update_ui_after_check():
            self._lock_controls(False)
            self.query_one("#start").disabled = not self.checks_passed
            can_migrate_text = "\n".join(f"- {name}" for name in self.can_migrate_vms)
            cannot_migrate_text = "\n".join(f"- {name}" for name in self.cannot_migrate_vms)
            self.query_one("#can-migrate-list").update(can_migrate_text)
            self.query_one("#cannot-migrate-list").update(cannot_migrate_text)
            self.query_one("#migration-summary-grid").styles.display = "block"
        self.app.call_from_thread(update_ui_after_check)

    @work(exclusive=True, thread=True)
    def run_migration(self):
        def write_log(line):
            if threading.current_thread() is threading.main_thread():
                self._write_log_line(line)
            else:
                self.app.call_from_thread(self._write_log_line, line)

        self.app.call_from_thread(self._lock_controls, True)

        progress_bar = self.query_one("#migration-progress", ProgressBar)

        self.app.call_from_thread(lambda: setattr(progress_bar, "total", len(self.vms_to_migrate)))
        self.app.call_from_thread(lambda: setattr(progress_bar, "progress", 0))
        self.app.call_from_thread(lambda: setattr(progress_bar.styles, "display", "block"))

        last_log_percentage = -1

        def log_progress(p):
            p = p*10
            nonlocal last_log_percentage
            # Log at 0 (start), then every 5%, then 100%
            if p == 0 or p >= last_log_percentage + 20:
                write_log(f"  ... {int(p)}%")
                last_log_percentage = int(p)
            if p >= 100:
                last_log_percentage = -1

        # Hide the migration summary grid when migration starts
        self.query_one("#migration-summary-grid").styles.display = "none"

        copy_storage_all = self.query_one("#copy-storage-all", Checkbox).value
        unsafe = self.query_one("#unsafe", Checkbox).value
        persistent = self.query_one("#persistent", Checkbox).value
        compress = self.query_one("#compress", Checkbox).value
        tunnelled = self.query_one("#tunnelled", Checkbox).value

        custom_migration = self.query_one("#custom", Checkbox).value

        for vm in self.vms_to_migrate:
            write_log(StaticText.MIGRATING_VM_HEADER_TEMPLATE.format(vm_name=vm.name()))

            if custom_migration:
                try:
                    actions = custom_migrate_vm(self.source_conn, self.dest_conn, vm, log_callback=write_log)

                    migration_confirmed = threading.Event()
                    user_selections = {}

                    def on_custom_migration_confirm(selections: dict | None):
                        if selections:
                            user_selections.update(selections)
                        migration_confirmed.set()

                    self.app.call_from_thread(self.app.push_screen, CustomMigrationModal(actions), on_custom_migration_confirm)

                    # Wait for the user to interact with the modal
                    migration_confirmed.wait()

                    if user_selections:
                        write_log(StaticText.CUSTOM_MIGRATION_CONFIRMED)
                        execute_custom_migration(
                            self.source_conn,
                            self.dest_conn,
                            actions,
                            user_selections,
                            log_callback=write_log,
                            progress_callback=log_progress
                         )
                    else:
                        write_log(StaticText.CUSTOM_MIGRATION_CANCELLED)

                except Exception as e:
                    write_log(StaticText.CUSTOM_MIGRATION_FAILED_TEMPLATE.format(vm_name=vm.name(), error=e))

                self.app.call_from_thread(progress_bar.advance, 1)
                continue

            try:
                if self.is_live:
                    flags = libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PEER2PEER

                    if copy_storage_all:
                        flags |= libvirt.VIR_MIGRATE_NON_SHARED_DISK
                    if unsafe:
                        flags |= libvirt.VIR_MIGRATE_UNSAFE
                    if persistent:
                        flags |= libvirt.VIR_MIGRATE_PERSIST_DEST
                    if compress:
                        flags |= libvirt.VIR_MIGRATE_COMPRESSED
                    if tunnelled:
                        flags |= libvirt.VIR_MIGRATE_TUNNELLED

                    write_log(StaticText.LIVE_MIGRATION_FLAGS_TEMPLATE.format(flags=flags))
                    # Pass self.dest_uri as the uri argument to ensure correct port/transport is used
                    vm.migrate(self.dest_conn, flags, None, self.dest_uri, 0)
                else:  # Offline migration
                    flags = libvirt.VIR_MIGRATE_OFFLINE | libvirt.VIR_MIGRATE_PEER2PEER
                    if persistent:
                        flags |= libvirt.VIR_MIGRATE_PERSIST_DEST
                    # VIR_MIGRATE_TUNNELLED is not applied for offline migration as it does not make sense.

                    if copy_storage_all:
                        flags |= libvirt.VIR_MIGRATE_NON_SHARED_DISK
                        params = {libvirt.VIR_MIGRATE_PARAM_MIGRATE_DISKS: "*"}
                        write_log(StaticText.OFFLINE_MIGRATION_URI3)
                        vm.migrateToURI3(self.dest_uri, params, flags)
                    else:
                        write_log(StaticText.OFFLINE_MIGRATION_MIGRATE)
                        vm.migrate(self.dest_conn, flags, None, self.dest_uri, 0)


                write_log(StaticText.MIGRATION_SUCCESS_TEMPLATE.format(vm_name=vm.name()))

                if persistent:
                    try:
                        vm.undefine()
                        write_log(StaticText.UNDEFINE_SUCCESS_TEMPLATE.format(vm_name=vm.name()))
                    except libvirt.libvirtError as e:
                        write_log(StaticText.UNDEFINE_FAILED_TEMPLATE.format(vm_name=vm.name(), error=e))

            except libvirt.libvirtError as e:
                write_log(StaticText.MIGRATION_FAILED_TEMPLATE.format(vm_name=vm.name(), error=e))
                if StaticText.HOST_KEY_VERIFICATION_FAILED in str(e):
                    write_log(StaticText.HOST_KEY_HINT)

            self.app.call_from_thread(progress_bar.advance, 1)

        def final_ui_state():
            """Disables all controls except the Close button after migration is finished."""
            self.query_one("#check").disabled = True
            self.query_one("#start").disabled = True
            self.query_one("#dest-server-select").disabled = True
            self.query_one("#copy-storage-all").disabled = True
            self.query_one("#unsafe").disabled = True
            self.query_one("#persistent").disabled = True
            self.query_one("#compress").disabled = True
            self.query_one("#tunnelled").disabled = True
            self.query_one("#close").disabled = False

        write_log(StaticText.MIGRATION_PROCESS_FINISHED)
        self.app.call_from_thread(lambda: setattr(progress_bar.styles, "display", "none"))
        self.app.call_from_thread(self.app.refresh_vm_list, force=True)
        self.app.call_from_thread(final_ui_state)

    @on(Checkbox.Changed, "#custom")
    def on_custom_migration_changed(self, event: Checkbox.Changed):
        """Enable or disable other migration options when custom migration is selected."""
        is_custom = event.value
        self.query_one("#copy-storage-all").disabled = False
        self.query_one("#copy-storage-all").value = is_custom
        self.query_one("#unsafe").disabled = is_custom or not self.is_live
        self.query_one("#persistent").disabled = is_custom
        self.query_one("#compress").disabled = is_custom or not self.is_live
        self.query_one("#tunnelled").disabled = is_custom or not self.is_live

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "check":
            if not self.dest_conn:
                self.app.show_error_message(ErrorMessages.SELECT_DESTINATION_SERVER)
                return
            self._clear_log()
            self.run_compatibility_checks()

        elif event.button.id == "start":
            if not self.compatibility_checked:
                self.app.show_error_message(ErrorMessages.RUN_COMPATIBILITY_CHECK_FIRST)
                return
            if not self.checks_passed:
                self.app.show_error_message(ErrorMessages.MIGRATION_COMPATIBILITY_ERRORS)
                return

            self._clear_log()
            self.run_migration()

        elif event.button.id == "close":
            self.dismiss()
