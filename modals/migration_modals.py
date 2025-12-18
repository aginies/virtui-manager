"""
Modals for handling VM migration.
"""
from typing import List, Dict
import libvirt

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static, Select, Log, Checkbox, Label
from textual import on, work

from vm_actions import check_migration_compatibility
from utils import extract_server_name_from_uri

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
        self.compatibility_checked = False
        self.checks_passed = False
        self.bg_red = "\033[41m"
        self.bg_green = "\033[42m"
        self.bg_yellow = "\033[43m"
        self.bg_blue = "\033[44m"
        self.red = "\033[31m"
        self.green = "\033[32m"
        self.blue = "\033[34m"
        self.reset = "\033[0m"

    def compose(self) -> ComposeResult:
        vm_names = ", ".join([vm.name() for vm in self.vms_to_migrate])
        source_uri = self.source_conn.getURI()
        dest_servers = [
            (extract_server_name_from_uri(uri), uri)
            for uri in self.connections
            if uri != source_uri
        ]
        migration_type = "Live" if self.is_live else "Offline"

        with Vertical(id="migration-dialog", classes="info-details"):
            yield Label(f"[{migration_type}] Migrate VMs: [b]{vm_names}[/b]")
            yield Static("Select destination server:")
            yield Select(dest_servers, id="dest-server-select", prompt="Destination...", disabled=not dest_servers)
            if not dest_servers:
                yield Static("[i]No destination servers available (all active servers are either the source, or there are no active servers).[/i]")
            
            yield Static("Migration Options:")
            with Horizontal():
                yield Checkbox("Copy storage all", id="copy-storage-all", tooltip="Copy all disk files during migration")
                yield Checkbox("Unsafe migration", id="unsafe", tooltip="Perform unsafe migration (may lose data)")
                yield Checkbox("Persistent migration", id="persistent", tooltip="Keep VM persistent on destination")
            with Horizontal():
                yield Checkbox("Compress data", id="compress", tooltip="Compress data during migration")
                yield Checkbox("Tunnelled migration", id="tunnelled", tooltip="Tunnel migration data through libvirt daemon")
            yield Static("Compatibility Check Results / Migration Log:")
            yield Log(id="results-log", highlight=False)
            with Horizontal(classes="modal-buttons"):
                yield Button("Check Compatibility", variant="primary", id="check", classes="Buttonpage")
                yield Button("Start Migration", variant="success", id="start", disabled=True, classes="Buttonpage")
                yield Button("Close", variant="default", id="close", disabled=False, classes="close-button")

    def _lock_controls(self, lock: bool):
        self.query_one("#check").disabled = lock
        self.query_one("#start").disabled = True
        self.query_one("#dest-server-select").disabled = lock
        self.query_one("#close").disabled = lock

    @on(Select.Changed, "#dest-server-select")
    def on_select_changed(self, event: Select.Changed):
        dest_uri = event.value
        if dest_uri:
            self.dest_conn = self.connections[dest_uri]
        else:
            self.dest_conn = None

        self.query_one("#start", Button).disabled = True
        self.compatibility_checked = False
        self.checks_passed = False
        self.query_one("#results-log", Log).clear()

    @work(exclusive=True, thread=True)
    async def run_compatibility_checks(self):
        log = self.query_one("#results-log", Log)
        self.app.call_from_thread(self._lock_controls, True)

        def write_log(line):
            self.app.call_from_thread(log.write_line, line)

        all_checks_ok = True
        for vm in self.vms_to_migrate:
            write_log(f"\n{self.bg_blue}---{self.reset} Checking {vm.name()} {self.bg_blue}---{self.reset}")
            issues = check_migration_compatibility(self.source_conn, self.dest_conn, vm, self.is_live)
            errors = [i for i in issues if "ERROR" in i]
            if errors:
                all_checks_ok = False
                for issue in errors:
                    write_log(f"{self.red}{issue}{self.reset}")

            warnings = [i for i in issues if "WARNING" in i]
            if warnings:
                for issue in warnings:
                     write_log(f"{self.red}WARNING{self.reset}{issue}")

            infos = [i for i in issues if "INFO" in i]
            if infos:
                 for issue in infos:
                      write_log(f"{self.green}INFO{self.reset}{issue}")

            if not errors:
                write_log(f"{self.green}✓ Compatibility checks passed{self.reset} (with warnings if any shown above).")

        self.checks_passed = all_checks_ok
        self.compatibility_checked = True

        def update_ui_after_check():
            self._lock_controls(False)
            self.query_one("#start").disabled = not self.checks_passed
        self.app.call_from_thread(update_ui_after_check)

    @work(exclusive=True, thread=True)
    async def run_migration(self):
        log = self.query_one("#results-log", Log)
        def write_log(line):
            self.app.call_from_thread(log.write_line, line)

        self.app.call_from_thread(self._lock_controls, True)

        for vm in self.vms_to_migrate:
            write_log(f"\n{self.blue}---{self.reset} Migrating {vm.name()} {self.blue}---{self.reset}")
            try:
                if self.is_live:
                    flags = libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PEER2PEER | libvirt.VIR_MIGRATE_PERSIST_DEST
                    # Get checkbox values
                    copy_storage_all = self.query_one("#copy-storage-all", Checkbox).value
                    unsafe = self.query_one("#unsafe", Checkbox).value
                    persistent = self.query_one("#persistent", Checkbox).value
                    compress = self.query_one("#compress", Checkbox).value
                    tunnelled = self.query_one("#tunnelled", Checkbox).value

                    # Apply flags based on checkbox values
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

                    vm.migrate(self.dest_conn, flags, None, None, 0)
                else: # Offline migration
                    xml_desc = vm.XMLDesc(0)
                    self.dest_conn.defineXML(xml_desc)
                    vm.undefine()
                write_log(f"{self.bg_green}✓ Successfully migrated{self.reset} {vm.name()}.")
            except libvirt.libvirtError as e:
                write_log(f"{self.bg_red}ERROR: Failed to migrate{self.reset} {vm.name()}: {e}")

        write_log("\n--- Migration process finished ---")
        self.app.call_from_thread(self.app.refresh_vm_list)
        self.app.call_from_thread(self._lock_controls, False)

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed):
        log = self.query_one("#results-log", Log)
        if event.button.id == "check":
            if not self.dest_conn:
                self.app.show_error_message("Please select a destination server.")
                return
            log.clear()
            self.run_compatibility_checks()

        elif event.button.id == "start":
            if not self.compatibility_checked:
                self.app.show_error_message("Please run compatibility check first.")
                return
            if not self.checks_passed:
                self.app.show_error_message("Cannot start migration due to compatibility errors.")
                return

            log.clear()
            self.run_migration()

        elif event.button.id == "close":
            self.dismiss()
